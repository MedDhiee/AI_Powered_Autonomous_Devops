from __future__ import annotations

import logging

from .models import ServiceInfo

LOGGER = logging.getLogger(__name__)


class MermaidGenerator:
    def generate(self, services: list[ServiceInfo], adjacency: dict[str, list[str]]) -> str:
        """
        Generates a Mermaid.js flowchart (graph TD) representing the architecture.
        """
        lines = ["graph TD"]
        
        # Add service nodes
        for svc in services:
            stack_label = f"\\n({svc.stack})" if svc.stack and svc.stack != "unknown" else ""
            # Escape strings for mermaid node labels
            display_name = f"{svc.name}{stack_label}".replace('"', "'")
            lines.append(f"    {svc.name}[\"{display_name}\"]")
            
        # Add database notes as nodes
        db_nodes = set()
        for svc in services:
            for db in svc.database_connections:
                db_id = self._normalize_id(db)
                if db_id not in db_nodes:
                    db_label = f"DB: {db[:25]}..." if len(db) > 25 else f"DB: {db}"
                    lines.append(f"    {db_id}[(\"{db_label}\")]")
                    db_nodes.add(db_id)
                # Link service to database
                lines.append(f"    {svc.name} -->|connects| {db_id}")
                
        # Add service-to-service dependencies
        for source, targets in adjacency.items():
            for target in targets:
                lines.append(f"    {source} -->|depends on| {target}")
                
        # Enhance styling
        if services:
            service_ids = ",".join(svc.name for svc in services)
            lines.append(f"    class {service_ids} mservice")
        if db_nodes:
            db_ids = ",".join(db_nodes)
            lines.append(f"    class {db_ids} mdb")
            
        lines.append("    classDef mservice fill:#e1f5fe,stroke:#333,stroke-width:2px;")
        lines.append("    classDef mdb fill:#fff3e0,stroke:#333,stroke-width:2px;")
        
        result = "\n".join(lines)
        LOGGER.info("Generated Mermaid diagram with %d items (nodes/edges)", len(lines) - 1)
        return result

    def _normalize_id(self, value: str) -> str:
        return "db_" + "".join(ch.lower() if ch.isalnum() else "_" for ch in value)
