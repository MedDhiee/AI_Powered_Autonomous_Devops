from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

LOGGER = logging.getLogger(__name__)

TRIVY_IMAGE = os.getenv("DEVSECOPS_TRIVY_IMAGE", "aquasec/trivy:0.57.1")
GITLEAKS_IMAGE = os.getenv("DEVSECOPS_GITLEAKS_IMAGE", "zricethezav/gitleaks:v8.24.2")
CHECKOV_IMAGE = os.getenv("DEVSECOPS_CHECKOV_IMAGE", "bridgecrew/checkov:3.2.368")
TRIVY_RETRIES = int(os.getenv("DEVSECOPS_TRIVY_RETRIES", "2"))
TRIVY_RETRY_DELAY_SEC = float(os.getenv("DEVSECOPS_TRIVY_RETRY_DELAY_SEC", "5"))
TRIVY_SKIP_DB_UPDATE = os.getenv("DEVSECOPS_TRIVY_SKIP_DB_UPDATE", "false").lower() in {
    "1",
    "true",
    "yes",
}
TRIVY_CACHE_DIR = Path(os.getenv("DEVSECOPS_TRIVY_CACHE_DIR", str(Path.cwd() / ".trivy-cache")))
TRIVY_TIMEOUT = os.getenv("DEVSECOPS_TRIVY_TIMEOUT", "20m")
TRIVY_DB_REPOSITORIES = [
    item.strip()
    for item in os.getenv(
        "DEVSECOPS_TRIVY_DB_REPOSITORIES",
        "ghcr.io/aquasecurity/trivy-db,public.ecr.aws/aquasecurity/trivy-db",
    ).split(",")
    if item.strip()
]

REQUIRED_TOOL_IMAGES = [TRIVY_IMAGE, GITLEAKS_IMAGE, CHECKOV_IMAGE]


