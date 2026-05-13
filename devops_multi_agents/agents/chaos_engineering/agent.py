from __future__ import annotations

import logging
import os
import time
from typing import Any

from shared import BaseAgent, ProtocolType

from .chaos_runner import ChaosRunner
from .experiment_types import (
    ExperimentConfig,
    ExperimentResult,
    ExperimentStatus,
    ExperimentType,
    MetricsSnapshot,
)
from .grafana_client import GrafanaClient
from .metrics_collector import PrometheusClient
from .report_generator import build_report

logger = logging.getLogger("chaos_engineering_agent")

# Experiment types that are always planned (regardless of service properties)
_ALWAYS = {ExperimentType.POD_FAILURE}

# Experiment types planned only when the service has exposed ports
_REQUIRES_PORTS = {ExperimentType.NETWORK_LATENCY, ExperimentType.NETWORK_PARTITION}

# Experiment types planned only for specific tech stacks
_STACK_EXPERIMENTS: dict[str, set[ExperimentType]] = {
    "nodejs": {ExperimentType.CPU_STRESS, ExperimentType.MEMORY_STRESS},
    "python": {ExperimentType.CPU_STRESS, ExperimentType.MEMORY_STRESS},
    "java": {ExperimentType.CPU_STRESS, ExperimentType.MEMORY_STRESS, ExperimentType.DISK_STRESS},
    "spring": {ExperimentType.CPU_STRESS, ExperimentType.MEMORY_STRESS, ExperimentType.DISK_STRESS},
    "go": {ExperimentType.CPU_STRESS},
    "ruby": {ExperimentType.CPU_STRESS},
}


