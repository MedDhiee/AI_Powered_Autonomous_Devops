from __future__ import annotations

import logging

import networkx as nx

from .models import ServiceInfo

LOGGER = logging.getLogger(__name__)


class DependencyGraphBuilder:
    def build(self, services: list[ServiceInfo]) -> tuple[nx.DiGraph, dict[str, list[str]]]:
        graph = nx.DiGraph()
        service_names = {svc.name for svc in services}
        service_name_tokens = {self._normalize_name(name): name for name in service_names}

        adjacency: dict[str, list[str]] = {}

        for service in services:
            graph.add_node(
                service.name,
                stack=service.stack,
                path=str(service.path),
                exposed_ports=service.exposed_ports,
                database_connections=service.database_connections,
            )

        for service in services:
            linked_services: set[str] = set()
            for dep in service.dependencies:
                norm_dep = self._normalize_name(dep)
                for token, actual_name in service_name_tokens.items():
                    if token and token in norm_dep and actual_name != service.name:
                        linked_services.add(actual_name)

            adjacency[service.name] = sorted(linked_services)
            for target in linked_services:
                graph.add_edge(service.name, target, relation="depends_on")

        LOGGER.info(
            "Graph built with %d nodes and %d edges",
            graph.number_of_nodes(),
            graph.number_of_edges(),
        )
        return graph, adjacency

    def to_serializable_graph(self, graph: nx.DiGraph) -> dict:
        return nx.node_link_data(graph, edges="links")

    def _normalize_name(self, value: str) -> str:
        return "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
