from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from shared import BaseAgent, ProtocolType, save_json_output, setup_logging


class ChaosEngineeringAgent(BaseAgent):
    """A2A agent: proposes resilience experiments from deployment context."""

    def __init__(self) -> None:
        super().__init__(agent_name="chaos_engineering_agent", protocol=ProtocolType.A2A.value)

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        architecture_data: dict[str, Any] = kwargs["architecture_data"]

        experiments: list[dict[str, Any]] = []
        for service in architecture_data.get("services", []):
            service_name = str(service.get("name", "service"))
            ports = service.get("exposed_ports", [])
            experiments.append(
                {
                    "name": f"kill-pod-{service_name}",
                    "target": service_name,
                    "type": "pod-failure",
                    "expected_outcome": "Service recovers within SLO",
                }
            )
            if ports:
                experiments.append(
                    {
                        "name": f"network-latency-{service_name}",
                        "target": service_name,
                        "type": "network-latency",
                        "expected_outcome": "Retries/backoff handle transient failures",
                    }
                )

        return {
            "protocol": self.protocol,
            "summary": f"Prepared {len(experiments)} chaos experiment(s)",
            "experiments": experiments,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Chaos Engineering Agent")
    parser.add_argument("--arch", required=True, help="Architecture JSON path")
    parser.add_argument("--output", default="devops_multi_agents/outputs/chaos-experiments.json")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    setup_logging(args.log_level)
    import json

    arch_data = json.loads(Path(args.arch).read_text(encoding="utf-8"))
    agent = ChaosEngineeringAgent()
    result = agent.run(architecture_data=arch_data)
    save_json_output(Path(args.output), result.__dict__)


if __name__ == "__main__":
    main()
