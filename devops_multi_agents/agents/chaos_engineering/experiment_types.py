from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ExperimentType(str, Enum):
    POD_FAILURE = "pod-failure"
    NETWORK_LATENCY = "network-latency"
    NETWORK_PARTITION = "network-partition"
    CPU_STRESS = "cpu-stress"
    MEMORY_STRESS = "memory-stress"
    DISK_STRESS = "disk-stress"
    SERVICE_UNAVAILABLE = "service-unavailable"


class ExperimentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ExperimentConfig:
    name: str
    experiment_type: ExperimentType
    target_service: str
    description: str = ""
    target_container: str | None = None
    target_namespace: str = "default"
    duration_seconds: int = 60
    observation_window_seconds: int = 30
    parameters: dict[str, Any] = field(default_factory=dict)
    expected_outcome: str = ""
    # SLO thresholds
    slo_error_rate_threshold: float = 0.05       # 5% max error rate during failure
    slo_latency_p95_ms: float = 500.0            # 500ms max p95 latency
    recovery_time_slo_seconds: int = 120         # 2 min max recovery time
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.experiment_type.value,
            "target": self.target_service,
            "description": self.description,
            "duration_seconds": self.duration_seconds,
            "parameters": self.parameters,
            "expected_outcome": self.expected_outcome,
            "slo": {
                "error_rate_threshold": self.slo_error_rate_threshold,
                "latency_p95_ms": self.slo_latency_p95_ms,
                "recovery_time_seconds": self.recovery_time_slo_seconds,
            },
            "tags": self.tags,
        }


@dataclass
class MetricsSnapshot:
    timestamp: float
    service: str
    phase: str  # "baseline" | "during" | "recovery"
    error_rate: float | None = None
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    latency_p99_ms: float | None = None
    throughput_rps: float | None = None
    cpu_usage_percent: float | None = None
    memory_usage_mb: float | None = None
    is_up: bool | None = None
    source: str = "prometheus"  # "prometheus" | "synthetic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "service": self.service,
            "phase": self.phase,
            "source": self.source,
            "error_rate": self.error_rate,
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "latency_p99_ms": self.latency_p99_ms,
            "throughput_rps": self.throughput_rps,
            "cpu_usage_percent": self.cpu_usage_percent,
            "memory_usage_mb": self.memory_usage_mb,
            "is_up": self.is_up,
        }


@dataclass
class ExperimentResult:
    config: ExperimentConfig
    status: ExperimentStatus
    start_time: float
    end_time: float = 0.0
    baseline_metrics: MetricsSnapshot | None = None
    during_metrics: MetricsSnapshot | None = None
    recovery_metrics: MetricsSnapshot | None = None
    recovery_time_seconds: float | None = None
    slo_met: bool | None = None
    slo_violations: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    grafana_annotation_id: int | None = None
    error: str | None = None
    actions_taken: list[str] = field(default_factory=list)

    def duration_seconds(self) -> float:
        return round(self.end_time - self.start_time, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.config.name,
            "type": self.config.experiment_type.value,
            "target": self.config.target_service,
            "description": self.config.description,
            "status": self.status.value,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "total_duration_seconds": self.duration_seconds(),
            "actions_taken": self.actions_taken,
            "metrics": {
                "baseline": self.baseline_metrics.to_dict() if self.baseline_metrics else None,
                "during_failure": self.during_metrics.to_dict() if self.during_metrics else None,
                "recovery": self.recovery_metrics.to_dict() if self.recovery_metrics else None,
            },
            "recovery_time_seconds": self.recovery_time_seconds,
            "slo_met": self.slo_met,
            "slo_violations": self.slo_violations,
            "logs_sample": self.logs[-20:],
            "error": self.error,
        }


