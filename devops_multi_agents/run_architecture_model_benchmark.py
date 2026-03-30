from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

from shared import save_json_output, setup_logging

from devops_multi_agents.agents import ArchitectureAnalysisAgent
from devops_multi_agents.llm_judge import LLMJudge


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run architecture analysis for two models with real latency measurement and judge comparison"
    )
    parser.add_argument("repo_path", help="Path to the repository to analyze")
    parser.add_argument("--model-a", required=True, help="Model A id")
    parser.add_argument("--model-b", required=True, help="Model B id")
    parser.add_argument(
        "--output-a",
        default="devops_multi_agents/outputs/architecture-analysis-model-a.json",
        help="Output file for model A architecture analysis",
    )
    parser.add_argument(
        "--output-b",
        default="devops_multi_agents/outputs/architecture-analysis-model-b.json",
        help="Output file for model B architecture analysis",
    )
    parser.add_argument(
        "--judge-output",
        default="devops_multi_agents/outputs/architecture-only-judge-report.json",
        help="Output file for architecture-only judge report",
    )
    parser.add_argument("--judge-model", default=None, help="Judge model override")
    parser.add_argument("--require-real-judge", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def run_architecture_once(repo_path: Path, llm_model: str) -> tuple[dict[str, Any], float]:
    agent = ArchitectureAnalysisAgent()
    start = time.perf_counter()
    result = agent.analyze_repository(repo_path, include_llm=True, llm_model=llm_model)
    duration_seconds = time.perf_counter() - start
    return result, duration_seconds


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)

    repo_path = Path(args.repo_path)
    result_a, duration_a = run_architecture_once(repo_path, args.model_a)
    result_b, duration_b = run_architecture_once(repo_path, args.model_b)

    save_json_output(Path(args.output_a), result_a)
    save_json_output(Path(args.output_b), result_b)

    workflow_a = {
        "agents": {
            "architecture_analysis_agent": {
                "success": True,
                "data": result_a,
                "duration_seconds": duration_a,
            }
        }
    }
    workflow_b = {
        "agents": {
            "architecture_analysis_agent": {
                "success": True,
                "data": result_b,
                "duration_seconds": duration_b,
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
