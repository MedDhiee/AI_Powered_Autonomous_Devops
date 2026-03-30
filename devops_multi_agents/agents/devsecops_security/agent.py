from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shared import BaseAgent, ProtocolType

from .llm_advisor import DevSecOpsLLMAdvisor
from .parsers import compute_risk_score, parse_checkov_findings, parse_gitleaks_findings, parse_trivy_findings
from .tool_runner import SecurityToolRunner


class DevSecOpsSecurityAgent(BaseAgent):
    """MCP agent: run local security tools (Trivy, Gitleaks, Checkov) via Docker."""

    def __init__(self) -> None:
        super().__init__(agent_name="devsecops_security_agent", protocol=ProtocolType.MCP.value)
        self.runner = SecurityToolRunner()
        self.llm_advisor = DevSecOpsLLMAdvisor()

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        repository_path: Path = kwargs["repository_path"]
        architecture_data: dict[str, Any] = kwargs["architecture_data"]
        include_llm: bool = bool(kwargs.get("include_llm", True))
        strict_tools: bool = bool(kwargs.get("strict_tools", False))
        skip_image_scan: bool = bool(kwargs.get("skip_image_scan", False))
        llm_model: str | None = kwargs.get("llm_model")
        require_real_llm: bool = bool(kwargs.get("require_real_llm", False))

        findings: list[dict[str, Any]] = []
        tool_status: dict[str, Any] = {}

        # Precheck before any scan: Docker daemon + required tool images.
        precheck = self.runner.precheck_environment()
        tool_status["precheck"] = precheck
        if not precheck.get("ok", False):
            summary = "Precheck failed before scans"
            response = {
                "protocol": self.protocol,
                "summary": summary,
                "risk_score": 0,
                "tool_status": tool_status,
                "findings": findings,
            }
            if strict_tools:
                details = "; ".join(precheck.get("messages", []))
                raise RuntimeError(details or "Precheck failed before scans")
            if include_llm:
                response["llm_output"] = self.llm_advisor.generate(
                    findings=findings,
                    risk_score=0,
                    llm_model=llm_model,
                    require_real_llm=require_real_llm,
                )
            return response

        # 1) Trivy filesystem scan
        trivy_fs_ok, trivy_fs_out, trivy_fs_err = self.runner.run_trivy_fs(repository_path)
        trivy_fs_json = self.runner.parse_json(trivy_fs_out)
        if isinstance(trivy_fs_json, dict):
            findings.extend(parse_trivy_findings(trivy_fs_json, source="filesystem"))
            tool_status["trivy_fs"] = {"success": trivy_fs_ok, "error": trivy_fs_err or None}
        else:
            tool_status["trivy_fs"] = {"success": False, "error": trivy_fs_err or "No valid JSON output"}

        # 2) Trivy image scans based on architecture services (optional)
        image_scan_results: list[dict[str, Any]] = []
        if skip_image_scan:
            tool_status["trivy_images"] = {
                "skipped": True,
                "reason": "skip_image_scan option enabled",
            }
        else:
            for service in architecture_data.get("services", []):
                image_ref = f"{service.get('name', 'service')}:latest"
                ok, out, err = self.runner.run_trivy_image(image_ref)
                data = self.runner.parse_json(out)
                if isinstance(data, dict):
                    findings.extend(parse_trivy_findings(data, source=f"image:{image_ref}"))
                image_scan_results.append(
                    {
                        "image": image_ref,
                        "success": ok,
                        "error": err or None,
                    }
                )
            tool_status["trivy_images"] = image_scan_results

        # 3) Gitleaks scan
        gitleaks_ok, gitleaks_out, gitleaks_err = self.runner.run_gitleaks(repository_path)
        gitleaks_json = self.runner.parse_json(gitleaks_out)
        if isinstance(gitleaks_json, list):
            findings.extend(parse_gitleaks_findings(gitleaks_json))
            tool_status["gitleaks"] = {"success": gitleaks_ok, "error": gitleaks_err or None}
        else:
            tool_status["gitleaks"] = {"success": False, "error": gitleaks_err or "No valid JSON output"}

        # 4) Checkov scan
        checkov_ok, checkov_out, checkov_err = self.runner.run_checkov(repository_path)
        checkov_json = self.runner.parse_json(checkov_out)
        if isinstance(checkov_json, dict):
            findings.extend(parse_checkov_findings(checkov_json))
            tool_status["checkov"] = {
                "success": True,
                "error": checkov_err or None,
                "process_return_ok": checkov_ok,
            }
        elif isinstance(checkov_json, list):
            tool_status["checkov"] = {
                "success": True,
                "error": checkov_err or None,
                "process_return_ok": checkov_ok,
                "note": "Checkov returned JSON list; no failed_checks parsed from list format",
            }
        elif checkov_ok:
            tool_status["checkov"] = {
                "success": True,
                "error": checkov_err or None,
                "process_return_ok": True,
                "note": "Checkov command succeeded but no parseable JSON payload was found",
            }
        else:
            tool_status["checkov"] = {"success": False, "error": checkov_err or "No valid JSON output"}

        risk_score = compute_risk_score(findings)

        response = {
            "protocol": self.protocol,
            "summary": f"Detected {len(findings)} security finding(s) using Trivy/Gitleaks/Checkov",
            "risk_score": risk_score,
            "tool_status": tool_status,
            "findings": findings,
        }

        if strict_tools:
            failed_tools = self._failed_tools(tool_status, skip_image_scan=skip_image_scan)
            response["strict_gate"] = {
                "enabled": True,
                "skip_image_scan": skip_image_scan,
                "failed_tools": failed_tools,
                "passed": not failed_tools,
            }
            if failed_tools:
                failed_details: dict[str, Any] = {}
                for tool_name in failed_tools:
                    raw = tool_status.get(tool_name)
                    if isinstance(raw, dict):
                        failed_details[tool_name] = {
                            "error": raw.get("error"),
                            "note": raw.get("note"),
                        }
                    else:
                        failed_details[tool_name] = raw
                raise RuntimeError(
                    "Strict security mode enabled and tool execution failed for: "
                    + ", ".join(failed_tools)
                    + " | strict_gate="
                    + json.dumps(response["strict_gate"])
                    + " | failed_details="
                    + json.dumps(failed_details)
                )

        if include_llm:
            response["llm_output"] = self.llm_advisor.generate(
                findings=findings,
                risk_score=risk_score,
                llm_model=llm_model,
                require_real_llm=require_real_llm,
            )

        return response

    def _failed_tools(self, tool_status: dict[str, Any], skip_image_scan: bool = False) -> list[str]:
        failed: list[str] = []
        if not tool_status.get("trivy_fs", {}).get("success", False):
            failed.append("trivy_fs")
        if not tool_status.get("gitleaks", {}).get("success", False):
            failed.append("gitleaks")
        if not tool_status.get("checkov", {}).get("success", False):
            failed.append("checkov")

        if not skip_image_scan:
            image_results = tool_status.get("trivy_images", [])
            if isinstance(image_results, list) and any(not item.get("success", False) for item in image_results):
                failed.append("trivy_images")
        return failed
