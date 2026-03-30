from __future__ import annotations

import argparse
from pathlib import Path

from shared import save_json_output, setup_logging

from devops_multi_agents.agents import ArchitectureAnalysisAgent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run integrated architecture analysis agent and output structured JSON"
    )
    parser.add_argument("repo_path", help="Path to the repository to analyze")
    parser.add_argument(
        "--output",
        default="devops_multi_agents/outputs/architecture-analysis.json",
        help="Output JSON path",
    )
    parser.add_argument(
        "--mermaid-output",
        default="devops_multi_agents/outputs/architecture-diagram.mmd",
        help="Output Mermaid diagram path",
    )
    parser.add_argument("--include-llm", action="store_true")
    parser.add_argument("--llm-model", default=None, help="Override LLM model for architecture summary")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)

    agent = ArchitectureAnalysisAgent()
    result = agent.analyze_repository(
        Path(args.repo_path),
        include_llm=args.include_llm,
        llm_model=args.llm_model,
    )

    output_path = Path(args.output)
    save_json_output(output_path, result)

    if result.get("mermaid_diagram"):
        mermaid_path = Path(args.mermaid_output)
        mermaid_path.parent.mkdir(parents=True, exist_ok=True)
        mermaid_path.write_text(result["mermaid_diagram"], encoding="utf-8")


if __name__ == "__main__":
    main()
