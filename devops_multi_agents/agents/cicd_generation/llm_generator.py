"""LLM-based CI/CD pipeline generation with prompt building and validation."""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
from typing import Any

from shared import (
    first_choice_text,
    get_default_model_for_provider,
    get_provider_connection,
    resolve_provider,
    strip_markdown_code_fence,
)

from .models import (
    CICDProvider,
    PipelineStage,
    ServiceContext,
    STAGE_ORDER,
    SUPPORTED_LLM_PROVIDERS,
    to_posix,
)
from .stack_profiles import STACK_EXAMPLES_FOR_PROMPT, StackCommandProfile

LOGGER = logging.getLogger("cicd_llm_generator")


def resolve_llm_provider(provider: str | None) -> str:
    if provider and provider.strip():
        normalized = provider.strip().lower()
        if normalized not in SUPPORTED_LLM_PROVIDERS:
            supported = ", ".join(sorted(SUPPORTED_LLM_PROVIDERS))
            raise ValueError(f"Unsupported llm_provider '{provider}'. Supported values: {supported}")
        return normalized
    return resolve_provider("CICD_LLM_PROVIDER", default_provider="openrouter")


def build_pipeline_with_llm(
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

    llm_provider = resolve_llm_provider(llm_provider_override)
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

    prompt = _build_llm_prompt(
        provider=provider,
        service=service,
        architecture_data=architecture_data,
        profile=profile,
        reference_pipelines=reference_pipelines,
    )

    LOGGER.info(
        "Generating CI/CD pipeline via LLM for service '%s' with provider '%s' and model '%s'",
        service.name, llm_provider, model,
    )

    client = OpenAI(base_url=base_url, api_key=api_key)
    temperature = float(os.getenv("CICD_LLM_TEMPERATURE", "0.2"))
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )

    content = strip_markdown_code_fence(first_choice_text(response))
    content = sanitize_pipeline_yaml(content=content, provider=provider)
    try:
        validate_generated_pipeline(content=content, provider=provider)
    except Exception as e:
        LOGGER.warning("Pipeline validation failed for %s, returning it anyway. Error: %s", service.name, e)
    return content, llm_provider, model


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def _build_llm_prompt(
    *,
    provider: CICDProvider,
    service: ServiceContext,
    architecture_data: dict[str, Any],
    profile: StackCommandProfile,
    reference_pipelines: dict[str, str],
) -> str:
    architecture_summary = _architecture_summary_for_prompt(architecture_data)
    stage_text = " -> ".join(stage.value for stage in STAGE_ORDER)

    # service.path is already relative (e.g. "gestion_stock_ui/hb_electronics")
    rel_service_path = to_posix(service.path)

    service_payload = {
        "name": service.name,
        "stack": service.stack,
        "path": rel_service_path,
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

    provider_hint = _provider_prompt_hint(provider)
    reference_guidance = _reference_alignment_guidance(service=service, reference_pipelines=reference_pipelines)

    github_workspace_note = (
        f"For GitHub Actions working-directory always use: ${{{{ github.workspace }}}}/{rel_service_path}"
        if provider == CICDProvider.GITHUB else
        f"Use relative path '{rel_service_path}' for cd commands — never absolute paths."
    )

    return f"""
You are a senior DevOps and Platform engineer.

Generate exactly one production-ready CI/CD pipeline YAML for provider '{provider.value}' and service '{service.name}'.
The pipeline must be syntactically valid and executable without manual edits on a standard Linux CI runner.

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
6) Security scan stage: use aquasecurity/trivy-action@master (NOT aquasec/trivy-action). Scan image and fail on HIGH/CRITICAL.
7) Deploy stage: keep it optional and guarded by ENABLE_DEPLOY=true.
8) Use environment variables for registry credentials/secrets; never hardcode secrets.
9) If Dockerfile is missing, keep conditional logic to skip Docker build gracefully.
10) {github_workspace_note}
11) runner must be ubuntu-latest (Linux). Never use windows-latest or macos-latest.
12) For GitHub Actions deploy job condition use: if: ${{{{ github.ref == 'refs/heads/main' && vars.ENABLE_DEPLOY == 'true' }}}}
    Do NOT use env context in job-level if conditions.

CRITICAL path rules — violating any of these makes the pipeline broken:
- NEVER use absolute local file system paths (e.g. C:/Users/..., /home/user/..., /tmp/...).
- NEVER use Windows-style paths (backslashes or drive letters like C:\\).
- NEVER reference the local machine path where this pipeline was generated.
- All paths in the YAML must be relative to the repository root or use CI-provided variables.
- For artifact upload paths, prefix with the service relative path: '{rel_service_path}/'.

CRITICAL Maven Wrapper rule — violating this guarantees an "exit code 126 Permission denied" failure:
- NEVER write './mvnw' in any run: step. The mvnw file is typically committed without the execute bit (Git on Windows drops it), so '/bin/bash: ./mvnw: Permission denied' kills the job.
- ALWAYS invoke the wrapper as 'bash mvnw' (e.g. 'bash mvnw -B test', 'bash mvnw clean package').
- Do NOT add a 'chmod +x mvnw' step — 'bash mvnw' makes it unnecessary and the chmod step itself can fail on shallow checkouts.

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


def _architecture_summary_for_prompt(architecture_data: dict[str, Any]) -> str:
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
        path = to_posix(str(raw_service.get("path", ".")))
        exposed_ports = raw_service.get("exposed_ports", [])
        db_connections = raw_service.get("database_connections", [])
        lib_deps = raw_service.get("dependencies", [])
        service_deps = dependency_map.get(name, [])

        for field in (exposed_ports, db_connections, lib_deps, service_deps):
            if not isinstance(field, list):
                field = []

        lines.append(
            "- " + json.dumps({
                "name": name, "stack": stack, "path": path,
                "service_dependencies": service_deps if isinstance(service_deps, list) else [],
                "ports": exposed_ports if isinstance(exposed_ports, list) else [],
                "database_connections": db_connections if isinstance(db_connections, list) else [],
                "library_dependencies_sample": (lib_deps if isinstance(lib_deps, list) else [])[:12],
            })
        )
    return "\n".join(lines)


def _provider_prompt_hint(provider: CICDProvider) -> str:
    hints = {
        CICDProvider.GITHUB: (
            "Use GitHub Actions syntax with `on`, `jobs`, `runs-on`, `steps`, and `needs`. "
            "ALL job-level `if:` conditions MUST use the expression wrapper: "
            "if: ${{ github.ref == 'refs/heads/main' && vars.ENABLE_DEPLOY == 'true' }} — "
            "never omit ${{ }}. Never use fileExists() — it is not a valid GitHub Actions function."
        ),
        CICDProvider.GITLAB: (
            "Use GitLab CI syntax with top-level `stages`, jobs with `stage`, `script`, `artifacts`, and `rules`. "
            "Guard deploy with branch + ENABLE_DEPLOY rules."
        ),
        CICDProvider.AZURE_DEVOPS: (
            "Use Azure DevOps YAML with `trigger`, `pr`, and `stages` containing jobs/steps. "
            "Use `condition` for optional deploy stage."
        ),
        CICDProvider.JENKINS: (
            "Use Declarative Jenkins Pipeline syntax with `pipeline`, `agent`, `stages`, `steps`. "
            "Use `when` block with `expression` for conditional deploy."
        ),
    }
    return hints.get(provider, (
        "Use Bitbucket Pipelines YAML with `pipelines`, `default` or `branches`, and sequential `step` blocks. "
        "Keep deploy optional via shell condition on ENABLE_DEPLOY."
    ))


def _reference_alignment_guidance(*, service: ServiceContext, reference_pipelines: dict[str, str]) -> str:
    if not reference_pipelines:
        return "No reference pipelines supplied. Follow provider best practices and required stage flow."

    role = service_role_hint(service.stack, service.name)
    ref_yaml = str(reference_pipelines.get(role, "") or "")
    if not ref_yaml.strip():
        return "Reference pipelines are partially provided. Keep exact stage order and trigger policy from payload requirements."

    ref_excerpt = ref_yaml[:8000]
    return (
        "You can use the selected reference pipeline as a subtle inspiration for styling, "
        "but do not blindly copy its paths, triggers, or job names if they conflict with the current service.\n"
        f"Reference role: {role}\n"
        "Reference excerpt (YAML):\n"
        f"{ref_excerpt}"
    )


# ---------------------------------------------------------------------------
# Sanitization  (runs before validation to auto-fix common LLM hallucinations)
# ---------------------------------------------------------------------------

def sanitize_pipeline_yaml(*, content: str, provider: CICDProvider) -> str:
    """Auto-fix common LLM hallucinations in generated pipeline YAML."""
    if not content:
        return content

    if provider == CICDProvider.GITHUB:
        # Remove fileExists() — not a valid GitHub Actions function.
        # Strip it from conditions like: if: fileExists('...') && ...
        content = re.sub(r'\bfileExists\s*\([^)]*\)\s*(?:&&\s*)?', '', content)
        content = re.sub(r'(?:\s*&&\s*)?\bfileExists\s*\([^)]*\)', '', content)

        # Wrap bare job-level `if:` conditions that lack ${{ }} expression syntax.
        # Example: `if: github.ref == 'refs/heads/main'` → `if: ${{ github.ref == ... }}`
        def _wrap_if(m: re.Match) -> str:
            indent = m.group(1)
            condition = m.group(2).strip()
            if condition.startswith("${{"):
                return m.group(0)
            return f"{indent}if: ${{{{ {condition} }}}}"

        content = re.sub(
            r"^(\s*)if:\s+(?!\$\{\{)(.+)$",
            _wrap_if,
            content,
            flags=re.MULTILINE,
        )

    return content


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_generated_pipeline(*, content: str, provider: CICDProvider) -> None:
    payload = content.strip()
    if not payload:
        raise ValueError("LLM returned an empty pipeline")

    if "```" in payload:
        raise ValueError("LLM output must be raw YAML without markdown fences")

    # Detect fileExists() — not a valid GitHub Actions function
    if provider == CICDProvider.GITHUB and "fileExists(" in payload:
        raise ValueError(
            "LLM pipeline uses fileExists() which is not a valid GitHub Actions function. "
            "Use conditional shell logic or a prior step output instead."
        )

    # Detect hallucinated absolute paths (Windows drive letters or Unix /tmp /home /var paths)
    abs_path_patterns = [
        r"[A-Za-z]:[/\\]",            # Windows drive: C:/ or C:\
        r"/tmp/devops_multi_agents",    # local temp clone dirs
        r"/home/\w+/",                  # Unix home dirs
        r"AppData[/\\]Local[/\\]Temp",  # Windows temp
    ]
    for pat in abs_path_patterns:
        if re.search(pat, payload):
            raise ValueError(
                f"LLM pipeline contains hardcoded absolute path matching '{pat}'. "
                "Pipeline must use only relative paths or CI-provided variables."
            )

    required_markers: dict[CICDProvider, tuple[str, ...]] = {
        CICDProvider.GITHUB: ("on:", "jobs:", "deploy"),
        CICDProvider.GITLAB: ("stages:", "script:", "deploy"),
        CICDProvider.AZURE_DEVOPS: ("trigger:", "stages:", "deploy"),
        CICDProvider.BITBUCKET: ("pipelines:", "step:", "deploy"),
        CICDProvider.JENKINS: ("pipeline", "stages", "deploy"),
    }

    lower_payload = payload.lower()
    markers = required_markers.get(provider, ("deploy",))
    missing = [marker for marker in markers if marker not in lower_payload]
    if missing:
        raise ValueError(f"LLM output does not look like a {provider.value} pipeline; missing markers: {', '.join(missing)}")

    for stage in STAGE_ORDER:
        if stage == PipelineStage.SECURITY_SCAN:
            if not all(token in lower_payload for token in ("security", "scan")):
                raise ValueError("LLM pipeline is missing security scan stage")
            continue
        if stage == PipelineStage.UPLOAD_ARTIFACT:
            if "artifact" not in lower_payload:
                raise ValueError("LLM pipeline is missing upload artifact stage")
            continue
        if stage.value.split("_")[0] not in lower_payload:
            raise ValueError(f"LLM pipeline appears to miss stage '{stage.value}'")

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


# ---------------------------------------------------------------------------
# Reference alignment helpers
# ---------------------------------------------------------------------------

def service_role_hint(stack: str, service_name: str) -> str:
    normalized_stack = stack.strip().lower()
    normalized_name = service_name.strip().lower()
    if any(token in normalized_stack for token in ["node", "react", "angular", "vue", "front", "web", "ui"]):
        return "frontend"
    if any(token in normalized_name for token in ["front", "web", "ui"]):
        return "frontend"
    return "backend"


def text_similarity(reference_yaml: str, candidate_yaml: str) -> float:
    return difflib.SequenceMatcher(
        None,
        (reference_yaml or "")[:50000].lower(),
        (candidate_yaml or "")[:50000].lower(),
    ).ratio()


def reference_aligned_pipeline(reference_yaml: str, service: ServiceContext) -> str:
    yaml_text = str(reference_yaml or "")
    if re.search(r"(?m)^name:\s*", yaml_text):
        yaml_text = re.sub(r"(?m)^name:\s*.*$", f"name: {service.slug}-pipeline", yaml_text, count=1)
    return yaml_text
