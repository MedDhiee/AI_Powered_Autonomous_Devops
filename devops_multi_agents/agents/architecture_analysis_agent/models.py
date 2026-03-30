from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class DependencyParseResult:
    command: str
    dependencies: list[str] = field(default_factory=list)
    raw_summary: dict[str, Any] = field(default_factory=dict)
    success: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ServiceInfo:
    name: str
    path: Path
    stack: str
    config_files: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    dependency_tool_output: dict[str, Any] = field(default_factory=dict)
    exposed_ports: list[int] = field(default_factory=list)
    database_connections: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["path"] = str(self.path)
        return data
