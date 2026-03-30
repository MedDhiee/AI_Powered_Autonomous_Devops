from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class AgentType(str, Enum):
	ARCHITECTURE = "architecture_analysis_agent"
	DEVSECOPS = "devsecops_security_agent"
	CICD = "cicd_generation_agent"
	DEPLOYMENT = "deployment_agent"
	CHAOS = "chaos_engineering_agent"
	INCIDENT = "incident_response_agent"
	ORCHESTRATOR = "ai_orchestrator"


class ProtocolType(str, Enum):
	MCP = "MCP"
	ACP = "ACP"
	A2A = "A2A"
	HYBRID_MCP_ACP = "HYBRID_MCP_ACP"


@dataclass
class LLMConfig:
	provider: str = "openrouter"
	model: str = "z-ai/glm-4.5-air:free"
	key_env_name: str = "OPENROUTER_API_KEY"


@dataclass
class ServiceInfo:
	name: str
	stack: str
	path: str
	dependencies: list[str] = field(default_factory=list)
	exposed_ports: list[int] = field(default_factory=list)
	database_connections: list[str] = field(default_factory=list)


@dataclass
class RepositoryContext:
	repository_path: Path
	architecture_output_path: Path


@dataclass
class AgentOutput:
	agent_type: AgentType
	protocol: ProtocolType
	summary: str
	artifacts: dict[str, Any] = field(default_factory=dict)


@dataclass
class SecurityOutput:
	findings: list[dict[str, Any]] = field(default_factory=list)
	risk_score: int = 0


@dataclass
class CICDOutput:
	pipelines: dict[str, str] = field(default_factory=dict)


@dataclass
class IaCOutput:
	deployment_plan: list[str] = field(default_factory=list)
	generated_manifests: dict[str, str] = field(default_factory=dict)


@dataclass
class MonitoringOutput:
	experiments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DocumentationOutput:
	runbook: list[str] = field(default_factory=list)
	incident_actions: list[str] = field(default_factory=list)
