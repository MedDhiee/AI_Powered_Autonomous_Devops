from __future__ import annotations

import argparse
import os
from pathlib import Path

from shared import save_json_output, sanitize_name, setup_logging

from devops_multi_agents.llm_judge import LLMJudge
from devops_multi_agents.orchestrator import DevOpsOrchestrator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark two LLM models across all agents, then run LLM-as-judge"
    )
    parser.add_argument("--repo", required=True, help="Path to repository to analyze")
    parser.add_argument("--model-a", required=True, help="First model ID")
    parser.add_argument("--model-b", required=True, help="Second model ID")
    parser.add_argument(
        "--judge-model",
        default=None,
        help="Optional judge model ID (defaults to OPENROUTER_JUDGE_MODEL or OPENROUTER_MODEL)",
    )
    parser.add_argument(
        "--allow-fallback",
        action="store_true",
        help="Allow benchmark to run even if OPENROUTER_API_KEY is missing",
    )
    parser.add_argument(
        "--require-real-judge",
        action="store_true",
        help="Fail benchmark if LLM judge cannot return a real response",
    )
    parser.add_argument(
        "--output-dir",
        default="devops_multi_agents/outputs/llm-benchmark",
        help="Output directory for benchmark artifacts",
    )
    parser.add_argument(
        "--strict-security-tools",
        action="store_true",
        help="Fail benchmark if security tools fail",
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


def run_single_model(
    orchestrator: DevOpsOrchestrator,
    repository_path: Path,
    output_dir: Path,
    model_name: str,
    strict_security_tools: bool,
    skip_image_scan: bool,
    cicd_provider: str,
) -> dict:
    model_slug = sanitize_name(model_name)
    model_output_dir = output_dir / model_slug
    return orchestrator.run_full_workflow(
        repository_path=repository_path,
        output_dir=model_output_dir,
        include_llm=True,
        strict_security_tools=strict_security_tools,
        skip_image_scan=skip_image_scan,
        llm_model=model_name,
        cicd_provider=cicd_provider,
    )


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)

    if not os.getenv("OPENROUTER_API_KEY") and not args.allow_fallback:
        raise SystemExit(
            "OPENROUTER_API_KEY is required for real multi-model benchmark. "
            "Set the key or rerun with --allow-fallback."
        )

    workspace_root = Path(__file__).resolve().parents[1]
    orchestrator = DevOpsOrchestrator(workspace_root=workspace_root)
    judge = LLMJudge()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_a = run_single_model(
        orchestrator=orchestrator,
        repository_path=Path(args.repo),
        output_dir=output_dir,
        model_name=args.model_a,
        strict_security_tools=args.strict_security_tools,
        skip_image_scan=args.skip_image_scan,
        cicd_provider=args.cicd_provider,
    )
    summary_b = run_single_model(
        orchestrator=orchestrator,
        repository_path=Path(args.repo),
        output_dir=output_dir,
        model_name=args.model_b,
        strict_security_tools=args.strict_security_tools,
        skip_image_scan=args.skip_image_scan,
        cicd_provider=args.cicd_provider,
    )

    report = judge.compare_models(
        model_a=args.model_a,
        model_b=args.model_b,
        workflow_a=summary_a,
        workflow_b=summary_b,
        judge_model=args.judge_model,
        require_real_judge=args.require_real_judge,
    )

    benchmark_output = {
        "repo": str(Path(args.repo)),
        "model_runs": {
            "model_a": {
                "name": args.model_a,
                "output_dir": str(output_dir / sanitize_name(args.model_a)),
            },
            "model_b": {
                "name": args.model_b,
                "output_dir": str(output_dir / sanitize_name(args.model_b)),
            },
        },
        "comparison_report": report,
    }
    save_json_output(output_dir / "llm-comparison-report.json", benchmark_output)


if __name__ == "__main__":
    main()
