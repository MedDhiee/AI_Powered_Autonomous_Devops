"""
Entry point for:
    python -m devops_multi_agents.agents.incident_response [options]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
from pathlib import Path

from shared.utils import setup_logging


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Incident Response Agent – RAG + LLM remediation pipeline"
    )
    p.add_argument(
        "--mock-incidents",
        metavar="PATH",
        default=None,
        help="Path to a JSON file containing mock incidents (offline mode).",
    )
    p.add_argument(
        "--output",
        metavar="PATH",
        default="devops_multi_agents/outputs/incident-response.json",
        help="Where to write the JSON report.",
    )
    p.add_argument(
        "--output-dir",
        metavar="DIR",
        default=None,
        help="Directory for generated fix scripts (default: outputs/incident-fixes).",
    )
    p.add_argument(
        "--similarity-threshold",
        type=float,
        default=None,
        help="Minimum cosine similarity for a RAG hit to count as a match (0-1).",
    )
    p.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Number of RAG candidates to retrieve per incident.",
    )
    p.add_argument(
        "--auto-apply",
        action="store_true",
        default=False,
        help="Automatically execute safe fix commands (not just diagnostics).",
    )
    p.add_argument(
        "--arch",
        metavar="PATH",
        default=None,
        help="Path to architecture-analysis.json (optional context).",
    )
    p.add_argument(
        "--security",
        metavar="PATH",
        default=None,
        help="Path to devsecops-security.json (optional context).",
    )
    p.add_argument(
        "--chaos",
        metavar="PATH",
        default=None,
        help="Path to chaos-experiments.json (optional context).",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p.parse_args()


def _load_json(path: str | None) -> dict:
    if not path:
        return {}
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        logging.warning("Could not load %s: %s", path, exc)
        return {}


def main() -> None:
    args = _parse_args()
    setup_logging(args.log_level)

    from .agent import IncidentResponseAgent

    agent = IncidentResponseAgent(
        auto_apply=args.auto_apply,
        output_dir=args.output_dir,
        mock_incidents_path=args.mock_incidents,
        **(
            {"similarity_threshold": args.similarity_threshold}
            if args.similarity_threshold is not None
            else {}
        ),
    )

    kwargs: dict = {
        "top_k": args.top_k,
        "architecture_data": _load_json(args.arch),
        "security_data": _load_json(args.security),
        "chaos_data": _load_json(args.chaos),
    }
    if args.similarity_threshold is not None:
        kwargs["similarity_threshold"] = args.similarity_threshold

    result = agent.run(**kwargs)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = dataclasses.asdict(result) if dataclasses.is_dataclass(result) else result
    out_path.write_text(
        json.dumps(payload, indent=2, default=str, ensure_ascii=False),
        encoding="utf-8",
    )

    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    print(data.get("summary", "Done."))
    print(f"Report written to: {out_path}")


if __name__ == "__main__":
    main()
