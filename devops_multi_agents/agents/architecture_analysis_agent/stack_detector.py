from __future__ import annotations

from pathlib import Path

STACK_MARKERS: dict[str, list[str]] = {
    "nodejs": ["package.json"],
    "python": ["requirements.txt", "pyproject.toml"],
    "java-maven": ["pom.xml"],
    "java-gradle": ["build.gradle", "build.gradle.kts"],
}


class StackDetector:
    def detect(self, service_dir: Path) -> tuple[str, list[str]]:
        present_files: list[str] = []
        for markers in STACK_MARKERS.values():
            for marker in markers:
                if (service_dir / marker).is_file():
                    present_files.append(marker)

        # Priority avoids ambiguous classification when multiple marker files exist.
        if (service_dir / "package.json").is_file():
            return "nodejs", sorted(set(present_files))
        if (service_dir / "requirements.txt").is_file() or (service_dir / "pyproject.toml").is_file():
            return "python", sorted(set(present_files))
        if (service_dir / "pom.xml").is_file():
            return "java-maven", sorted(set(present_files))
        if (service_dir / "build.gradle").is_file() or (service_dir / "build.gradle.kts").is_file():
            return "java-gradle", sorted(set(present_files))

        return "unknown", sorted(set(present_files))
