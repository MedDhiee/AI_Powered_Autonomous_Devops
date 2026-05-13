from __future__ import annotations

import logging
import subprocess
import threading
import time
from typing import Any

from .experiment_types import ExperimentConfig, ExperimentType

logger = logging.getLogger("chaos_engineering.runner")

_SUPPORTED_BACKENDS = frozenset({"docker", "kubernetes", "simulation"})


class ChaosRunner:
    """Executes chaos experiments against Docker containers or Kubernetes pods.

    Backends:
    - ``docker``      — uses Docker CLI (kill, stop, network, exec + tc/stress-ng)
    - ``kubernetes``  — uses kubectl CLI (delete pod, scale, exec)
    - ``simulation``  — dry-run: logs what would happen, returns immediately
    """

    def __init__(
        self,
        backend: str = "simulation",
        dry_run: bool = False,
        namespace: str = "default",
        container_map: dict[str, str] | None = None,
    ) -> None:
        if backend not in _SUPPORTED_BACKENDS:
            raise ValueError(f"backend must be one of {sorted(_SUPPORTED_BACKENDS)}, got {backend!r}")
        self.backend = backend
        self.dry_run = dry_run
        self.namespace = namespace
        self._explicit_map: dict[str, str] = container_map or {}
        self._container_cache: dict[str, str] = {}

    # ── public interface ──────────────────────────────────────────────────────

    def _resolve_container(self, name: str) -> str:
        """Map a service name to an actual Docker container name.

        Resolution order:
        1. Explicit container_map (passed at construction or via --container-map)
        2. Exact match — container with that exact name exists
        3. Prefix/substring fuzzy match against running containers
        4. Return original name unchanged (will fail with a clear Docker error)
        """
        if self.backend != "docker":
            return name
        if name in self._container_cache:
            return self._container_cache[name]
        if name in self._explicit_map:
            resolved = self._explicit_map[name]
            logger.info("Container map: %r -> %r", name, resolved)
            self._container_cache[name] = resolved
            return resolved

        # Exact check
        check = self._capture(f"docker inspect --format {{{{.Name}}}} {name}")
        if check.strip():
            self._container_cache[name] = name
            return name

        # Fuzzy match against all running containers
        all_names_raw = self._capture("docker ps --format {{{{.Names}}}}")
        all_names = [c.strip() for c in all_names_raw.splitlines() if c.strip()]
        # Prefer containers whose name starts with the service name
        for candidate in all_names:
            if candidate.startswith(name) or name in candidate:
                logger.info("Auto-resolved service %r to container %r", name, candidate)
                self._container_cache[name] = candidate
                return candidate

        logger.warning(
            "No running container found for service %r. "
            "Use --container-map %s=<actual-name> to set it explicitly. "
            "Running containers: %s",
            name, name, all_names,
        )
        self._container_cache[name] = name
        return name

    def start_failure(self, config: ExperimentConfig) -> list[str]:
        """Inject the failure defined by *config*.

        Returns a list of human-readable action descriptions.
        """
        t = config.experiment_type
        svc = config.target_service
        container = self._resolve_container(config.target_container or svc)
        params = config.parameters
        duration = config.duration_seconds

        dispatch: dict[ExperimentType, Any] = {
            ExperimentType.POD_FAILURE: lambda: self._pod_failure(container, duration),
            ExperimentType.NETWORK_LATENCY: lambda: self._network_latency(
                container,
                int(params.get("delay_ms", 200)),
                int(params.get("jitter_ms", 50)),
                duration,
            ),
            ExperimentType.NETWORK_PARTITION: lambda: self._network_partition(container, duration),
            ExperimentType.CPU_STRESS: lambda: self._cpu_stress(
                container, int(params.get("workers", 2)), duration
            ),
            ExperimentType.MEMORY_STRESS: lambda: self._memory_stress(
                container, str(params.get("vm_bytes", "70%")), duration
            ),
            ExperimentType.DISK_STRESS: lambda: self._disk_stress(container, duration),
            ExperimentType.SERVICE_UNAVAILABLE: lambda: self._service_unavailable(container, duration),
        }

        handler = dispatch.get(t)
        if handler is None:
            return [f"[UNKNOWN] Experiment type {t.value!r} has no handler"]
        action = handler()
        return [action]

    def wait_for_recovery(self, config: ExperimentConfig, timeout: int = 180) -> float | None:
        """Poll until the service reports healthy.

        Returns elapsed recovery time in seconds, or None if it timed out.
        """
        if self.backend == "simulation":
            # Simulate a realistic recovery time (15–90 s based on experiment type)
            simulated = {
                ExperimentType.POD_FAILURE: 35.0,
                ExperimentType.NETWORK_LATENCY: 5.0,
                ExperimentType.NETWORK_PARTITION: 8.0,
                ExperimentType.CPU_STRESS: 12.0,
                ExperimentType.MEMORY_STRESS: 20.0,
                ExperimentType.DISK_STRESS: 18.0,
                ExperimentType.SERVICE_UNAVAILABLE: 45.0,
            }.get(config.experiment_type, 30.0)
            logger.info("[SIMULATION] Simulated recovery time: %.1fs", simulated)
            return simulated

        container = self._resolve_container(config.target_container or config.target_service)
        start = time.time()
        while time.time() - start < timeout:
            if self._is_healthy(container):
                elapsed = round(time.time() - start, 2)
                logger.info("Service %r recovered in %.1fs", container, elapsed)
                return elapsed
            time.sleep(5)

        logger.warning("Service %r did not recover within %ds", container, timeout)
        return None

    # ── pod / container failure ───────────────────────────────────────────────

    def _pod_failure(self, container: str, duration: int) -> str:
        if self.backend == "docker":
            self._run(f"docker kill {container}")
            # Schedule restart after the experiment window so we can measure real recovery time
            self._schedule(duration, f"docker start {container}", f"Restart {container}")
            return (
                f"Sent SIGKILL to Docker container '{container}' "
                f"(auto-restart scheduled after {duration}s)"
            )

        if self.backend == "kubernetes":
            self._run(
                f"kubectl delete pod -l app={container} -n {self.namespace} --grace-period=0"
            )
            return f"Deleted Kubernetes pod (app={container}) in namespace '{self.namespace}'"

        return f"[SIMULATION] Would send SIGKILL to container/pod '{container}'"

    # ── network latency ───────────────────────────────────────────────────────

    def _network_latency(
        self, container: str, delay_ms: int, jitter_ms: int, duration: int
    ) -> str:
        add_tc = f"tc qdisc add dev eth0 root netem delay {delay_ms}ms {jitter_ms}ms"
        del_tc = "tc qdisc del dev eth0 root"

        if self.backend == "docker":
            self._run(f"docker exec {container} {add_tc}")
            self._schedule(duration, f"docker exec {container} {del_tc}", "Remove network latency")
            return (
                f"Injected {delay_ms}ms ± {jitter_ms}ms network latency on container '{container}' "
                f"for {duration}s (auto-cleanup scheduled)"
            )

        if self.backend == "kubernetes":
            base = f"kubectl exec -n {self.namespace} deployment/{container} --"
            self._run(f"{base} {add_tc}")
            self._schedule(duration, f"{base} {del_tc}", "Remove network latency (k8s)")
            return (
                f"Injected {delay_ms}ms ± {jitter_ms}ms latency on deployment '{container}' "
                f"in namespace '{self.namespace}' for {duration}s"
            )

        return (
            f"[SIMULATION] Would inject {delay_ms}ms ± {jitter_ms}ms latency "
            f"on '{container}' for {duration}s"
        )

    # ── network partition ─────────────────────────────────────────────────────

    def _network_partition(self, container: str, duration: int) -> str:
        if self.backend == "docker":
            raw = self._capture(
                f"docker inspect {container} "
                '--format "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}"'
            )
            networks = [n for n in raw.strip().split() if n]
            for net in networks:
                self._run(f"docker network disconnect {net} {container}")
                self._schedule(
                    duration,
                    f"docker network connect {net} {container}",
                    f"Reconnect {container} to {net}",
                )
            return (
                f"Disconnected container '{container}' from networks {networks} for {duration}s "
                "(auto-reconnect scheduled)"
            )

        if self.backend == "kubernetes":
            self._run(
                f"kubectl label pod -l app={container} -n {self.namespace} "
                "chaos=partition --overwrite"
            )
            return (
                f"[KUBERNETES] Applied chaos=partition label to pods (app={container}); "
                "apply a NetworkPolicy deny rule to complete the partition"
            )

        return f"[SIMULATION] Would network-partition container '{container}' for {duration}s"

    # ── CPU stress ────────────────────────────────────────────────────────────

    def _cpu_stress(self, container: str, workers: int, duration: int) -> str:
        stress_cmd = f"stress-ng --cpu {workers} --timeout {duration}s --metrics-brief"

        if self.backend == "docker":
            out = self._run(f"docker exec -d {container} {stress_cmd}")
            if out is None:
                return f"[FALLBACK] stress-ng not available in '{container}'"
            return f"Started CPU stress ({workers} workers, {duration}s) in container '{container}'"

        if self.backend == "kubernetes":
            self._run(
                f"kubectl exec -n {self.namespace} deployment/{container} "
                f"-- {stress_cmd}"
            )
            return (
                f"Started CPU stress ({workers} workers, {duration}s) "
                f"in deployment '{container}' (ns={self.namespace})"
            )

        return f"[SIMULATION] Would run CPU stress ({workers} workers, {duration}s) on '{container}'"

    # ── memory stress ─────────────────────────────────────────────────────────

    def _memory_stress(self, container: str, vm_bytes: str, duration: int) -> str:
        stress_cmd = f"stress-ng --vm 1 --vm-bytes {vm_bytes} --timeout {duration}s"

        if self.backend == "docker":
            self._run(f"docker exec -d {container} {stress_cmd}")
            return f"Started memory stress ({vm_bytes}) in container '{container}' for {duration}s"

        if self.backend == "kubernetes":
            self._run(
                f"kubectl exec -n {self.namespace} deployment/{container} "
                f"-- {stress_cmd}"
            )
            return (
                f"Started memory stress ({vm_bytes}) in deployment '{container}' "
                f"(ns={self.namespace}) for {duration}s"
            )

        return f"[SIMULATION] Would run memory stress ({vm_bytes}) on '{container}' for {duration}s"

    # ── disk stress ───────────────────────────────────────────────────────────

    def _disk_stress(self, container: str, duration: int) -> str:
        stress_cmd = f"stress-ng --hdd 1 --hdd-bytes 256m --timeout {duration}s"

        if self.backend == "docker":
            self._run(f"docker exec -d {container} {stress_cmd}")
            return f"Started disk I/O stress in container '{container}' for {duration}s"

        if self.backend == "kubernetes":
            self._run(
                f"kubectl exec -n {self.namespace} deployment/{container} "
                f"-- {stress_cmd}"
            )
            return f"Started disk I/O stress in deployment '{container}' for {duration}s"

        return f"[SIMULATION] Would run disk I/O stress on '{container}' for {duration}s"

    # ── service unavailability ────────────────────────────────────────────────

    def _service_unavailable(self, container: str, duration: int) -> str:
        if self.backend == "docker":
            self._run(f"docker stop {container}")
            self._schedule(duration, f"docker start {container}", f"Restart {container}")
            return f"Stopped container '{container}' for {duration}s (auto-start scheduled)"

        if self.backend == "kubernetes":
            self._run(
                f"kubectl scale deployment/{container} --replicas=0 -n {self.namespace}"
            )
            self._schedule(
                duration,
                f"kubectl scale deployment/{container} --replicas=1 -n {self.namespace}",
                f"Scale up {container}",
            )
            return (
                f"Scaled deployment '{container}' to 0 replicas for {duration}s "
                "(auto scale-up scheduled)"
            )

        return f"[SIMULATION] Would make service '{container}' unavailable for {duration}s"

    # ── health check ──────────────────────────────────────────────────────────

    def _is_healthy(self, container: str) -> bool:
        if self.backend == "docker":
            # Use double quotes so cmd.exe (shell=True on Windows) strips them correctly
            out = self._capture(
                f'docker inspect -f "{{{{.State.Running}}}}" {container}'
            )
            return out.strip() == "true"

        if self.backend == "kubernetes":
            out = self._capture(
                f"kubectl get pods -l app={container} -n {self.namespace} "
                "--field-selector=status.phase=Running --no-headers"
            )
            return bool(out.strip())

        return True

    # ── shell helpers ─────────────────────────────────────────────────────────

    def _run(self, cmd: str, description: str = "") -> str | None:
        """Run a shell command, returning stdout or None on error."""
        if self.dry_run:
            logger.info("[DRY-RUN] %s", cmd)
            return ""
        label = description or cmd[:80]
        logger.info("Executing: %s", label)
        try:
            proc = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=30
            )
            if proc.returncode != 0:
                logger.warning(
                    "Command exited %d: %s | stderr: %s",
                    proc.returncode, cmd[:80], proc.stderr.strip()[:200],
                )
                return None
            return proc.stdout.strip()
        except subprocess.TimeoutExpired:
            logger.error("Command timed out (30s): %s", cmd[:80])
            return None
        except Exception as exc:
            logger.error("Command error: %s | %s", cmd[:80], exc)
            return None

    def _capture(self, cmd: str) -> str:
        """Run a command and return stdout (empty string on any error)."""
        try:
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            return proc.stdout
        except Exception:
            return ""

    def _schedule(self, delay: int, cmd: str, description: str = "") -> None:
        """Run *cmd* in a background thread after *delay* seconds."""
        def _worker() -> None:
            time.sleep(delay)
            self._run(cmd, description)

        t = threading.Thread(target=_worker, daemon=True, name=f"chaos-cleanup-{description[:20]}")
        t.start()
