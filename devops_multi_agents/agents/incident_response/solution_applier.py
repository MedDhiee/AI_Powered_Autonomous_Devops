"""
Local solution applier.

Given a RAG match for a MongoDB incident, this module:

  1. Formats the solution steps as a human-readable fix plan.
  2. Interpolates known context (service name, pod name, …) into command
     templates.
  3. Optionally executes safe, read-only diagnostic commands automatically
     (kubectl logs, kubectl describe, docker images, …).
  4. Writes a fix script to disk so an operator can review and execute the
     destructive/recovery commands manually (or with --auto-apply).
  5. Returns a structured report of what was done / what to do next.
"""

from __future__ import annotations

import logging
import os
import subprocess
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Commands considered safe for automatic execution (read-only diagnostics)
_SAFE_PREFIXES = (
    "kubectl logs",
    "kubectl describe",
    "kubectl get",
    "kubectl rollout status",
    "kubectl top",
    "docker images",
    "docker system df",
    "terraform show",
    "terraform plan",
)


def _is_safe(cmd: str) -> bool:
    stripped = cmd.strip().lower()
    return any(stripped.startswith(prefix) for prefix in _SAFE_PREFIXES)


def _interpolate(cmd: str, ctx: dict[str, str]) -> str:
    """Replace {placeholder} tokens with values from ctx."""
    try:
        return cmd.format_map(ctx)
    except KeyError:
        return cmd  # leave unresolved placeholders as-is


