"""
Incident Response Agent – entry point.

Delegates to the full implementation in
`devops_multi_agents.agents.incident_response.agent`.

Usage (from workspace root):

    python -m devops_multi_agents.agents.incident_response_agent \\
        --arch   devops_multi_agents/outputs/architecture-analysis.json \\
        --security devops_multi_agents/outputs/devsecops-security.json \\
        --chaos  devops_multi_agents/outputs/chaos-experiments.json \\
        --output devops_multi_agents/outputs/incident-response.json

    # With auto-apply of safe fix commands:
    python -m devops_multi_agents.agents.incident_response_agent \\
        --arch devops_multi_agents/outputs/architecture-analysis.json \\
        --auto-apply \\
        --similarity-threshold 0.60
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from shared import save_json_output, setup_logging
from devops_multi_agents.agents.incident_response import IncidentResponseAgent


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Incident Response Agent (RAG + MCP)")
    parser.add_argument("--arch", required=True, help="Architecture analysis JSON path")
    parser.add_argument("--security", required=False, help="DevSecOps security output JSON path")
    parser.add_argument("--chaos", required=False, help="Chaos engineering output JSON path")
    parser.add_argument(
        "--output",
        default="devops_multi_agents/outputs/incident-response.json",
        help="Output JSON path",
    )
    parser.add_argument(
        "--output-dir",
        default="devops_multi_agents/outputs/incident-fixes",
        help="Directory for generated fix scripts",
    )
    parser.add_argument(
        "--similarity-threshold",
        type=float,
        default=0.7,
        help="Minimum RAG cosine similarity to accept a match (default: 0.7)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Number of RAG candidates to retrieve per incident (default: 3)",
    )
    parser.add_argument(
        "--max-incidents",
        type=int,
        default=20,
        help="Maximum incidents to pull from MongoDB (default: 20)",
    )
    parser.add_argument(
        "--auto-apply",
        action="store_true",
        default=False,
        help="Also execute fix (write) commands, not just diagnostics",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    setup_logging(args.log_level)

    arch_data = json.loads(Path(args.arch).read_text(encoding="utf-8"))
    security_data = (
        json.loads(Path(args.security).read_text(encoding="utf-8")) if args.security else {}
    )
    chaos_data = (
        json.loads(Path(args.chaos).read_text(encoding="utf-8")) if args.chaos else {}
    )

    agent = IncidentResponseAgent(
        similarity_threshold=args.similarity_threshold,
        auto_apply=args.auto_apply,
        output_dir=args.output_dir,
    )

    result = agent.run(
        architecture_data=arch_data,
        security_data=security_data,
        chaos_data=chaos_data,
        top_k=args.top_k,
        max_incidents=args.max_incidents,
        output_dir=args.output_dir,
    )

    save_json_output(Path(args.output), result.__dict__)
    print(f"[incident_response_agent] Done → {args.output}")
    print(f"  Summary: {result.data.get('summary', '')}")


if __name__ == "__main__":
    main()
