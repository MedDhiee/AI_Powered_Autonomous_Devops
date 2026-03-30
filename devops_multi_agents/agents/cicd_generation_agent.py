from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from shared import BaseAgent, ProtocolType, save_json_output, setup_logging


class CICDGenerationAgent(BaseAgent):
    """ACP agent: generates CI/CD pipeline templates from architecture metadata."""

    SUPPORTED_PROVIDERS: dict[str, str] = {
        "github": "github",
        "gh": "github",
        "gitlab": "gitlab",
        "gl": "gitlab",
        "azure": "azure-devops",
        "azure-devops": "azure-devops",
        "ado": "azure-devops",
        "bitbucket": "bitbucket",
        "bb": "bitbucket",
    }

    def __init__(self) -> None:
        super().__init__(agent_name="cicd_generation_agent", protocol=ProtocolType.ACP.value)

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        architecture_data: dict[str, Any] = kwargs["architecture_data"]
        requested_provider = kwargs.get("provider")
        ask_provider = bool(kwargs.get("ask_provider", False))
        provider = self._resolve_provider(requested_provider, ask_provider=ask_provider)

        pipelines: dict[str, str] = {}
        for service in architecture_data.get("services", []):
            stack = str(service.get("stack", "unknown"))
            service_name = str(service.get("name", "service"))
            pipeline_name = self._pipeline_filename(service_name, provider)
            pipelines[pipeline_name] = self._build_pipeline(service_name, stack, provider)

        return {
            "protocol": self.protocol,
            "provider": provider,
            "summary": f"Generated {len(pipelines)} pipeline template(s) for {provider}",
            "pipelines": pipelines,
        }

    def _resolve_provider(self, provider: Any, ask_provider: bool) -> str:
        if provider is None:
            if ask_provider:
                return self._prompt_provider()
            return "github"

        normalized = str(provider).strip().lower()
        if normalized in self.SUPPORTED_PROVIDERS:
            return self.SUPPORTED_PROVIDERS[normalized]

        supported = ", ".join(sorted(set(self.SUPPORTED_PROVIDERS.values())))
        raise ValueError(
            f"Unsupported provider '{provider}'. Supported providers: {supported}"
        )

    def _prompt_provider(self) -> str:
        options = sorted(set(self.SUPPORTED_PROVIDERS.values()))
        print("Select CI/CD provider to generate templates for.")
        print(f"Available providers: {', '.join(options)}")
        try:
            choice = input("Provider [github]: ").strip()
        except EOFError:
            self.logger.warning("No interactive input available, defaulting provider to github")
            return "github"

        if not choice:
            return "github"
        return self._resolve_provider(choice, ask_provider=False)

    def _pipeline_filename(self, service_name: str, provider: str) -> str:
        if provider == "github":
            return f".github/workflows/{service_name}-pipeline.yml"
        if provider == "gitlab":
            return f".gitlab-ci-{service_name}.yml"
        if provider == "azure-devops":
            return f"azure-pipelines-{service_name}.yml"
        if provider == "bitbucket":
            return f"bitbucket-pipelines-{service_name}.yml"
        return f"{service_name}-pipeline.yml"

    def _build_pipeline(self, service_name: str, stack: str, provider: str) -> str:
        build_lines = ["echo 'No build step configured'"]
        test_lines = ["echo 'No test step configured'"]
        normalized_stack = stack.strip().lower()

        if normalized_stack in {"nodejs", "node", "javascript", "typescript", "angular", "react", "vue"}:
            build_lines = ["npm ci", "npm run build"]
            test_lines = ["npm test -- --watch=false"]
        elif normalized_stack in {"java-maven", "maven", "java"}:
            build_lines = ["mvn -B clean package -DskipTests"]
            test_lines = ["mvn -B test"]
        elif normalized_stack in {"python", "fastapi", "flask", "django"}:
            build_lines = ["pip install -r requirements.txt"]
            test_lines = ["pytest -q"]
        elif normalized_stack in {"java-gradle", "gradle", "kotlin"}:
            build_lines = ["./gradlew build -x test"]
            test_lines = ["./gradlew test"]
        elif normalized_stack in {"dotnet", "csharp", "aspnet"}:
            build_lines = ["dotnet restore", "dotnet build --configuration Release --no-restore"]
            test_lines = ["dotnet test --configuration Release --no-build"]
        elif normalized_stack in {"go", "golang"}:
            build_lines = ["go build ./..."]
            test_lines = ["go test ./..."]

        if provider == "github":
            return self._github_actions_template(service_name, build_lines, test_lines)
        if provider == "gitlab":
            return self._gitlab_ci_template(service_name, build_lines, test_lines)
        if provider == "azure-devops":
            return self._azure_pipelines_template(service_name, build_lines, test_lines)
        if provider == "bitbucket":
            return self._bitbucket_pipelines_template(service_name, build_lines, test_lines)

        raise ValueError(f"Unsupported provider template '{provider}'")

    def _github_actions_template(self, service_name: str, build_lines: list[str], test_lines: list[str]) -> str:
        build_step = self._indent_lines(build_lines, spaces=10)
        test_step = self._indent_lines(test_lines, spaces=10)

        return f"""name: {service_name}-pipeline
on:
  push:
    branches: [ main ]
  pull_request:

jobs:
  build-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build
        run: |
{build_step}
      - name: Test
        run: |
{test_step}
"""

    def _gitlab_ci_template(self, service_name: str, build_lines: list[str], test_lines: list[str]) -> str:
        build_step = self._indent_lines([f"- {line}" for line in build_lines], spaces=4)
        test_step = self._indent_lines([f"- {line}" for line in test_lines], spaces=4)

        return f"""stages:
  - build
  - test

build_{service_name}:
  stage: build
  script:
{build_step}
  rules:
    - if: '$CI_PIPELINE_SOURCE == "push"'
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'

test_{service_name}:
  stage: test
  script:
{test_step}
  needs: ["build_{service_name}"]
"""

    def _azure_pipelines_template(self, service_name: str, build_lines: list[str], test_lines: list[str]) -> str:
        build_step = self._indent_lines(build_lines, spaces=14)
        test_step = self._indent_lines(test_lines, spaces=14)

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