def _run_command(cmd: str, timeout: int = 30) -> dict[str, Any]:
    """Execute a shell command and capture output."""
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "command": cmd,
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip()[-4000:],  # truncate large output
            "stderr": proc.stderr.strip()[-2000:],
            "success": proc.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"command": cmd, "returncode": -1, "stdout": "", "stderr": "Timeout", "success": False}
    except Exception as exc:
        return {"command": cmd, "returncode": -1, "stdout": "", "stderr": str(exc), "success": False}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class SolutionApplier:
    """
    Applies the solution from a RAG match to a live MongoDB incident.

    Args:
        output_dir:       Where to write fix scripts and reports.
        auto_apply:       If True, also execute fix (non-read-only) commands.
                          Defaults to False – only diagnostic commands run.
        repository_path:  Local clone of the target repository.  When set,
                          GitHub Actions incidents are auto-patched directly
                          in the workflow files and committed.
    """

    def __init__(
        self,
        output_dir: str | Path | None = None,
        auto_apply: bool = False,
        repository_path: str | Path | None = None,
        auto_approve_fix: bool = False,
    ) -> None:
        self.output_dir = Path(output_dir or "devops_multi_agents/outputs/incident-fixes")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.auto_apply = auto_apply
        self.repository_path = Path(repository_path).resolve() if repository_path else None
        # If True, skip the HITL prompt and auto-apply detected fixes.
        # If False (default), the user is prompted before any file is changed.
        self.auto_approve_fix = auto_approve_fix

    # ------------------------------------------------------------------
    def apply(
        self,
        incident: dict[str, Any],
        rag_match: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Process one incident against its best RAG match.

        Args:
            incident:  MongoDB incident document (dict).
            rag_match: Best hit from RagMcpClient.search_similar_incidents().

        Returns:
            A structured dict with applied/pending actions and the fix script
            path.
        """
        incident_id = str(incident.get("_id", incident.get("signature", "unknown")))
        service_names: list[str] = incident.get("service_names", [])
        environment: str = incident.get("environment", "local")
        error_payload: list[dict] = incident.get("last_error_payload", [])

        # Build interpolation context from incident metadata
        ctx = {
            "service_name": service_names[0] if service_names else "service",
            "pod_name": f"{service_names[0]}-pod" if service_names else "pod",
            "environment": environment,
            "image_name": f"{service_names[0]}:latest" if service_names else "service:latest",
            "service_path": ".",
            "manifest_path": f"k8s/{service_names[0]}.yaml" if service_names else "k8s/deployment.yaml",
            "lock_id": "LOCK_ID",
        }

        solution_text: str = rag_match.get("solution", "")
        auto_commands: list[str] = rag_match.get("auto_commands", [])
        fix_commands: list[str] = rag_match.get("fix_commands", [])
        similarity: float = rag_match.get("similarity", 0.0)

        # 1) Run safe diagnostic commands automatically
        diagnostic_results: list[dict[str, Any]] = []
        for raw_cmd in auto_commands:
            cmd = _interpolate(raw_cmd, ctx)
            if _is_safe(cmd):
                logger.info("[auto-diag] %s", cmd)
                diagnostic_results.append(_run_command(cmd))
            else:
                logger.debug("Skipping non-safe diagnostic: %s", cmd)

        # 2) Optionally run fix commands
        fix_results: list[dict[str, Any]] = []
        pending_fix_commands: list[str] = []
        for raw_cmd in fix_commands:
            cmd = _interpolate(raw_cmd, ctx)
            if self.auto_apply:
                logger.info("[auto-fix] %s", cmd)
                fix_results.append(_run_command(cmd))
            else:
                pending_fix_commands.append(cmd)

        # 3) Write a fix script to disk
        fix_script_path = self._write_fix_script(
            incident_id=incident_id,
            incident=incident,
            rag_match=rag_match,
            solution_text=solution_text,
            diagnostic_results=diagnostic_results,
            pending_fix_commands=pending_fix_commands,
            ctx=ctx,
        )

        # 4) Auto-patch the repository when a repo path is available and the
        #    incident originates from GitHub Actions.  Plan first, then ask
        #    the human, then apply on approval.
        patch_result: dict[str, Any] = {}
        if self.repository_path and incident.get("_source") in (
            "github_actions", "github_actions_validation", "mock_json"
        ):
            try:
                from .workflow_patcher import WorkflowPatcher

                llm_commands: list[str] = list(rag_match.get("fix_commands") or [])
                file_patches: list[dict[str, Any]] = list(rag_match.get("file_patches") or [])

                patcher = WorkflowPatcher(self.repository_path)
                plans = patcher.plan_patches(
                    incident,
                    llm_commands=llm_commands or None,
                    file_patches=file_patches or None,
                )

                if not plans:
                    patch_result = {
                        "patched": False,
                        "reason": (
                            "No auto-applicable fix pattern detected in this incident. "
                            "Review the .sh script for manual remediation."
                        ),
                        "fixes": [], "commit": {}, "errors": [],
                    }
                    print("  ℹ️   No auto-applicable fix detected — see .sh script for manual steps.")
                else:
                    approved = self._prompt_human_for_approval(plans, incident_id)
                    if approved:
                        patch_result = patcher.apply_patches(plans)
                        if patch_result.get("patched"):
                            logger.info(
                                "WorkflowPatcher applied %d fix(es) and pushed to '%s'.",
                                len(patch_result.get("fixes", [])),
                                patch_result.get("commit", {}).get("branch"),
                            )
                            print(
                                f"  🔧  WorkflowPatcher: applied {len(patch_result.get('fixes', []))} fix(es)"
                                f" → committed and pushed to '{patch_result.get('commit', {}).get('branch')}'"
                            )
                        else:
                            print(
                                f"  ❌  WorkflowPatcher could not push: "
                                f"{patch_result.get('commit', {}).get('reason', 'unknown error')}"
                            )
                    else:
                        patch_result = {
                            "patched": False,
                            "reason": "User declined the auto-fix. See .sh script for manual steps.",
                            "fixes": [], "commit": {}, "errors": [],
                            "user_approved": False,
                        }
                        print("  ⏭️   Auto-fix declined by user — see .sh script for manual steps.")
            except Exception as exc:
                logger.warning("WorkflowPatcher failed: %s", exc)
                patch_result = {"patched": False, "error": str(exc)}

        status = "auto_patched" if patch_result.get("patched") else (
            "applied" if fix_results else "pending_manual_fix"
        )

        return {
            "incident_id": incident_id,
            "rag_match_id": rag_match.get("id"),
            "rag_match_title": rag_match.get("title"),
            "similarity": similarity,
            "solution": solution_text,
            "diagnostics_run": len(diagnostic_results),
            "diagnostic_results": diagnostic_results,
            "fix_commands_applied": len(fix_results),
            "fix_results": fix_results,
            "pending_fix_commands": pending_fix_commands,
            "fix_script": str(fix_script_path),
            "patch_result": patch_result,
            "status": status,
            "note": (
                "Fix auto-applied to repository and pushed."
                if patch_result.get("patched")
                else (
                    "Fix commands written to script – review and execute manually."
                    if pending_fix_commands
                    else "Diagnostic commands executed. No fix commands defined."
                )
            ),
        }

    # Known-safe fix types: pure YAML edits that are easily reversible via git.
    # These are auto-approved without a HITL prompt (no destructive side-effects).
    _AUTO_APPROVE_TYPES = frozenset({
        "nodejs_version_update",
        "nodejs20_force_node24_env",
        "mvnw_to_mvn",
        "mvnw_chmod",
        "npm_add_test_script",
        "maven_no_tests_fix",
        "maven_skip_failing_tests",
        "angular_chromeless_fix",
    })

    # ------------------------------------------------------------------
    def _prompt_human_for_approval(
        self,
        plans: list[dict[str, Any]],
        incident_id: str,
    ) -> bool:
        """
        Show the planned fixes to the user and ask whether to apply them.

        Pattern-based workflow file edits (in _AUTO_APPROVE_TYPES) are
        auto-approved because they are pure YAML changes, git-tracked and
        easily reverted.  LLM-generated shell commands (type='llm_command')
        still require explicit approval because their effects are arbitrary.
        """
        if self.auto_approve_fix:
            logger.info("auto_approve_fix=True — skipping HITL prompt.")
            return True

        # Auto-approve when every plan is a known-safe pattern fix
        has_unsafe_plan = any(
            p.get("type") not in self._AUTO_APPROVE_TYPES for p in plans
        )
        if not has_unsafe_plan:
            self._print_plans(plans, incident_id)
            print("  ✅  Auto-approved: all fixes are safe, reversible workflow edits.")
            logger.info(
                "Auto-approved %d safe pattern fix(es) for incident %s.",
                len(plans), incident_id,
            )
            return True

        import sys
        is_tty = sys.stdin.isatty()

        self._print_plans(plans, incident_id)

        if not is_tty:
            print("  ⏭️   Non-interactive session — fix requires explicit approval.")
            print("       Re-run with --auto-approve-fix to apply automatically.")
            return False

        try:
            answer = input("  Apply these fixes now? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        return answer in ("y", "yes")

    def _print_plans(self, plans: list[dict[str, Any]], incident_id: str) -> None:
        print()
        print("  " + "═" * 64)
        print(f"   Incident Response Agent — Auto-fix Proposal")
        print(f"   Incident: {incident_id}")
        print("  " + "═" * 64)
        for i, plan in enumerate(plans, 1):
            print(f"  {i}. {plan.get('description', '(no description)')}")
            print(f"     File   : {plan.get('file', '(unknown)')}")
            preview = plan.get("preview", "")
            if preview:
                for line in str(preview).splitlines():
                    print(f"     | {line}")
            print()
        print("  The agent will edit these file(s), commit and push to the branch.")
        print("  " + "─" * 64)

    # ------------------------------------------------------------------
    def _write_fix_script(
        self,
        *,
        incident_id: str,
        incident: dict[str, Any],
        rag_match: dict[str, Any],
        solution_text: str,
        diagnostic_results: list[dict[str, Any]],
        pending_fix_commands: list[str],
        ctx: dict[str, str],
    ) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_id = incident_id[:24].replace("/", "-")
        script_path = self.output_dir / f"fix_{safe_id}_{ts}.sh"

        lines = [
            "#!/usr/bin/env bash",
            "# ============================================================",
            f"# Auto-generated incident fix script",
            f"# Generated : {ts}",
            f"# Incident  : {incident_id}",
            f"# RAG match : {rag_match.get('title')} (similarity={rag_match.get('similarity', 0):.3f})",
            f"# Category  : {rag_match.get('category')}  Severity: {rag_match.get('severity')}",
            "# ============================================================",
            "",
            "# ── Incident details ──────────────────────────────────────────",
            f"# Status    : {incident.get('status')}",
            f"# Services  : {', '.join(incident.get('service_names', []))}",
            f"# Env       : {incident.get('environment')}",
            f"# Occurred  : {incident.get('occurrences', 1)}x",
            "",
            "# ── Remediation plan ──────────────────────────────────────────",
        ]

        for i, step in enumerate(solution_text.splitlines(), 1):
            lines.append(f"# {step}")

        lines += [
            "",
            "# ── Diagnostic commands (already executed) ────────────────────",
        ]
        for r in diagnostic_results:
            lines.append(f"# CMD: {r['command']}")
            lines.append(f"# RC : {r['returncode']}")
            if r["stdout"]:
                for out_line in r["stdout"].splitlines()[:10]:
                    lines.append(f"#   {out_line}")
            lines.append("")

        if pending_fix_commands:
            lines += [
                "# ── Fix commands (review before running!) ─────────────────",
                "set -euo pipefail",
                "",
            ]
            for cmd in pending_fix_commands:
                lines.append(cmd)
            lines.append("")

        lines += [
            "echo 'Fix script completed.'",
        ]

        script_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Fix script written: %s", script_path)
        return script_path
