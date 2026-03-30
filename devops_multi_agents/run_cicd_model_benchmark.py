from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from shared import save_json_output, setup_logging

from devops_multi_agents.agents import CICDGenerationAgent
from devops_multi_agents.llm_judge import LLMJudge


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run CI/CD generation agent for two model labels and compare them with LLMJudge"
    )
    parser.add_argument("--arch", required=True, help="Architecture JSON path")
    parser.add_argument("--model-a", required=True, help="Model A id (label for comparison report)")
    parser.add_argument("--model-b", required=True, help="Model B id (label for comparison report)")
    parser.add_argument(
        "--provider",
        default="github",
        help="CI/CD provider (github, gitlab, azure-devops, bitbucket)",
    )
    parser.add_argument(
        "--output-a",
        default="devops_multi_agents/outputs/cicd-generation-model-a.json",
        help="Output file for model A CI/CD generation",
    )
    parser.add_argument(
        "--output-b",
        default="devops_multi_agents/outputs/cicd-generation-model-b.json",
        help="Output file for model B CI/CD generation",
    )
    parser.add_argument(
        "--judge-output",
        default="devops_multi_agents/outputs/cicd-only-judge-report.json",
        help="Output file for CI/CD-only judge report",
    )
    parser.add_argument("--judge-model", default=None, help="Judge model override")
    parser.add_argument("--require-real-judge", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def run_cicd_once(*, architecture_data: dict[str, Any], provider: str) -> dict[str, Any]:
    agent = CICDGenerationAgent()
    result = agent.run(
        architecture_data=architecture_data,
        provider=provider,
        ask_provider=False,
    )
    return result.__dict__


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)

    arch_path = Path(args.arch)
    if not arch_path.exists():
        raise FileNotFoundError(f"Architecture file not found: {arch_path}")
    architecture_data = json.loads(arch_path.read_text(encoding="utf-8"))

    result_a = run_cicd_once(
        architecture_data=architecture_data,
        provider=args.provider,
    )
    result_b = run_cicd_once(
        architecture_data=architecture_data,
        provider=args.provider,
    )

    save_json_output(Path(args.output_a), result_a)
    save_json_output(Path(args.output_b), result_b)

    workflow_a = {
        "agents": {
            "cicd_generation_agent": {
                "success": bool(result_a.get("success", False)),
                "data": result_a.get("data", {}),
                "duration_seconds": result_a.get("duration_seconds"),
            }
        }
    }
    workflow_b = {
        "agents": {
            "cicd_generation_agent": {
                "success": bool(result_b.get("success", False)),
                "data": result_b.get("data", {}),
                "duration_seconds": result_b.get("duration_seconds"),
            }
        }
    }

    report = LLMJudge().compare_models(
        model_a=args.model_a,
        model_b=args.model_b,
        workflow_a=workflow_a,
        workflow_b=workflow_b,
        judge_model=args.judge_model,
        require_real_judge=args.require_real_judge,
    )
    save_json_output(Path(args.judge_output), report)


if __name__ == "__main__":
    main()
