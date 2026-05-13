from __future__ import annotations

import time
from typing import Any

from .experiment_types import ExperimentResult, ExperimentStatus, ExperimentType


# ── resilience scoring ────────────────────────────────────────────────────────

_WEIGHTS = {
    "slo_met": 40,
    "recovered": 30,
    "recovery_time": 20,
    "error_rate_during": 10,
}


def calculate_resilience_score(results: list[ExperimentResult]) -> dict[str, Any]:
    """Score system resilience from 0–100 based on completed experiment outcomes.

    Scoring breakdown (per experiment, then averaged):
    - 40 pts  SLO not violated during failure
    - 30 pts  Service recovered (is_up after experiment)
    - 20 pts  Recovery time within SLO
    - 10 pts  Error rate stayed below threshold during failure
    """
    completed = [r for r in results if r.status == ExperimentStatus.COMPLETED]
    if not completed:
        return {
            "score": 0,
            "grade": "N/A",
            "experiments_run": 0,
            "experiments_slo_met": 0,
            "experiments_recovered": 0,
            "details": "No experiments completed successfully",
        }

    total_pts = 0.0
    max_pts = len(completed) * 100.0

    for r in completed:
        pts = 0.0

        # SLO compliance
        if r.slo_met is True:
            pts += _WEIGHTS["slo_met"]

        # Recovery confirmed
        recovered = (r.recovery_metrics and r.recovery_metrics.is_up) or (
            r.recovery_time_seconds is not None
        )
        if recovered:
            pts += _WEIGHTS["recovered"]

        # Recovery time vs SLO
        if r.recovery_time_seconds is not None:
            slo_rt = r.config.recovery_time_slo_seconds
            if r.recovery_time_seconds <= slo_rt:
                pts += _WEIGHTS["recovery_time"]
            else:
                ratio = slo_rt / max(r.recovery_time_seconds, 1)
                pts += _WEIGHTS["recovery_time"] * min(ratio, 1.0)

        # Error rate during failure
        if r.during_metrics and r.during_metrics.error_rate is not None:
            threshold = r.config.slo_error_rate_threshold
            if r.during_metrics.error_rate <= threshold:
                pts += _WEIGHTS["error_rate_during"]
            else:
                ratio = threshold / max(r.during_metrics.error_rate, 0.001)
                pts += _WEIGHTS["error_rate_during"] * min(ratio, 1.0)

        total_pts += pts

    score = round((total_pts / max_pts) * 100) if max_pts > 0 else 0
    score = min(100, max(0, score))

    grade = "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F"

    slo_met_count = sum(1 for r in completed if r.slo_met is True)
    recovered_count = sum(
        1 for r in completed
        if (r.recovery_metrics and r.recovery_metrics.is_up) or r.recovery_time_seconds is not None
    )

    return {
        "score": score,
        "grade": grade,
        "experiments_run": len(completed),
        "experiments_slo_met": slo_met_count,
        "experiments_recovered": recovered_count,
        "details": f"{slo_met_count}/{len(completed)} experiments met SLO; "
                   f"{recovered_count}/{len(completed)} services recovered",
    }


# ── recommendations ───────────────────────────────────────────────────────────

