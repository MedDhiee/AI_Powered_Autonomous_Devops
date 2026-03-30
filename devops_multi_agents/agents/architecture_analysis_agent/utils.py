from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

LOGGER = logging.getLogger(__name__)


class CommandRunner:
    def __init__(self, timeout_seconds: int = 120) -> None:
        self.timeout_seconds = timeout_seconds

    def command_exists(self, command: str) -> bool:
        return shutil.which(command) is not None

    def run(self, command: list[str], cwd: Path) -> tuple[bool, str, str]:
        try:
            proc = subprocess.run(
                command,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                shell=False,
            )
            return proc.returncode == 0, proc.stdout, proc.stderr
        except FileNotFoundError:
            return False, "", f"Command not found: {command[0]}"
        except subprocess.TimeoutExpired:
            return False, "", f"Command timed out after {self.timeout_seconds}s"
        except Exception as exc:  # Defensive guard for unexpected platform/runtime issues.
            LOGGER.exception("Unexpected error while running command %s", command)
            return False, "", str(exc)