class ChaosEngineeringAgent(BaseAgent):
    """Chaos Engineering Agent (A2A protocol).

    Injects realistic failures into services, collects Prometheus metrics
    before / during / after each experiment, annotates Grafana dashboards,
    queries Loki logs, and produces a resilience report with a scored grade
    and actionable recommendations.

    Constructor parameters
    ----------------------
    prometheus_url      : Prometheus HTTP API endpoint
    grafana_url         : Grafana HTTP API endpoint
    grafana_api_key     : Grafana service-account token (preferred over user/pass)
    grafana_username    : Basic-auth username (fallback when no API key)
    grafana_password    : Basic-auth password (fallback when no API key)
    loki_datasource_uid : UID of the Loki datasource in Grafana
    backend             : "simulation" | "docker" | "kubernetes"
    dry_run             : Log commands without executing them (implies simulation)
    experiment_duration : Seconds each failure is held (default 60)
    observation_window  : Seconds to observe after recovery before taking final snapshot
    k8s_namespace       : Kubernetes namespace for pod operations
    """

    def __init__(
        self,
        prometheus_url: str = "http://localhost:9090",
        grafana_url: str = "http://localhost:3000",
        grafana_api_key: str | None = None,
        grafana_username: str = "admin",
        grafana_password: str = "admin",
        loki_datasource_uid: str = "loki",
        backend: str = "simulation",
        dry_run: bool = False,
        experiment_duration: int = 60,
        observation_window: int = 30,
        k8s_namespace: str = "default",
        container_map: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            agent_name="chaos_engineering_agent",
            protocol=ProtocolType.A2A.value,
        )
        self.prometheus = PrometheusClient(url=prometheus_url)
        self.grafana = GrafanaClient(
            url=grafana_url,
            api_key=grafana_api_key,
            username=grafana_username,
            password=grafana_password,
            loki_datasource_uid=loki_datasource_uid,
        )
        self.runner = ChaosRunner(
            backend=backend,
            dry_run=dry_run,
            namespace=k8s_namespace,
            container_map=container_map,
        )
        self.experiment_duration = experiment_duration
        self.observation_window = observation_window

        # In simulation mode, use very short sleeps to keep demo runs fast
        self._sim_sleep = backend == "simulation" and not dry_run

    # ── BaseAgent interface ───────────────────────────────────────────────────

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        architecture_data: dict[str, Any] = kwargs["architecture_data"]
        # Optional filters
        allowed_types: list[str] | None = kwargs.get("experiment_types")
        max_experiments: int = int(kwargs.get("max_experiments", 50))

        services = architecture_data.get("services", [])
        logger.info(
            "Chaos engineering: %d service(s) detected | backend=%s prometheus=%s grafana=%s",
            len(services),
            self.runner.backend,
            self.prometheus.url,
            self.grafana.url,
        )

        # Auto-detect Loki datasource UID if connected
        if self.grafana.is_available():
            self.grafana.find_loki_uid()

        # Plan → execute → report
        planned = self._plan_experiments(services, allowed_types)[:max_experiments]
        logger.info("%d experiment(s) planned", len(planned))

        results: list[ExperimentResult] = []
        for i, cfg in enumerate(planned, 1):
            logger.info("[%d/%d] %s on %s", i, len(planned), cfg.experiment_type.value, cfg.target_service)
            results.append(self._run_experiment(cfg))

        return build_report(
            experiments_planned=len(planned),
            results=results,
            backend=self.runner.backend,
            prometheus_available=self.prometheus.is_available(),
            grafana_available=self.grafana.is_available(),
        )

    # ── experiment planning ───────────────────────────────────────────────────

    def _plan_experiments(
        self,
        services: list[dict[str, Any]],
        allowed_types: list[str] | None,
    ) -> list[ExperimentConfig]:
        allowed: set[str] | None = set(allowed_types) if allowed_types else None
        experiments: list[ExperimentConfig] = []

        for svc in services:
            name: str = str(svc.get("name", "service"))
            ports: list = svc.get("exposed_ports", [])
            stack: str = str(svc.get("stack", "")).lower()
            deps: list = svc.get("dependencies", [])

            # Pod failure — universal
            if self._type_allowed(ExperimentType.POD_FAILURE, allowed):
                experiments.append(ExperimentConfig(
                    name=f"pod-failure-{name}",
                    experiment_type=ExperimentType.POD_FAILURE,
                    target_service=name,
                    description=f"Kill the container/pod for '{name}' and measure recovery time",
                    duration_seconds=self.experiment_duration,
                    observation_window_seconds=self.observation_window,
                    expected_outcome="Service restarts and recovers within 120s SLO",
                    tags=["pod-failure", name],
                ))

            # Network latency — services with exposed ports
            if ports and self._type_allowed(ExperimentType.NETWORK_LATENCY, allowed):
                experiments.append(ExperimentConfig(
                    name=f"network-latency-{name}",
                    experiment_type=ExperimentType.NETWORK_LATENCY,
                    target_service=name,
                    description=f"Inject 200ms ± 50ms latency on '{name}' network interface",
                    duration_seconds=self.experiment_duration,
                    observation_window_seconds=self.observation_window,
                    parameters={"delay_ms": 200, "jitter_ms": 50},
                    expected_outcome="Retry / back-off logic handles transient latency gracefully",
                    tags=["network-latency", name],
                ))

            # Network partition — services with many dependencies
            if len(deps) > 3 and self._type_allowed(ExperimentType.NETWORK_PARTITION, allowed):
                experiments.append(ExperimentConfig(
                    name=f"network-partition-{name}",
                    experiment_type=ExperimentType.NETWORK_PARTITION,
                    target_service=name,
                    description=f"Partition '{name}' from its network peers",
                    duration_seconds=min(self.experiment_duration, 30),
                    observation_window_seconds=self.observation_window,
                    expected_outcome="Circuit breaker opens; fallback response served",
                    slo_error_rate_threshold=0.10,  # relax SLO for full partition
                    tags=["network-partition", name],
                ))

            # Stack-specific resource stress tests
            stack_types = _STACK_EXPERIMENTS.get(stack, set())
            for exp_type in stack_types:
                if not self._type_allowed(exp_type, allowed):
                    continue
                if exp_type == ExperimentType.CPU_STRESS:
                    experiments.append(ExperimentConfig(
                        name=f"cpu-stress-{name}",
                        experiment_type=exp_type,
                        target_service=name,
                        description=f"Saturate CPU on '{name}' ({stack})",
                        duration_seconds=self.experiment_duration,
                        observation_window_seconds=self.observation_window,
                        parameters={"workers": 2},
                        expected_outcome="Service degrades gracefully; requests still served within relaxed latency",
                        slo_latency_p95_ms=1000.0,
                        tags=["cpu-stress", name, stack],
                    ))
                elif exp_type == ExperimentType.MEMORY_STRESS:
                    experiments.append(ExperimentConfig(
                        name=f"memory-stress-{name}",
                        experiment_type=exp_type,
                        target_service=name,
                        description=f"Apply memory pressure on '{name}'",
                        duration_seconds=self.experiment_duration,
                        observation_window_seconds=self.observation_window,
                        parameters={"vm_bytes": "70%"},
                        expected_outcome="OOM killer does not trigger; service stays responsive",
                        tags=["memory-stress", name],
                    ))
                elif exp_type == ExperimentType.DISK_STRESS:
                    experiments.append(ExperimentConfig(
                        name=f"disk-stress-{name}",
                        experiment_type=exp_type,
                        target_service=name,
                        description=f"Saturate disk I/O on '{name}'",
                        duration_seconds=self.experiment_duration,
                        observation_window_seconds=self.observation_window,
                        expected_outcome="Logging/DB writes degrade but service remains responsive",
                        tags=["disk-stress", name],
                    ))

        return experiments

    @staticmethod
    def _type_allowed(exp_type: ExperimentType, allowed: set[str] | None) -> bool:
        return allowed is None or exp_type.value in allowed

    # ── experiment execution ──────────────────────────────────────────────────

    def _run_experiment(self, cfg: ExperimentConfig) -> ExperimentResult:
        result = ExperimentResult(
            config=cfg,
            status=ExperimentStatus.RUNNING,
            start_time=time.time(),
        )

        try:
            # ── 1. Grafana annotation (start) ──
            ann_id = self.grafana.create_annotation(
                text=f"CHAOS START: {cfg.name}",
                tags=["chaos", cfg.experiment_type.value, cfg.target_service, "start"],
            )
            result.grafana_annotation_id = ann_id

            # ── 2. Baseline metrics ──
            logger.info("  [baseline] collecting metrics for %r", cfg.target_service)
            result.baseline_metrics = self.prometheus.collect_service_metrics(
                cfg.target_service, phase="baseline", experiment_type=cfg.experiment_type
            )

            # ── 3. Inject failure ──
            logger.info("  [inject] %s on %r", cfg.experiment_type.value, cfg.target_service)
            actions = self.runner.start_failure(cfg)
            result.actions_taken = actions
            for a in actions:
                logger.info("    action: %s", a)

            # ── 4. Mid-experiment metrics (halfway through) ──
            half = cfg.duration_seconds // 2
            self._sleep(half, f"waiting {half}s mid-experiment")
            result.during_metrics = self.prometheus.collect_service_metrics(
                cfg.target_service, phase="during", experiment_type=cfg.experiment_type
            )

            # Wait for the remaining experiment duration
            self._sleep(max(cfg.duration_seconds - half, 2), "remaining experiment window")

            # ── 5. Wait for recovery ──
            logger.info("  [recovery] waiting for %r to recover…", cfg.target_service)
            recovery_time = self.runner.wait_for_recovery(
                cfg, timeout=cfg.recovery_time_slo_seconds + 60
            )
            result.recovery_time_seconds = recovery_time

            # ── 6. Post-recovery observation window ──
            self._sleep(cfg.observation_window_seconds, "post-recovery observation")
            result.recovery_metrics = self.prometheus.collect_service_metrics(
                cfg.target_service, phase="recovery", experiment_type=cfg.experiment_type
            )

            # ── 7. Loki log collection ──
            if self.grafana.is_available():
                start_ns = int(result.start_time * 1e9)
                end_ns = int(time.time() * 1e9)
                logs = self.grafana.query_loki_logs(cfg.target_service, start_ns, end_ns)
                result.logs = [e["message"] for e in logs[:30]]
                # Also collect error-level entries
                errors = self.grafana.search_logs_for_errors(cfg.target_service, start_ns, end_ns)
                for line in errors:
                    if line not in result.logs:
                        result.logs.append(f"[ERROR] {line}")

            # ── 8. SLO evaluation ──
            result.slo_met, result.slo_violations = self._evaluate_slo(result)
            result.status = ExperimentStatus.COMPLETED

            # ── 9. Grafana annotation (end) ──
            if ann_id is not None:
                self.grafana.update_annotation(
                    ann_id,
                    text=(
                        f"CHAOS END: {cfg.name} | "
                        f"SLO {'MET' if result.slo_met else 'VIOLATED'} | "
                        f"recovery {result.recovery_time_seconds or '?'}s"
                    ),
                    tags=["chaos", cfg.experiment_type.value, cfg.target_service, "end"],
                )

        except Exception as exc:
            logger.exception("Experiment %r raised an exception", cfg.name)
            result.status = ExperimentStatus.FAILED
            result.error = str(exc)

        result.end_time = time.time()
        logger.info(
            "  [done] %s | status=%s slo=%s recovery=%ss",
            cfg.name, result.status.value, result.slo_met,
            f"{result.recovery_time_seconds:.1f}" if result.recovery_time_seconds else "?",
        )
        return result

    # ── SLO evaluation ────────────────────────────────────────────────────────

    def _evaluate_slo(self, result: ExperimentResult) -> tuple[bool, list[str]]:
        """Check all SLO dimensions and return (met, [violation messages])."""
        cfg = result.config
        violations: list[str] = []

        # Recovery time
        if result.recovery_time_seconds is not None:
            if result.recovery_time_seconds > cfg.recovery_time_slo_seconds:
                violations.append(
                    f"Recovery time {result.recovery_time_seconds:.0f}s "
                    f"exceeded SLO of {cfg.recovery_time_slo_seconds}s"
                )

        # Error rate during failure
        if result.during_metrics and result.during_metrics.error_rate is not None:
            err = result.during_metrics.error_rate
            if err > cfg.slo_error_rate_threshold:
                violations.append(
                    f"Error rate {err:.1%} exceeded SLO threshold {cfg.slo_error_rate_threshold:.1%}"
                )

        # P95 latency during failure
        if result.during_metrics and result.during_metrics.latency_p95_ms is not None:
            lat = result.during_metrics.latency_p95_ms
            if lat > cfg.slo_latency_p95_ms:
                violations.append(
                    f"P95 latency {lat:.0f}ms exceeded SLO of {cfg.slo_latency_p95_ms:.0f}ms"
                )

        for v in violations:
            logger.warning("  [SLO violation] %s: %s", cfg.name, v)

        return (len(violations) == 0), violations

    # ── helpers ───────────────────────────────────────────────────────────────

    def _sleep(self, seconds: int, reason: str = "") -> None:
        """Sleep for *seconds*, but only 1s in simulation mode for fast demos."""
        if self._sim_sleep:
            logger.debug("[SIMULATION] Skipping %ds sleep (%s)", seconds, reason)
            time.sleep(1)
        else:
            logger.debug("Sleeping %ds — %s", seconds, reason)
            time.sleep(seconds)


