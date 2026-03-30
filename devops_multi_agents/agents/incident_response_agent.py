from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from shared import BaseAgent, ProtocolType, save_json_output, setup_logging


class IncidentResponseAgent(BaseAgent):
    """Hybrid MCP+ACP agent: local evidence + coordinated response plan."""

    def __init__(self) -> None:
        super().__init__(agent_name="incident_response_agent", protocol=ProtocolType.HYBRID_MCP_ACP.value)

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        architecture_data: dict[str, Any] = kwargs["architecture_data"]
        security_data: dict[str, Any] = kwargs.get("security_data", {})
        chaos_data: dict[str, Any] = kwargs.get("chaos_data", {})

        findings_count = len(security_data.get("data", {}).get("findings", []))
        experiments_count = len(chaos_data.get("data", {}).get("experiments", []))

        runbook: list[str] = [
            "1) Detect incident from alerts/logs",
            "2) Classify severity and impacted services",
            "3) Contain impact (traffic shift / scale / isolate)",
            "4) Recover service and validate SLO",
            "5) Run postmortem with action items",
        ]

        impacted = [svc.get("name", "unknown") for svc in architecture_data.get("services", [])]
        incident_actions = [
            f"Review {findings_count} security findings for exploitability",
            f"Use {experiments_count} chaos scenarios to validate resilience playbooks",
            "Create RCA template and incident timeline",
            f"Track service ownership for: {', '.join(impacted)}",
        ]

        return {
            "protocol": self.protocol,
            "summary": "Generated incident response playbook",
            "runbook": runbook,
            "incident_actions": incident_actions,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Incident Response Agent")
    parser.add_argument("--arch", required=True, help="Architecture JSON path")
    parser.add_argument("--security", required=False, help="Security output JSON path")
    parser.add_argument("--chaos", required=False, help="Chaos output JSON path")
    parser.add_argument("--output", default="devops_multi_agents/outputs/incident-response.json")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    setup_logging(args.log_level)
    import json

    arch_data = json.loads(Path(args.arch).read_text(encoding="utf-8"))
    security_data = json.loads(Path(args.security).read_text(encoding="utf-8")) if args.security else {}
    chaos_data = json.loads(Path(args.chaos).read_text(encoding="utf-8")) if args.chaos else {}

    agent = IncidentResponseAgent()
    result = agent.run(architecture_data=arch_data, security_data=security_data, chaos_data=chaos_data)
    save_json_output(Path(args.output), result.__dict__)


if __name__ == "__main__":
    main()
