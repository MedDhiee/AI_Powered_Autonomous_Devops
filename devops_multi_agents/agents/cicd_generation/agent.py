"""Main CI/CD Generation Agent — orchestrates static and LLM-based pipeline generation."""
from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Any

from shared import BaseAgent, ProtocolType

from .models import (
    CICDProvider,
    PROVIDER_ALIASES,
    ServiceContext,
    safe_slug,
)
from .stack_profiles import commands_for_stack
from .templates import build_static_pipeline
from .user_input import collect_project_config
from .llm_generator import (
    build_pipeline_with_llm,
    reference_aligned_pipeline,
    service_role_hint,
    text_similarity,
)


def write_pipeline_files(pipelines: dict[str, str], root_dir: Path) -> list[str]:
    """Write generated pipeline files under root_dir and return relative paths."""
    written: list[str] = []
    root = root_dir.resolve()

    for relative_file, yaml_content in pipelines.items():
        target = (root / relative_file).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"Refusing to write pipeline outside root '{root}': {relative_file}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml_content, encoding="utf-8")
        written.append(target.relative_to(root).as_posix())

    return written


class CICDGenerationAgent(BaseAgent):
    """ACP agent: generates provider-specific CI/CD pipeline files from architecture metadata."""

    def __init__(self) -> None:
        super().__init__(agent_name="cicd_generation_agent", protocol=ProtocolType.ACP.value)

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        architecture_data: dict[str, Any] = kwargs["architecture_data"]
        requested_provider = kwargs.get("provider")
        ask_provider = bool(kwargs.get("ask_provider", False))
        ask_user_input = bool(kwargs.get("ask_user_input", False))
        main_branch_override: str | None = kwargs.get("main_branch")
        include_llm = bool(kwargs.get("include_llm", False))
        llm_model = kwargs.get("llm_model")
        llm_provider_override = kwargs.get("llm_provider")
        require_real_llm = bool(kwargs.get("require_real_llm", False))
        reference_pipelines = kwargs.get("reference_pipelines")
        repository_path: Path | None = kwargs.get("repository_path")
        if repository_path is not None:
            repository_path = Path(repository_path).resolve()

        if isinstance(reference_pipelines, dict):
            reference_pipelines_clean = {
                str(role): str(text)
                for role, text in reference_pipelines.items()
                if str(text or "").strip()
            }
        else:
            reference_pipelines_clean = {}

        provider = self._resolve_provider(requested_provider, ask_provider=ask_provider)
        service_contexts = self._extract_service_contexts(architecture_data, repository_path=repository_path)

        # Optionally collect project-specific config interactively
        project_config = collect_project_config(
            service_contexts,
            non_interactive=not ask_user_input,
        )

        # Override main_branch from the detected repo branch (passed by orchestrator)
        if main_branch_override:
            project_config.main_branch = main_branch_override

        pipelines: dict[str, str] = {}
        llm_generated = 0
        llm_fallbacks = 0
        llm_provider_used: str | None = None
        llm_model_used: str | None = None

        for service in service_contexts:
            profile = commands_for_stack(service.stack, project_config)
            pipeline_name = self._pipeline_filename(service.slug, provider)
            pipeline_yaml, generation_meta = self._build_pipeline_with_fallback(
                provider=provider,
                service=service,
                architecture_data=architecture_data,
                profile=profile,
                include_llm=include_llm,
                llm_model=llm_model,
                llm_provider_override=llm_provider_override,
                require_real_llm=require_real_llm,
                reference_pipelines=reference_pipelines_clean,
                project_config=project_config,
            )
            pipelines[pipeline_name] = pipeline_yaml

            if generation_meta.get("used_llm"):
                llm_generated += 1
                llm_provider_used = str(generation_meta.get("llm_provider") or llm_provider_used)
                llm_model_used = str(generation_meta.get("llm_model") or llm_model_used)
            elif include_llm:
                llm_fallbacks += 1

        generation_mode = "template"
        if include_llm and llm_generated and llm_fallbacks:
            generation_mode = "llm-with-fallback"
        elif include_llm and llm_generated == len(pipelines):
            generation_mode = "llm"

        return {
            "protocol": self.protocol,
            "provider": provider.value,
            "summary": f"Generated {len(pipelines)} pipeline template(s) for {provider.value}",
            "pipelines": pipelines,
            "generation_mode": generation_mode,
            "llm_enabled": include_llm,
            "llm_generated_pipelines": llm_generated,
            "llm_fallback_pipelines": llm_fallbacks,
            "llm_provider": llm_provider_used,
            "llm_model": llm_model_used,
        }

    # -----------------------------------------------------------------------
    # Path helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _to_relative_path(raw_path: str, repository_path: Path | None) -> str:
        """
        Convert an absolute service path to a POSIX relative path from the
        repository root. Falls back to the basename if anything goes wrong.
        """
        p = Path(raw_path)
        if repository_path is not None and p.is_absolute():
            try:
                rel = p.relative_to(repository_path)
                return rel.as_posix()
            except ValueError:
                pass
        # Already relative or can't relativize — just forward-slash it
        if p.is_absolute():
            # Last resort: use only the final component
            return p.name
        return raw_path.replace("\\", "/")

    # -----------------------------------------------------------------------
    # Service context extraction
    # -----------------------------------------------------------------------

    def _extract_service_contexts(
        self,
        architecture_data: dict[str, Any],
        repository_path: Path | None = None,
    ) -> list[ServiceContext]:
        dependency_map = architecture_data.get("dependencies", {})
        if not isinstance(dependency_map, dict):
            dependency_map = {}

        contexts: list[ServiceContext] = []
        for raw_service in architecture_data.get("services", []):
            if not isinstance(raw_service, dict):
                continue

            name = str(raw_service.get("name", "service")).strip() or "service"
            raw_path = str(raw_service.get("path", ".")).strip() or "."
            # Convert absolute path to relative from repository root
            path = self._to_relative_path(raw_path, repository_path)
            stack = str(raw_service.get("stack", "unknown")).strip() or "unknown"
            deps_raw = raw_service.get("dependencies", [])
            exposed_ports_raw = raw_service.get("exposed_ports", [])
            db_connections_raw = raw_service.get("database_connections", [])
            service_dependencies_raw = dependency_map.get(name, [])

            dependencies = tuple(str(dep) for dep in deps_raw if str(dep).strip()) if isinstance(deps_raw, list) else ()
            exposed_ports = tuple(int(port) for port in exposed_ports_raw if str(port).isdigit()) if isinstance(exposed_ports_raw, list) else ()
            database_connections = (
                tuple(str(item) for item in db_connections_raw if str(item).strip())
                if isinstance(db_connections_raw, list) else ()
            )
            service_dependencies = (
                tuple(str(item) for item in service_dependencies_raw if str(item).strip())
                if isinstance(service_dependencies_raw, list) else ()
            )

            contexts.append(
                ServiceContext(
                    name=name, slug=safe_slug(name), stack=stack, path=path,
                    dependencies=dependencies, service_dependencies=service_dependencies,
                    exposed_ports=exposed_ports, database_connections=database_connections,
                )
            )
        return contexts

    # -----------------------------------------------------------------------
    # Pipeline building with LLM fallback
    # -----------------------------------------------------------------------

    def _build_pipeline_with_fallback(
        self, *, provider, service, architecture_data, profile,
        include_llm, llm_model, llm_provider_override, require_real_llm, reference_pipelines,
        project_config=None,
    ) -> tuple[str, dict[str, Any]]:
        if not include_llm:
            return (
                build_static_pipeline(provider=provider, service=service, profile=profile, config=project_config),
                {"used_llm": False},
            )

        try:
            pipeline_yaml, llm_provider, model_used = build_pipeline_with_llm(
                provider=provider, service=service, architecture_data=architecture_data,
                profile=profile, llm_model=llm_model, llm_provider_override=llm_provider_override,
                reference_pipelines=reference_pipelines,
            )
            return pipeline_yaml, {"used_llm": True, "llm_provider": llm_provider, "llm_model": model_used}
        except Exception as exc:
            reference_fallback = self._reference_fallback_pipeline_for_service(
                provider=provider, service=service, reference_pipelines=reference_pipelines,
            )
            if reference_fallback is not None:
                self.logger.warning(
                    "CI/CD LLM generation failed for service '%s' (%s). Using reference-aligned fallback.",
                    service.name, exc,
                )
                return reference_fallback, {"used_llm": False}

            if require_real_llm:
                raise RuntimeError(f"CI/CD LLM generation failed for service '{service.name}': {exc}") from exc
            self.logger.warning(
                "CI/CD LLM generation failed for service '%s' (%s). Falling back to static template.",
                service.name, exc,
            )
            return (
                build_static_pipeline(provider=provider, service=service, profile=profile, config=project_config),
                {"used_llm": False},
            )

    def _reference_fallback_pipeline_for_service(self, *, provider, service, reference_pipelines) -> str | None:
        if provider != CICDProvider.GITHUB:
            return None
        if not reference_pipelines:
            return None
        role = service_role_hint(service.stack, service.name)
        ref_yaml = str(reference_pipelines.get(role, "") or "")
        if not ref_yaml.strip():
            return None
        return reference_aligned_pipeline(reference_yaml=ref_yaml, service=service)

    # -----------------------------------------------------------------------
    # Provider resolution
    # -----------------------------------------------------------------------

    def _resolve_provider(self, provider: Any, ask_provider: bool) -> CICDProvider:
        if provider is None:
            if ask_provider:
                return self._prompt_provider()
            return CICDProvider.GITHUB

        normalized = str(provider).strip().lower()
        mapped = PROVIDER_ALIASES.get(normalized)
        if mapped:
            return mapped

        supported = ", ".join(sorted({item.value for item in PROVIDER_ALIASES.values()}))
        raise ValueError(f"Unsupported provider '{provider}'. Supported providers: {supported}")

    def _prompt_provider(self) -> CICDProvider:
        options = sorted({item.value for item in PROVIDER_ALIASES.values()})
        print("Select CI/CD provider to generate templates for.")
        print(f"Available providers: {', '.join(options)}")

        try:
            choice = input("Provider [github]: ").strip()
        except EOFError:
            self.logger.warning("No interactive input available, defaulting provider to github")
            return CICDProvider.GITHUB

        if not choice:
            return CICDProvider.GITHUB
        return self._resolve_provider(choice, ask_provider=False)

    def _pipeline_filename(self, service_slug: str, provider: CICDProvider) -> str:
        if provider == CICDProvider.GITHUB:
            return f".github/workflows/{service_slug}-pipeline.yml"
        if provider == CICDProvider.GITLAB:
            return f".gitlab-ci-{service_slug}.yml"
        if provider == CICDProvider.AZURE_DEVOPS:
            return f"azure-pipelines-{service_slug}.yml"
        if provider == CICDProvider.BITBUCKET:
            return f"bitbucket-pipelines-{service_slug}.yml"
        if provider == CICDProvider.JENKINS:
            return "Jenkinsfile"
        return f"{service_slug}-pipeline.yml"
