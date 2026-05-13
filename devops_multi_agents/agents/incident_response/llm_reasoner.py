"""
LLM-based incident reasoner.

Default backend: Ollama running ``gpt-oss:120b`` locally
(OpenAI-compatible endpoint at $OLLAMA_BASE_URL).

The provider can be overridden per-run via the ``INCIDENT_LLM_PROVIDER``
env var (one of: ``ollama``, ``groq``, ``openrouter``, ``nvidia``) and the
model via ``INCIDENT_LLM_MODEL``. The orchestrator sets these temporarily
when invoking the agent so other agents are not affected.

Invoked as a fallback when the RAG knowledge base returns no match
above the similarity threshold.  The LLM receives the incident context,
applies debug-style reasoning, and returns a structured diagnosis with
root cause, proposed fix, risk level, and validation steps.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI

    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logger.warning(
        "openai package not installed – LLM fallback disabled. "
        "Install with: pip install openai"
    )

# Backwards-compat alias used elsewhere in the file (was Groq-specific).
GROQ_AVAILABLE = OPENAI_AVAILABLE

_DEFAULT_MODEL = "gpt-oss:120b"
_DEFAULT_PROVIDER = "ollama"

_SYSTEM_PROMPT = """\
You are a senior DevOps/SRE engineer specializing in GitHub Actions CI/CD pipelines.

You MUST diagnose ANY failure shown in the incident and propose a concrete fix.
There is no list of supported error types — read the log, find the real cause, and act.

OUTPUT CONTRACT — cross-platform fixes ONLY:
The agent that applies your fix runs on Windows AND Linux. Therefore:
- Prefer `file_patches` (literal find/replace inside a file). These are applied
  in Python and work everywhere.
- Use `commands` ONLY for OS-agnostic shell commands (git, npm, mvn, docker, kubectl,
  curl). NEVER use sed, awk, perl -i, tr, cut, grep -P, or any GNU-coreutils flag —
  they are missing or different on Windows.

Tips for common GitHub Actions failures (NOT exhaustive — apply your own judgment):
- Java Maven exit 126 "./mvnw: Permission denied" → replace `./mvnw` with `bash mvnw`.
- Maven "There are test failures" with JDBC/Connection refused → exclude Spring
  context-load tests: append `-Dtest='!*ApplicationTests' -DfailIfNoTests=false` to the test command.
- Karma/Angular "Missing X server / Cannot start Chrome / exit 126" → force
  `--browsers=ChromeHeadless` on the npm test command.
- npm ERR! ELIFECYCLE on missing script → add the script to package.json OR change
  the test command in the workflow.
- Node.js version too old → bump `node-version` in setup-node.
- Trivy/Checkov action 404 → switch to the correct image name (aquasecurity/trivy-action).
- GitHub Actions Node.js 20 deprecation warning from runner is INFRA — set confidence=low
  and add it to additional_context_needed; do not patch the workflow for that warning alone.

Constraints:
- No destructive ops (drop DB, delete volumes, force-push, rm -rf).
- If risk_level is high/critical, add "Get human approval" as first validation step.
- Patches must be deterministic: the `find` string MUST appear EXACTLY ONCE in the
  target file unless you set `count > 1` or `count: -1` for all occurrences.

Reply with JSON only (no markdown, no commentary):
{
  "root_cause_hypothesis": "...",
  "confidence": "high|medium|low",
  "proposed_fix": {
    "summary": "one-line description of the fix",
    "steps": ["human-readable steps explaining what changes and why"],
    "file_patches": [
      {
        "file": ".github/workflows/<workflow>.yml",
        "find": "exact substring currently in the file",
        "replace": "the new substring that should appear instead",
        "count": -1
      }
    ],
    "commands": []
  },
  "risk_level": "low|medium|high|critical",
  "validation_steps": ["..."],
  "additional_context_needed": []
}

Rules:
- `file_patches` is the PRIMARY output channel. Use it whenever the fix is a YAML/config edit.
- `count` = number of occurrences to replace (-1 = all, default 1).
- `commands` is for actions outside file edits (e.g. `npm install --save-dev karma-chrome-headless`).
  Leave it as an empty list when not needed.
- Every `find` must be lifted verbatim from the workflow file content. Never invent
  text that isn't there — the patch will silently no-op.
