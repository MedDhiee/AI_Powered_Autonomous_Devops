from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .models import DependencyParseResult
from .utils import CommandRunner

LOGGER = logging.getLogger(__name__)


class DependencyParser:
    def __init__(self, runner: CommandRunner | None = None) -> None:
        self.runner = runner or CommandRunner()

    def parse(self, service_path: Path, stack: str) -> DependencyParseResult:
        if stack == "nodejs":
            return self._parse_nodejs(service_path)
        if stack == "python":
            return self._parse_python(service_path)
        if stack == "java-maven":
            return self._parse_maven(service_path)
        if stack == "java-gradle":
            return self._parse_gradle(service_path)
        return DependencyParseResult(
            command="",
            success=False,
            error=f"Unsupported stack: {stack}",
        )

    def _parse_nodejs(self, service_path: Path) -> DependencyParseResult:
        command = ["npm", "ls", "--json"]
        ok, stdout, stderr = self.runner.run(command, service_path)
        if not ok:
            fallback_deps = self._parse_nodejs_static(service_path)
            if fallback_deps:
                return DependencyParseResult(
                    command="npm ls --json (fallback: package.json)",
                    dependencies=sorted(fallback_deps),
                    raw_summary={"dependency_count": len(fallback_deps), "fallback": "package.json"},
                    success=True,
                    error=stderr.strip() or "npm unavailable; used package.json fallback",
                )
            return DependencyParseResult(
                command=" ".join(command),
                success=False,
                error=stderr.strip() or "npm dependency resolution failed",
            )

        try:
            data = json.loads(stdout or "{}")
        except json.JSONDecodeError as exc:
            return DependencyParseResult(
                command=" ".join(command),
                success=False,
                error=f"Invalid JSON from npm ls: {exc}",
            )

        deps = self._flatten_node_dependencies(data.get("dependencies", {}))
        return DependencyParseResult(
            command=" ".join(command),
            dependencies=sorted(deps),
            raw_summary={"dependency_count": len(deps)},
            success=True,
        )

    def _parse_python(self, service_path: Path) -> DependencyParseResult:
        command = ["pipdeptree", "--json"]
        ok, stdout, stderr = self.runner.run(command, service_path)
        if not ok:
            return DependencyParseResult(
                command=" ".join(command),
                success=False,
                error=stderr.strip()
                or "pipdeptree unavailable; install with `pip install pipdeptree`",
            )

        try:
            data = json.loads(stdout or "[]")
        except json.JSONDecodeError as exc:
            return DependencyParseResult(
                command=" ".join(command),
                success=False,
                error=f"Invalid JSON from pipdeptree: {exc}",
            )

        deps: set[str] = set()
        for entry in data:
            package = entry.get("package", {})
            package_name = package.get("package_name") or package.get("key")
            if package_name:
                deps.add(str(package_name))
            for child in entry.get("dependencies", []):
                child_name = child.get("package_name") or child.get("key")
                if child_name:
                    deps.add(str(child_name))

        return DependencyParseResult(
            command=" ".join(command),
            dependencies=sorted(deps),
            raw_summary={"dependency_count": len(deps)},
            success=True,
        )

    def _parse_maven(self, service_path: Path) -> DependencyParseResult:
        command = ["mvn", "dependency:tree", "-DoutputType=text"]
        ok, stdout, stderr = self.runner.run(command, service_path)
        if not ok:
            fallback_deps = self._parse_maven_static(service_path)
            if fallback_deps:
                return DependencyParseResult(
                    command="mvn dependency:tree -DoutputType=text (fallback: pom.xml)",
                    dependencies=sorted(fallback_deps),
                    raw_summary={"dependency_count": len(fallback_deps), "fallback": "pom.xml"},
                    success=True,
                    error=stderr.strip() or "mvn unavailable; used pom.xml fallback",
                )
            return DependencyParseResult(
                command=" ".join(command),
                success=False,
                error=stderr.strip() or "mvn dependency:tree failed",
            )

        deps = self._parse_maven_output(stdout)
        return DependencyParseResult(
            command=" ".join(command),
            dependencies=sorted(deps),
            raw_summary={"dependency_count": len(deps)},
            success=True,
        )

    def _parse_gradle(self, service_path: Path) -> DependencyParseResult:
        command = ["gradle", "dependencies"]
        ok, stdout, stderr = self.runner.run(command, service_path)
        if not ok:
            return DependencyParseResult(
                command=" ".join(command),
                success=False,
                error=stderr.strip() or "gradle dependencies failed",
            )

        deps = self._parse_gradle_output(stdout)
        return DependencyParseResult(
            command=" ".join(command),
            dependencies=sorted(deps),
            raw_summary={"dependency_count": len(deps)},
            success=True,
        )

    def _flatten_node_dependencies(self, dep_obj: dict) -> set[str]:
        flat: set[str] = set()

        def walk(node: dict[str, dict]) -> None:
            for dep_name, dep_data in node.items():
                flat.add(dep_name)
                nested = dep_data.get("dependencies", {}) if isinstance(dep_data, dict) else {}
                if isinstance(nested, dict):
                    walk(nested)

        walk(dep_obj)
        return flat

    def _parse_maven_output(self, output: str) -> set[str]:
        deps: set[str] = set()
        # Example: group:artifact:jar:version:scope
        pattern = re.compile(r"[+\\|\\\\- ]*([\w.-]+):([\w.-]+):[\w.-]+:[\w.-]+(?::[\w.-]+)?")
        for line in output.splitlines():
            match = pattern.search(line)
            if match:
                deps.add(f"{match.group(1)}:{match.group(2)}")
        return deps

    def _parse_gradle_output(self, output: str) -> set[str]:
        deps: set[str] = set()
        # Example lines often contain: group:artifact:version
        pattern = re.compile(r"([\w.-]+):([\w.-]+):([\w.-]+)")
        for line in output.splitlines():
            if "---" not in line and "+---" not in line and "\\---" not in line:
                continue
            match = pattern.search(line)
            if match:
                deps.add(f"{match.group(1)}:{match.group(2)}")
        return deps

    def _parse_nodejs_static(self, service_path: Path) -> set[str]:
        package_json = service_path / "package.json"
        if not package_json.exists():
            return set()

        try:
            data = json.loads(package_json.read_text(encoding="utf-8", errors="ignore"))
        except json.JSONDecodeError:
            return set()

        deps: set[str] = set()
        for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            section_deps = data.get(section, {})
            if isinstance(section_deps, dict):
                deps.update(section_deps.keys())
        return deps

    def _parse_maven_static(self, service_path: Path) -> set[str]:
        pom_xml = service_path / "pom.xml"
        if not pom_xml.exists():
            return set()

        text = pom_xml.read_text(encoding="utf-8", errors="ignore")
        # Extract dependency pairs from <dependency> blocks.
        block_pattern = re.compile(r"<dependency>(.*?)</dependency>", re.DOTALL)
        group_pattern = re.compile(r"<groupId>(.*?)</groupId>")
        artifact_pattern = re.compile(r"<artifactId>(.*?)</artifactId>")

        deps: set[str] = set()
        for block in block_pattern.findall(text):
            group_match = group_pattern.search(block)
            artifact_match = artifact_pattern.search(block)
            if group_match and artifact_match:
                group = group_match.group(1).strip()
                artifact = artifact_match.group(1).strip()
                if group and artifact:
                    deps.add(f"{group}:{artifact}")
        return deps
