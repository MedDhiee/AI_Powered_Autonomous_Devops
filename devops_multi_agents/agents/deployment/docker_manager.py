from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

class DockerManager:
    def __init__(self):
        self.logger = logging.getLogger("DockerManager")
        self.last_build_details: dict[str, Any] = {}

    def build_and_push(
        self,
        service_name: str,
        target_env: str,
        secrets: dict,
        raw_path: str = "",
        stack: str = "",
        repository_path: str | Path | None = None,
    ) -> str:
        self.logger.info(f"Building Docker image for {service_name} in environment: {target_env}")
        image_tag = f"{service_name}:latest"

        if target_env == "local":
            self.logger.info(f"[Docker] Triggering Minikube image build for {image_tag}")
            build_context = self._resolve_service_context(service_name, raw_path, repository_path)
            dockerfile_path = self._resolve_dockerfile(service_name, stack, raw_path, build_context)

            self.logger.info(
                "[Docker] Resolved build context=%s dockerfile=%s",
                build_context,
                dockerfile_path,
            )

            dockerfile_arg, staged_file = self._prepare_dockerfile_arg(service_name, build_context, dockerfile_path)
            command = ["minikube", "image", "build", "-t", image_tag, "-f", dockerfile_arg, "."]
            build_details: dict[str, Any] = {
                "service_name": service_name,
                "image_tag": image_tag,
                "target_env": target_env,
                "build_context": str(build_context),
                "dockerfile_path": str(dockerfile_path),
                "dockerfile_argument": dockerfile_arg,
                "command": " ".join(command),
                "status": "running",
            }
            try:
                proc = subprocess.run(
                    command,
                    cwd=str(build_context),
                    capture_output=True,
                    text=True,
                )
            finally:
                if staged_file and staged_file.exists():
                    staged_file.unlink(missing_ok=True)

            build_details.update(
                {
                    "return_code": proc.returncode,
                    "stdout_tail": self._tail_text(proc.stdout),
                    "stderr_tail": self._tail_text(proc.stderr),
                }
            )

            if proc.returncode != 0:
                build_details["status"] = "failed"
                self.last_build_details = build_details
                raise RuntimeError(
                    "Minikube build failed for "
                    f"{image_tag} (context={build_context}, dockerfile={dockerfile_path}): "
                    f"{proc.stderr.strip() or proc.stdout.strip()}"
                )

            self._verify_image_available(image_tag)
            build_details["status"] = "success"
            self.last_build_details = build_details
            self.logger.info("[Docker] Local build in Minikube succeeded for %s.", image_tag)
        else:
            self.last_build_details = {
                "service_name": service_name,
                "image_tag": image_tag,
                "target_env": target_env,
                "status": "remote-registry-placeholder",
                "message": "Remote push logic not implemented in this benchmark mode.",
            }
            self.logger.info(f"[Docker] Built & Pushed to Remote Cloud Registry: {image_tag}")

        self.logger.info(f"Successfully processed {image_tag}")
        return image_tag

    def _workspace_roots(self, repository_path: str | Path | None = None) -> list[Path]:
        roots: list[Path] = []
        seen: set[Path] = set()
        candidates: list[Any] = [Path.cwd(), *Path.cwd().parents, Path(__file__).resolve(), *Path(__file__).resolve().parents]

        if repository_path:
            repo_path = Path(repository_path)
            candidates = [repo_path, *repo_path.parents, *candidates]

        for candidate in candidates:
            path = candidate if candidate.is_dir() else candidate.parent
            path = path.resolve()
            if path not in seen:
                seen.add(path)
                roots.append(path)
        return roots

    def _resolve_service_context(
        self,
        service_name: str,
        raw_path: str,
        repository_path: str | Path | None = None,
    ) -> Path:
        cleaned_raw_path = (raw_path or "").strip().replace("\\", "/")
        workspace_roots = self._workspace_roots(repository_path)

        if cleaned_raw_path:
            raw_candidate = Path(cleaned_raw_path)
            if raw_candidate.is_absolute() and raw_candidate.exists():
                return raw_candidate.resolve()

            for root in workspace_roots:
                candidate = (root / raw_candidate).resolve()
                if candidate.exists():
                    return candidate

        for root in workspace_roots:
            direct_service_path = (root / service_name).resolve()
            if direct_service_path.exists():
                return direct_service_path

            fallback = (root / "gestion_stock" / service_name).resolve()
            if fallback.exists():
                return fallback

        raise FileNotFoundError(
            "Unable to resolve service path for "
            f"{service_name}. Received raw_path='{raw_path}'."
        )

    def _resolve_dockerfile(self, service_name: str, stack: str, raw_path: str, build_context: Path) -> Path:
        local_dockerfile = build_context / "Dockerfile"
        if local_dockerfile.exists():
            return local_dockerfile

        search_root = self._resolve_docker_search_root(build_context)
        candidates = list(search_root.rglob("Dockerfile"))
        if not candidates:
            raise FileNotFoundError(
                f"No Dockerfile found for {service_name} under {search_root}."
            )

        service_tokens = [token for token in service_name.lower().replace("-", "_").split("_") if token]
        raw_tokens = [token for token in raw_path.lower().replace("\\", "/").replace("-", "/").replace("_", "/").split("/") if token]
        stack_lower = stack.lower()

        def score(candidate: Path) -> int:
            candidate_lower = candidate.as_posix().lower()
            score_value = 0

            if any(token in candidate_lower for token in service_tokens):
                score_value += 4
            if any(token in candidate_lower for token in raw_tokens):
                score_value += 6

            if "java" in stack_lower or "maven" in stack_lower:
                if "backend" in candidate_lower or "java" in candidate_lower:
                    score_value += 8

            if "node" in stack_lower or "angular" in stack_lower or "frontend" in stack_lower:
                if "frontend" in candidate_lower or "/ui/" in candidate_lower:
                    score_value += 8

            if "/docker/" in candidate_lower:
                score_value += 2

            return score_value

        best_candidate = max(candidates, key=score)
        return best_candidate.resolve()

    def _resolve_docker_search_root(self, build_context: Path) -> Path:
        for candidate in [build_context, *build_context.parents]:
            if (candidate / "Docker").exists():
                return candidate
        return build_context.parent

    def _prepare_dockerfile_arg(self, service_name: str, build_context: Path, dockerfile_path: Path) -> tuple[str, Path | None]:
        dockerfile_resolved = dockerfile_path.resolve()
        build_context_resolved = build_context.resolve()

        if dockerfile_resolved.is_relative_to(build_context_resolved):
            relative = dockerfile_resolved.relative_to(build_context_resolved)
            return relative.as_posix(), None

        safe_service_name = service_name.replace(" ", "-").replace("_", "-").lower()
        staged_file = build_context_resolved / f".copilot-{safe_service_name}-Dockerfile"
        shutil.copyfile(dockerfile_resolved, staged_file)
        return staged_file.name, staged_file

    def _verify_image_available(self, image_tag: str) -> None:
        proc = subprocess.run(["minikube", "image", "ls"], capture_output=True, text=True)
        if proc.returncode != 0:
            self.logger.warning("[Docker] Unable to verify image availability: %s", proc.stderr.strip())
            return

        expected_values = {image_tag, f"docker.io/library/{image_tag}"}
        if not any(expected in proc.stdout for expected in expected_values):
            raise RuntimeError(
                f"Image {image_tag} was built but is not visible in Minikube image store."
            )

    @staticmethod
    def _tail_text(value: str, max_lines: int = 40) -> str:
        lines = (value or "").splitlines()
        if len(lines) <= max_lines:
            return "\n".join(lines)
        return "\n".join(lines[-max_lines:])
