from __future__ import annotations

import argparse
from pathlib import Path

from shared import save_json_output, setup_logging

from .devsecops_security import DevSecOpsSecurityAgent

def main() -> None:
    parser = argparse.ArgumentParser(description="Run DevSecOps Security Agent")
    parser.add_argument("--repo", required=True, help="Repository path")
    parser.add_argument("--arch", required=True, help="Architecture JSON path")
    parser.add_argument("--output", default="devops_multi_agents/outputs/devsecops-security.json")
    parser.add_argument("--disable-llm", action="store_true", help="Disable LLM advisory summary")
    parser.add_argument("--llm-model", default=None, help="Override LLM model for security advisory")
    parser.add_argument(
        "--require-real-llm",
        action="store_true",
        help="Fail if LLM call cannot be completed (no fallback)",
    )
    parser.add_argument("--strict-tools", action="store_true", help="Fail if any security tool execution fails")
    parser.add_argument("--skip-image-scan", action="store_true", help="Skip Trivy image scans")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    setup_logging(args.log_level)
    arch_path = Path(args.arch)
    if not arch_path.exists():
        suggested = Path("devops_multi_agents/outputs/architecture-analysis.json")
        raise FileNotFoundError(
            f"Architecture file not found: {arch_path}. "
            f"Use --arch {suggested} or run architecture analysis first."
        )
    arch_data = arch_path.read_text(encoding="utf-8")

    import json

    agent = DevSecOpsSecurityAgent()
    result = agent.run(
        repository_path=Path(args.repo),
        architecture_data=json.loads(arch_data),
        include_llm=not args.disable_llm,
        llm_model=args.llm_model,
        require_real_llm=args.require_real_llm,
        strict_tools=args.strict_tools,
        skip_image_scan=args.skip_image_scan,
    )
    save_json_output(Path(args.output), result.__dict__)

    if args.strict_tools and not result.success:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
