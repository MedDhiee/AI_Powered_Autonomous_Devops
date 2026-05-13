"""
IncidentResponseAgent – Hybrid MCP + RAG + LLM remediation agent.

Pipeline
────────
1. Load incidents from MongoDB (or a mock JSON file for offline runs).
2. For each incident build a rich query string from its error payload.
3. Query the RAG MCP server for semantically similar known incidents.
   a. High confidence match  →  apply known solution directly.
   b. No / low confidence   →  fall back to LLM reasoning (Groq).
4. LLM path:
   a. The LLM receives incident context (logs, metadata) and produces a
      structured diagnosis: root cause, proposed fix, risk level, and
      required validation steps.
   b. A validation layer runs rule-based safety checks on the proposed fix.
   c. The validated fix is surfaced to the operator (or auto-applied when
      risk_level is low and auto_apply is set).
5. Feedback loop: after a fix is confirmed (RAG or LLM), the incident +
   solution is persisted back to the RAG knowledge base so future
   occurrences are handled automatically.
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared import BaseAgent, ProtocolType

from .llm_reasoner import LLMReasoner
from .mcp_client import RagMcpClient
from .solution_applier import SolutionApplier

try:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    PYMONGO_AVAILABLE = True
except ImportError:
    MongoClient = None  # type: ignore[assignment,misc]
    PyMongoError = Exception  # type: ignore[assignment,misc]
    PYMONGO_AVAILABLE = False

logger = logging.getLogger("incident_response_agent")

_DEFAULT_SIMILARITY_THRESHOLD = float(os.getenv("RAG_SIMILARITY_THRESHOLD", "0.85"))

# Commands / keywords that must never appear in auto-applied fixes
_BLOCKED_PATTERNS = (
    "drop table",
    "drop database",
    "delete from",
    "rm -rf /",
    "format ",
    "mkfs",
    "dd if=",
    "git push --force",
    "git push -f",
    "> /dev/sda",
)


# ---------------------------------------------------------------------------
# Validation layer
# ---------------------------------------------------------------------------

def _validate_fix(llm_result: dict[str, Any]) -> dict[str, Any]:
    """
    Apply rule-based safety checks to an LLM-proposed fix.

    Returns a validation report:
      {
        "allowed": bool,
        "blocked_reasons": [...],
        "warnings": [...],
        "requires_human_approval": bool,
      }
    """
    blocked: list[str] = []
    warnings: list[str] = []

    proposed = llm_result.get("proposed_fix", {})
    patch_text_parts: list[str] = []
    for p in proposed.get("file_patches", []) or []:
        if isinstance(p, dict):
            patch_text_parts.append(str(p.get("replace", "")))
    all_text = " ".join(
        proposed.get("steps", [])
        + proposed.get("commands", [])
        + patch_text_parts
    ).lower()

    for pattern in _BLOCKED_PATTERNS:
        if pattern in all_text:
            blocked.append(f"Fix contains blocked pattern: '{pattern}'")

    risk = llm_result.get("risk_level", "medium")
    requires_approval = risk in ("high", "critical")

    if requires_approval:
        warnings.append(
            f"Risk level is '{risk}' – human approval required before applying."
        )

    confidence = llm_result.get("confidence", "low")
    if confidence == "low":
        warnings.append(
            "LLM confidence is low – treat the proposed fix as a hypothesis only."
        )

    return {
        "allowed": len(blocked) == 0,
        "blocked_reasons": blocked,
        "warnings": warnings,
        "requires_human_approval": requires_approval,
    }


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class IncidentResponseAgent(BaseAgent):
    """
    Hybrid MCP+ACP agent:
    - Fetches live incidents from MongoDB (or a local mock JSON).
    - Queries the RAG knowledge base via MCP for known solutions.
    - Falls back to Groq LLM reasoning when RAG has no confident match.
    - Validates proposed fixes before surfacing / applying them.
    - Stores resolved incidents back into the RAG knowledge base.
    """

    def __init__(
        self,
        similarity_threshold: float = _DEFAULT_SIMILARITY_THRESHOLD,
        auto_apply: bool = False,
        output_dir: str | None = None,
        mock_incidents_path: str | None = None,
        repository_path: str | None = None,
        auto_approve_fix: bool = False,
    ) -> None:
        super().__init__(
            agent_name="incident_response_agent",
            protocol=ProtocolType.HYBRID_MCP_ACP.value,
        )
        self.similarity_threshold = similarity_threshold
        self.mcp = RagMcpClient()
        self.applier = SolutionApplier(
            output_dir=output_dir,
            auto_apply=auto_apply,
            repository_path=repository_path,
            auto_approve_fix=auto_approve_fix,
        )
        self.llm = LLMReasoner()
        self.mock_incidents_path = mock_incidents_path
        self._mongo_client: Any = None
        self._incidents_col: Any = None
        self._init_mongo()

    # ------------------------------------------------------------------
    # MongoDB
    # ------------------------------------------------------------------

    def _init_mongo(self) -> None:
        if not PYMONGO_AVAILABLE:
            logger.warning("pymongo not installed – MongoDB integration disabled.")
            return
        mongo_uri = os.getenv("MONGO_URI") or os.getenv("MOONGO_URI", "mongodb://localhost:27017/")
        db_name = os.getenv("MONGO_DB_NAME", "CogniOps")
        try:
            self._mongo_client = MongoClient(mongo_uri, serverSelectionTimeoutMS=3000)
            self._mongo_client.admin.command("ping")
            db = self._mongo_client[db_name]
            self._incidents_col = db["incidents"]
            logger.info("Connected to MongoDB @ %s / %s", mongo_uri, db_name)
        except Exception as exc:
            logger.warning("Could not connect to MongoDB: %s – will use mock/synthetic incidents.", exc)
            self._mongo_client = None

    # ------------------------------------------------------------------
    # Core execute
    # ------------------------------------------------------------------

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        architecture_data: dict[str, Any] = kwargs.get("architecture_data", {})
        security_data: dict[str, Any] = kwargs.get("security_data", {})
        chaos_data: dict[str, Any] = kwargs.get("chaos_data", {})

        similarity_threshold: float = float(
            kwargs.get("similarity_threshold", self.similarity_threshold)
        )
        top_k: int = int(kwargs.get("top_k", 3))
        max_incidents: int = int(kwargs.get("max_incidents", 20))
        output_dir: str | None = kwargs.get("output_dir")
        mock_path: str | None = kwargs.get("mock_incidents_path", self.mock_incidents_path)
        repository_path: str | None = kwargs.get("repository_path")

        if output_dir:
            self.applier.output_dir = Path(output_dir)
            self.applier.output_dir.mkdir(parents=True, exist_ok=True)

        if repository_path:
            from pathlib import Path as _Path
            self.applier.repository_path = _Path(repository_path).resolve()

        auto_approve_fix = kwargs.get("auto_approve_fix")
        if auto_approve_fix is not None:
            self.applier.auto_approve_fix = bool(auto_approve_fix)

        # ── Step 1: load incidents ────────────────────────────────────────────
        mongo_incidents = self._fetch_mongo_incidents(max_incidents)
        mock_incidents = self._load_mock_incidents(mock_path)
        synthetic = self._build_synthetic_incidents(security_data, chaos_data, architecture_data)

        all_incidents = mongo_incidents + mock_incidents + synthetic
        logger.info(
            "Incidents to process: %d MongoDB + %d mock + %d synthetic = %d total",
            len(mongo_incidents),
            len(mock_incidents),
            len(synthetic),
            len(all_incidents),
        )

        # ── Step 2: process incidents in parallel ─────────────────────────────
        # RAG calls each spawn a short-lived MCP subprocess; LLM calls hit the
        # Groq API. Both are I/O-bound, so threading gives a meaningful speedup.
        max_workers = int(os.getenv("INCIDENT_AGENT_WORKERS", "4"))
        processed_map: dict[int, dict[str, Any]] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    self._process_incident,
                    inc,
                    similarity_threshold=similarity_threshold,
                    top_k=top_k,
                ): idx
                for idx, inc in enumerate(all_incidents)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    processed_map[idx] = future.result()
                except Exception as exc:
                    inc = all_incidents[idx]
                    logger.error("Unhandled error processing incident %s: %s", inc.get("_id", idx), exc)
                    processed_map[idx] = {
                        "incident_id": str(inc.get("_id", idx)),
                        "source": inc.get("_source", "unknown"),
                        "status": inc.get("status"),
                        "severity": inc.get("severity"),
                        "services": inc.get("service_names", []),
                        "summary": inc.get("summary", ""),
                        "resolution_path": "none",
                        "rag_hits": [],
                        "apply_result": None,
                        "llm_diagnosis": None,
                        "validation_report": None,
                        "note": f"Processing error: {exc}",
                    }

        # Restore original order (futures complete out-of-order)
        processed = [processed_map[i] for i in range(len(all_incidents))]

        rag_matched = sum(1 for p in processed if p["resolution_path"] == "rag")
        llm_resolved = sum(1 for p in processed if p["resolution_path"] == "llm")
        unresolved = sum(1 for p in processed if p["resolution_path"] == "none")

        # ── Step 3: build legacy runbook + actions ────────────────────────────
        runbook = self._build_runbook(architecture_data, security_data, chaos_data)
        incident_actions = self._build_incident_actions(security_data, chaos_data, architecture_data)

        return {
            "protocol": self.protocol,
            "summary": (
                f"Processed {len(all_incidents)} incident(s): "
                f"{rag_matched} resolved via RAG, "
                f"{llm_resolved} via LLM reasoning, "
                f"{unresolved} unresolved."
            ),
            "total_incidents": len(all_incidents),
            "rag_matched": rag_matched,
            "llm_resolved": llm_resolved,
            "unresolved": unresolved,
            "similarity_threshold": similarity_threshold,
            "processed_incidents": processed,
            "runbook": runbook,
            "incident_actions": incident_actions,
        }

    # ------------------------------------------------------------------
    # Single-incident processing
    # ------------------------------------------------------------------

    def _process_incident(
        self,
        inc: dict[str, Any],
        similarity_threshold: float,
        top_k: int,
    ) -> dict[str, Any]:
        incident_id = str(inc.get("_id", inc.get("signature", "?")))
        query = self._build_query(inc)
        logger.info("Processing incident %s …", incident_id)

        # ── Path 1: RAG retrieval ─────────────────────────────────────────────
        hits = self.mcp.search_similar_incidents(query, top_k=top_k)
        good_hits = [h for h in hits if h.get("similarity", 0) >= similarity_threshold]

        if good_hits:
            best = good_hits[0]
            logger.info(
                "RAG match → '%s' (similarity=%.3f)",
                best.get("title"),
                best.get("similarity", 0),
            )
            apply_result = self.applier.apply(incident=inc, rag_match=best)

            # Only accept the RAG path when the fix produced concrete output:
            # - the workflow was patched (WorkflowPatcher committed a change), OR
            # - there are pending manual commands (written to .sh for operator), OR
            # - fix commands were already auto-executed (auto_apply=True).
            # A non-empty solution text alone is NOT enough — it may be a stale
            # description with no commands, which would block LLM escalation.
            patch_result = (apply_result or {}).get("patch_result") or {}
            pending = (apply_result or {}).get("pending_fix_commands") or []
            applied_count = len((apply_result or {}).get("fix_results") or [])
            solution_text = best.get("solution", "").strip()
            rag_concrete = patch_result.get("patched") or bool(pending) or applied_count > 0

            if rag_concrete:
                self._update_mongo_incident(inc, apply_result, best)
                self._feedback_reinforce(inc, solution_text, path="rag")

                return {
                    "incident_id": incident_id,
                    "source": inc.get("_source", "unknown"),
                    "status": inc.get("status"),
                    "severity": inc.get("severity"),
                    "services": inc.get("service_names", []),
                    "summary": inc.get("summary", ""),
                    "resolution_path": "rag",
                    "rag_hits": good_hits,
                    "apply_result": apply_result,
                    "llm_diagnosis": None,
                    "validation_report": None,
                }

            logger.info(
                "RAG hit '%s' (%.3f) produced no concrete fix — escalating to LLM.",
                best.get("title"), best.get("similarity", 0),
            )

        # ── Path 2: LLM reasoning fallback ───────────────────────────────────
        if good_hits:
            logger.info("LLM escalated from non-actionable RAG match for incident %s.", incident_id)
        else:
            logger.info(
                "No RAG match above %.2f for incident %s – invoking LLM reasoning.",
                similarity_threshold,
                incident_id,
            )
        llm_diagnosis = self.llm.reason(inc)

        if llm_diagnosis:
            # Validation layer
            validation = _validate_fix(llm_diagnosis)
            logger.info(
                "Validation: allowed=%s requires_approval=%s",
                validation["allowed"],
                validation["requires_human_approval"],
            )

            # Build a synthetic rag_match from the LLM result so SolutionApplier
            # can write a real fix script with the generated steps and commands.
            proposed = llm_diagnosis.get("proposed_fix", {})
            llm_as_rag = {
                "id": f"llm-{incident_id}",
                "title": inc.get("summary", f"LLM diagnosis for {incident_id}")[:120],
                "category": "llm-diagnosed",
                "severity": inc.get("severity", "medium"),
                "similarity": 0.0,
                "solution": "\n".join(
                    [proposed.get("summary", "")] + proposed.get("steps", [])
                ),
                "auto_commands": [],
                "fix_commands": proposed.get("commands", []),
                # Cross-platform structured edits — applied in Python regardless of OS
                "file_patches": proposed.get("file_patches", []),
            }

            if validation["allowed"] and not validation["requires_human_approval"]:
                apply_result = self.applier.apply(incident=inc, rag_match=llm_as_rag)
                apply_result["llm_source"] = True
                self._feedback_store_llm(inc, llm_diagnosis)
                logger.info("LLM fix script written for incident %s.", incident_id)
            elif validation["allowed"]:
                # Write the fix script but do NOT execute — requires human review
                saved_auto = self.applier.auto_apply
                self.applier.auto_apply = False
                apply_result = self.applier.apply(incident=inc, rag_match=llm_as_rag)
                self.applier.auto_apply = saved_auto
                apply_result["status"] = "pending_human_approval"
                apply_result["llm_source"] = True
                self._feedback_store_llm(inc, llm_diagnosis)
                logger.info("Fix blocked pending human approval for incident %s.", incident_id)
            else:
                apply_result = {"status": "blocked_by_validation", "llm_source": True}
                logger.warning(
                    "Fix BLOCKED for incident %s: %s",
                    incident_id,
                    validation["blocked_reasons"],
                )

            return {
                "incident_id": incident_id,
                "source": inc.get("_source", "unknown"),
                "status": inc.get("status"),
                "severity": inc.get("severity"),
                "services": inc.get("service_names", []),
                "summary": inc.get("summary", ""),
                "resolution_path": "llm",
                "rag_hits": hits,
                "apply_result": apply_result,
                "llm_diagnosis": llm_diagnosis,
                "validation_report": validation,
            }

        # ── Path 3: no resolution ─────────────────────────────────────────────
        # Do NOT ingest into the RAG KB here — adding incidents with no real
        # solution poisons future searches (they self-match at ~0.97 with
        # "Manual investigation required" as the answer).
        logger.warning("Incident %s could not be resolved (RAG and LLM both failed).", incident_id)
        return {
            "incident_id": incident_id,
            "source": inc.get("_source", "unknown"),
            "status": inc.get("status"),
            "severity": inc.get("severity"),
            "services": inc.get("service_names", []),
            "summary": inc.get("summary", ""),
            "resolution_path": "none",
            "rag_hits": hits,
            "apply_result": None,
            "llm_diagnosis": None,
            "validation_report": None,
            "note": "No known solution. Incident saved to knowledge base for future matching.",
        }

    # ------------------------------------------------------------------
    # Feedback loop helpers
    # ------------------------------------------------------------------

    def _feedback_reinforce(
        self,
        incident: dict[str, Any],
        solution: str,
        path: str,
    ) -> None:
        """Reinforce a RAG-matched entry with any new incident details."""
        # For now just log; a production system could bump a hit-count field
        logger.debug(
            "Feedback (reinforce/%s): incident %s matched existing knowledge base entry.",
            path,
            incident.get("_id", "?"),
        )

    def _feedback_store_llm(
        self,
        incident: dict[str, Any],
        llm_diagnosis: dict[str, Any],
    ) -> None:
        """
        Persist a successfully diagnosed incident to the RAG knowledge base
        so future similar incidents are matched directly without LLM overhead.

        Only stores entries when the LLM produced concrete fix steps or commands.
        Storing empty / "manual investigation" diagnoses would pollute the RAG
        and cause future incidents to match a useless entry at high similarity.
        """
        incident_id = str(incident.get("_id", "llm-resolved"))
        proposed = llm_diagnosis.get("proposed_fix", {})
        steps: list[str] = proposed.get("steps", [])
        commands: list[str] = proposed.get("commands", [])
        summary_text: str = proposed.get("summary", "").strip()

        # Guard: skip storage when there's nothing useful to offer
        meaningless = {"manual investigation required", ""}
        if (
            not steps
            and not commands
            and summary_text.lower() in meaningless
        ):
            logger.info(
                "Feedback loop: skipping RAG storage for incident %s "
                "— LLM produced no concrete fix steps/commands.",
                incident_id,
            )
            return

        # Build solution text from summary + numbered steps
        solution_parts = [summary_text] if summary_text else []
        solution_parts += [f"{i+1}. {s}" for i, s in enumerate(steps)]
        solution_text = "\n".join(solution_parts)

        title = incident.get("summary", f"Incident {incident_id}")[:120]
        description = self._build_query(incident)[:500]

        ok = self.mcp.add_incident_solution(
            incident_id=f"llm-{incident_id}",
            title=title,
            description=description,
            solution=solution_text,
            category=llm_diagnosis.get("category", "llm-diagnosed"),
            severity=incident.get("severity", "medium"),
        )
        if ok:
            logger.info("Feedback loop: incident %s stored in RAG knowledge base.", incident_id)
        else:
            logger.warning("Feedback loop: could not store incident %s in knowledge base.", incident_id)

    # ------------------------------------------------------------------
    # Incident loaders
    # ------------------------------------------------------------------

    def _load_mock_incidents(self, path: str | None) -> list[dict[str, Any]]:
        """Load incidents from a local JSON file (offline / testing mode)."""
        if not path:
            return []
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            incidents = data if isinstance(data, list) else [data]
            for inc in incidents:
                inc.setdefault("_source", "mock_json")
            logger.info("Loaded %d mock incident(s) from %s", len(incidents), path)
            return incidents
        except Exception as exc:
            logger.warning("Could not load mock incidents from %s: %s", path, exc)
            return []

    def _fetch_mongo_incidents(self, limit: int) -> list[dict[str, Any]]:
        if self._incidents_col is None:
            return []
        # limit=0 is the caller's signal to skip MongoDB entirely.
        # (pymongo treats .limit(0) as "no limit", which is the opposite.)
        if limit <= 0:
            return []
        try:
            cursor = (
                self._incidents_col.find(
                    {"status": {"$in": ["open", "investigating"]}}
                )
                .sort("severity", -1)
                .limit(limit)
            )
            docs = []
            for doc in cursor:
                doc["_source"] = "mongodb"
                docs.append(doc)
            return docs
        except PyMongoError as exc:
            logger.error("Failed to fetch incidents from MongoDB: %s", exc)
            return []

    def _update_mongo_incident(
        self,
        incident: dict[str, Any],
        apply_result: dict[str, Any],
        rag_match: dict[str, Any],
    ) -> None:
        if self._incidents_col is None or incident.get("_source") != "mongodb":
            return
        inc_id = incident.get("_id")
        if not inc_id:
            return
        try:
            self._incidents_col.update_one(
                {"_id": inc_id},
                {
                    "$set": {
                        "status": "investigating",
                        "updated_at": datetime.now(timezone.utc),
                        "rag_resolution": {
                            "matched_at": datetime.now(timezone.utc).isoformat(),
                            "rag_id": rag_match.get("id"),
                            "rag_title": rag_match.get("title"),
                            "similarity": rag_match.get("similarity"),
                            "solution": rag_match.get("solution"),
                            "fix_script": apply_result.get("fix_script"),
                            "apply_status": apply_result.get("status"),
                        },
                    }
                },
            )
        except PyMongoError as exc:
            logger.warning("Could not update incident in MongoDB: %s", exc)

    def _ingest_unmatched_incident(self, incident: dict[str, Any]) -> None:
        errors = incident.get("last_error_payload", [])
        if not errors:
            return
        description = " | ".join(
            str(e.get("stderr_tail", e.get("message", "")))[:200]
            for e in errors[:3]
            if isinstance(e, dict)
        )
        if not description.strip():
            return
        self.mcp.add_incident_solution(
            incident_id=str(incident.get("_id", incident.get("signature", "custom"))),
            title=f"Unresolved: {incident.get('summary', 'deployment failure')}"[:120],
            description=description,
            solution="Manual investigation required – no automated solution found.",
            category="unresolved",
            severity=incident.get("severity", "medium"),
        )

    # ------------------------------------------------------------------
    # Query building
    # ------------------------------------------------------------------

    def _build_query(self, incident: dict[str, Any]) -> str:
        """
        Build a RAG search query from an incident.

        Order (most discriminating → least):
        1. Full message field  — already concatenates job name + failed steps +
           the first annotation text (e.g. "Job 'test' failed at step(s): Unit tests
           — [warning] Node.js 20 actions are deprecated …")
        2. All annotation lines — exact GitHub step-level error text
        3. Log tail / stderr   — actual command output
        4. Failed job/step names
        5. Service + environment
        6. Summary headline (last — generic for GitHub Actions failures)
        """
        parts: list[str] = []

        for err in incident.get("last_error_payload", [])[:3]:
            if not isinstance(err, dict):
                continue

            # 1. Full message field (job + steps + first annotation joined together)
            message = str(err.get("message", "") or "").strip()
            if message:
                parts.append(f"error message:\n{message}")

            # 2. All annotation lines (no truncation — each can be long but specific)
            annotations = err.get("annotations", [])
            if annotations:
                ann_text = "\n".join(str(a) for a in annotations)
                parts.append(f"annotations:\n{ann_text}")

            # 3. Log tail — strip the "ANNOTATIONS:" header block if present
            tail = str(err.get("stderr_tail", "") or "").strip()
            if "LOG TAIL:" in tail:
                tail = tail.split("LOG TAIL:", 1)[-1].strip()
            elif tail.startswith("ANNOTATIONS:") and "LOG TAIL:" not in tail:
                tail = ""
            if tail:
                parts.append(f"log output:\n{tail}")

            # 4. Job / step names
            job = err.get("job", "")
            steps = err.get("failed_steps", [])
            if job or steps:
                detail = f"failed job: {job}"
                if steps:
                    detail += f" | failed steps: {', '.join(steps)}"
                parts.append(detail)

            break  # first error entry is the primary failure

        # 5. Service + environment
        services = incident.get("service_names", [])
        env = incident.get("environment", "")
        if services or env:
            parts.append(
                f"service: {', '.join(services)}"
                + (f" | environment: {env}" if env else "")
            )

        # 6. Summary headline (last — too generic to be the primary signal)
        summary = incident.get("summary", "")
        if summary:
            parts.append(f"summary: {summary}")

        return "\n".join(parts) or "deployment failure unknown error"

    # ------------------------------------------------------------------
    # Synthetic incidents from agent outputs (backward compat)
    # ------------------------------------------------------------------

    def _build_synthetic_incidents(
        self,
        security_data: dict[str, Any],
        chaos_data: dict[str, Any],
        architecture_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        synthetic: list[dict[str, Any]] = []
        findings = (security_data.get("data") or security_data).get("findings", [])
        critical = [f for f in findings if str(f.get("severity", "")).upper() in {"CRITICAL", "HIGH"}]
        if critical:
            err_msgs = [str(f.get("description", f.get("rule_id", "")))[:200] for f in critical[:5]]
            synthetic.append(
                {
                    "_id": None,
                    "_source": "devsecops",
                    "status": "open",
                    "severity": "high",
                    "environment": "ci",
                    "service_names": [
                        s.get("name", "service")
                        for s in architecture_data.get("services", [])[:3]
                    ],
                    "summary": f"{len(critical)} critical/high security finding(s) detected",
                    "last_error_payload": [{"stderr_tail": m} for m in err_msgs],
                    "occurrences": 1,
                }
            )
        return synthetic

    # ------------------------------------------------------------------
    # Legacy runbook / actions (backward compatibility with orchestrator)
    # ------------------------------------------------------------------

    def _build_runbook(
        self,
        architecture_data: dict[str, Any],
        security_data: dict[str, Any],
        chaos_data: dict[str, Any],
    ) -> list[str]:
        return [
            "1) Detect incident from MongoDB alerts / deployment events",
            "2) Query RAG knowledge base via MCP for similar known incidents",
            "3) If RAG match above threshold → apply known solution",
            "4) If no RAG match → invoke LLM reasoning (Groq openai/gpt-oss-120b)",
            "5) Run validation layer: safety checks + risk assessment",
            "6) Apply fix (auto if low-risk, human approval if high-risk)",
            "7) Store resolved incident in RAG knowledge base (feedback loop)",
            "8) Run postmortem – update runbooks and SECURITY.md",
        ]

    def _build_incident_actions(
        self,
        security_data: dict[str, Any],
        chaos_data: dict[str, Any],
        architecture_data: dict[str, Any],
    ) -> list[str]:
        findings_count = len(
            (security_data.get("data") or security_data).get("findings", [])
        )
        experiments_count = len(
            (chaos_data.get("data") or chaos_data).get("experiments", [])
        )
        impacted = [svc.get("name", "unknown") for svc in architecture_data.get("services", [])]
        return [
            f"Review {findings_count} security finding(s) via RAG + LLM reasoning",
            f"Use {experiments_count} chaos scenario(s) to validate resilience playbooks",
            "Create RCA template and incident timeline",
            f"Track service ownership for: {', '.join(impacted) or 'N/A'}",
            "Enrich RAG knowledge base with postmortem findings (feedback loop)",
            "Validate LLM-proposed fixes in staging before production rollout",
        ]
