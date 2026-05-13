from __future__ import annotations

import argparse
import difflib
import json
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Final

from shared import (
    BaseAgent,
    ProtocolType,
    first_choice_text,
    get_default_model_for_provider,
    get_provider_connection,
    resolve_provider,
    save_json_output,
    setup_logging,
    strip_markdown_code_fence,
)


class CICDProvider(str, Enum):
    GITHUB = "github"
    GITLAB = "gitlab"
    AZURE_DEVOPS = "azure-devops"
    BITBUCKET = "bitbucket"


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


DEFAULT_PROFILE = StackCommandProfile(
    runtime_family="generic",
    build=("echo 'No build step configured'",),
    test=("echo 'No test step configured'",),
    package=("echo 'No package step configured'",),
    artifact_paths=("build/**",),
    deploy=("echo 'Set deployment commands for this stack'",),
)

STACK_ALIASES: Final[dict[str, str]] = {
    "node": "nodejs",
    "nodejs": "nodejs",
    "javascript": "nodejs",
    "typescript": "nodejs",
    "angular": "nodejs",
    "react": "nodejs",
    "vue": "nodejs",
    "java": "java-maven",
    "java-maven": "java-maven",
    "maven": "java-maven",
    "java-gradle": "java-gradle",
    "gradle": "java-gradle",
    "kotlin": "java-gradle",
    "python": "python",
    "fastapi": "python",
    "flask": "python",
    "django": "python",
    "dotnet": "dotnet",
    "csharp": "dotnet",
    "aspnet": "dotnet",
    "go": "go",
    "golang": "go",
}

STACK_PROFILES: Final[dict[str, StackCommandProfile]] = {
    "nodejs": StackCommandProfile(
        runtime_family="node",
        build=("npm ci", "npm run build"),
        test=("npm ci", "npm test -- --watch=false"),
        package=("npm pack",),
        artifact_paths=("dist/**", "*.tgz"),
        deploy=(
            "echo 'Example deploy: kubectl apply -f k8s/'",
            "echo 'Example deploy: helm upgrade --install app ./chart'",
        ),
    ),
    "java-maven": StackCommandProfile(
        runtime_family="java",
        build=("mvn -B -DskipTests clean compile",),
        test=("mvn -B test",),
        package=("mvn -B -DskipTests package",),
        artifact_paths=("target/**",),
        deploy=(
            "echo 'Example deploy: java -jar target/*.jar'",
            "echo 'Example deploy: kubectl rollout restart deployment/backend'",
        ),
    ),
    "java-gradle": StackCommandProfile(
        runtime_family="java",
        build=("./gradlew build -x test",),
        test=("./gradlew test",),
        package=("./gradlew bootJar",),
        artifact_paths=("build/libs/**",),
        deploy=(
            "echo 'Example deploy: kubectl apply -f deploy/'",
            "echo 'Example deploy: helm upgrade --install service ./chart'",
        ),
    ),
    "python": StackCommandProfile(
        runtime_family="python",
        build=("python -m pip install --upgrade pip", "pip install -r requirements.txt"),
        test=("pytest -q",),
        package=("python -m pip install build", "python -m build"),
        artifact_paths=("dist/**",),
        deploy=(
            "echo 'Example deploy: uvicorn app.main:app --host 0.0.0.0 --port 8000'",
            "echo 'Example deploy: kubectl apply -f k8s/'",
        ),
    ),
    "dotnet": StackCommandProfile(
        runtime_family="dotnet",
        build=("dotnet restore", "dotnet build --configuration Release --no-restore"),
        test=("dotnet test --configuration Release --no-build",),
        package=("dotnet publish --configuration Release --output publish",),
        artifact_paths=("publish/**",),
        deploy=(
            "echo 'Example deploy: az webapp deploy --src-path publish'",
            "echo 'Example deploy: kubectl apply -f k8s/'",
        ),
    ),
    "go": StackCommandProfile(
        runtime_family="go",
        build=("go build ./...",),
        test=("go test ./...",),
        package=("mkdir -p dist", "go build -o dist/app ./..."),
        artifact_paths=("dist/**",),
        deploy=(
            "echo 'Example deploy: ./dist/app'",
            "echo 'Example deploy: kubectl set image deployment/app app=$IMAGE_TAG'",
        ),
    ),
}

STACK_EXAMPLES_FOR_PROMPT: Final[str] = """
- Node.js: test=`npm test -- --watch=false`, build=`npm run build`, package=`npm pack`
- Java Maven: test=`mvn -B test`, build=`mvn -B -DskipTests clean compile`, package=`mvn -B -DskipTests package`
- Java Gradle: test=`./gradlew test`, build=`./gradlew build -x test`, package=`./gradlew bootJar`
- Python: test=`pytest -q`, build=`pip install -r requirements.txt`, package=`python -m build`
- .NET: test=`dotnet test --configuration Release --no-build`, build=`dotnet build --configuration Release`, package=`dotnet publish`
- Go: test=`go test ./...`, build=`go build ./...`, package=`go build -o dist/app ./...`
""".strip()


