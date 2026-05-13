from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared import save_json_output, setup_logging

from devops_multi_agents.agents.deployment.agent import UnifiedDeploymentAgent
from devops_multi_agents.llm_judge import LLMJudge


DEPLOYMENT_MODEL_SUGGESTIONS = [
    "glm-5:cloud",
    "minimax-m2.7:cloud",
    "qwen3:14b",
    "qwen2.5:14b-instruct-q6_K",
]

JUDGE_PROVIDER_SUGGESTIONS = ["ollama", "openrouter", "groq"]


def default_judge_model_for_provider(provider: str) -> str:
    normalized = (provider or "").strip().lower()
    if normalized == "ollama":
        return "nemotron-3-super"
    if normalized == "groq":
        return "llama-3.1-8b-instant"
    return "z-ai/glm-4.5-air:free"


def prompt_choice(label: str, options: list[str], default_value: str) -> str:
    print(f"\n{label}")
    for idx, option in enumerate(options, start=1):
        print(f"  {idx}) {option}")
    print(f"  {len(options) + 1}) custom")

    try:
        answer = input(f"Selection [1-{len(options) + 1}] (default: {default_value}): ").strip()
    except EOFError:
        return default_value

    if not answer:
        return default_value

    if answer.isdigit():
        index = int(answer)
        if 1 <= index <= len(options):
            return options[index - 1]
        if index == len(options) + 1:
            try:
                custom = input("Custom value: ").strip()
            except EOFError:
                custom = ""
            return custom or default_value

    return answer


