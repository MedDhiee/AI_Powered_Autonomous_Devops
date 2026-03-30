from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .dependency_parser import DependencyParser
from .diagram_generator import MermaidGenerator
from .graph_builder import DependencyGraphBuilder
from .llm_integration import LLMArchitectureAdvisor
from .models import ServiceInfo
from .scanner import RepositoryScanner
from .stack_detector import StackDetector
from .structure_extractor import ServiceStructureExtractor

LOGGER = logging.getLogger(__name__)


class ArchitectureAnalysisAgent:
    def __init__(self) -> None:
        self.scanner = RepositoryScanner()
        self.stack_detector = StackDetector()
        self.dependency_parser = DependencyParser()
        self.structure_extractor = ServiceStructureExtractor()
        self.graph_builder = DependencyGraphBuilder()
        self.mermaid_generator = MermaidGenerator()
        self.llm_advisor = LLMArchitectureAdvisor()

    def analyze_repository(
        self,
        repository_path: Path,
        include_llm: bool = False,
        llm_model: str | None = None,
    ) -> dict[str, Any]:
        LOGGER.info("Starting architecture analysis for repository: %s", repository_path)

        service_paths = self.scanner.scan(repository_path)
        services: list[ServiceInfo] = []

        for service_path in service_paths:
            stack, config_files = self.stack_detector.detect(service_path)
            service_name = service_path.name
            LOGGER.info("Analyzing service '%s' with detected stack '%s'", service_name, stack)

            dep_result = self.dependency_parser.parse(service_path, stack)
            if not dep_result.success:
                LOGGER.warning(
                    "Dependency parsing failed for %s (%s): %s",
                    service_name,
                    stack,
                    dep_result.error,
                )

            service_info = ServiceInfo(
                name=service_name,
                path=service_path,
                stack=stack,
                config_files=config_files,
                dependencies=dep_result.dependencies,
                dependency_tool_output=dep_result.to_dict(),
                exposed_ports=self.structure_extractor.extract_ports(
                    service_path=service_path,
                    repository_path=repository_path,
                    stack=stack,
                    service_name=service_name,
                ),
                database_connections=self.structure_extractor.detect_database_connections(service_path),
            )
            services.append(service_info)

        graph, adjacency = self.graph_builder.build(services)

        output: dict[str, Any] = {
            "services": [svc.to_dict() for svc in services],
            "dependencies": adjacency,
            "graph": self.graph_builder.to_serializable_graph(graph),
            "mermaid_diagram": self.mermaid_generator.generate(services, adjacency),
            "meta": {
                "service_count": len(services),
                "repository": str(repository_path),
            },
        }

        if include_llm:
            output["llm_output"] = self.llm_advisor.generate(output, llm_model=llm_model)

        LOGGER.info("Architecture analysis completed for repository: %s", repository_path)
        return output
