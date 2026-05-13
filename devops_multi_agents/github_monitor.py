"""
GitHub Actions workflow monitor.

Polls the GitHub REST API to track workflow runs triggered by a CI/CD
pipeline push.  On failure, returns structured error details suitable
for incident creation and Incident Response Agent processing.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
_TERMINAL = {"completed", "timed_out"}
_FAILURE_CONCLUSIONS = {"failure", "cancelled", "action_required", "stale", "timed_out"}


class GitHubActionsMonitor:
    """Thin wrapper around the GitHub REST API focused on Actions workflow runs."""

    def __init__(self, token: str, repo: str) -> None:
        try:
            import requests as _req
        except ImportError as exc:
            raise RuntimeError(
                "The 'requests' package is required for GitHub monitoring. "
                "Install with: pip install requests"
            ) from exc

        self._requests = _req
        self.repo = repo
        self._session = _req.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        self._validate_auth(token)

    def _validate_auth(self, token: str) -> None:
        """Fail fast if the token is missing or rejected by GitHub."""
        if not token or not token.strip():
            raise ValueError(
                "GitHub token is empty. "
                "Pass --github-token <token> or set GITHUB_TOKEN in your environment."
            )
        try:
            resp = self._session.get(f"{GITHUB_API}/repos/{self.repo}")
            if resp.status_code == 401:
                raise RuntimeError(
                    "GitHub token is invalid or expired (401 Unauthorized). "
                    "Generate a new token at https://github.com/settings/tokens with "
                    "'repo' and 'workflow' scopes."
                )
            if resp.status_code == 403:
                raise RuntimeError(
                    f"GitHub token lacks required permissions for '{self.repo}' (403 Forbidden). "
                    "Ensure the token has 'repo' and 'workflow' scopes."
                )
            if resp.status_code == 404:
                raise RuntimeError(
                    f"Repository '{self.repo}' not found or token has no access to it. "
                    "Check that --github-repo is 'owner/repo' and the token has repo access."
                )
            resp.raise_for_status()
            logger.info("GitHub token validated — repo '%s' accessible.", self.repo)
        except self._requests.RequestException as exc:
            raise RuntimeError(f"GitHub API connectivity check failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Low-level API helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, **params: Any) -> Any:
        url = path if path.startswith("http") else f"{GITHUB_API}{path}"
        resp = self._session.get(url, params=params or None)
        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"GitHub API auth error {resp.status_code} for {url}. "
                "Check your GITHUB_TOKEN is valid and has 'repo' + 'workflow' scopes."
            )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Run discovery
    # ------------------------------------------------------------------

    def find_triggered_runs(
        self,
        *,
        branch: str = "main",
        after_timestamp: str | None = None,
        pipeline_names: list[str] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Return workflow runs on *branch* created after *after_timestamp*.

        NOTE: The GitHub Actions REST API does not support a `created` query
        filter — that syntax belongs to the Search API.  We fetch recent runs
        and filter client-side by comparing ``created_at`` to *after_timestamp*.
        """
        seen: set[int] = set()
        runs: list[dict[str, Any]] = []

        # Fetch across active and recent-completed statuses
        for status in ("in_progress", "queued", "waiting", "completed"):
            params: dict[str, Any] = {
                "branch": branch,
                "per_page": limit,
                "status": status,
            }
            try:
                page = self._get(
                    f"/repos/{self.repo}/actions/runs", **params
                ).get("workflow_runs", [])
            except RuntimeError as exc:
                # Auth errors (401/403) are raised as RuntimeError by _get — propagate them
                raise
            except Exception as exc:
                logger.debug("Skipping status=%s (API error: %s)", status, exc)
                continue

            for run in page:
                if run["id"] in seen:
                    continue

                # Client-side timestamp filter (compare ISO strings lexicographically)
                if after_timestamp:
                    run_created = run.get("created_at", "")
                    if run_created and run_created < after_timestamp:
                        continue

                # Loose name filter: accept run if ANY slug appears in the run name,
                # or skip filtering entirely when no names provided
                if pipeline_names:
                    run_name = run.get("name", "")
                    # Normalise both sides: lowercase, replace separators
                    norm_run = run_name.lower().replace("-", "_")
                    if not any(
                        slug.lower().replace("-", "_") in norm_run
                        for slug in pipeline_names
                    ):
                        continue

                seen.add(run["id"])
                runs.append(run)

        return runs

    def find_triggered_runs_with_retry(
        self,
        *,
        branch: str,
        pipeline_names: list[str],
        after_timestamp: str,
        retries: int = 10,
        wait: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Retry finding runs because GitHub may take 30-60 s to register them
        after receiving the push webhook.

        Falls back to a name-agnostic search on the last two attempts so that
        a slightly different workflow display name doesn't cause a miss.
        """
        for attempt in range(1, retries + 1):
            # On the last two attempts drop the name filter to catch anything recent
            names = pipeline_names if attempt <= retries - 2 else None
            runs = self.find_triggered_runs(
                branch=branch,
                pipeline_names=names,
                after_timestamp=after_timestamp,
            )
            if runs:
                logger.info(
                    "Found %d workflow run(s) on attempt %d/%d.",
                    len(runs), attempt, retries,
                )
                return runs
            logger.info(
                "No runs found yet (attempt %d/%d) — waiting %ds…",
                attempt, retries, wait,
            )
            time.sleep(wait)
        logger.warning(
            "No GitHub Actions runs discovered for pipelines %s after %d attempts.",
            pipeline_names, retries,
        )
        return []

    # ------------------------------------------------------------------
    # Run polling
    # ------------------------------------------------------------------

    def wait_for_run(
        self,
        run_id: int,
        *,
        poll_interval: int = 30,
        timeout: int = 1800,
    ) -> dict[str, Any]:
        """
        Poll a workflow run until it reaches a terminal state or times out.
        Returns the final run dict (with status/conclusion fields populated).
        """
        elapsed = 0
        while elapsed < timeout:
            try:
                run = self._get(f"/repos/{self.repo}/actions/runs/{run_id}")
            except Exception as exc:
                logger.warning("Error fetching run #%s: %s — retrying…", run_id, exc)
                time.sleep(poll_interval)
                elapsed += poll_interval
                continue

            status = run.get("status", "")
            conclusion = run.get("conclusion")

            logger.info(
                "  Run #%s %-14s conclusion=%-12s  elapsed=%ds",
                run_id, status, conclusion or "—", elapsed,
            )
            # Stdout heartbeat so the user can see we're waiting, not idle
            print(f"    ... run #{run_id} status={status} (elapsed {elapsed}s)", flush=True)

            if status in _TERMINAL:
                print(
                    f"    ✓ run #{run_id} reached terminal state '{status}' "
                    f"after {elapsed}s — conclusion='{conclusion or 'unknown'}'",
                    flush=True,
                )
                return run

            time.sleep(poll_interval)
            elapsed += poll_interval

        logger.warning("Timed out waiting for run #%s after %ds.", run_id, timeout)
        return {"id": run_id, "status": "timed_out", "conclusion": "timed_out"}

    # ------------------------------------------------------------------
    # Failure detail extraction
    # ------------------------------------------------------------------

    def get_run_jobs(self, run_id: int) -> list[dict[str, Any]]:
        try:
            return self._get(
                f"/repos/{self.repo}/actions/runs/{run_id}/jobs"
            ).get("jobs", [])
        except Exception as exc:
            logger.warning("Could not fetch jobs for run #%s: %s", run_id, exc)
            return []

    # Lines containing any of these markers are preserved verbatim during
    # log compaction — they almost always carry the actual failure message.
    _ERROR_LINE_MARKERS = (
        "ERROR", "FAILED", "FAILURE", "Exception", "Traceback",
        "npm ERR!", "npm err!", "fatal:", "error:", "Error:",
        "Permission denied", "Connection refused", "Cannot find",
        "Cannot start", "No such file", "Could not resolve", "BUILD FAILURE",
        "exit code", "exit status", "Missing X server", "$DISPLAY",
        "failed to initialize",
    )

    def get_job_logs(self, job_id: int, max_bytes: int = 16000) -> str:
        """
        Fetch raw text logs for a job (GitHub redirects to a pre-signed S3 URL).

        Smart truncation: keep a head slice, a tail slice, AND every line that
        looks like an actual error message — so the real failure survives even
        when the log is dominated by deprecation warnings or build noise.
        """
        try:
            url = f"{GITHUB_API}/repos/{self.repo}/actions/jobs/{job_id}/logs"
            resp = self._session.get(url, allow_redirects=True)
            if resp.status_code != 200:
                return ""
            text = resp.text
            if len(text) <= max_bytes:
                return text

            head = text[: max_bytes // 4]
            tail = text[-(max_bytes // 2):]

            # Extract error-marker lines from the middle (between head and tail)
            # so they aren't lost to truncation.
            middle = text[len(head): len(text) - len(tail)]
            error_lines = [
                ln for ln in middle.splitlines()
                if any(marker in ln for marker in self._ERROR_LINE_MARKERS)
            ]
            seen: set[str] = set()
            preserved: list[str] = []
            for ln in reversed(error_lines):
                key = ln.strip()
                if key and key not in seen:
                    seen.add(key)
                    preserved.append(ln)
                if len(preserved) >= 50:
                    break
            preserved.reverse()
            error_block = "\n".join(preserved)

            sep = "\n...[truncated]...\n"
            if error_block:
                return f"{head}{sep}ERROR LINES (preserved):\n{error_block}{sep}{tail}"
            return f"{head}{sep}{tail}"
        except Exception as exc:
            logger.debug("Could not fetch logs for job #%s: %s", job_id, exc)
        return ""

    def get_job_annotations(self, job_id: int) -> list[dict[str, Any]]:
        """
        Fetch check-run annotations for a job.

        GitHub Actions posts step-level warnings/errors as annotations on the
        underlying check run — the job ID doubles as the check-run ID.
        """
        try:
            result = self._get(
                f"/repos/{self.repo}/check-runs/{job_id}/annotations",
                per_page=50,
            )
            if isinstance(result, list):
                return result
        except Exception as exc:
            logger.debug("Could not fetch annotations for job #%s: %s", job_id, exc)
        return []

    def get_workflow_file_errors(self, commit_sha: str) -> list[dict[str, Any]]:
        """
        Detect "Invalid workflow file" errors that GitHub reports as check-run
        annotations on the commit — these never create a workflow run, so the
        normal run-polling loop would miss them entirely.

        Returns a list of error dicts, one per invalid workflow file.
        """
        errors: list[dict[str, Any]] = []
        try:
            check_runs = self._get(
                f"/repos/{self.repo}/commits/{commit_sha}/check-runs",
                per_page=50,
            ).get("check_runs", [])
        except Exception as exc:
            logger.warning("Could not fetch check-runs for %s: %s", commit_sha, exc)
            return errors

        for cr in check_runs:
            if cr.get("conclusion") not in ("failure", "action_required"):
                continue
            output = cr.get("output") or {}
            title = output.get("title", "")
            summary = output.get("summary", "")
            # GitHub labels workflow file errors with this title pattern
            if "invalid workflow" not in title.lower() and "workflow" not in summary.lower():
                continue

            # Fetch annotations for this check-run (contain the exact line/col)
            ann_url = output.get("annotations_url", "")
            annotations: list[dict] = []
            if ann_url:
                try:
                    annotations = self._get(ann_url, per_page=50)
                    if not isinstance(annotations, list):
                        annotations = []
                except Exception:
                    pass

            errors.append(
                {
                    "check_run_id": cr.get("id"),
                    "workflow_file": cr.get("name", ""),
                    "title": title,
                    "summary": summary,
                    "annotations": annotations,
                    "details_url": cr.get("details_url", ""),
                }
            )
        return errors

    def get_head_commit_sha(self, branch: str = "main") -> str | None:
        """Return the SHA of the latest commit on *branch*."""
        try:
            ref = self._get(f"/repos/{self.repo}/git/ref/heads/{branch}")
            return ref.get("object", {}).get("sha")
        except Exception as exc:
            logger.warning("Could not resolve HEAD sha for branch '%s': %s", branch, exc)
            return None

    def extract_failure_details(self, run: dict[str, Any]) -> dict[str, Any]:
        """
        Build a structured incident dict from a failed workflow run.
        The dict matches the schema expected by IncidentResponseAgent.
        """
        run_id: int = run.get("id", 0)
        run_name: str = run.get("name", "unknown-workflow")
        branch: str = run.get("head_branch", "main")
        sha: str = (run.get("head_sha") or "")[:8]
        conclusion: str = run.get("conclusion", "failure")
        html_url: str = run.get("html_url", "")

        failed_jobs: list[str] = []
        error_payloads: list[dict[str, Any]] = []

        for job in self.get_run_jobs(run_id):
            if job.get("conclusion") not in _FAILURE_CONCLUSIONS:
                continue

            job_name: str = job.get("name", "unknown-job")
            job_id: int = job["id"]
            failed_jobs.append(job_name)

            failed_steps = [
                s["name"]
                for s in job.get("steps", [])
                if s.get("conclusion") in _FAILURE_CONCLUSIONS
            ]

            logs = self.get_job_logs(job_id)

            # Fetch check-run annotations (step-level warnings/errors posted by GitHub)
            raw_annotations = self.get_job_annotations(job_id)
            error_annotations: list[str] = []   # failure/error level
            warning_annotations: list[str] = []  # notice/warning level
            for ann in raw_annotations:
                level = ann.get("annotation_level", "notice")
                msg = ann.get("message", "").strip()
                ann_path = ann.get("path", "")
                line = ann.get("start_line", "")
                if msg:
                    location = f"{ann_path}:{line} " if ann_path else ""
                    formatted = f"[{level}] {location}{msg}"
                    if level in ("failure", "error"):
                        error_annotations.append(formatted)
                    else:
                        warning_annotations.append(formatted)

            # All annotations preserved for context; errors come first
            annotation_messages = error_annotations + warning_annotations

            # Primary annotations: only errors, or all if no errors found
            primary_annotations = error_annotations if error_annotations else annotation_messages

            combined_output = (
                f"ANNOTATIONS:\n{chr(10).join(annotation_messages)}\n\nLOG TAIL:\n{logs}"
                if annotation_messages else logs
            )

            # Always scan the log for concrete error lines — annotations are
            # often generic ("Process completed with exit code 1") while the
            # real failure (npm ERR!, Missing X server, Connection refused, …)
            # lives in the log. Promoting these into message + summary makes
            # RAG retrieval and deterministic pattern matching meaningful.
            extracted_error_lines: list[str] = []
            if logs:
                seen_lines: set[str] = set()
                for ln in logs.splitlines():
                    stripped = ln.strip()
                    if not stripped or stripped in seen_lines:
                        continue
                    if any(marker in stripped for marker in self._ERROR_LINE_MARKERS):
                        seen_lines.add(stripped)
                        extracted_error_lines.append(stripped)
                    if len(extracted_error_lines) >= 10:
                        break

            full_message = f"Job '{job_name}' failed"
            if failed_steps:
                full_message += f" at step(s): {', '.join(failed_steps)}"
            if primary_annotations:
                full_message += " — " + " | ".join(primary_annotations)
            if extracted_error_lines:
                full_message += " — LOG ERRORS: " + " | ".join(extracted_error_lines[:5])

            error_payloads.append(
                {
                    "job": job_name,
                    "failed_steps": failed_steps,
                    "stderr_tail": combined_output,
                    "annotations": annotation_messages,
                    "extracted_error_lines": extracted_error_lines,
                    "message": full_message,
                    "error": conclusion,
                }
            )

        severity = "critical" if conclusion == "failure" and failed_jobs else "high"
        service_slug = (
            run_name.replace("-pipeline", "").replace("_pipeline", "").strip()
        )

        # Collect all annotation messages AND extracted log error lines across
        # all failed jobs — both feed the summary, RAG query, and pattern match.
        all_annotations: list[str] = []
        all_log_errors: list[str] = []
        for ep in error_payloads:
            all_annotations.extend(ep.get("annotations", []))
            all_log_errors.extend(ep.get("extracted_error_lines", []))

        summary = (
            f"GitHub Actions pipeline '{run_name}' {conclusion} on branch "
            f"'{branch}' (commit {sha})"
            + (f" — failed jobs: {', '.join(failed_jobs)}" if failed_jobs else "")
        )
        if all_annotations:
            summary += " | annotations: " + " | ".join(all_annotations)
        if all_log_errors:
            # Cap to avoid bloating the doc; full set lives in stderr_tail.
            summary += " | log errors: " + " | ".join(all_log_errors[:8])

        return {
            "_source": "github_actions",
            "status": "open",
            "severity": severity,
            "environment": "ci",
            "service_names": [service_slug],
            "summary": summary,
            "last_error_payload": error_payloads,
            "occurrences": 1,
            "github_run_url": html_url,
            "github_run_id": run_id,
            "github_workflow_file": f".github/workflows/{service_slug}-pipeline.yml",
            "branch": branch,
            "commit_sha": run.get("head_sha", ""),
            "failed_jobs": failed_jobs,
        }