"""


class LLMReasoner:
    """
    Reasoner for incidents with no RAG match.

    Uses an OpenAI-compatible client (so any of Ollama / Groq / OpenRouter /
    NVIDIA NIM works). The provider and model are read at construction time
    from env vars set by the orchestrator:

        INCIDENT_LLM_PROVIDER  one of: ollama (default), groq, openrouter, nvidia
        INCIDENT_LLM_MODEL     model id (default: gpt-oss:120b)

    Streams the response to avoid timeouts on long outputs, then parses
    and validates the structured JSON reply.
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._model = os.getenv("INCIDENT_LLM_MODEL") or _DEFAULT_MODEL
        self._provider = (os.getenv("INCIDENT_LLM_PROVIDER") or _DEFAULT_PROVIDER).strip().lower()

        if not OPENAI_AVAILABLE:
            return

        base_url, api_key = self._resolve_endpoint(self._provider)
        if base_url is None:
            logger.warning(
                "No endpoint resolved for INCIDENT_LLM_PROVIDER=%s – LLM reasoning disabled.",
                self._provider,
            )
            return
        if api_key is None:
            logger.warning(
                "API key missing for provider %s – LLM reasoning disabled.",
                self._provider,
            )
            return

        try:
            self._client = OpenAI(base_url=base_url, api_key=api_key)
            logger.info(
                "Incident reasoner ready: provider=%s model=%s base_url=%s",
                self._provider, self._model, base_url,
            )
        except Exception as exc:
            logger.warning("Failed to construct OpenAI client for %s: %s", self._provider, exc)

    @staticmethod
    def _resolve_endpoint(provider: str) -> tuple[str | None, str | None]:
        """Return (base_url, api_key) for the requested provider."""
        if provider == "ollama":
            return (
                os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
                os.getenv("OLLAMA_API_KEY", "ollama"),
            )
        if provider == "groq":
            return (
                os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
                os.getenv("GROQ_API_KEY"),
            )
        if provider == "openrouter":
            return (
                os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                os.getenv("OPENROUTER_API_KEY"),
            )
        if provider == "nvidia":
            return (
                os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
                os.getenv("NVIDIA_API_KEY"),
            )
        return (None, None)

    # ------------------------------------------------------------------
    def is_available(self) -> bool:
        return OPENAI_AVAILABLE and self._client is not None

    # ------------------------------------------------------------------
    def reason(self, incident: dict[str, Any]) -> dict[str, Any] | None:
        """
        Analyse an unmatched incident and return a structured diagnosis.

        Returns None if the LLM is unavailable or reasoning fails, so the
        caller can degrade gracefully.
        """
        if not self.is_available():
            logger.warning(
                "LLM reasoner unavailable – no diagnosis for incident %s.",
                incident.get("_id", "?"),
            )
            return None

        prompt = self._build_user_prompt(incident)
        incident_id = str(incident.get("_id", "unknown"))
        logger.info("Invoking LLM reasoning for incident %s …", incident_id)

        for attempt in range(1, 4):
            try:
                raw = self._stream_completion(prompt)
                result = self._parse_response(raw)
                logger.info(
                    "LLM reasoning complete – confidence=%s risk=%s",
                    result.get("confidence"),
                    result.get("risk_level"),
                )
                return result
            except Exception as exc:
                err_str = str(exc)
                # Retry on rate-limit (429 / 413) with exponential backoff
                if any(code in err_str for code in ("429", "413", "rate_limit", "tokens")):
                    wait = 2 ** attempt
                    logger.warning(
                        "LLM rate-limited for incident %s (attempt %d/3) – retrying in %ds.",
                        incident_id, attempt, wait,
                    )
                    time.sleep(wait)
                else:
                    logger.error("LLM reasoning failed for incident %s: %s", incident_id, exc)
                    return None
        logger.error("LLM reasoning gave up after 3 retries for incident %s.", incident_id)
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_user_prompt(self, incident: dict[str, Any]) -> str:
        summary = incident.get("summary", "")
        severity = incident.get("severity", "unknown").upper()
        env = incident.get("environment", "unknown")
        services = ", ".join(incident.get("service_names", []) or ["(none)"])

        # Derive workflow file path from incident metadata
        workflow_file = (
            incident.get("github_workflow_file")
            or incident.get("github_workflow_file_path")
            or ""
        )
        if not workflow_file:
            service_names = incident.get("service_names", [])
            if service_names:
                workflow_file = f".github/workflows/{service_names[0]}-pipeline.yml"

        lines = [
            f"SEVERITY:{severity} ENV:{env} SERVICES:{services}",
            f"SUMMARY:{summary[:200]}",
        ]
        if workflow_file:
            lines.append(f"WORKFLOW FILE:{workflow_file}")

        for err in (incident.get("last_error_payload", []) or [])[:2]:
            if not isinstance(err, dict):
                continue

            # Full message field (job + failed steps + first annotation concatenated)
            message = str(err.get("message", "") or "").strip()
            if message:
                lines.append(f"ERROR MESSAGE:\n{message}")

            # All annotation lines — exact GitHub step-level error text
            annotations = err.get("annotations", [])
            if annotations:
                ann_block = "\n".join(str(a) for a in annotations)
                lines.append(f"ANNOTATIONS:\n{ann_block}")

            # Log tail — strip the ANNOTATIONS header block if present
            tail = str(err.get("stderr_tail", "") or "").strip()
            if "LOG TAIL:" in tail:
                tail = tail.split("LOG TAIL:", 1)[-1].strip()
            elif tail.startswith("ANNOTATIONS:") and "LOG TAIL:" not in tail:
                tail = ""
            if tail:
                lines.append(f"LOG TAIL:\n{tail}")

            # Job / step names
            job = err.get("job", "")
            steps = err.get("failed_steps", [])
            if job:
                lines.append(f"FAILED JOB:{job}" + (f" STEPS:{', '.join(steps)}" if steps else ""))

            break  # only use the first error payload entry

        lines.append("Diagnose and respond in JSON.")
        return "\n".join(lines)

    def _stream_completion(self, user_message: str) -> str:
        """Stream the completion and return the fully accumulated text."""
        completion = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=1,
            max_tokens=1024,
            top_p=1,
            stream=True,
        )

        chunks: list[str] = []
        for chunk in completion:
            if not getattr(chunk, "choices", None):
                continue
            delta = getattr(chunk.choices[0], "delta", None)
            if delta is None:
                continue
            content = getattr(delta, "content", None)
            if content:
                chunks.append(content)

        return "".join(chunks)

    def _parse_response(self, raw: str) -> dict[str, Any]:
        """Extract and validate the JSON diagnosis from the LLM output."""
        text = raw.strip()

        # Strip markdown code fences
        if text.startswith("```"):
            parts = text.split("```", 2)
            if len(parts) >= 2:
                inner = parts[1]
                if inner.startswith("json"):
                    inner = inner[4:]
                text = inner.rsplit("```", 1)[0].strip()

        # Try direct parse first
        try:
            parsed: dict[str, Any] = json.loads(text)
        except json.JSONDecodeError:
            # Fallback: find the first {...} block in the output (handles cases
            # where the LLM wraps JSON inside a prose or string value)
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                try:
                    parsed = json.loads(match.group())
                except json.JSONDecodeError as exc:
                    logger.warning("JSON extraction fallback also failed (%s). Raw: %r", exc, raw[:200])
                    parsed = None
            else:
                parsed = None

        if parsed is None:
            logger.warning(
                "LLM response was not valid JSON. Wrapping raw text as degraded result. Raw: %r",
                raw[:200],
            )
            parsed = {
                "root_cause_hypothesis": raw[:600] if raw else "Unable to determine root cause.",
                "confidence": "low",
                "proposed_fix": {
                    "summary": "Manual investigation required – LLM output was not structured.",
                    "steps": ["Review error logs manually", "Engage the on-call engineer"],
                    "commands": [],
                },
                "risk_level": "medium",
                "validation_steps": [
                    "Review with the team before applying any fix",
                    "Test in a non-production environment first",
                ],
                "additional_context_needed": [
                    "Full error logs",
                    "Recent changes / deploy history",
                ],
            }

        # Guarantee all required keys exist
        parsed.setdefault("root_cause_hypothesis", "Unknown")
        parsed.setdefault("confidence", "low")
        parsed.setdefault(
            "proposed_fix",
            {"summary": "", "steps": [], "commands": [], "file_patches": []},
        )
        parsed.setdefault("risk_level", "medium")
        parsed.setdefault("validation_steps", [])
        parsed.setdefault("additional_context_needed", [])

        fix = parsed["proposed_fix"]
        if not isinstance(fix, dict):
            parsed["proposed_fix"] = {
                "summary": str(fix), "steps": [], "commands": [], "file_patches": [],
            }
        else:
            fix.setdefault("steps", [])
            fix.setdefault("commands", [])
            fix.setdefault("file_patches", [])
            # Normalise patches: skip malformed ones, ensure default count
            clean_patches: list[dict[str, Any]] = []
            for p in fix.get("file_patches", []) or []:
                if not isinstance(p, dict):
                    continue
                if not p.get("file") or "find" not in p or "replace" not in p:
                    continue
                p.setdefault("count", 1)
                clean_patches.append(p)
            fix["file_patches"] = clean_patches

        return parsed