stages:
  - stage: Build
    jobs:
      - job: Build_{service_name}
        steps:
          - script: |
{build_step}
            displayName: Build

  - stage: Test
    dependsOn: Build
    jobs:
      - job: Test_{service_name}
        steps:
          - script: |
{test_step}
            displayName: Test
"""

    def _bitbucket_pipelines_template(self, service_name: str, build_lines: list[str], test_lines: list[str]) -> str:
        build_step = self._indent_lines([f"- {line}" for line in build_lines], spaces=12)
        test_step = self._indent_lines([f"- {line}" for line in test_lines], spaces=12)

        return f"""image: atlassian/default-image:4

pipelines:
  default:
    - step:
        name: Build {service_name}
        script:
{build_step}
    - step:
        name: Test {service_name}
        script:
{test_step}

  pull-requests:
    "**":
      - step:
          name: Build and Test {service_name}
          script:
{build_step}
{test_step}
"""

    @staticmethod
    def _indent_lines(lines: list[str], spaces: int) -> str:
        indent = " " * spaces
        return "\n".join(f"{indent}{line}" for line in lines)


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
    parser.add_argument("--output", default="devops_multi_agents/outputs/cicd-generation.json")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    setup_logging(args.log_level)
    import json

    arch_data = json.loads(Path(args.arch).read_text(encoding="utf-8"))
    agent = CICDGenerationAgent()
    result = agent.run(
        architecture_data=arch_data,
        provider=args.provider,
        ask_provider=not args.no_prompt_provider and args.provider is None,
    )
    save_json_output(Path(args.output), result.__dict__)


if __name__ == "__main__":
    main()