def synthetic_metrics(
    service: str,
    phase: str,
    experiment_type: ExperimentType,
) -> MetricsSnapshot:
    """Generate realistic synthetic metrics when Prometheus is unavailable."""
    import random

    rng = random.Random(hash((service, phase, experiment_type.value)) % (2**32))

    base_error = 0.01
    base_latency_p95 = 48.0 + rng.uniform(-5, 5)
    base_latency_p50 = 25.0 + rng.uniform(-3, 3)
    base_throughput = 120.0 + rng.uniform(-20, 20)
    base_cpu = 18.0 + rng.uniform(-5, 5)
    base_memory = 256.0 + rng.uniform(-30, 30)

    if phase == "baseline":
        return MetricsSnapshot(
            timestamp=time.time(),
            service=service,
            phase=phase,
            source="synthetic",
            error_rate=base_error,
            latency_p50_ms=round(base_latency_p50, 1),
            latency_p95_ms=round(base_latency_p95, 1),
            latency_p99_ms=round(base_latency_p95 * 1.8, 1),
            throughput_rps=round(base_throughput, 1),
            cpu_usage_percent=round(base_cpu, 1),
            memory_usage_mb=round(base_memory, 1),
            is_up=True,
        )

    if phase == "during":
        if experiment_type == ExperimentType.POD_FAILURE:
            return MetricsSnapshot(
                timestamp=time.time(), service=service, phase=phase, source="synthetic",
                error_rate=0.42, latency_p50_ms=None, latency_p95_ms=None,
                latency_p99_ms=None, throughput_rps=0.0, cpu_usage_percent=0.0,
                memory_usage_mb=0.0, is_up=False,
            )
        if experiment_type == ExperimentType.NETWORK_LATENCY:
            delay = 200.0
            return MetricsSnapshot(
                timestamp=time.time(), service=service, phase=phase, source="synthetic",
                error_rate=round(base_error * 2, 3),
                latency_p50_ms=round(base_latency_p50 + delay, 1),
                latency_p95_ms=round(base_latency_p95 + delay + 50, 1),
                latency_p99_ms=round(base_latency_p95 + delay + 120, 1),
                throughput_rps=round(base_throughput * 0.75, 1),
                cpu_usage_percent=round(base_cpu * 1.1, 1),
                memory_usage_mb=round(base_memory, 1),
                is_up=True,
            )
        if experiment_type == ExperimentType.NETWORK_PARTITION:
            return MetricsSnapshot(
                timestamp=time.time(), service=service, phase=phase, source="synthetic",
                error_rate=0.98, latency_p50_ms=None, latency_p95_ms=None,
                latency_p99_ms=None, throughput_rps=0.0,
                cpu_usage_percent=round(base_cpu * 0.5, 1),
                memory_usage_mb=round(base_memory, 1), is_up=False,
            )
        if experiment_type == ExperimentType.CPU_STRESS:
            return MetricsSnapshot(
                timestamp=time.time(), service=service, phase=phase, source="synthetic",
                error_rate=round(base_error * 5, 3),
                latency_p50_ms=round(base_latency_p50 * 6, 1),
                latency_p95_ms=round(base_latency_p95 * 8, 1),
                latency_p99_ms=round(base_latency_p95 * 12, 1),
                throughput_rps=round(base_throughput * 0.4, 1),
                cpu_usage_percent=96.0 + rng.uniform(0, 3),
                memory_usage_mb=round(base_memory, 1), is_up=True,
            )
        if experiment_type == ExperimentType.MEMORY_STRESS:
            return MetricsSnapshot(
                timestamp=time.time(), service=service, phase=phase, source="synthetic",
                error_rate=round(base_error * 3, 3),
                latency_p50_ms=round(base_latency_p50 * 3, 1),
                latency_p95_ms=round(base_latency_p95 * 4, 1),
                latency_p99_ms=round(base_latency_p95 * 6, 1),
                throughput_rps=round(base_throughput * 0.6, 1),
                cpu_usage_percent=round(base_cpu * 1.3, 1),
                memory_usage_mb=round(base_memory * 3.5, 1), is_up=True,
            )
        # disk stress or service unavailable
        return MetricsSnapshot(
            timestamp=time.time(), service=service, phase=phase, source="synthetic",
            error_rate=round(base_error * 4, 3),
            latency_p50_ms=round(base_latency_p50 * 4, 1),
            latency_p95_ms=round(base_latency_p95 * 5, 1),
            latency_p99_ms=round(base_latency_p95 * 7, 1),
            throughput_rps=round(base_throughput * 0.5, 1),
            cpu_usage_percent=round(base_cpu * 1.2, 1),
            memory_usage_mb=round(base_memory * 1.1, 1), is_up=True,
        )

    # recovery phase
    return MetricsSnapshot(
        timestamp=time.time(), service=service, phase=phase, source="synthetic",
        error_rate=round(base_error * 1.4, 3),
        latency_p50_ms=round(base_latency_p50 * 1.15, 1),
        latency_p95_ms=round(base_latency_p95 * 1.2, 1),
        latency_p99_ms=round(base_latency_p95 * 1.6, 1),
        throughput_rps=round(base_throughput * 0.92, 1),
        cpu_usage_percent=round(base_cpu * 1.05, 1),
        memory_usage_mb=round(base_memory * 1.02, 1), is_up=True,
    )
