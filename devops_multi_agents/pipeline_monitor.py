"""
Pipeline execution monitor — bridges CI/CD push → GitHub watch → incident store → resolution.

Flow
----
1. Pipelines are pushed to GitHub (by orchestrator).
2. PipelineMonitor discovers the triggered workflow runs.
3. It polls each run until completion.
4. On failure it:
   a. Extracts structured error details from the GitHub API.
   b. Stores an incident document in MongoDB ``incidents`` collection.
   c. Invokes IncidentResponseAgent to diagnose and propose a fix.
5. Returns a list of per-pipeline monitor results.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FAILURE_CONCLUSIONS = {"failure", "cancelled", "action_required", "stale", "timed_out"}


class PipelineMonitor:
    """
    Watches GitHub Actions runs and auto-triggers incident response on failure.

    Parameters
    ----------
    github_token:
        Personal access token (or GITHUB_TOKEN in Actions) with
        ``repo`` and ``actions:read`` scopes.
    github_repo:
        Repository slug in ``owner/repo`` format.
    mongo_uri:
        MongoDB connection string.  Falls back to ``MONGO_URI`` env var.
    mongo_db:
        Database name.  Falls back to ``MONGO_DB_NAME`` env var (default ``CogniOps``).
    poll_interval:
        Seconds between status checks (default 30).
    timeout:
        Maximum seconds to wait for a single run (default 1800 = 30 min).
    """

    def __init__(
        self,
        *,
        github_token: str,
        github_repo: str,
        mongo_uri: str | None = None,
        mongo_db: str | None = None,
        poll_interval: int = 30,
        timeout: int = 1800,
        auto_approve_fix: bool = False,
        auto_apply: bool = False,
    ) -> None:
        from .github_monitor import GitHubActionsMonitor

        try:
            self.gh = GitHubActionsMonitor(token=github_token, repo=github_repo)
        except (ValueError, RuntimeError) as exc:
            raise RuntimeError(
                f"GitHub monitoring cannot start: {exc}"
            ) from exc
        self.poll_interval = poll_interval
        self.timeout = timeout

        self._mongo_uri = (
            mongo_uri
            or os.getenv("MONGO_URI")
            or os.getenv("MOONGO_URI")
            or "mongodb://localhost:27017/"
        )
        self._mongo_db = mongo_db or os.getenv("MONGO_DB_NAME", "CogniOps")
        self._incidents_col = None
        self.auto_approve_fix = auto_approve_fix
        self.auto_apply = auto_apply
        self._init_mongo()

    # ------------------------------------------------------------------
    # MongoDB
    # ------------------------------------------------------------------

    def _init_mongo(self) -> None:
        try:
            from pymongo import MongoClient

            client = MongoClient(self._mongo_uri, serverSelectionTimeoutMS=5000)
            client.admin.command("ping")
            db = client[self._mongo_db]
            self._incidents_col = db["incidents"]
            logger.info(
                "PipelineMonitor connected to MongoDB @ %s / %s",
                self._mongo_uri, self._mongo_db,
            )
        except Exception as exc:
            logger.warning(
                "PipelineMonitor: MongoDB unavailable (%s) — "
                "incidents will not be persisted.",
                exc,
            )

    def _store_incident(self, incident: dict[str, Any]) -> str | None:
        """
        Upsert incident in MongoDB by ``github_run_id`` to avoid duplicates.
        Returns the document ``_id`` as a string (or None if MongoDB is down).
        """
        if self._incidents_col is None:
            logger.warning("No MongoDB connection — skipping incident persistence.")
            return None

        from pymongo import ReturnDocument

        now = datetime.now(timezone.utc).isoformat()
        incident.setdefault("created_at", now)
        incident["updated_at"] = now

        run_id = incident.get("github_run_id")
        if run_id:
            set_fields = {k: v for k, v in incident.items() if k != "occurrences"}
            doc = self._incidents_col.find_one_and_update(
                {"github_run_id": run_id},
                {"$set": set_fields, "$inc": {"occurrences": 1}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            incident_id = str(doc["_id"])
        else:
            result = self._incidents_col.insert_one(incident)
            incident_id = str(result.inserted_id)

        logger.info("Incident persisted in MongoDB — _id=%s", incident_id)
        return incident_id

    def _update_incident_status(
        self, incident_id: str, status: str, resolution: dict[str, Any]
    ) -> None:
        """Mark incident as resolved/investigating after agent finishes."""
        if self._incidents_col is None or not incident_id:
            return
        try:
            from bson import ObjectId

            self._incidents_col.update_one(
                {"_id": ObjectId(incident_id)},
                {
                    "$set": {
                        "status": status,
                        "resolution": resolution,
                        "resolved_at": datetime.now(timezone.utc).isoformat(),
                    }
                },
            )
        except Exception as exc:
            logger.warning("Could not update incident %s in MongoDB: %s", incident_id, exc)

    # ------------------------------------------------------------------
    # Incident Response Agent invocation
    # ------------------------------------------------------------------

    def _handle_workflow_file_errors(
        self,
        *,
        file_errors: list[dict],
        branch: str,
        architecture_data: dict,
        output_dir,
        repository_path: Path | None = None,
    ) -> list[dict]:
        """
        Build incidents from GitHub workflow file validation errors and invoke
        the Incident Response Agent for each one.
        """
        results = []
        for err in file_errors:
            workflow_file = err.get("workflow_file", "unknown")
            title = err.get("title", "Invalid workflow file")
            summary_text = err.get("summary", "")

            # Build a human-readable error message from annotations
            annotation_messages = []
            for ann in err.get("annotations", []):
                path = ann.get("path", "")
                line = ann.get("start_line", "?")
                col = ann.get("start_column", "?")
                msg = ann.get("message", "")
                if msg:
                    annotation_messages.append(f"{path} line {line} col {col}: {msg}")

            error_detail = "\n".join(annotation_messages) or summary_text

            print(f"\n  ❌  Workflow file error: {workflow_file}")
            print(f"      {title}")
            for ann_line in annotation_messages:
                print(f"      {ann_line}")

            incident: dict = {
                "_source": "github_actions_validation",
                "status": "open",
                "severity": "high",
                "environment": "ci",
                "service_names": [workflow_file.replace("-pipeline", "").replace(".yml", "")],
                "summary": (
                    f"GitHub Actions workflow file error in '{workflow_file}' "
                    f"on branch '{branch}': {title}"
                ),
                "last_error_payload": [
                    {
                        "job": "workflow-file-validation",
                        "failed_steps": ["YAML syntax / expression validation"],
                        "stderr_tail": error_detail,
                        "message": title,
                        "error": summary_text,
                        "annotations": err.get("annotations", []),
                    }
                ],
                "occurrences": 1,
                "github_workflow_file": workflow_file,
                "github_details_url": err.get("details_url", ""),
                "branch": branch,
            }

            incident_id = self._store_incident(incident)
            incident["_id"] = incident_id
            print(f"  💾  Incident stored — _id={incident_id}")
            print("  🤖  Invoking Incident Response Agent…")

            ir_result = self._invoke_incident_response(
                incident, architecture_data, output_dir, repository_path
            )

            ir_status = "investigating"
            processed = ir_result.get("processed_incidents") or []
            if processed:
                path_val = processed[0].get("resolution_path", "none")
                ir_status = "resolved" if path_val in ("rag", "llm") else "investigating"

            self._update_incident_status(incident_id or "", ir_status, ir_result)
            print(f"  ✅  Response complete — status='{ir_status}'")

            results.append(
                {
                    "run_id": None,
                    "run_name": workflow_file,
                    "conclusion": "invalid_workflow_file",
                    "url": err.get("details_url", ""),
                    "incident_id": incident_id,
                    "incident_response": ir_result,
                    "workflow_errors": err.get("annotations", []),
                }
            )
        return results

    def _invoke_incident_response(
        self,
        incident: dict[str, Any],
        architecture_data: dict[str, Any],
        output_dir: Path | None = None,
        repository_path: Path | None = None,
    ) -> dict[str, Any]:
        """
        Write the incident to a temporary JSON file and call IncidentResponseAgent.

        Using a temp mock file ensures the agent processes exactly this incident
        rather than pulling unrelated open incidents from MongoDB.
        """
        from .agents.incident_response.agent import IncidentResponseAgent

        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as fp:
                json.dump([incident], fp, default=str)
                tmp_path = Path(fp.name)

            agent = IncidentResponseAgent(
                repository_path=str(repository_path) if repository_path else None,
                auto_approve_fix=self.auto_approve_fix,
                auto_apply=self.auto_apply,
            )
            result = agent.run(
                architecture_data=architecture_data,
                security_data={},
                chaos_data={},
                mock_incidents_path=str(tmp_path),
                max_incidents=0,
                output_dir=str(output_dir) if output_dir else None,
                repository_path=str(repository_path) if repository_path else None,
                auto_approve_fix=self.auto_approve_fix,
            )
            return result.data if result.success else {"error": result.error}
        except Exception as exc:
            logger.error("IncidentResponseAgent invocation failed: %s", exc)
            return {"error": str(exc)}
        finally:
            if tmp_path and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Fix tracking
    # ------------------------------------------------------------------

    def _record_fix_attempt(
        self,
        incident_id: str | None,
        *,
        attempt: int,
        strategy: str,
        fix_type: str | None,
        patched: bool,
        commit_branch: str | None = None,
    ) -> None:
        """Append a fix-attempt entry to the incident document in MongoDB."""
        if self._incidents_col is None or not incident_id:
            return
        try:
            from bson import ObjectId

            entry = {
                "attempt": attempt,
                "strategy": strategy,
                "fix_type": fix_type or "unknown",
                "patched": patched,
                "applied_at": datetime.now(timezone.utc).isoformat(),
                "commit_branch": commit_branch,
                "re_run_conclusion": None,
            }
            self._incidents_col.update_one(
                {"_id": ObjectId(incident_id)},
                {"$push": {"fix_attempts": entry}},
            )
            logger.info("Fix attempt #%d recorded for incident %s", attempt, incident_id)
        except Exception as exc:
            logger.warning("Could not record fix attempt for %s: %s", incident_id, exc)

    def _update_fix_result(
        self,
        incident_id: str | None,
        *,
        attempt: int,
        re_run_conclusion: str,
    ) -> None:
        """Update the last fix-attempt entry with the re-run outcome."""
        if self._incidents_col is None or not incident_id:
            return
        try:
            from bson import ObjectId

            self._incidents_col.update_one(
                {"_id": ObjectId(incident_id), "fix_attempts.attempt": attempt},
                {"$set": {"fix_attempts.$.re_run_conclusion": re_run_conclusion}},
            )
        except Exception as exc:
            logger.warning("Could not update fix result for %s: %s", incident_id, exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def monitor_and_resolve(
        self,
        *,
        branch: str,
        pipeline_names: list[str],
        push_timestamp: str,
        architecture_data: dict[str, Any],
        output_dir: Path | None = None,
        repository_path: Path | None = None,
        _fix_attempt: int = 1,
        max_fix_retries: int = 2,
    ) -> list[dict[str, Any]]:
        """
        Discover runs triggered by the push, wait for each to finish,
        and handle failures end-to-end.

        After a fix is auto-patched and pushed, the method re-monitors the
        newly triggered pipeline run to verify the fix worked.  Up to
        *max_fix_retries* retry cycles are performed before giving up.
        """
        print(f"\n{'─'*60}")
        print(f"  🔍  Monitoring {len(pipeline_names)} pipeline(s) on branch '{branch}'"
              + (f"  [fix attempt {_fix_attempt}/{max_fix_retries}]" if _fix_attempt > 1 else ""))
        print(f"      Pushed at: {push_timestamp}")
        print(f"{'─'*60}\n")

        runs = self.gh.find_triggered_runs_with_retry(
            branch=branch,
            pipeline_names=pipeline_names,
            after_timestamp=push_timestamp,
        )

        if not runs:
            # No runs found — could be invalid workflow files (GitHub rejects them
            # before creating a run). Check commit check-runs for file errors.
            print("\n  ⚠️  No workflow runs discovered — checking for workflow file errors…")
            commit_sha = self.gh.get_head_commit_sha(branch)
            file_errors = self.gh.get_workflow_file_errors(commit_sha) if commit_sha else []

            if file_errors:
                return self._handle_workflow_file_errors(
                    file_errors=file_errors,
                    branch=branch,
                    architecture_data=architecture_data,
                    output_dir=output_dir,
                    repository_path=repository_path,
                )

            logger.warning(
                "No workflow runs found for pipelines %s. "
                "Check that GitHub Actions is enabled on the repository.",
                pipeline_names,
            )
            return [
                {
                    "status": "not_found",
                    "message": (
                        "No GitHub Actions runs were discovered and no workflow file "
                        "errors were found. Ensure the repository has Actions enabled "
                        "and the workflow files were committed to the correct branch."
                    ),
                }
            ]

        results: list[dict[str, Any]] = []

        for run in runs:
            run_id: int = run["id"]
            run_name: str = run.get("name", "unknown")

            print(f"  ⏳  Watching run #{run_id}  ({run_name})…")

            final_run = self.gh.wait_for_run(
                run_id,
                poll_interval=self.poll_interval,
                timeout=self.timeout,
            )

            conclusion = final_run.get("conclusion", "unknown")
            run_url = final_run.get("html_url", "")

            entry: dict[str, Any] = {
                "run_id": run_id,
                "run_name": run_name,
                "conclusion": conclusion,
                "url": run_url,
                "fix_attempt": _fix_attempt,
                "incident_id": None,
                "incident_response": None,
            }

            if conclusion in _FAILURE_CONCLUSIONS:
                print(f"\n  ⚠️   Run #{run_id} FAILED (conclusion={conclusion})")
                print(f"      URL: {run_url}")
                print("  📋  Extracting failure details…")

                incident = self.gh.extract_failure_details(final_run)
                incident_id = self._store_incident(incident)
                incident["_id"] = incident_id

                print(f"  💾  Incident stored in MongoDB — _id={incident_id}")
                print("  🤖  Invoking Incident Response Agent…")

                ir_result = self._invoke_incident_response(
                    incident, architecture_data, output_dir, repository_path
                )

                # Determine resolution path and patch details
                ir_status = "investigating"
                patched = False
                fix_type: str | None = None
                commit_branch: str | None = None
                processed = (ir_result.get("processed_incidents") or [])
                if processed:
                    res_path = processed[0].get("resolution_path", "none")
                    ir_status = "resolved" if res_path in ("rag", "llm") else "investigating"
                    apply_result = processed[0].get("apply_result") or {}
                    patch_result = apply_result.get("patch_result") or {}
                    patched = bool(patch_result.get("patched"))
                    if patched:
                        fixes = patch_result.get("fixes") or []
                        fix_type = fixes[0].get("type") if fixes else None
                        commit_branch = (patch_result.get("commit") or {}).get("branch")

                self._update_incident_status(incident_id or "", ir_status, ir_result)
                self._record_fix_attempt(
                    incident_id,
                    attempt=_fix_attempt,
                    strategy=processed[0].get("resolution_path", "none") if processed else "none",
                    fix_type=fix_type,
                    patched=patched,
                    commit_branch=commit_branch,
                )

                entry["incident_id"] = incident_id
                entry["incident_response"] = ir_result

                print(
                    f"  ✅  Incident response complete — status='{ir_status}'"
                    f" path={processed[0].get('resolution_path') if processed else 'n/a'}"
                    + (f" | fix_type={fix_type} → pushed to '{commit_branch}'" if patched else "")
                )

                # ── Re-monitoring loop ────────────────────────────────────────
                # If a fix was auto-patched and pushed, wait for the newly triggered
                # pipeline run and verify whether the fix resolved the issue.
                if patched and _fix_attempt < max_fix_retries:
                    import time
                    wait_s = 45
                    print(f"\n  🔄  Fix pushed — waiting {wait_s}s for GitHub to trigger new run…")
                    time.sleep(wait_s)
                    new_ts = datetime.now(timezone.utc).isoformat()
                    retry_results = self.monitor_and_resolve(
                        branch=branch,
                        pipeline_names=pipeline_names,
                        push_timestamp=new_ts,
                        architecture_data=architecture_data,
                        output_dir=output_dir,
                        repository_path=repository_path,
                        _fix_attempt=_fix_attempt + 1,
                        max_fix_retries=max_fix_retries,
                    )
                    # Record re-run outcome back on the original incident
                    if retry_results:
                        re_conclusion = retry_results[0].get("conclusion", "unknown")
                        self._update_fix_result(
                            incident_id,
                            attempt=_fix_attempt,
                            re_run_conclusion=re_conclusion,
                        )
                        if re_conclusion not in _FAILURE_CONCLUSIONS:
                            print(f"  ✅  Re-run succeeded after fix (attempt {_fix_attempt}) — pipeline green.")
                        else:
                            print(f"  ⚠️   Re-run still failing after fix (attempt {_fix_attempt}/{max_fix_retries}).")
                    results.extend(retry_results)

            else:
                print(f"  ✅  Run #{run_id} succeeded (conclusion={conclusion})")

            results.append(entry)

        print(f"\n{'─'*60}")
        print(f"  Monitor complete — {len(results)} run(s) processed (fix attempt {_fix_attempt}).")
        print(f"{'─'*60}\n")

        return results
