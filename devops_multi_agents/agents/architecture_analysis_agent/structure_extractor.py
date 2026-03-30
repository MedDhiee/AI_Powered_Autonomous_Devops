from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

LOGGER = logging.getLogger(__name__)


class ServiceStructureExtractor:
    def extract_ports(self, service_path: Path, repository_path: Path, stack: str, service_name: str) -> list[int]:
        dockerfiles = self._candidate_dockerfiles(service_path, repository_path, stack, service_name)
        if not dockerfiles:
            return []

        ports: set[int] = set()
        for dockerfile in dockerfiles:
            ports.update(self._parse_exposed_ports(dockerfile))
        return sorted(ports)

    def _parse_exposed_ports(self, dockerfile: Path) -> set[int]:
        if not dockerfile.exists():
            return set()

        ports: set[int] = set()
        content = dockerfile.read_text(encoding="utf-8", errors="ignore")
        for line in content.splitlines():
            line = line.strip()
            if not line.upper().startswith("EXPOSE"):
                continue
            for token in line.split()[1:]:
                token = token.split("/")[0]
                if token.isdigit():
                    ports.add(int(token))
        return ports

    def _candidate_dockerfiles(self, service_path: Path, repository_path: Path, stack: str, service_name: str) -> list[Path]:
        candidates: list[Path] = []

        local_dockerfile = service_path / "Dockerfile"
        if local_dockerfile.exists():
            candidates.append(local_dockerfile)

        docker_root = repository_path / "Docker"
        if not docker_root.exists():
            return candidates

        # Explicit conventional mapping for split source/docker repositories.
        if stack == "java-maven" or "api" in service_name.lower() or "backend" in service_name.lower():
            backend_df = docker_root / "backend" / "Dockerfile"
            if backend_df.exists():
                candidates.append(backend_df)

        if stack == "nodejs" or "ui" in service_name.lower() or "front" in service_name.lower():
            frontend_df = docker_root / "frontend" / "Dockerfile"
            if frontend_df.exists():
                candidates.append(frontend_df)

        # Generic fallback by name hints only (not all Dockerfiles), to keep mapping precise.
        if not candidates:
            service_tokens = {
                token
                for token in re.split(r"[^a-zA-Z0-9]+", service_name.lower())
                if token and len(token) > 2
            }
            for path in docker_root.rglob("Dockerfile"):
                path_text = str(path).lower()
                if any(token in path_text for token in service_tokens):
                    candidates.append(path)

        unique_candidates: list[Path] = []
        seen: set[Path] = set()
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                unique_candidates.append(candidate)
        return unique_candidates

    def detect_database_connections(self, service_path: Path) -> list[str]:
        patterns = [
            re.compile(r"jdbc:[\w:./-]+", re.IGNORECASE),
            re.compile(r"mongodb(?:\+srv)?:\/\/[^\s\"']+", re.IGNORECASE),
            re.compile(r"postgres(?:ql)?:\/\/[^\s\"']+", re.IGNORECASE),
            re.compile(r"mysql:\/\/[^\s\"']+", re.IGNORECASE),
            re.compile(r"redis:\/\/[^\s\"']+", re.IGNORECASE),
            re.compile(r"DB_HOST\s*[:=]\s*[^\s\"']+", re.IGNORECASE),
            re.compile(r"DATABASE_URL\s*[:=]\s*[^\s\"']+", re.IGNORECASE),
        ]

        candidate_files = self._collect_candidate_files(service_path)
        matches: set[str] = set()

        for file_path in candidate_files:
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for pattern in patterns:
                for m in pattern.findall(text):
                    matches.add(str(m)[:200])

        return sorted(matches)

    def _collect_candidate_files(self, service_path: Path) -> list[Path]:
        wanted_names = {
            "application.yml",
            "application.yaml",
            "application.properties",
            ".env",
            "docker-compose.yml",
            "docker-compose.yaml",
            "settings.py",
            "config.py",
            "config.json",
            "package.json",
            "pom.xml",
        }
        wanted_suffixes = {".yml", ".yaml", ".properties", ".env", ".json", ".py", ".xml", ".js", ".ts"}

        files: list[Path] = []
        ignore_dirs = {
            ".git",
            "node_modules",
            "dist",
            "build",
            "target",
            "venv",
            ".venv",
            "__pycache__",
        }

        for dirpath_str, dirnames, filenames in os.walk(service_path, topdown=True):
            dirpath = Path(dirpath_str)
            dirnames[:] = [d for d in dirnames if d not in ignore_dirs]
            for name in filenames:
                path = dirpath / name
                if name in wanted_names or path.suffix.lower() in wanted_suffixes:
                    if path.stat().st_size <= 200_000:
                        files.append(path)

        LOGGER.debug("Database detection scanning %d files under %s", len(files), service_path)
        return files