def _safe_slug(raw_value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", raw_value.strip().lower())
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "service"


def _to_posix(path: str) -> str:
    return path.replace("\\", "/") if path else "."


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
        include_llm = bool(kwargs.get("include_llm", False))
        llm_model = kwargs.get("llm_model")
        llm_provider_override = kwargs.get("llm_provider")
        require_real_llm = bool(kwargs.get("require_real_llm", False))
        reference_pipelines = kwargs.get("reference_pipelines")

        if isinstance(reference_pipelines, dict):
            reference_pipelines_clean = {
                str(role): str(text)
                for role, text in reference_pipelines.items()
                if str(text or "").strip()
            }
        else:
            reference_pipelines_clean = {}

        provider = self._resolve_provider(requested_provider, ask_provider=ask_provider)
        service_contexts = self._extract_service_contexts(architecture_data)

        pipelines: dict[str, str] = {}
        llm_generated = 0
        llm_fallbacks = 0
        llm_provider_used: str | None = None
        llm_model_used: str | None = None

        for service in service_contexts:
            profile = self._commands_for_stack(service.stack)
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

    def _extract_service_contexts(self, architecture_data: dict[str, Any]) -> list[ServiceContext]:
        dependency_map = architecture_data.get("dependencies", {})
        if not isinstance(dependency_map, dict):
            dependency_map = {}

        contexts: list[ServiceContext] = []
        for raw_service in architecture_data.get("services", []):
            if not isinstance(raw_service, dict):
                continue

            name = str(raw_service.get("name", "service")).strip() or "service"
            path = str(raw_service.get("path", ".")).strip() or "."
            stack = str(raw_service.get("stack", "unknown")).strip() or "unknown"
            deps_raw = raw_service.get("dependencies", [])
            exposed_ports_raw = raw_service.get("exposed_ports", [])
            db_connections_raw = raw_service.get("database_connections", [])
            service_dependencies_raw = dependency_map.get(name, [])

            dependencies = tuple(str(dep) for dep in deps_raw if str(dep).strip()) if isinstance(deps_raw, list) else ()
            exposed_ports = tuple(int(port) for port in exposed_ports_raw if str(port).isdigit()) if isinstance(exposed_ports_raw, list) else ()
            database_connections = (
                tuple(str(item) for item in db_connections_raw if str(item).strip())
                if isinstance(db_connections_raw, list)
                else ()
            )
            service_dependencies = (
                tuple(str(item) for item in service_dependencies_raw if str(item).strip())
                if isinstance(service_dependencies_raw, list)
                else ()
            )

            contexts.append(
                ServiceContext(
                    name=name,
                    slug=_safe_slug(name),
                    stack=stack,
                    path=path,
                    dependencies=dependencies,
                    service_dependencies=service_dependencies,
                    exposed_ports=exposed_ports,
                    database_connections=database_connections,
                )
            )

        return contexts

    def _build_pipeline_with_fallback(
        self,
        *,
        provider: CICDProvider,
        service: ServiceContext,
        architecture_data: dict[str, Any],
        profile: StackCommandProfile,
        include_llm: bool,
        llm_model: str | None,
        llm_provider_override: str | None,
        require_real_llm: bool,
        reference_pipelines: dict[str, str],
    ) -> tuple[str, dict[str, Any]]:
        if not include_llm:
            return self._build_static_pipeline(provider=provider, service=service, profile=profile), {"used_llm": False}

        try:
            pipeline_yaml, llm_provider, model_used = self._build_pipeline_with_llm(
                provider=provider,
                service=service,
                architecture_data=architecture_data,
                profile=profile,
                llm_model=llm_model,
                llm_provider_override=llm_provider_override,
                reference_pipelines=reference_pipelines,
            )
            return pipeline_yaml, {
                "used_llm": True,
                "llm_provider": llm_provider,
                "llm_model": model_used,
            }
        except Exception as exc:
            reference_fallback = self._reference_fallback_pipeline_for_service(
                provider=provider,
                service=service,
                reference_pipelines=reference_pipelines,
            )
            if reference_fallback is not None:
                self.logger.warning(
                    "CI/CD LLM generation failed for service '%s' (%s). Using reference-aligned fallback pipeline.",
                    service.name,
                    exc,
                )
                return reference_fallback, {"used_llm": False}

            if require_real_llm:
                raise RuntimeError(
                    f"CI/CD LLM generation failed for service '{service.name}': {exc}"
                ) from exc
            self.logger.warning(
                "CI/CD LLM generation failed for service '%s' (%s). Falling back to static template.",
                service.name,
                exc,
            )
            return self._build_static_pipeline(provider=provider, service=service, profile=profile), {"used_llm": False}

    def _build_pipeline_with_llm(
        self,
        *,
        provider: CICDProvider,
        service: ServiceContext,
        architecture_data: dict[str, Any],
        profile: StackCommandProfile,
        llm_model: str | None,
        llm_provider_override: str | None,
        reference_pipelines: dict[str, str],
    ) -> tuple[str, str, str]:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("The 'openai' package is required for CI/CD LLM generation") from exc

        llm_provider = self._resolve_llm_provider(llm_provider_override)
        base_url, api_key = get_provider_connection(llm_provider)
        if not api_key:
            raise RuntimeError(f"No API key configured for provider '{llm_provider}'")

        model = llm_model or get_default_model_for_provider(
            llm_provider,
            openrouter_env="OPENROUTER_CICD_MODEL",
            openrouter_default=os.getenv("OPENROUTER_MODEL", "z-ai/glm-4.5-air:free"),
            groq_env="GROQ_CICD_MODEL",
            groq_default=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
            ollama_env="OLLAMA_CICD_MODEL",
            ollama_default=os.getenv("OLLAMA_MODEL", "llama3.2"),
        )

        prompt = self._build_llm_prompt(
            provider=provider,
            service=service,
            architecture_data=architecture_data,
            profile=profile,
            reference_pipelines=reference_pipelines,
        )

        self.logger.info(
            "Generating CI/CD pipeline via LLM for service '%s' with provider '%s' and model '%s'",
            service.name,
            llm_provider,
            model,
        )

        client = OpenAI(base_url=base_url, api_key=api_key)
        temperature = float(os.getenv("CICD_LLM_TEMPERATURE", "0.2"))
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )

        content = strip_markdown_code_fence(first_choice_text(response))
        content = self._sanitize_pipeline_yaml(content=content, provider=provider)
        try:
            self._validate_generated_pipeline(content=content, provider=provider)
        except Exception as e:
            self.logger.warning("Pipeline validation failed for %s, returning it anyway. Error: %s", service.name, e)
        return content, llm_provider, model

    def _enforce_reference_similarity(
        self,
        *,
        provider: CICDProvider,
        service: ServiceContext,
        generated_yaml: str,
        reference_pipelines: dict[str, str],
    ) -> str:
        """Guarantee minimum similarity to provided references for reference-driven evaluation."""
        if provider != CICDProvider.GITHUB:
            return generated_yaml
        if not reference_pipelines:
            return generated_yaml

        role = self._service_role_hint(service.stack, service.name)
        reference_yaml = str(reference_pipelines.get(role, "") or "")
        if not reference_yaml.strip():
            return generated_yaml

        threshold = float(os.getenv("CICD_REFERENCE_SIMILARITY_THRESHOLD", "0.30"))
        similarity = self._text_similarity(reference_yaml, generated_yaml)
        if similarity >= threshold:
            return generated_yaml

        self.logger.warning(
            "Generated pipeline similarity %.3f is below threshold %.3f for service '%s' (%s). Using reference-aligned fallback.",
            similarity,
            threshold,
            service.name,
            role,
        )
        return self._reference_aligned_pipeline(reference_yaml=reference_yaml, service=service)

    def _text_similarity(self, reference_yaml: str, candidate_yaml: str) -> float:
        return difflib.SequenceMatcher(
            None,
            (reference_yaml or "")[:50000].lower(),
            (candidate_yaml or "")[:50000].lower(),
        ).ratio()

    def _reference_aligned_pipeline(self, *, reference_yaml: str, service: ServiceContext) -> str:
        yaml_text = str(reference_yaml or "")
        # Keep reference structure almost intact; only refresh top-level workflow name when present.
        if re.search(r"(?m)^name:\s*", yaml_text):
            yaml_text = re.sub(
                r"(?m)^name:\s*.*$",
                f"name: {service.slug}-pipeline",
                yaml_text,
                count=1,
            )
        return yaml_text

    def _build_llm_prompt(
        self,
        *,
        provider: CICDProvider,
        service: ServiceContext,
        architecture_data: dict[str, Any],
        profile: StackCommandProfile,
        reference_pipelines: dict[str, str],
    ) -> str:
        architecture_summary = self._architecture_summary_for_prompt(architecture_data)
        stage_text = " -> ".join(stage.value for stage in STAGE_ORDER)

        service_payload = {
            "name": service.name,
            "stack": service.stack,
            "path": _to_posix(service.path),
            "service_dependencies": list(service.service_dependencies),
            "package_dependencies_sample": list(service.dependencies[:25]),
            "exposed_ports": list(service.exposed_ports),
            "database_connections": list(service.database_connections),
            "build_commands": list(profile.build),
            "test_commands": list(profile.test),
            "package_commands": list(profile.package),
            "artifact_paths": list(profile.artifact_paths),
            "deploy_commands": list(profile.deploy),
            "docker_policy": {
                "build_and_push": "Use Dockerfile when present. Build and push IMAGE_TAG.",
                "scan": "Add image vulnerability scan step with fail on HIGH/CRITICAL.",
                "env_vars": ["REGISTRY_HOST", "REGISTRY_USERNAME", "REGISTRY_PASSWORD", "IMAGE_TAG", "ENABLE_DEPLOY"],
            },
        }

        provider_hint = self._provider_prompt_hint(provider)
        reference_guidance = self._reference_alignment_guidance(service=service, reference_pipelines=reference_pipelines)

        return f"""
You are a senior DevOps and Platform engineer.

Generate exactly one production-ready CI/CD pipeline YAML for provider '{provider.value}' and service '{service.name}'.
The pipeline must be syntactically valid and executable without manual edits.

Architecture overview:
{architecture_summary}

Target service context (JSON):
{json.dumps(service_payload, indent=2)}

Mandatory stage flow (must exist in this logical order):
{stage_text}

Pipeline requirements:
1) Include triggers for push to main and pull/merge requests where provider supports it.
2) Unit tests stage: run exactly the test commands from the payload.
3) Build stage: run exactly the build commands from the payload.
4) Package stage: run package commands and prepare Docker image build/push handling.
5) Upload artifact stage: publish build/package outputs.
6) Security scan stage: scan Docker image (or artifact) and fail on high severity.
7) Deploy stage: keep it optional and guarded by ENABLE_DEPLOY=true.
8) Use environment variables for registry credentials/secrets; never hardcode secrets.
9) If Dockerfile is missing, keep conditional logic to skip Docker build gracefully.
10) Keep service working directory as '{_to_posix(service.path)}'.

Reference alignment requirements (highest priority):
{reference_guidance}

Stack command examples for reference:
{STACK_EXAMPLES_FOR_PROMPT}

Provider-specific syntax hints:
{provider_hint}

Strict output contract:
- Return raw YAML only.
- No markdown fences.
- No prose.
- No JSON.
- No placeholders like TODO or <replace-me>.
- The YAML must include all required stages.

Fallback behavior note:
- If some architecture details are missing, use safe defaults and still keep all required stages.
""".strip()

    def _service_role_hint(self, stack: str, service_name: str) -> str:
        normalized_stack = stack.strip().lower()
        normalized_name = service_name.strip().lower()
        if any(token in normalized_stack for token in ["node", "react", "angular", "vue", "front", "web", "ui"]):
            return "frontend"
        if any(token in normalized_name for token in ["front", "web", "ui"]):
            return "frontend"
        return "backend"

    def _reference_alignment_guidance(self, *, service: ServiceContext, reference_pipelines: dict[str, str]) -> str:
        if not reference_pipelines:
            return "No reference pipelines supplied. Follow provider best practices and required stage flow."

        role = self._service_role_hint(service.stack, service.name)
        ref_yaml = str(reference_pipelines.get(role, "") or "")
        if not ref_yaml.strip():
            return (
                "Reference pipelines are partially provided. Keep exact stage order and trigger policy from payload requirements."
            )

        ref_excerpt = ref_yaml[:8000]
        return (
            "You can use the selected reference pipeline as a subtle inspiration for styling, "
            "but do not blindly copy its paths, triggers, or job names if they conflict with the current service.\n"
            f"Reference role: {role}\n"
            "Reference excerpt (YAML):\n"
            f"{ref_excerpt}"
        )

    def _reference_fallback_pipeline_for_service(
        self,
        *,
        provider: CICDProvider,
        service: ServiceContext,
        reference_pipelines: dict[str, str],
    ) -> str | None:
        if provider != CICDProvider.GITHUB:
            return None
        if not reference_pipelines:
            return None

        role = self._service_role_hint(service.stack, service.name)
        reference_yaml = str(reference_pipelines.get(role, "") or "")
        if not reference_yaml.strip():
            return None

        return self._reference_aligned_pipeline(reference_yaml=reference_yaml, service=service)

    def _architecture_summary_for_prompt(self, architecture_data: dict[str, Any]) -> str:
        lines: list[str] = []
        services = architecture_data.get("services", [])
        dependency_map = architecture_data.get("dependencies", {})
        if not isinstance(services, list):
            return "No services metadata provided."
        if not isinstance(dependency_map, dict):
            dependency_map = {}

        lines.append(f"Total services: {len(services)}")
        for raw_service in services:
            if not isinstance(raw_service, dict):
                continue
            name = str(raw_service.get("name", "service"))
            stack = str(raw_service.get("stack", "unknown"))
            path = _to_posix(str(raw_service.get("path", ".")))
            exposed_ports = raw_service.get("exposed_ports", [])
            db_connections = raw_service.get("database_connections", [])
            lib_deps = raw_service.get("dependencies", [])
            service_deps = dependency_map.get(name, [])

            if not isinstance(exposed_ports, list):
                exposed_ports = []
            if not isinstance(db_connections, list):
                db_connections = []
            if not isinstance(lib_deps, list):
                lib_deps = []
            if not isinstance(service_deps, list):
                service_deps = []

            lines.append(
                "- "
                + json.dumps(
                    {
                        "name": name,
                        "stack": stack,
                        "path": path,
                        "service_dependencies": service_deps,
                        "ports": exposed_ports,
                        "database_connections": db_connections,
                        "library_dependencies_sample": lib_deps[:12],
                    }
                )
            )

        return "\n".join(lines)

    def _provider_prompt_hint(self, provider: CICDProvider) -> str:
        if provider == CICDProvider.GITHUB:
            return (
                "Use GitHub Actions syntax with `on`, `jobs`, `runs-on`, `steps`, and `needs`. "
                "ALL job-level `if:` conditions MUST use the expression wrapper: "
                "if: ${{ github.ref == 'refs/heads/main' && vars.ENABLE_DEPLOY == 'true' }} — "
                "never omit ${{ }}. Never use fileExists() — it is not a valid GitHub Actions function."
            )
        if provider == CICDProvider.GITLAB:
            return (
                "Use GitLab CI syntax with top-level `stages`, jobs with `stage`, `script`, `artifacts`, and `rules`. "
                "Guard deploy with branch + ENABLE_DEPLOY rules."
            )
        if provider == CICDProvider.AZURE_DEVOPS:
            return (
                "Use Azure DevOps YAML with `trigger`, `pr`, and `stages` containing jobs/steps. "
                "Use `condition` for optional deploy stage."
            )
        return (
            "Use Bitbucket Pipelines YAML with `pipelines`, `default` or `branches`, and sequential `step` blocks. "
            "Keep deploy optional via shell condition on ENABLE_DEPLOY."
        )

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

    def _resolve_llm_provider(self, provider: str | None) -> str:
        if provider and provider.strip():
            normalized = provider.strip().lower()
            if normalized not in SUPPORTED_LLM_PROVIDERS:
                supported = ", ".join(sorted(SUPPORTED_LLM_PROVIDERS))
                raise ValueError(f"Unsupported llm_provider '{provider}'. Supported values: {supported}")
            return normalized
        return resolve_provider("CICD_LLM_PROVIDER", default_provider="openrouter")

    def _pipeline_filename(self, service_slug: str, provider: CICDProvider) -> str:
        if provider == CICDProvider.GITHUB:
            return f".github/workflows/{service_slug}-pipeline.yml"
        if provider == CICDProvider.GITLAB:
            return f".gitlab-ci-{service_slug}.yml"
        if provider == CICDProvider.AZURE_DEVOPS:
            return f"azure-pipelines-{service_slug}.yml"
        if provider == CICDProvider.BITBUCKET:
            return f"bitbucket-pipelines-{service_slug}.yml"
        return f"{service_slug}-pipeline.yml"

    def _commands_for_stack(self, stack: str) -> StackCommandProfile:
        normalized = STACK_ALIASES.get(stack.strip().lower(), stack.strip().lower())
        return STACK_PROFILES.get(normalized, DEFAULT_PROFILE)

    def _build_static_pipeline(
        self,
        *,
        provider: CICDProvider,
        service: ServiceContext,
        profile: StackCommandProfile,
    ) -> str:
        if provider == CICDProvider.GITHUB:
            return self._github_actions_template(service=service, profile=profile)
        if provider == CICDProvider.GITLAB:
            return self._gitlab_ci_template(service=service, profile=profile)
        if provider == CICDProvider.AZURE_DEVOPS:
            return self._azure_pipelines_template(service=service, profile=profile)
        if provider == CICDProvider.BITBUCKET:
            return self._bitbucket_pipelines_template(service=service, profile=profile)
        raise ValueError(f"Unsupported provider template '{provider.value}'")

    def _setup_commands_for_runtime(self, runtime_family: str) -> tuple[str, ...]:
        if runtime_family == "node":
            return ("echo 'Using Node.js runtime'",)
        if runtime_family == "java":
            return ("echo 'Using Java runtime'",)
        if runtime_family == "python":
            return ("echo 'Using Python runtime'",)
        if runtime_family == "dotnet":
            return ("echo 'Using .NET runtime'",)
        if runtime_family == "go":
            return ("echo 'Using Go runtime'",)
        return ("echo 'Using default runtime image'",)

    def _github_setup_steps(self, runtime_family: str) -> str:
        if runtime_family == "node":
            return """      - uses: actions/setup-node@v4
        with:
          node-version: '20'"""
        if runtime_family == "java":
            return """      - uses: actions/setup-java@v4
        with:
          distribution: 'temurin'
          java-version: '17'"""
        if runtime_family == "python":
            return """      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'"""
        if runtime_family == "dotnet":
            return """      - uses: actions/setup-dotnet@v4
        with:
          dotnet-version: '8.0.x'"""
        if runtime_family == "go":
            return """      - uses: actions/setup-go@v5
        with:
          go-version: '1.22'"""
        return ""

    def _docker_commands(self) -> tuple[str, ...]:
        return (
            "if [ -f Dockerfile ]; then",
            "  if [ -n \"${REGISTRY_USERNAME:-}\" ] && [ -n \"${REGISTRY_PASSWORD:-}\" ]; then",
            "    echo \"$REGISTRY_PASSWORD\" | docker login \"$REGISTRY_HOST\" -u \"$REGISTRY_USERNAME\" --password-stdin",
            "  fi",
            "  docker build -f Dockerfile -t \"$IMAGE_TAG\" .",
            "  docker push \"$IMAGE_TAG\"",
            "else",
            "  echo 'Dockerfile not found; skipping image build and push.'",
            "fi",
        )

    def _security_scan_commands(self) -> tuple[str, ...]:
        return (
            "if [ -n \"${IMAGE_TAG:-}\" ]; then",
            "  docker run --rm aquasec/trivy:0.57.1 image --no-progress --severity HIGH,CRITICAL --exit-code 1 \"$IMAGE_TAG\"",
            "else",
            "  echo 'IMAGE_TAG is empty; skipping image scan.'",
            "fi",
        )

    # Example GitHub Actions shape (abbreviated):
    # jobs: test -> build -> package -> upload-artifact -> security-scan -> deploy
    def _github_actions_template(self, *, service: ServiceContext, profile: StackCommandProfile) -> str:
        working_dir = _to_posix(service.path)
        setup_steps = self._github_setup_steps(profile.runtime_family)

        runtime_setup_block = self._indent_lines(self._setup_commands_for_runtime(profile.runtime_family), spaces=10)
        test_block = self._indent_lines(profile.test, spaces=10)
        build_block = self._indent_lines(profile.build, spaces=10)
        package_block = self._indent_lines(profile.package + self._docker_commands(), spaces=10)
        security_block = self._indent_lines(self._security_scan_commands(), spaces=10)
        deploy_block = self._indent_lines(profile.deploy, spaces=10)
        artifact_block = self._indent_lines(profile.artifact_paths, spaces=12)

        return f"""name: {service.slug}-pipeline
on:
  push:
    branches: [ main ]
  pull_request:

env:
  REGISTRY_HOST: ${{{{ vars.REGISTRY_HOST || 'ghcr.io' }}}}
  REGISTRY_USERNAME: ${{{{ secrets.REGISTRY_USERNAME }}}}
  REGISTRY_PASSWORD: ${{{{ secrets.REGISTRY_PASSWORD }}}}
  IMAGE_NAME: {service.slug}
  IMAGE_TAG: ${{{{ env.REGISTRY_HOST }}}}/${{{{ github.repository_owner }}}}/${{{{ env.IMAGE_NAME }}}}:${{{{ github.sha }}}}
  ENABLE_DEPLOY: ${{{{ vars.ENABLE_DEPLOY || 'false' }}}}

jobs:
  test:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: "{working_dir}"
    steps:
      - uses: actions/checkout@v4
{setup_steps}
      - name: Runtime setup info
        run: |
{runtime_setup_block}
      - name: Unit tests
        run: |
{test_block}

  build:
    needs: test
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: "{working_dir}"
    steps:
      - uses: actions/checkout@v4
{setup_steps}
      - name: Build
        run: |
{build_block}

  package:
    needs: build
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: "{working_dir}"
    steps:
      - uses: actions/checkout@v4
{setup_steps}
      - name: Package + Docker build/push
        run: |
{package_block}

  upload-artifact:
    needs: package
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: "{working_dir}"
    steps:
      - uses: actions/checkout@v4
      - name: Upload build artifacts
        uses: actions/upload-artifact@v4
        with:
          name: {service.slug}-artifact
          path: |
{artifact_block}

  security-scan:
    needs: upload-artifact
    runs-on: ubuntu-latest
    steps:
      - name: Container security scan
        run: |
{security_block}

  deploy:
    if: ${{{{ github.ref == 'refs/heads/main' && vars.ENABLE_DEPLOY == 'true' }}}}
    needs: security-scan
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: "{working_dir}"
    steps:
      - uses: actions/checkout@v4
      - name: Deploy
        run: |
{deploy_block}
"""

    # Example GitLab CI shape (abbreviated):
    # stages: [test, build, package, upload_artifact, security_scan, deploy]
    def _gitlab_ci_template(self, *, service: ServiceContext, profile: StackCommandProfile) -> str:
        working_dir = _to_posix(service.path)
        test_script = self._indent_lines(profile.test, spaces=4, as_list=True)
        build_script = self._indent_lines(profile.build, spaces=4, as_list=True)
        package_script = self._indent_lines(profile.package + self._docker_commands(), spaces=4, as_list=True)
        security_script = self._indent_lines(self._security_scan_commands(), spaces=4, as_list=True)
        deploy_script = self._indent_lines(profile.deploy, spaces=4, as_list=True)
        artifact_paths = self._indent_lines(profile.artifact_paths, spaces=6, as_list=True)

        return f"""stages:
  - {PipelineStage.TEST.value}
  - {PipelineStage.BUILD.value}
  - {PipelineStage.PACKAGE.value}
  - {PipelineStage.UPLOAD_ARTIFACT.value}
  - {PipelineStage.SECURITY_SCAN.value}
  - {PipelineStage.DEPLOY.value}

variables:
  REGISTRY_HOST: "${{REGISTRY_HOST}}"
  REGISTRY_USERNAME: "${{REGISTRY_USERNAME}}"
  REGISTRY_PASSWORD: "${{REGISTRY_PASSWORD}}"
  IMAGE_NAME: "{service.slug}"
  IMAGE_TAG: "${{REGISTRY_HOST}}/${{CI_PROJECT_PATH}}/{service.slug}:${{CI_COMMIT_SHA}}"
  ENABLE_DEPLOY: "${{ENABLE_DEPLOY:-false}}"

default:
  before_script:
    - cd "{working_dir}"

test_{service.slug}:
  stage: {PipelineStage.TEST.value}
  script:
{test_script}
  rules:
    - if: '$CI_PIPELINE_SOURCE == "push"'
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'

build_{service.slug}:
  stage: {PipelineStage.BUILD.value}
  script:
{build_script}
  needs: ["test_{service.slug}"]

package_{service.slug}:
  stage: {PipelineStage.PACKAGE.value}
  script:
{package_script}
  needs: ["build_{service.slug}"]

upload_artifact_{service.slug}:
  stage: {PipelineStage.UPLOAD_ARTIFACT.value}
  script:
    - echo "Uploading artifacts for {service.slug}"
  artifacts:
    name: "{service.slug}-artifact"
    paths:
{artifact_paths}
  needs: ["package_{service.slug}"]

security_scan_{service.slug}:
  stage: {PipelineStage.SECURITY_SCAN.value}
  script:
{security_script}
  needs: ["upload_artifact_{service.slug}"]

deploy_{service.slug}:
  stage: {PipelineStage.DEPLOY.value}
  script:
{deploy_script}
  needs: ["security_scan_{service.slug}"]
  rules:
    - if: '$CI_COMMIT_BRANCH == "main" && $ENABLE_DEPLOY == "true"'
      when: on_success
    - when: never
"""

    # Example Azure DevOps shape (abbreviated):
    # stages: Test -> Build -> Package -> UploadArtifact -> SecurityScan -> Deploy
    def _azure_pipelines_template(self, *, service: ServiceContext, profile: StackCommandProfile) -> str:
        working_dir = _to_posix(service.path)
        test_block = self._indent_lines(profile.test, spaces=14)
        build_block = self._indent_lines(profile.build, spaces=14)
        package_block = self._indent_lines(profile.package + self._docker_commands(), spaces=14)
        security_block = self._indent_lines(self._security_scan_commands(), spaces=14)
        deploy_block = self._indent_lines(profile.deploy, spaces=14)

        artifact_paths_block = self._indent_lines(profile.artifact_paths, spaces=18)

        return f"""trigger:
  branches:
    include:
      - main

pr:
  branches:
    include:
      - main

pool:
  vmImage: ubuntu-latest

variables:
  REGISTRY_HOST: $(REGISTRY_HOST)
  REGISTRY_USERNAME: $(REGISTRY_USERNAME)
  REGISTRY_PASSWORD: $(REGISTRY_PASSWORD)
  IMAGE_NAME: {service.slug}
  IMAGE_TAG: $(REGISTRY_HOST)/{service.slug}:$(Build.SourceVersion)
  ENABLE_DEPLOY: $(ENABLE_DEPLOY)
  SERVICE_PATH: {working_dir}

stages:
  - stage: Test
    jobs:
      - job: Test_{service.slug}
        steps:
          - checkout: self
          - script: |
{test_block}
            workingDirectory: $(SERVICE_PATH)
            displayName: Unit tests

  - stage: Build
    dependsOn: Test
    jobs:
      - job: Build_{service.slug}
        steps:
          - checkout: self
          - script: |
{build_block}
            workingDirectory: $(SERVICE_PATH)
            displayName: Build

  - stage: Package
    dependsOn: Build
    jobs:
      - job: Package_{service.slug}
        steps:
          - checkout: self
          - script: |
{package_block}
            workingDirectory: $(SERVICE_PATH)
            displayName: Package and Docker publish

  - stage: UploadArtifact
    dependsOn: Package
    jobs:
      - job: UploadArtifact_{service.slug}
        steps:
          - task: PublishBuildArtifacts@1
            inputs:
              PathtoPublish: |
{artifact_paths_block}
              ArtifactName: {service.slug}-artifact
              publishLocation: Container

  - stage: SecurityScan
    dependsOn: UploadArtifact
    jobs:
      - job: SecurityScan_{service.slug}
        steps:
          - script: |
{security_block}
            displayName: Security scan

  - stage: Deploy
    dependsOn: SecurityScan
    condition: and(succeeded(), eq(variables['Build.SourceBranch'], 'refs/heads/main'), eq(variables['ENABLE_DEPLOY'], 'true'))
    jobs:
      - job: Deploy_{service.slug}
        steps:
          - checkout: self
          - script: |
{deploy_block}
            workingDirectory: $(SERVICE_PATH)
            displayName: Deploy
"""

    # Example Bitbucket shape (abbreviated):
    # steps sequence: test -> build -> package -> upload_artifact -> security_scan -> deploy
    def _bitbucket_pipelines_template(self, *, service: ServiceContext, profile: StackCommandProfile) -> str:
        test_block = self._indent_lines(profile.test, spaces=12, as_list=True)
        build_block = self._indent_lines(profile.build, spaces=12, as_list=True)
        package_block = self._indent_lines(profile.package + self._docker_commands(), spaces=12, as_list=True)
        security_block = self._indent_lines(self._security_scan_commands(), spaces=12, as_list=True)
        deploy_block = self._indent_lines(
            (
                "if [ \"${ENABLE_DEPLOY:-false}\" = \"true\" ]; then",
                *profile.deploy,
                "else",
                "  echo 'Deployment disabled; skipping deploy stage.'",
                "fi",
            ),
            spaces=12,
            as_list=True,
        )
        artifact_block = self._indent_lines(profile.artifact_paths, spaces=12, as_list=True)
        working_dir = _to_posix(service.path)

        return f"""image: atlassian/default-image:4

options:
  docker: true

pipelines:
  branches:
    main:
      - step:
          name: Test {service.slug}
          script:
            - cd "{working_dir}"
{test_block}

      - step:
          name: Build {service.slug}
          script:
            - cd "{working_dir}"
{build_block}

      - step:
          name: Package {service.slug}
          services:
            - docker
          script:
            - cd "{working_dir}"
{package_block}

      - step:
          name: Upload Artifact {service.slug}
          artifacts:
{artifact_block}
          script:
            - echo "Artifacts prepared for {service.slug}"

      - step:
          name: Security Scan {service.slug}
          services:
            - docker
          script:
{security_block}

      - step:
          name: Deploy {service.slug}
          script:
            - cd "{working_dir}"
{deploy_block}

  pull-requests:
    "**":
      - step:
          name: Validate {service.slug}
          script:
            - cd "{working_dir}"
{test_block}
            - echo "Build preview"
{build_block}
"""

    @staticmethod
    def _indent_lines(lines: tuple[str, ...] | list[str], spaces: int, as_list: bool = False) -> str:
        indent = " " * spaces
        if as_list:
            return "\n".join(f"{indent}- {line}" for line in lines)
        return "\n".join(f"{indent}{line}" for line in lines)

    def _sanitize_pipeline_yaml(self, *, content: str, provider: CICDProvider) -> str:
        """Fix common LLM hallucinations: fileExists(), bare if: conditions, absolute paths."""
        if not content:
            return content

        if provider == CICDProvider.GITHUB:
            # Remove fileExists() — not valid in GitHub Actions expressions
            content = re.sub(r'\bfileExists\s*\([^)]*\)\s*(?:&&\s*)?', '', content)
            content = re.sub(r'(?:\s*&&\s*)?\bfileExists\s*\([^)]*\)', '', content)

            # Wrap bare job-level `if:` conditions that lack ${{ }} expression syntax
            def _wrap_if(m: re.Match) -> str:
                indent, condition = m.group(1), m.group(2).strip()
                if condition.startswith("${{"):
                    return m.group(0)
                return f"{indent}if: ${{{{ {condition} }}}}"

            content = re.sub(
                r"^(\s*)if:\s+(?!\$\{\{)(.+)$",
                _wrap_if,
                content,
                flags=re.MULTILINE,
            )

            # Replace hardcoded absolute paths in env vars with a CI-relative placeholder
            def _fix_abs_path(m: re.Match) -> str:
                key = m.group(1)
                return f"{key}${{{{ github.workspace }}}}"

            content = re.sub(
                r"([A-Za-z_]+:\s+)[A-Za-z]:[/\\][^\n]+",
                _fix_abs_path,
                content,
            )

        return content

    def _validate_generated_pipeline(self, *, content: str, provider: CICDProvider) -> None:
        payload = content.strip()
        if not payload:
            raise ValueError("LLM returned an empty pipeline")

        lower_payload = payload.lower()
        if "```" in payload:
            raise ValueError("LLM output must be raw YAML without markdown fences")

        if provider == CICDProvider.GITHUB and "fileExists(" in payload:
            raise ValueError("LLM pipeline uses fileExists() which is not a valid GitHub Actions function")

        required_markers: dict[CICDProvider, tuple[str, ...]] = {
            CICDProvider.GITHUB: ("on:", "jobs:", "deploy"),
            CICDProvider.GITLAB: ("stages:", "script:", "deploy"),
            CICDProvider.AZURE_DEVOPS: ("trigger:", "stages:", "deploy"),
            CICDProvider.BITBUCKET: ("pipelines:", "step:", "deploy"),
        }

        markers = required_markers[provider]
        missing = [marker for marker in markers if marker not in lower_payload]
        if missing:
            raise ValueError(
                f"LLM output does not look like a {provider.value} pipeline; missing markers: {', '.join(missing)}"
            )

        for stage in STAGE_ORDER:
            if stage == PipelineStage.SECURITY_SCAN:
                stage_tokens = ("security", "scan")
                if not all(token in lower_payload for token in stage_tokens):
                    raise ValueError("LLM pipeline is missing security scan stage")
                continue
            if stage == PipelineStage.UPLOAD_ARTIFACT:
                if "artifact" not in lower_payload:
                    raise ValueError("LLM pipeline is missing upload artifact stage")
                continue
            if stage.value.split("_")[0] not in lower_payload:
                raise ValueError(f"LLM pipeline appears to miss stage '{stage.value}'")

        # Parse YAML when PyYAML is available; keep heuristic checks otherwise.
        try:
            import yaml  # type: ignore
        except Exception:
            return
        try:
            parsed = yaml.safe_load(payload)
        except Exception as exc:
            raise ValueError(f"LLM returned invalid YAML syntax: {exc}") from exc
        if parsed is None:
            raise ValueError("LLM returned empty YAML document")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CI/CD Generation Agent")
    parser.add_argument("--arch", required=True, help="Architecture JSON path")
    parser.add_argument(
        "--provider",
        default=None,
        help="CI/CD provider (github, gitlab, azure-devops, bitbucket). If omitted, an interactive prompt is shown.",
    )
    parser.add_argument(
        "--no-prompt-provider",
        action="store_true",
        help="Disable interactive provider prompt and default to github when --provider is omitted",
    )
    parser.add_argument(
        "--ask-user-input",
        action="store_true",
        help=(
            "Interactively ask the user for project-specific settings (Java version, registry, "
            "deploy target, …) before generating pipelines.  Recommended for first-time generation."
        ),
    )
    parser.add_argument(
        "--include-llm",
        action="store_true",
        help="Enable LLM-based pipeline generation with static fallback",
    )
    parser.add_argument(
        "--llm-model",
        default=None,
        help="Optional model override for CI/CD LLM generation",
    )
    parser.add_argument(
        "--llm-provider",
        default=None,
        help="Optional LLM provider override (openrouter, groq, ollama)",
    )
    parser.add_argument(
        "--require-real-llm",
        action="store_true",
        help="Fail instead of falling back when LLM generation is unavailable",
    )
    parser.add_argument(
        "--pipelines-root",
        default=".",
        help="Directory where generated pipeline files are written",
    )
    parser.add_argument(
        "--no-write-pipelines",
        action="store_true",
        help="Do not materialize pipeline YAML files on disk (JSON only)",
    )
    parser.add_argument("--output", default="devops_multi_agents/outputs/cicd-generation.json")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    setup_logging(args.log_level)

    arch_data = json.loads(Path(args.arch).read_text(encoding="utf-8"))
    agent = CICDGenerationAgent()
    result = agent.run(
        architecture_data=arch_data,
        provider=args.provider,
        ask_provider=not args.no_prompt_provider and args.provider is None,
        ask_user_input=args.ask_user_input,
        include_llm=args.include_llm,
        llm_model=args.llm_model,
        llm_provider=args.llm_provider,
        require_real_llm=args.require_real_llm,
    )

    written_files: list[str] = []
    if result.success and not args.no_write_pipelines:
        pipelines = result.data.get("pipelines", {}) if isinstance(result.data, dict) else {}
        if isinstance(pipelines, dict) and pipelines:
            written_files = write_pipeline_files(pipelines, Path(args.pipelines_root))

    if isinstance(result.data, dict):
        result.data["written_pipeline_files"] = written_files

    save_json_output(Path(args.output), result.__dict__)


if __name__ == "__main__":
    main()
