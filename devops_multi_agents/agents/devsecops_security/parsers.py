from __future__ import annotations

from typing import Any


def parse_trivy_findings(data: dict[str, Any], source: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for result in data.get("Results", []):
        target = result.get("Target", "unknown")
        for vuln in result.get("Vulnerabilities", []) or []:
            findings.append(
                {
                    "tool": "trivy",
                    "type": "vulnerability",
                    "severity": str(vuln.get("Severity", "UNKNOWN")).lower(),
                    "source": source,
                    "target": target,
                    "id": vuln.get("VulnerabilityID"),
                    "package": vuln.get("PkgName"),
                    "installed_version": vuln.get("InstalledVersion"),
                    "fixed_version": vuln.get("FixedVersion"),
                    "message": vuln.get("Title") or vuln.get("Description") or "Trivy vulnerability finding",
                }
            )
    return findings


def parse_gitleaks_findings(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for leak in data:
        findings.append(
            {
                "tool": "gitleaks",
                "type": "secret",
                "severity": "high",
                "file": leak.get("File"),
                "line": leak.get("StartLine"),
                "rule_id": leak.get("RuleID"),
                "message": leak.get("Description") or "Potential leaked secret",
            }
        )
    return findings


def parse_checkov_findings(data: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    check_sets = []
    if isinstance(data, dict):
        if "results" in data:
            check_sets.append(data)
        if "summary" in data and "results" in data:
            check_sets.append(data)

    for check_data in check_sets:
        for failed in check_data.get("results", {}).get("failed_checks", []) or []:
            findings.append(
                {
                    "tool": "checkov",
                    "type": "iac_misconfiguration",
                    "severity": "medium",
                    "resource": failed.get("resource"),
                    "file": failed.get("file_path"),
                    "check_id": failed.get("check_id"),
                    "check_name": failed.get("check_name"),
                    "message": failed.get("guideline") or failed.get("check_name") or "Checkov failed check",
                }
            )
    return findings


def compute_risk_score(findings: list[dict[str, Any]]) -> int:
    weights = {
        "critical": 40,
        "high": 25,
        "medium": 10,
        "low": 3,
        "unknown": 1,
    }
    total = sum(weights.get(str(f.get("severity", "unknown")).lower(), 1) for f in findings)
    return min(100, total)
