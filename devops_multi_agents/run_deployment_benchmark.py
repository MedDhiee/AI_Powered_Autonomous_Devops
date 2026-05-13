from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared import save_json_output, setup_logging

def main() -> None:
    from devops_multi_agents.agents.deployment.agent import UnifiedDeploymentAgent
    parser = argparse.ArgumentParser(description="Run Deployment Agent benchmark/standalone")
    parser.add_argument("--arch", required=True, help="Architecture JSON path")
    parser.add_argument("--cicd", required=False, help="CI/CD output JSON path")
    parser.add_argument(
        "--target-env",
        default="minikube",
        choices=["minikube", "local", "aws", "azure", "gcp"],
        help="Deployment environment",
    )
    parser.add_argument("--auto-approve", action="store_true", help="Deploy without asking HITL (testing only)")
    parser.add_argument("--repo", default=".", help="Repository path used for dynamic service resolution")
    parser.add_argument("--llm-model", default=None, help="Deployment Terraform LLM model (cloud only)")
    parser.add_argument("--llm-provider", default="ollama", help="Deployment Terraform LLM provider (cloud only)")
    parser.add_argument("--require-real-llm", action="store_true", help="Fail if Terraform LLM output is not real")
    parser.add_argument(
        "--auto-approve-terraform",
        action="store_true",
        help="Skip manual Terraform validation before plan/apply",
    )
    parser.add_argument(
        "--skip-terraform-apply",
        action="store_true",
        help="Generate Terraform and approve flow, but skip terraform apply",
    )
    parser.add_argument("--output", default="devops_multi_agents/outputs/deployment-plan.json")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    setup_logging(args.log_level)
    import json

    arch_data = json.loads(Path(args.arch).read_text(encoding="utf-8"))
    cicd_data = json.loads(Path(args.cicd).read_text(encoding="utf-8")) if args.cicd else {}
    target_env = "minikube" if args.target_env == "local" else args.target_env
    llm_model = args.llm_model
    if target_env in {"aws", "azure", "gcp"} and not llm_model:
        llm_model = "glm-5:cloud"
    
    agent = UnifiedDeploymentAgent()
    result = agent.run(
        architecture_data=arch_data, 
        cicd_data=cicd_data,
        target_env=target_env,
        require_approval=not args.auto_approve,
        repository_path=str(Path(args.repo).resolve()),
        llm_model=llm_model,
        llm_provider=args.llm_provider,
        require_real_llm=args.require_real_llm,
        auto_approve_terraform=args.auto_approve_terraform,
        skip_terraform_apply=args.skip_terraform_apply,
    )
    save_json_output(Path(args.output), result.__dict__)

if __name__ == "__main__":
    main()