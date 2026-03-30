from __future__ import annotations

import argparse
from pathlib import Path

from shared import save_json_output, setup_logging

from devops_multi_agents.orchestrator import DevOpsOrchestrator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run full DevOps multi-agent workflow")
    parser.add_argument("--repo", required=True, help="Path to repository to analyze")
    parser.add_argument("--output-dir", default="devops_multi_agents/outputs", help="Output directory")
    parser.add_argument("--include-llm", action="store_true", help="Enable LLM in architecture analysis phase")
    parser.add_argument(
        "--llm-model",
        default=None,
        help="Override LLM model for all agents that use LLM",
    )
    parser.add_argument(
        "--strict-security-tools",
        action="store_true",
        help="Fail workflow if Trivy/Gitleaks/Checkov execution fails",
    )
    parser.add_argument(
        "--skip-image-scan",
        action="store_true",
        help="Skip Trivy image scan in security phase",
    )
    parser.add_argument(
        "--cicd-provider",
        default="github",
        help="CI/CD provider for generated templates (github, gitlab, azure-devops, bitbucket)",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)

    workspace_root = Path(__file__).resolve().parents[1]
    orchestrator = DevOpsOrchestrator(workspace_root=workspace_root)
    summary = orchestrator.run_full_workflow(
        repository_path=Path(args.repo),
        output_dir=Path(args.output_dir),
        include_llm=args.include_llm,
        strict_security_tools=args.strict_security_tools,
        skip_image_scan=args.skip_image_scan,
        llm_model=args.llm_model,
        cicd_provider=args.cicd_provider,
    )
    save_json_output(Path(args.output_dir) / "workflow-summary.json", summary)


if __name__ == "__main__":
    main()
