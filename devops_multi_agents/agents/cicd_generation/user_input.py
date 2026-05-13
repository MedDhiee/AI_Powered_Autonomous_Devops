"""
Interactive project configuration collector for CI/CD pipeline generation.

Asks the user for project-specific settings that cannot be reliably inferred
from architecture analysis alone (Java version, Maven wrapper, registry, deploy
target, …).  Falls back to safe defaults in non-interactive environments.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ProjectConfig:
    """All project-specific values that drive accurate pipeline generation."""

    # Git
    main_branch: str = "main"

    # Java / Maven
    java_version: str = "17"
    java_distribution: str = "temurin"   # temurin | corretto | microsoft | zulu
    use_mvn_wrapper: bool = True          # ./mvnw vs mvn

    # Node.js
    node_version: str = "24"

    # Python
    python_version: str = "3.12"

    # Container registry
    registry_host: str = "ghcr.io"       # ghcr.io | docker.io | <custom>
    registry_secret_username: str = "REGISTRY_USERNAME"
    registry_secret_password: str = "REGISTRY_PASSWORD"

    # Deployment
    deploy_target: str = "kubernetes"     # kubernetes | docker-compose | vm | none
    k8s_namespace: str = "default"
    k8s_deployment_name: str = ""         # empty → service slug used at render time
    k8s_context: str = ""                 # kubeconfig context (empty = current-context)

    # Extra secrets to inject as env vars
    extra_secrets: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_interactive() -> bool:
    return sys.stdin.isatty()


def _ask(
    prompt: str,
    default: str,
    choices: list[str] | None = None,
    required: bool = False,
) -> str:
    """
    Print a prompt and read one line.  Returns *default* on EOF or empty input.
    Validates against *choices* when provided.
    """
    choices_hint = f" [{'/'.join(choices)}]" if choices else ""
    full_prompt = f"  {prompt}{choices_hint} [{default}]: "

    while True:
        try:
            raw = input(full_prompt).strip()
        except EOFError:
            return default

        value = raw or default

        if choices and value.lower() not in [c.lower() for c in choices]:
            print(f"    ⚠  Invalid choice '{value}'. Valid options: {', '.join(choices)}")
            continue

        return value.lower() if choices else value


def _section(title: str) -> None:
    print(f"\n  ── {title} {'─' * max(0, 48 - len(title))}")


# ---------------------------------------------------------------------------
# Main questionnaire
# ---------------------------------------------------------------------------

def collect_project_config(
    services: list[Any],
    *,
    non_interactive: bool = False,
) -> ProjectConfig:
    """
    Ask the user for project-specific CI/CD settings.

    If *non_interactive* is True (or stdin is not a tty), returns a
    ProjectConfig with safe defaults without asking anything.

    Args:
        services: list of ServiceContext objects (used to detect stacks).
        non_interactive: skip all prompts and return defaults.
    """
    if non_interactive or not _is_interactive():
        return ProjectConfig()

    # Detect present stacks to show only relevant questions
    stacks = {str(getattr(s, "stack", "")).lower() for s in services}
    has_java = any(
        tok in " ".join(stacks)
        for tok in ("java", "maven", "gradle", "spring", "kotlin")
    )
    has_node = any(
        tok in " ".join(stacks)
        for tok in ("node", "angular", "react", "vue", "typescript", "javascript")
    )
    has_python = any(
        tok in " ".join(stacks)
        for tok in ("python", "fastapi", "flask", "django")
    )

    print()
    print("  " + "═" * 56)
    print("   CI/CD Pipeline Configuration — Project Settings")
    print("  " + "═" * 56)
    print("  Answer the questions below to generate accurate pipelines.")
    print("  Press Enter to accept the default shown in [brackets].\n")

    # ── Git ──────────────────────────────────────────────────────────────────
    _section("Git")
    main_branch = _ask("Main branch name", "main", ["main", "master", "develop"])

    # ── Container registry ───────────────────────────────────────────────────
    _section("Container Registry")
    registry_type = _ask(
        "Registry type",
        "ghcr",
        ["ghcr", "dockerhub", "ecr", "custom"],
    )
    registry_host_map = {
        "ghcr":      "ghcr.io",
        "dockerhub": "docker.io",
        "ecr":       "<aws-account-id>.dkr.ecr.<region>.amazonaws.com",
    }
    if registry_type == "custom":
        registry_host = input("  Registry URL (e.g. registry.company.com): ").strip() or "ghcr.io"
    else:
        registry_host = registry_host_map.get(registry_type, "ghcr.io")
        print(f"    → registry host: {registry_host}")

    reg_user_secret = _ask("Secret name for registry username", "REGISTRY_USERNAME")
    reg_pass_secret = _ask("Secret name for registry password/token", "REGISTRY_PASSWORD")

    # ── Runtime — Java ───────────────────────────────────────────────────────
    java_version = "17"
    java_distribution = "temurin"
    use_mvn_wrapper = True

    if has_java:
        _section("Java / Maven")
        java_version = _ask("Java version", "17", ["11", "17", "21"])
        java_distribution = _ask(
            "JDK distribution",
            "temurin",
            ["temurin", "corretto", "microsoft", "zulu"],
        )
        mvnw_ans = _ask("Use Maven Wrapper (./mvnw)?", "y", ["y", "n"])
        use_mvn_wrapper = mvnw_ans == "y"

    # ── Runtime — Node.js ────────────────────────────────────────────────────
    node_version = "24"
    if has_node:
        _section("Node.js")
        node_version = _ask("Node.js version", "24", ["20", "22", "24"])

    # ── Runtime — Python ─────────────────────────────────────────────────────
    python_version = "3.12"
    if has_python:
        _section("Python")
        python_version = _ask("Python version", "3.12", ["3.10", "3.11", "3.12", "3.13"])

    # ── Deployment ───────────────────────────────────────────────────────────
    _section("Deployment")
    deploy_target = _ask(
        "Deployment target",
        "kubernetes",
        ["kubernetes", "docker-compose", "vm", "none"],
    )

    k8s_namespace = "default"
    k8s_deployment_name = ""
    k8s_context = ""

    if deploy_target == "kubernetes":
        k8s_namespace = input("  Kubernetes namespace [default]: ").strip() or "default"
        k8s_deployment_name = input("  Deployment name (leave empty to use service name): ").strip()
        k8s_context = input("  kubeconfig context (leave empty for current-context): ").strip()

    # ── Extra secrets ────────────────────────────────────────────────────────
    _section("Extra Secrets")
    extra_raw = input(
        "  Additional secret names to expose as env vars (comma-separated, or Enter to skip): "
    ).strip()
    extra_secrets = [s.strip() for s in extra_raw.split(",") if s.strip()] if extra_raw else []

    print()
    print("  ✅  Configuration collected — generating pipelines...\n")

    return ProjectConfig(
        main_branch=main_branch,
        java_version=java_version,
        java_distribution=java_distribution,
        use_mvn_wrapper=use_mvn_wrapper,
        node_version=node_version,
        python_version=python_version,
        registry_host=registry_host,
        registry_secret_username=reg_user_secret,
        registry_secret_password=reg_pass_secret,
        deploy_target=deploy_target,
        k8s_namespace=k8s_namespace,
        k8s_deployment_name=k8s_deployment_name,
        k8s_context=k8s_context,
        extra_secrets=extra_secrets,
    )
