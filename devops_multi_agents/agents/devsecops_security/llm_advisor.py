from __future__ import annotations

import json
import logging
import os
from typing import Any

from shared import (
    first_choice_text,
    get_default_model_for_provider,
    get_provider_connection,
    resolve_provider,
    strip_markdown_code_fence,
)

LOGGER = logging.getLogger(__name__)


class DevSecOpsLLMAdvisor:
    def generate(
        self,
        findings: list[dict[str, Any]],
        risk_score: int,
        llm_model: str | None = None,
        require_real_llm: bool = False,
    ) -> dict[str, Any]:
        provider = resolve_provider("DEVSECOPS_LLM_PROVIDER", default_provider="openrouter")
        base_url, api_key = get_provider_connection(provider)
        if not api_key:
            if require_real_llm:
                raise RuntimeError(f"API key is required for provider '{provider}' when require_real_llm=True")
            return self._fallback(findings, risk_score, f"No API key configured for provider '{provider}'")

        model = llm_model or get_default_model_for_provider(
            provider,
            openrouter_env="OPENROUTER_MODEL",
            openrouter_default="nvidia/nemotron-3-super-120b-a12b:free",
        )
        temperature = float(os.getenv("DEVSECOPS_LLM_TEMPERATURE", "0.7"))
        payload = {
            "risk_score": risk_score,
            "finding_count": len(findings),
            "findings": findings[:120],
        }

        try:
            from openai import OpenAI
        except ImportError:
            return self._fallback(findings, risk_score, "openai package missing")

        client = OpenAI(base_url=base_url, api_key=api_key)

        prompt = f"""
    You are a senior DevSecOps architect.
    Analyze the findings and produce a practical, non-generic response.

    Output must be valid JSON with at least these keys:
    - security_summary (string)
    - remediation_priorities (array of strings)
    - compliance_notes (array of strings)

    Guidelines:
    - Do not use generic boilerplate.
    - Ground recommendations in concrete evidence from findings (package names, files, CVEs, or rule ids when available).
    - Prioritize by exploitability and business impact.
    - If secrets are found, include immediate containment actions.
    - Keep recommendations concise and actionable.

    Findings:
    {json.dumps(payload, indent=2)}
    """

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
            )
            content = strip_markdown_code_fence(first_choice_text(response))
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                normalized = self._normalize_llm_output(parsed)
                normalized["llm_provider"] = provider
                normalized["llm_model"] = model
                return normalized
            return self._fallback(findings, risk_score, "LLM did not return a JSON object")
        except Exception as exc:
            LOGGER.warning("LLM advisor failed: %s", exc)
            if require_real_llm:
                raise RuntimeError(f"DevSecOps LLM call failed: {exc}") from exc
            return self._fallback(findings, risk_score, str(exc))

    def _normalize_llm_output(self, value: dict[str, Any]) -> dict[str, Any]:
        summary = value.get("security_summary")
        remediation = value.get("remediation_priorities")
        compliance = value.get("compliance_notes")

        if not isinstance(summary, str):
            summary = "Security analysis generated."
        if not isinstance(remediation, list):
            remediation = []
        if not isinstance(compliance, list):
            compliance = []

        return {
            "security_summary": summary,
            "remediation_priorities": [str(item) for item in remediation if str(item).strip()],
            "compliance_notes": [str(item) for item in compliance if str(item).strip()],
        }

    def _fallback(self, findings: list[dict[str, Any]], risk_score: int, reason: str) -> dict[str, Any]:
        critical = sum(1 for f in findings if str(f.get("severity", "")).lower() == "critical")
        high = sum(1 for f in findings if str(f.get("severity", "")).lower() == "high")
        medium = sum(1 for f in findings if str(f.get("severity", "")).lower() == "medium")

        by_tool: dict[str, int] = {}
        for finding in findings:
            tool = str(finding.get("tool", "unknown")).lower()
            by_tool[tool] = by_tool.get(tool, 0) + 1

        secret_findings = [f for f in findings if str(f.get("type", "")).lower() == "secret"]
        top_targets = self._top_targets(findings, limit=3)

        remediation_priorities: list[str] = []
        if critical or high:
            remediation_priorities.append(
                f"Patch {critical} critical and {high} high vulnerabilities first (focus on internet-facing services)."
            )
        if secret_findings:
            remediation_priorities.append(
                f"Rotate and revoke {len(secret_findings)} exposed secret(s), then purge them from git history."
            )
        if by_tool.get("checkov", 0) > 0:
            remediation_priorities.append(
                "Fix IaC misconfigurations flagged by Checkov and enforce policy-as-code in CI."
            )
        if top_targets:
            remediation_priorities.append(
                "Prioritize remediation on highest-risk files: " + ", ".join(top_targets) + "."
            )
        if not remediation_priorities:
            remediation_priorities.append("No immediate high-risk issue detected; keep continuous scanning enabled.")

        compliance_notes: list[str] = []
        if critical > 0:
            compliance_notes.append("Set SLA <= 24h for critical findings and track closure evidence.")
        elif high > 0:
            compliance_notes.append("Set SLA <= 7 days for high findings and report weekly burn-down.")
        else:
            compliance_notes.append("Maintain remediation SLA tracking for medium/low findings.")

        if secret_findings:
            compliance_notes.append("Trigger secret exposure incident workflow (rotation, audit, and post-incident review).")

        if by_tool.get("checkov", 0) > 0:
            compliance_notes.append("Require IaC security gate before deployment approval.")

        compliance_notes.append(f"LLM fallback used: {reason}")

        security_summary = (
            f"Risk score {risk_score}/100 with {len(findings)} finding(s): "
            f"{critical} critical, {high} high, {medium} medium. "
            f"Tool distribution: "
            + ", ".join(f"{tool}={count}" for tool, count in sorted(by_tool.items()))
        )

        return {
            "security_summary": security_summary,
            "remediation_priorities": remediation_priorities,
            "compliance_notes": compliance_notes,
        }

    def _top_targets(self, findings: list[dict[str, Any]], limit: int = 3) -> list[str]:
        weights = {"critical": 5, "high": 3, "medium": 2, "low": 1}
        scores: dict[str, int] = {}
        for finding in findings:
            target = str(
                finding.get("target")
                or finding.get("file")
                or finding.get("package")
                or "unknown"
            )
            sev = str(finding.get("severity", "low")).lower()
            scores[target] = scores.get(target, 0) + weights.get(sev, 1)

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return [target for target, _ in ranked[:limit] if target and target != "unknown"]