from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from shared import save_json_output, setup_logging

from devops_multi_agents.agents.devsecops_security import DevSecOpsSecurityAgent
from devops_multi_agents.llm_judge import LLMJudge


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run DevSecOps security agent for two models and compare them with LLMJudge"
    )
    parser.add_argument("--repo", required=True, help="Repository path")
    parser.add_argument("--arch", required=True, help="Architecture JSON path")
    parser.add_argument("--model-a", required=True, help="Model A id")
    parser.add_argument("--model-b", required=True, help="Model B id")
    parser.add_argument(
        "--output-a",
        default="devops_multi_agents/outputs/devsecops-security-model-a.json",
        help="Output file for model A",
    )
    parser.add_argument(
        "--output-b",
        default="devops_multi_agents/outputs/devsecops-security-model-b.json",
        help="Output file for model B",
    )
    parser.add_argument(
        "--judge-output",
        default="devops_multi_agents/outputs/devsecops-only-judge-report.json",
        help="Output file for devsecops-only judge report",
    )
    parser.add_argument("--judge-model", default=None, help="Judge model override")
    parser.add_argument("--require-real-judge", action="store_true")
    parser.add_argument("--require-real-llm", action="store_true")
    parser.add_argument("--strict-tools", action="store_true")
    parser.add_argument("--skip-image-scan", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def run_devsecops_once(
    *,
    repository_path: Path,
    architecture_data: dict[str, Any],
    llm_model: str,
    require_real_llm: bool,
    strict_tools: bool,
    skip_image_scan: bool,
) -> dict[str, Any]:
    agent = DevSecOpsSecurityAgent()
    result = agent.run(
        repository_path=repository_path,
        architecture_data=architecture_data,
        include_llm=True,
        llm_model=llm_model,
        require_real_llm=require_real_llm,
        strict_tools=strict_tools,
        skip_image_scan=skip_image_scan,
    )
    return result.__dict__


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)

    arch_path = Path(args.arch)
    if not arch_path.exists():
        raise FileNotFoundError(f"Architecture file not found: {arch_path}")
    architecture_data = json.loads(arch_path.read_text(encoding="utf-8"))

    repository_path = Path(args.repo)

    result_a = run_devsecops_once(
        repository_path=repository_path,
        architecture_data=architecture_data,
        llm_model=args.model_a,
        require_real_llm=args.require_real_llm,
        strict_tools=args.strict_tools,
        skip_image_scan=args.skip_image_scan,
    )
    result_b = run_devsecops_once(
        repository_path=repository_path,
        architecture_data=architecture_data,
        llm_model=args.model_b,
        require_real_llm=args.require_real_llm,
        strict_tools=args.strict_tools,
        skip_image_scan=args.skip_image_scan,
    )

    save_json_output(Path(args.output_a), result_a)
    save_json_output(Path(args.output_b), result_b)

    workflow_a = {
        "agents": {
            "devsecops_security_agent": {
                "success": bool(result_a.get("success", False)),
                "data": result_a.get("data", {}),
                "duration_seconds": result_a.get("duration_seconds"),
            }
        }
    }
    workflow_b = {
        "agents": {
            "devsecops_security_agent": {
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