class SecurityToolRunner:
    def precheck_environment(self) -> dict[str, object]:
        daemon_ok, daemon_msg = self._check_docker_daemon()
        image_status: dict[str, bool] = {}
        missing_images: list[str] = []

        if daemon_ok:
            for image in REQUIRED_TOOL_IMAGES:
                installed = self._check_image_installed(image)
                image_status[image] = installed
                if not installed:
                    missing_images.append(image)
        else:
            # If daemon is down, image checks are not actionable.
            for image in REQUIRED_TOOL_IMAGES:
                image_status[image] = False

        ok = daemon_ok and not missing_images
        messages: list[str] = []
        if not daemon_ok:
            messages.append(
                "Docker daemon is not running. Start Docker Desktop and retry."
            )
            if daemon_msg:
                messages.append(daemon_msg)
        if missing_images:
            messages.append(
                "Required security tool images are not installed locally: "
                + ", ".join(missing_images)
            )
            messages.append(
                "Install them with: "
                + "; ".join(f"docker pull {image}" for image in REQUIRED_TOOL_IMAGES)
            )

        return {
            "ok": ok,
            "docker_daemon_running": daemon_ok,
            "image_status": image_status,
            "missing_images": missing_images,
            "messages": messages,
        }

    def run_trivy_fs(self, repo_path: Path) -> tuple[bool, str, str]:
        TRIVY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        base_command = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{repo_path.resolve()}:/repo",
            "-v",
            f"{TRIVY_CACHE_DIR.resolve()}:/root/.cache/trivy",
            TRIVY_IMAGE,
            "fs",
            "/repo",
            "--format",
            "json",
            "--quiet",
            "--timeout",
            TRIVY_TIMEOUT,
        ]
        return self._run_trivy_with_db_fallback(base_command)

    def run_trivy_image(self, image_ref: str) -> tuple[bool, str, str]:
        TRIVY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        base_command = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{TRIVY_CACHE_DIR.resolve()}:/root/.cache/trivy",
            TRIVY_IMAGE,
            "image",
            "--format",
            "json",
            "--quiet",
            "--timeout",
            TRIVY_TIMEOUT,
            image_ref,
        ]
        return self._run_trivy_with_db_fallback(base_command)

    def _run_trivy_with_db_fallback(self, base_command: list[str]) -> tuple[bool, str, str]:
        retry_on = ("failed to download vulnerability DB", "context deadline exceeded")
        repositories = TRIVY_DB_REPOSITORIES or ["ghcr.io/aquasecurity/trivy-db"]

        if TRIVY_SKIP_DB_UPDATE:
            command = [*base_command, "--skip-db-update"]
            return self._run_with_retry(
                command,
                retries=max(TRIVY_RETRIES, 0),
                delay_seconds=max(TRIVY_RETRY_DELAY_SEC, 0.0),
                retry_on=retry_on,
            )

        last_result: tuple[bool, str, str] = (False, "", "")
        for repo in repositories:
            command = [*base_command, "--db-repository", repo]
            ok, stdout, stderr = self._run_with_retry(
                command,
                retries=max(TRIVY_RETRIES, 0),
                delay_seconds=max(TRIVY_RETRY_DELAY_SEC, 0.0),
                retry_on=retry_on,
            )
            if ok:
                return ok, stdout, stderr
            last_result = (ok, stdout, stderr)
            LOGGER.warning("Trivy failed with DB repository '%s', trying next repository", repo)

        return last_result

    def run_gitleaks(self, repo_path: Path) -> tuple[bool, str, str]:
        report_name = ".gitleaks-report.json"
        report_path = repo_path / report_name
        if report_path.exists():
            report_path.unlink()

        command = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{repo_path.resolve()}:/repo",
            GITLEAKS_IMAGE,
            "detect",
            "--source",
            "/repo",
            "--report-format",
            "json",
            "--report-path",
            f"/repo/{report_name}",
            "--no-banner",
        ]

        ok, stdout, stderr = self._run(command)
        # gitleaks often exits non-zero when leaks are found; still parse report.
        if report_path.exists():
            report_content = report_path.read_text(encoding="utf-8", errors="ignore")
            report_path.unlink(missing_ok=True)
            return True, report_content, stderr
        return ok, stdout, stderr

    def run_checkov(self, repo_path: Path) -> tuple[bool, str, str]:
        command = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{repo_path.resolve()}:/repo",
            CHECKOV_IMAGE,
            "-d",
            "/repo",
            "-o",
            "json",
            "--soft-fail",
        ]
        return self._run(command)

    def _run(self, command: list[str]) -> tuple[bool, str, str]:
        try:
            proc = subprocess.run(command, capture_output=True, text=False, shell=False)
            stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
            stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
            return proc.returncode == 0, stdout, stderr
        except FileNotFoundError:
            return False, "", "Command not found: docker"
        except Exception as exc:
            LOGGER.exception("Tool command failed")
            return False, "", str(exc)

    def _run_with_retry(
        self,
        command: list[str],
        retries: int,
        delay_seconds: float,
        retry_on: tuple[str, ...],
    ) -> tuple[bool, str, str]:
        attempts = retries + 1
        last_result: tuple[bool, str, str] = (False, "", "")
        for attempt in range(1, attempts + 1):
            ok, stdout, stderr = self._run(command)
            last_result = (ok, stdout, stderr)
            if ok:
                return last_result

            blob = f"{stdout}\n{stderr}".lower()
            should_retry = any(token.lower() in blob for token in retry_on)
            if not should_retry or attempt == attempts:
                return last_result

            LOGGER.warning(
                "Retrying command after transient error (attempt %d/%d): %s",
                attempt,
                attempts,
                "; ".join(retry_on),
            )
            if delay_seconds > 0:
                time.sleep(delay_seconds)

        return last_result

    def _check_docker_daemon(self) -> tuple[bool, str]:
        ok, stdout, stderr = self._run(["docker", "info"])
        if ok:
            return True, ""
        return False, (stderr.strip() or stdout.strip() or "Unable to query Docker daemon")

    def _check_image_installed(self, image_ref: str) -> bool:
        ok, _, _ = self._run(["docker", "image", "inspect", image_ref])
        return ok

    def parse_json(self, payload: str | None) -> dict | list | None:
        if payload is None:
            return None
        payload = payload.strip()
        if not payload:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            # Some tools prepend logs before JSON payload.
            starts = [idx for idx in (payload.find("{"), payload.find("[")) if idx >= 0]
            if starts:
                start = min(starts)
                try:
                    return json.loads(payload[start:])
                except json.JSONDecodeError:
                    return None
        return None
