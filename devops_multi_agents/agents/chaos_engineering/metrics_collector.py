from __future__ import annotations

import logging
import time
from typing import Any

import requests

from .experiment_types import ExperimentType, MetricsSnapshot, synthetic_metrics

logger = logging.getLogger("chaos_engineering.prometheus")

# PromQL templates — %s is replaced with the service/job label value
_QUERIES: dict[str, str] = {
    "error_rate": (
        'rate(http_requests_total{{job="{svc}",status=~"5.."}}[1m]) / '
        'clamp_min(rate(http_requests_total{{job="{svc}"}}[1m]), 0.001)'
    ),
    "latency_p50": (
        'histogram_quantile(0.50, rate(http_request_duration_seconds_bucket{{job="{svc}"}}[1m]))'
    ),
    "latency_p95": (
        'histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{{job="{svc}"}}[1m]))'
    ),
    "latency_p99": (
        'histogram_quantile(0.99, rate(http_request_duration_seconds_bucket{{job="{svc}"}}[1m]))'
    ),
    "throughput": 'rate(http_requests_total{{job="{svc}"}}[1m])',
    "cpu": (
        'sum(rate(container_cpu_usage_seconds_total{{container="{svc}"}}[1m])) * 100'
    ),
    "memory": 'container_memory_usage_bytes{{container="{svc}"}}',
    "up": 'up{{job="{svc}"}}',
}


class PrometheusClient:
    """Thin wrapper around the Prometheus HTTP API for instant and range queries."""

    def __init__(self, url: str = "http://localhost:9090", timeout: int = 10) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._available: bool | None = None

    # ── availability ──────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            resp = requests.get(f"{self.url}/-/healthy", timeout=5)
            self._available = resp.status_code == 200
        except Exception:
            self._available = False
        if not self._available:
            logger.warning("Prometheus not reachable at %s — synthetic metrics will be used", self.url)
        return self._available

    # ── query helpers ─────────────────────────────────────────────────────────

    def query(self, promql: str) -> float | None:
        """Instant PromQL query → first scalar result, or None on error."""
        try:
            resp = requests.get(
                f"{self.url}/api/v1/query",
                params={"query": promql},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            results = resp.json().get("data", {}).get("result", [])
            if results:
                return float(results[0]["value"][1])
        except Exception as exc:
            logger.debug("Prometheus instant query failed — %s | %s", promql[:80], exc)
        return None

    def query_range(
        self,
        promql: str,
        start: float,
        end: float,
        step: str = "15s",
    ) -> list[tuple[float, float]]:
        """Range PromQL query → list of (timestamp, value) pairs."""
        try:
            resp = requests.get(
                f"{self.url}/api/v1/query_range",
                params={"query": promql, "start": start, "end": end, "step": step},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            results = resp.json().get("data", {}).get("result", [])
            if results:
                return [(float(v[0]), float(v[1])) for v in results[0].get("values", [])]
        except Exception as exc:
            logger.debug("Prometheus range query failed — %s | %s", promql[:80], exc)
        return []

    # ── metric snapshots ──────────────────────────────────────────────────────

    def collect_service_metrics(
        self,
        service: str,
        phase: str,
        experiment_type: ExperimentType | None = None,
    ) -> MetricsSnapshot:
        """Collect a full metrics snapshot for *service* at the given *phase*.

        Falls back to synthetic data when Prometheus is unreachable.
        """
        if not self.is_available():
            if experiment_type is not None:
                return synthetic_metrics(service, phase, experiment_type)
            return MetricsSnapshot(timestamp=time.time(), service=service, phase=phase, source="synthetic")

        snap = MetricsSnapshot(
            timestamp=time.time(),
            service=service,
            phase=phase,
            source="prometheus",
        )

        def _q(key: str) -> float | None:
            return self.query(_QUERIES[key].format(svc=service))

        error_rate = _q("error_rate")
        snap.error_rate = round(error_rate, 4) if error_rate is not None else None

        for key, attr in [
            ("latency_p50", "latency_p50_ms"),
            ("latency_p95", "latency_p95_ms"),
            ("latency_p99", "latency_p99_ms"),
        ]:
            val = _q(key)
            if val is not None:
                setattr(snap, attr, round(val * 1000, 2))  # seconds → ms

        throughput = _q("throughput")
        snap.throughput_rps = round(throughput, 2) if throughput is not None else None

        cpu = _q("cpu")
        snap.cpu_usage_percent = round(cpu, 2) if cpu is not None else None

        mem = _q("memory")
        snap.memory_usage_mb = round(mem / (1024 * 1024), 2) if mem is not None else None

        up = _q("up")
        snap.is_up = (up == 1.0) if up is not None else None

        logger.debug(
            "[%s][%s] err=%.3f p95=%.1fms rps=%.1f cpu=%.1f%% up=%s",
            phase, service,
            snap.error_rate or 0.0,
            snap.latency_p95_ms or 0.0,
            snap.throughput_rps or 0.0,
            snap.cpu_usage_percent or 0.0,
            snap.is_up,
        )
        return snap

    def collect_range_metrics(
        self,
        service: str,
        start: float,
        end: float,
        step: str = "15s",
    ) -> dict[str, list[tuple[float, float]]]:
        """Collect time-series data for a service over a time range."""
        if not self.is_available():
            return {}
        result: dict[str, list[tuple[float, float]]] = {}
        for key in ("error_rate", "latency_p95", "throughput", "cpu"):
            series = self.query_range(_QUERIES[key].format(svc=service), start, end, step)
            if series:
                result[key] = series
        return result
