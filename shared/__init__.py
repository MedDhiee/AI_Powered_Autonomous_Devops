"""
Shared module for DevOps Multi-Agent System.

Provides common models, base classes, and utilities for all agents.
"""

from .base_agent import AgentResult, BaseAgent
from .llm_common import (
    first_choice_text,
    get_default_model_for_provider,
    get_provider_connection,
    resolve_provider,
    strip_markdown_code_fence,
)
from .common_models import (
    AgentOutput,
    AgentType,
    ProtocolType,
    CICDOutput,
    DocumentationOutput,
    IaCOutput,
    LLMConfig,
    MonitoringOutput,
    RepositoryContext,
    SecurityOutput,
    ServiceInfo,
)
from .utils import (
    extract_dependencies_from_architecture_output,
    extract_services_from_architecture_output,
    format_duration,
    get_primary_stack,
    load_json_output,
    merge_outputs,
    sanitize_name,
    save_json_output,
    setup_logging,
    truncate_text,
)

__all__ = [
    # Base classes
    "BaseAgent",
    "AgentResult",
    # Models
    "AgentOutput",
    "AgentType",
    "ProtocolType",
    "CICDOutput",
    "DocumentationOutput",
    "IaCOutput",
    "LLMConfig",
    "MonitoringOutput",
    "RepositoryContext",
    "SecurityOutput",
    "ServiceInfo",
    # Utilities
    "extract_dependencies_from_architecture_output",
    "extract_services_from_architecture_output",
    "format_duration",
    "get_primary_stack",
    "load_json_output",
    "merge_outputs",
    "sanitize_name",
    "save_json_output",
    "setup_logging",
    "truncate_text",
    "resolve_provider",
    "get_provider_connection",
    "get_default_model_for_provider",
    "strip_markdown_code_fence",
    "first_choice_text",
]
