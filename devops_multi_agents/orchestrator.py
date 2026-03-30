from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from shared import save_json_output

from .agents import (
    ArchitectureAnalysisAgent,
    CICDGenerationAgent,
    ChaosEngineeringAgent,
    DeploymentAgent,
    DevSecOpsSecurityAgent,
    IncidentResponseAgent,
)

LOGGER = logging.getLogger("devops_orchestrator")


class DevOpsOrchestrator:
    """LangGraph-style orchestrator for event-driven multi-agent workflow."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.architecture_agent = ArchitectureAnalysisAgent()

        self.security_agent = DevSecOpsSecurityAgent()
        self.cicd_agent = CICDGenerationAgent()
        self.deployment_agent = DeploymentAgent()
        self.chaos_agent = ChaosEngineeringAgent()
        self.incident_agent = IncidentResponseAgent()

    def run_full_workflow(
        self,
        repository_path: Path,
        output_dir: Path,
        include_llm: bool = False,
        strict_security_tools: bool = False,
        skip_image_scan: bool = False,
        llm_model: str | None = None,
        cicd_provider: str = "github",
    ) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)

        architecture_output_path = output_dir / "architecture-analysis.json"
        architecture_data = self._run_architecture_agent(
            repository_path,
            include_llm=include_llm,
            llm_model=llm_model,
        )
        save_json_output(architecture_output_path, architecture_data)
        if architecture_data.get("mermaid_diagram"):
            (output_dir / "architecture-diagram.mmd").write_text(
                architecture_data["mermaid_diagram"], encoding="utf-8"
            )

        security_result = self.security_agent.run(
            repository_path=repository_path,
            architecture_data=architecture_data,
            include_llm=include_llm,
            strict_tools=strict_security_tools,
            skip_image_scan=skip_image_scan,
            llm_model=llm_model,
        )
        save_json_output(output_dir / "devsecops-security.json", security_result.__dict__)
        if strict_security_tools and not security_result.success:
            raise RuntimeError(
                f"Security gate failed in strict mode: {security_result.error or 'unknown error'}"
            )

        cicd_result = self.cicd_agent.run(
            architecture_data=architecture_data,
            provider=cicd_provider,
        )
        save_json_output(output_dir / "cicd-generation.json", cicd_result.__dict__)

        deployment_result = self.deployment_agent.run(
            architecture_data=architecture_data,
            cicd_data=cicd_result.__dict__,
        )
        save_json_output(output_dir / "deployment-plan.json", deployment_result.__dict__)

        chaos_result = self.chaos_agent.run(architecture_data=architecture_data)
        save_json_output(output_dir / "chaos-experiments.json", chaos_result.__dict__)

        incident_result = self.incident_agent.run(
            architecture_data=architecture_data,
            security_data=security_result.__dict__,
            chaos_data=chaos_result.__dict__,
        )
        save_json_output(output_dir / "incident-response.json", incident_result.__dict__)

        workflow_summary = {
            "repository": str(repository_path),
            "architecture_output": str(architecture_output_path),
            "agents": {
                "architecture_analysis_agent": {
                    "success": True,
                    "protocol": "MCP",
                    "output": str(architecture_output_path),
                },
                "devsecops_security_agent": security_result.__dict__,
                "cicd_generation_agent": cicd_result.__dict__,
                "deployment_agent": deployment_result.__dict__,
                "chaos_engineering_agent": chaos_result.__dict__,
                "incident_response_agent": incident_result.__dict__,
            },
        }
        save_json_output(output_dir / "workflow-summary.json", workflow_summary)
        return workflow_summary

    def _run_architecture_agent(
        self,
        repository_path: Path,
        include_llm: bool,
        llm_model: str | None = None,
    ) -> dict[str, Any]:
        LOGGER.info("Triggering integrated architecture analysis agent")
        return self.architecture_agent.analyze_repository(
            repository_path,
            include_llm=include_llm,
            llm_model=llm_model,
        )