# ── factory from environment variables ───────────────────────────────────────

def _parse_container_map(raw: str) -> dict[str, str]:
    """Parse 'svc1=container1,svc2=container2' into a dict."""
    result: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" in pair:
            k, _, v = pair.partition("=")
            if k.strip() and v.strip():
                result[k.strip()] = v.strip()
    return result


def from_env() -> ChaosEngineeringAgent:
    """Construct a ChaosEngineeringAgent from environment variables."""
    raw_map = os.getenv("CHAOS_CONTAINER_MAP", "")
    return ChaosEngineeringAgent(
        prometheus_url=os.getenv("PROMETHEUS_URL", "http://localhost:9090"),
        grafana_url=os.getenv("GRAFANA_URL", "http://localhost:3000"),
        grafana_api_key=os.getenv("GRAFANA_API_KEY"),
        grafana_username=os.getenv("GRAFANA_USERNAME", "admin"),
        grafana_password=os.getenv("GRAFANA_PASSWORD", "admin"),
        loki_datasource_uid=os.getenv("LOKI_DATASOURCE_UID", "loki"),
        backend=os.getenv("CHAOS_BACKEND", "simulation"),
        dry_run=os.getenv("CHAOS_DRY_RUN", "").lower() in ("1", "true", "yes"),
        experiment_duration=int(os.getenv("CHAOS_EXPERIMENT_DURATION", "60")),
        observation_window=int(os.getenv("CHAOS_OBSERVATION_WINDOW", "30")),
        k8s_namespace=os.getenv("CHAOS_K8S_NAMESPACE", "default"),
        container_map=_parse_container_map(raw_map) if raw_map else None,
    )