def resolve_models(args: argparse.Namespace) -> tuple[str, str, str, str]:
    model_a = args.model_a
    model_b = args.model_b
    judge_provider = (args.judge_provider or "ollama").strip().lower() or "ollama"
    judge_model = args.judge_model or default_judge_model_for_provider(judge_provider)

    if not args.interactive_model_selection:
        return model_a, model_b, judge_provider, judge_model

    model_a = prompt_choice("Choose deployment model A", DEPLOYMENT_MODEL_SUGGESTIONS, model_a)
    model_b = prompt_choice("Choose deployment model B", DEPLOYMENT_MODEL_SUGGESTIONS, model_b)
    judge_provider = prompt_choice("Choose judge provider", JUDGE_PROVIDER_SUGGESTIONS, judge_provider).strip().lower()

    judge_defaults = {
        "ollama": ["nemotron-3-super", "qwen3:14b", "qwen2.5:14b-instruct-q6_K", "llama3.2"],
        "openrouter": ["z-ai/glm-4.5-air:free", "gpt-oss"],
        "groq": ["llama-3.1-8b-instant", "llama-3.3-70b-versatile"],
    }
    judge_model = prompt_choice(
        "Choose judge model",
        judge_defaults.get(judge_provider, [judge_model]),
        judge_model,
    )
    return model_a, model_b, judge_provider, judge_model


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Deployment agent with two Terraform LLM models and compare with LLMJudge"
    )
    parser.add_argument("--repo", default=".", help="Repository path")
    parser.add_argument("--arch", required=True, help="Architecture JSON path")
    parser.add_argument("--cicd", required=False, help="CI/CD output JSON path")
    parser.add_argument(
        "--target-env",
        default="aws",
        choices=["aws", "azure", "gcp"],
        help="Cloud target environment for Terraform generation",
    )
    parser.add_argument("--model-a", default="glm-5:cloud", help="Model A id")
    parser.add_argument("--model-b", default="minimax-m2.7:cloud", help="Model B id")
    parser.add_argument("--llm-provider", default="ollama", help="LLM provider (default: ollama)")
    parser.add_argument("--require-real-llm", action="store_true")
    parser.add_argument("--require-real-judge", action="store_true")
    parser.add_argument(
        "--judge-provider",
        default="ollama",
        help="Judge LLM provider (default: ollama)",
    )
    parser.add_argument("--judge-model", default=None, help="Judge model override")
    parser.add_argument(
        "--interactive-model-selection",
        action="store_true",
        help="Prompt in terminal to select model A/model B/judge provider/judge model",
    )
    parser.add_argument(
        "--auto-approve-terraform",
        action="store_true",
        help="Skip manual Terraform validation before plan/apply",
    )
    parser.add_argument(
        "--require-deployment-approval",
        action="store_true",
        help="Require initial deployment approval prompt",
    )
    parser.add_argument(
        "--run-terraform-apply",
        dest="skip_terraform_apply",
        action="store_false",
        help="Execute terraform init/validate/plan/apply after approval",
    )
    parser.set_defaults(skip_terraform_apply=True)
    parser.add_argument(
        "--output-a",
        default="devops_multi_agents/outputs/deployment-model-a.json",
        help="Output file for model A",
    )
    parser.add_argument(
        "--output-b",
        default="devops_multi_agents/outputs/deployment-model-b.json",
        help="Output file for model B",
    )
    parser.add_argument(
        "--judge-output",
        default="devops_multi_agents/outputs/deployment-only-judge-report.json",
        help="Output file for deployment-only judge report",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def run_deployment_once(
    *,
    repository_path: Path,
    architecture_data: dict[str, Any],
    cicd_data: dict[str, Any],
    target_env: str,
    llm_model: str,
    llm_provider: str,
    require_real_llm: bool,
    require_deployment_approval: bool,
    auto_approve_terraform: bool,
    skip_terraform_apply: bool,
) -> dict[str, Any]:
    agent = UnifiedDeploymentAgent()
    result = agent.run(
        architecture_data=architecture_data,
        cicd_data=cicd_data,
        target_env=target_env,
        require_approval=require_deployment_approval,
        repository_path=str(repository_path.resolve()),
        llm_model=llm_model,
        llm_provider=llm_provider,
        require_real_llm=require_real_llm,
        auto_approve_terraform=auto_approve_terraform,
        skip_terraform_apply=skip_terraform_apply,
    )
    return result.__dict__


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)
    model_a, model_b, judge_provider, judge_model = resolve_models(args)

    arch_path = Path(args.arch)
    if not arch_path.exists():
        raise FileNotFoundError(f"Architecture file not found: {arch_path}")

    architecture_data = json.loads(arch_path.read_text(encoding="utf-8"))
    cicd_data = json.loads(Path(args.cicd).read_text(encoding="utf-8")) if args.cicd else {}
    repository_path = Path(args.repo)

    result_a = run_deployment_once(
        repository_path=repository_path,
        architecture_data=architecture_data,
        cicd_data=cicd_data,
        target_env=args.target_env,
        llm_model=model_a,
        llm_provider=args.llm_provider,
        require_real_llm=args.require_real_llm,
        require_deployment_approval=args.require_deployment_approval,
        auto_approve_terraform=args.auto_approve_terraform,
        skip_terraform_apply=args.skip_terraform_apply,
    )
    result_b = run_deployment_once(
        repository_path=repository_path,
        architecture_data=architecture_data,
        cicd_data=cicd_data,
        target_env=args.target_env,
        llm_model=model_b,
        llm_provider=args.llm_provider,
        require_real_llm=args.require_real_llm,
        require_deployment_approval=args.require_deployment_approval,
        auto_approve_terraform=args.auto_approve_terraform,
        skip_terraform_apply=args.skip_terraform_apply,
    )

    save_json_output(Path(args.output_a), result_a)
    save_json_output(Path(args.output_b), result_b)

    workflow_a = {
        "agents": {
            "deployment_agent": {
                "success": bool(result_a.get("success", False)),
                "data": result_a.get("data", {}),
                "duration_seconds": result_a.get("duration_seconds"),
            }
        }
    }
    workflow_b = {
        "agents": {
            "deployment_agent": {
                "success": bool(result_b.get("success", False)),
                "data": result_b.get("data", {}),
                "duration_seconds": result_b.get("duration_seconds"),
            }
        }
    }

    report = LLMJudge().compare_models(
        model_a=model_a,
        model_b=model_b,
        workflow_a=workflow_a,
        workflow_b=workflow_b,
        judge_model=judge_model,
        judge_provider=judge_provider,
        require_real_judge=args.require_real_judge,
    )
    save_json_output(Path(args.judge_output), report)


if __name__ == "__main__":
    main()
