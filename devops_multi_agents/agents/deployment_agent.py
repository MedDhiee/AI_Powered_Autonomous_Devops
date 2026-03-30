from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from shared import BaseAgent, ProtocolType, save_json_output, setup_logging


class DeploymentAgent(BaseAgent):
    """A2A agent: builds deployment plan from architecture + CI/CD artifacts."""

    def __init__(self) -> None:
        super().__init__(agent_name="deployment_agent", protocol=ProtocolType.A2A.value)

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        architecture_data: dict[str, Any] = kwargs["architecture_data"]
        cicd_data: dict[str, Any] = kwargs.get("cicd_data", {})

        services = architecture_data.get("services", [])
        plan: list[str] = [
            "Validate container image tags",
            "Create/update target namespace",
            "Apply secrets and config",
        ]

        manifests: dict[str, str] = {}
        for service in services:
            service_name = str(service.get("name", "service"))
            stack = str(service.get("stack", "unknown"))
            ports = service.get("exposed_ports", [])
            image = f"{service_name}:latest"
            port = ports[0] if ports else 80
            manifests[f"{service_name}-deployment.yaml"] = self._k8s_manifest(service_name, image, port, stack)
            plan.append(f"Deploy {service_name} ({stack})")

        if cicd_data.get("data", {}).get("pipelines"):
            plan.append("Attach generated CI/CD pipelines to deployment gates")

        return {
            "protocol": self.protocol,
            "summary": f"Generated deployment plan for {len(services)} service(s)",
            "deployment_plan": plan,
            "generated_manifests": manifests,
        }

    def _k8s_manifest(self, name: str, image: str, port: int, stack: str) -> str:
        return f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {name}
  labels:
    app: {name}
    stack: {stack}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {name}
  template:
    metadata:
      labels:
        app: {name}
    spec:
      containers:
        - name: {name}
          image: {image}
          ports:
            - containerPort: {port}
---
apiVersion: v1
kind: Service
metadata:
  name: {name}
spec:
  selector:
    app: {name}
  ports:
    - port: {port}
      targetPort: {port}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Deployment Agent")
    parser.add_argument("--arch", required=True, help="Architecture JSON path")
    parser.add_argument("--cicd", required=False, help="CI/CD output JSON path")
    parser.add_argument("--output", default="devops_multi_agents/outputs/deployment-plan.json")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    setup_logging(args.log_level)
    import json

    arch_data = json.loads(Path(args.arch).read_text(encoding="utf-8"))
    cicd_data = json.loads(Path(args.cicd).read_text(encoding="utf-8")) if args.cicd else {}
    agent = DeploymentAgent()
    result = agent.run(architecture_data=arch_data, cicd_data=cicd_data)
    save_json_output(Path(args.output), result.__dict__)


if __name__ == "__main__":
    main()
