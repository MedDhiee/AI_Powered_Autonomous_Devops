"""Data models, enums and constants for CI/CD generation."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Final


class CICDProvider(str, Enum):
    GITHUB = "github"
    GITLAB = "gitlab"
    AZURE_DEVOPS = "azure-devops"
    BITBUCKET = "bitbucket"
    JENKINS = "jenkins"


class PipelineStage(str, Enum):
    TEST = "test"
    BUILD = "build"
    PACKAGE = "package"
    UPLOAD_ARTIFACT = "upload_artifact"
    SECURITY_SCAN = "security_scan"
    DEPLOY = "deploy"


SUPPORTED_LLM_PROVIDERS: Final[set[str]] = {"openrouter", "groq", "ollama"}

PROVIDER_ALIASES: Final[dict[str, CICDProvider]] = {
    "github": CICDProvider.GITHUB,
    "gh": CICDProvider.GITHUB,
    "gitlab": CICDProvider.GITLAB,
    "gl": CICDProvider.GITLAB,
    "azure": CICDProvider.AZURE_DEVOPS,
    "ado": CICDProvider.AZURE_DEVOPS,
    "azure-devops": CICDProvider.AZURE_DEVOPS,
    "bitbucket": CICDProvider.BITBUCKET,
    "bb": CICDProvider.BITBUCKET,
    "jenkins": CICDProvider.JENKINS,
}

STAGE_ORDER: Final[tuple[PipelineStage, ...]] = (
    PipelineStage.TEST,
    PipelineStage.BUILD,
    PipelineStage.PACKAGE,
    PipelineStage.UPLOAD_ARTIFACT,
    PipelineStage.SECURITY_SCAN,
    PipelineStage.DEPLOY,
)


@dataclass(frozen=True)
class StackCommandProfile:
    runtime_family: str
    build: tuple[str, ...]
    test: tuple[str, ...]
    package: tuple[str, ...]
    artifact_paths: tuple[str, ...]
    deploy: tuple[str, ...]


@dataclass(frozen=True)
class ServiceContext:
    name: str
    slug: str
    stack: str
    path: str
    dependencies: tuple[str, ...]
    service_dependencies: tuple[str, ...]
    exposed_ports: tuple[int, ...]
    database_connections: tuple[str, ...]


def safe_slug(raw_value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", raw_value.strip().lower())
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "service"


def to_posix(path: str) -> str:
    return path.replace("\\", "/") if path else "."
