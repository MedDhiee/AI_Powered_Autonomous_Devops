"""CLI entry point for the Chaos Engineering Agent.

The full implementation lives in the ``chaos_engineering/`` package.
This module keeps the original ``python -m devops_multi_agents.agents.chaos_engineering_agent``
invocation working and re-exports ``ChaosEngineeringAgent`` for backward compatibility.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from shared import save_json_output, setup_logging

from .chaos_engineering import ChaosEngineeringAgent
from .chaos_engineering.agent import _parse_container_map

__all__ = ["ChaosEngineeringAgent"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Chaos Engineering Agent — injects failures, collects Prometheus/Grafana metrics"
    )
    parser.add_argument("--arch", required=True, help="Path to architecture-analysis.json")
    parser.add_argument(
        "--output",
        default="devops_multi_agents/outputs/chaos-experiments.json",
        help="Output JSON path",
    )
    parser.add_argument("--log-level", default="INFO")

    # Backend selection
    parser.add_argument(
        "--backend",
        choices=["simulation", "docker", "kubernetes"],
        default="simulation",
        help="Chaos execution backend (default: simulation)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log commands without executing them",
    )

    # Monitoring endpoints
    parser.add_argument(
        "--prometheus-url",
        default=None,
        help="Prometheus HTTP API URL (e.g. http://localhost:9090)",
    )
    parser.add_argument(
        "--grafana-url",
        default=None,
        help="Grafana HTTP API URL (e.g. http://localhost:3000)",
    )
    parser.add_argument("--grafana-api-key", default=None, help="Grafana service-account token")
    parser.add_argument("--grafana-username", default="admin")
    parser.add_argument("--grafana-password", default="admin")
    parser.add_argument(
        "--loki-datasource-uid",
        default="loki",
        help="UID of the Loki datasource in Grafana",
    )

    # Experiment control
    parser.add_argument(
        "--experiment-duration",
        type=int,
        default=None,
        help="Seconds to hold each failure (overrides CHAOS_EXPERIMENT_DURATION env var)",
    )
    parser.add_argument(
        "--observation-window",
        type=int,
        default=None,
        help="Post-recovery observation seconds (overrides CHAOS_OBSERVATION_WINDOW env var)",
    )
    parser.add_argument(
        "--experiment-types",
        default=None,
        help="Comma-separated list of experiment types to run "
             "(pod-failure,network-latency,network-partition,cpu-stress,memory-stress,disk-stress,service-unavailable)",
    )
    parser.add_argument(
        "--max-experiments",
        type=int,
        default=50,
        help="Maximum number of experiments to run",
    )
    parser.add_argument(
        "--namespace",
        default="default",
        help="Kubernetes namespace (only used with --backend kubernetes)",
    )
    parser.add_argument(
        "--container-map",
        default=None,
        metavar="SVC=CONTAINER,...",
        help=(
            "Comma-separated service-name=container-name mappings for the docker backend. "
            "Example: hb_electronics=gestiondestock-ui,gestiondestock=gestiondestock-api"
        ),
    )

    args = parser.parse_args()
    setup_logging(args.log_level)

    # Build agent — CLI args override env vars
    import os as _os

    raw_map = args.container_map or _os.getenv("CHAOS_CONTAINER_MAP", "")
    agent = ChaosEngineeringAgent(
        prometheus_url=args.prometheus_url or _os.getenv("PROMETHEUS_URL", "http://localhost:9090"),
        grafana_url=args.grafana_url or _os.getenv("GRAFANA_URL", "http://localhost:3000"),
        grafana_api_key=args.grafana_api_key or _os.getenv("GRAFANA_API_KEY"),
        grafana_username=args.grafana_username,
        grafana_password=args.grafana_password,
        loki_datasource_uid=args.loki_datasource_uid,
        backend=args.backend,
        dry_run=args.dry_run,
        experiment_duration=args.experiment_duration
        or int(_os.getenv("CHAOS_EXPERIMENT_DURATION", "60")),
        observation_window=args.observation_window
        or int(_os.getenv("CHAOS_OBSERVATION_WINDOW", "30")),
        k8s_namespace=args.namespace,
        container_map=_parse_container_map(raw_map) if raw_map else None,
    )

    arch_data = json.loads(Path(args.arch).read_text(encoding="utf-8"))

    selected_types = (
        [t.strip() for t in args.experiment_types.split(",") if t.strip()]
        if args.experiment_types
        else None
    )

    result = agent.run(
        architecture_data=arch_data,
        experiment_types=selected_types,
        max_experiments=args.max_experiments,
    )
    save_json_output(Path(args.output), result.__dict__)


if __name__ == "__main__":
    main()