def generate_recommendations(results: list[ExperimentResult]) -> list[str]:
    """Generate actionable recommendations from experiment outcomes."""
    recs: list[str] = []

    for r in results:
        if r.status != ExperimentStatus.COMPLETED:
            continue

        svc = r.config.target_service
        etype = r.config.experiment_type.value

        # SLO violated
        if r.slo_met is False:
            recs.append(
                f"[{svc}] SLO violated during '{etype}' — "
                "review circuit-breaker thresholds, retry budgets, and timeouts."
            )

        # Slow recovery
        if r.recovery_time_seconds is not None:
            slo_rt = r.config.recovery_time_slo_seconds
            if r.recovery_time_seconds > slo_rt:
                recs.append(
                    f"[{svc}] Recovery took {r.recovery_time_seconds:.0f}s "
                    f"(SLO: {slo_rt}s) — tune liveness/readiness probes or "
                    "increase replica count for faster failover."
                )

        # High error spike
        if r.during_metrics and r.during_metrics.error_rate is not None:
            err = r.during_metrics.error_rate
            if err > 0.20:
                recs.append(
                    f"[{svc}] Error rate reached {err:.0%} during '{etype}' — "
                    "implement bulkhead isolation and graceful degradation / fallback responses."
                )
            elif err > 0.05:
                recs.append(
                    f"[{svc}] Error rate elevated at {err:.0%} during '{etype}' — "
                    "consider adding a retry policy with exponential back-off."
                )

        # Latency tripled
        if (
            r.baseline_metrics
            and r.during_metrics
            and r.baseline_metrics.latency_p95_ms is not None
            and r.during_metrics.latency_p95_ms is not None
        ):
            b = r.baseline_metrics.latency_p95_ms
            d = r.during_metrics.latency_p95_ms
            if d > b * 3:
                recs.append(
                    f"[{svc}] P95 latency tripled during '{etype}' "
                    f"({b:.0f}ms → {d:.0f}ms) — add per-request timeouts and "
                    "consider async processing for non-critical paths."
                )

        # Experiment-type specific advice
        if r.config.experiment_type == ExperimentType.NETWORK_PARTITION and r.slo_met is False:
            recs.append(
                f"[{svc}] Failed network-partition test — implement a circuit breaker "
                "that opens on consecutive connection errors and returns a cached/fallback response."
            )

        if r.config.experiment_type == ExperimentType.CPU_STRESS and r.slo_met is False:
            recs.append(
                f"[{svc}] CPU saturation breaks SLO — set CPU resource limits in your "
                "container spec and configure horizontal pod autoscaler (HPA) with a CPU trigger."
            )

        if r.config.experiment_type == ExperimentType.MEMORY_STRESS and r.slo_met is False:
            recs.append(
                f"[{svc}] Memory pressure breaks SLO — set memory requests/limits, "
                "enable JVM GC tuning (if JVM-based), and add a VPA recommendation."
            )

    if not recs:
        recs.append(
            "System demonstrated strong resilience across all experiments — "
            "no critical SLO violations detected."
        )

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for r in recs:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique


# ── full report assembly ──────────────────────────────────────────────────────

def build_report(
    experiments_planned: int,
    results: list[ExperimentResult],
    backend: str,
    prometheus_available: bool,
    grafana_available: bool,
) -> dict[str, Any]:
    """Assemble the complete chaos engineering report."""
    resilience = calculate_resilience_score(results)
    recommendations = generate_recommendations(results)

    completed = [r for r in results if r.status == ExperimentStatus.COMPLETED]
    failed_exec = [r for r in results if r.status == ExperimentStatus.FAILED]

    recovery_times = [r.recovery_time_seconds for r in completed if r.recovery_time_seconds is not None]
    avg_recovery = round(sum(recovery_times) / len(recovery_times), 2) if recovery_times else None
    max_recovery = round(max(recovery_times), 2) if recovery_times else None

    # Per-experiment-type breakdown
    type_stats: dict[str, dict[str, Any]] = {}
    for r in completed:
        et = r.config.experiment_type.value
        if et not in type_stats:
            type_stats[et] = {"total": 0, "slo_met": 0, "avg_recovery_s": []}
        type_stats[et]["total"] += 1
        if r.slo_met:
            type_stats[et]["slo_met"] += 1
        if r.recovery_time_seconds is not None:
            type_stats[et]["avg_recovery_s"].append(r.recovery_time_seconds)

    for et, stats in type_stats.items():
        rts = stats.pop("avg_recovery_s")
        stats["avg_recovery_seconds"] = round(sum(rts) / len(rts), 2) if rts else None

    return {
        "protocol": "A2A",
        "summary": (
            f"Ran {len(completed)}/{experiments_planned} experiments — "
            f"resilience grade {resilience['grade']} ({resilience['score']}/100)"
        ),
        "run_metadata": {
            "experiments_planned": experiments_planned,
            "experiments_completed": len(completed),
            "experiments_failed_to_run": len(failed_exec),
            "avg_recovery_time_seconds": avg_recovery,
            "max_recovery_time_seconds": max_recovery,
            "backend": backend,
            "prometheus_connected": prometheus_available,
            "grafana_connected": grafana_available,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "resilience_score": resilience,
        "experiment_type_breakdown": type_stats,
        "experiments": [r.to_dict() for r in results],
        "recommendations": recommendations,
    }
