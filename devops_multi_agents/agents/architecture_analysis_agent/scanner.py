from __future__ import annotations

import logging
import os
from pathlib import Path

from .stack_detector import STACK_MARKERS

LOGGER = logging.getLogger(__name__)


class RepositoryScanner:
    def __init__(self) -> None:
        self._markers = {m for markers in STACK_MARKERS.values() for m in markers}
        self._ignore_dirs = {
            ".git",
            "node_modules",
            "dist",
            "build",
            "target",
            ".venv",
            "venv",
            "__pycache__",
        }

    def scan(self, root: Path) -> list[Path]:
        if not root.exists() or not root.is_dir():
            raise ValueError(f"Invalid repository path: {root}")

        service_dirs: set[Path] = set()

        for dirpath_str, dirnames, filenames in os.walk(root, topdown=True):
            dirpath = Path(dirpath_str)
            # Prune heavy/unimportant folders to reduce scan cost.
            dirnames[:] = [d for d in dirnames if d not in self._ignore_dirs]
            file_set = set(filenames)
            if file_set.intersection(self._markers):
                service_dirs.add(dirpath)

        # Prefer leaf-level services when nested markers exist.
        sorted_dirs = sorted(service_dirs, key=lambda p: len(p.parts), reverse=True)
        filtered: list[Path] = []
        for candidate in sorted_dirs:
            if not any(candidate in parent.parents for parent in filtered):
                filtered.append(candidate)

        result = sorted(filtered)
        LOGGER.info("Scanner identified %d service candidate(s)", len(result))
        return result
