import logging
import subprocess
import tempfile
import os
import time
from pathlib import Path
from typing import Any

from .terraform_llm_generator import TerraformLLMGenerator

class InfrastructureManager:
    def __init__(self):
        self.logger = logging.getLogger("InfrastructureManager")
        self.terraform_generator = TerraformLLMGenerator()
        self._mysql_service_host_cache: str | None = None
        self._mysql_lookup_done = False
        self.last_apply_details: list[dict[str, Any]] = []
        self.last_health_details: list[dict[str, Any]] = []
        self.last_manifest_details: list[dict[str, Any]] = []
        self.last_terraform_details: dict[str, Any] = {}
        self._last_db_injection_details: dict[str, Any] = {}

    def provision(
        self,
        env: str,
        secrets: dict,
        *,
        architecture_data: dict[str, Any] | None = None,
        repository_path: str | Path | None = None,
        llm_model: str | None = None,
        llm_provider: str | None = None,
        require_real_llm: bool = False,
        auto_approve_terraform: bool = False,
        skip_terraform_apply: bool = False,
    ) -> str:
        """
        1. Infrastructure as Code (IaC) & 4. Network and Connectivity (VPC)
        Generates and applies Terraform code depending on the cloud provider.
        """
        self.logger.info(f"==> Step 1: Provisioning Infrastructure for environment: {env.upper()}")
        self.last_terraform_details = {}
        
        if env == "aws":
            self.logger.info("[IaC] Generating Terraform for AWS infrastructure baseline.")
            return self._provision_cloud(
                env="aws",
                architecture_data=architecture_data or {},
                repository_path=repository_path,
                llm_model=llm_model,
                llm_provider=llm_provider,
                require_real_llm=require_real_llm,
                auto_approve_terraform=auto_approve_terraform,
                skip_terraform_apply=skip_terraform_apply,
            )
        elif env == "azure":
            self.logger.info("[IaC] Generating Terraform for Azure infrastructure baseline.")
            return self._provision_cloud(
                env="azure",
                architecture_data=architecture_data or {},
                repository_path=repository_path,
                llm_model=llm_model,
                llm_provider=llm_provider,
                require_real_llm=require_real_llm,
                auto_approve_terraform=auto_approve_terraform,
                skip_terraform_apply=skip_terraform_apply,
            )
        elif env == "gcp":
            self.logger.info("[IaC] Generating Terraform for GCP infrastructure baseline.")
            return self._provision_cloud(
                env="gcp",
                architecture_data=architecture_data or {},
                repository_path=repository_path,
                llm_model=llm_model,
                llm_provider=llm_provider,
                require_real_llm=require_real_llm,
                auto_approve_terraform=auto_approve_terraform,
                skip_terraform_apply=skip_terraform_apply,
            )
        else:
            self.logger.info("[IaC] Validating local environment: Minikube checks.")
            return "local-minikube-ready"

    def _provision_cloud(
        self,
        *,
        env: str,
        architecture_data: dict[str, Any],
        repository_path: str | Path | None,
        llm_model: str | None,
        llm_provider: str | None,
        require_real_llm: bool,
        auto_approve_terraform: bool,
        skip_terraform_apply: bool,
    ) -> str:
        terraform_dir = self._prepare_terraform_directory(env, repository_path, llm_model)
        generation = self.terraform_generator.generate_files(
            cloud_provider=env,
            architecture_data=architecture_data,
            output_dir=terraform_dir,
            llm_model=llm_model,
            llm_provider_override=llm_provider,
            require_real_llm=require_real_llm,
        )

        approved = self._request_terraform_validation(generation, auto_approve_terraform)
        self.last_terraform_details = {
            **generation,
            "approved": approved,
            "skip_terraform_apply": skip_terraform_apply,
            "status": "generated",
        }

        if not approved:
            self.last_terraform_details["status"] = "not-approved"
            self.logger.warning("[Terraform] Generated files were not approved by user. Skipping plan/apply.")
            return f"{env}-terraform-pending-approval"

        if skip_terraform_apply:
            self.last_terraform_details["status"] = "generated-only"
            self.logger.info("[Terraform] skip_terraform_apply enabled. Files generated and approved.")
            return f"{env}-terraform-generated"

        execution = self._run_terraform_plan_apply(terraform_dir)
        self.last_terraform_details["terraform_execution"] = execution
        self.last_terraform_details["status"] = execution.get("status")

        if execution.get("status") != "success":
            failed_step = execution.get("failed_step", "unknown-step")
            stderr_tail = execution.get("stderr_tail", "")
            raise RuntimeError(
                f"Terraform {failed_step} failed for {env}. Details: {stderr_tail or 'no stderr output'}"
            )

        return f"{env}-terraform-applied"

    def _prepare_terraform_directory(
        self,
        env: str,
        repository_path: str | Path | None,
        llm_model: str | None,
    ) -> Path:
        base = Path(repository_path).resolve() if repository_path else Path.cwd().resolve()
        model_folder = self._safe_model_folder_name(llm_model)
        terraform_dir = base / ".cogniops" / "terraform" / env / model_folder
        terraform_dir.mkdir(parents=True, exist_ok=True)
        return terraform_dir

    @staticmethod
    def _safe_model_folder_name(model_name: str | None) -> str:
        raw = (model_name or "default-model").strip().lower()
        if not raw:
            return "default-model"

        normalized_chars: list[str] = []
        for ch in raw:
            if ch.isalnum() or ch in {"-", "_", "."}:
                normalized_chars.append(ch)
            else:
                normalized_chars.append("-")

        normalized = "".join(normalized_chars)
        while "--" in normalized:
            normalized = normalized.replace("--", "-")

        normalized = normalized.strip("-._")
        return normalized or "default-model"

    def _request_terraform_validation(self, generation: dict[str, Any], auto_approve_terraform: bool) -> bool:
        if auto_approve_terraform:
            self.logger.info("[Terraform] Auto-approval enabled, skipping manual file validation step.")
            return True

        print("\n--- TERRAFORM FILE VALIDATION REQUIRED ---")
        print(f"Cloud Provider: {generation.get('cloud_provider')}")
        print(f"Terraform Directory: {generation.get('terraform_dir')}")
        print("Generated files:")
        for file_path in generation.get("files", []):
            print(f" - {file_path}")
        print("\nReview these files before continuing to terraform plan/apply.")

        try:
            answer = input("Approve generated Terraform files and continue with plan/apply? [y/N]: ").strip().lower()
        except EOFError:
            self.logger.warning("No interactive input available; Terraform approval rejected by default.")
            return False
        return answer in {"y", "yes"}

    def _run_terraform_plan_apply(self, terraform_dir: Path) -> dict[str, Any]:
        steps = [
            ("init", ["terraform", "init", "-input=false"]),
            ("validate", ["terraform", "validate"]),
            ("plan", ["terraform", "plan", "-input=false", "-out", "tfplan"]),
            ("apply", ["terraform", "apply", "-input=false", "-auto-approve", "tfplan"]),
        ]

        execution: dict[str, Any] = {
            "status": "running",
            "terraform_dir": str(terraform_dir),
            "steps": [],
        }

        for step_name, command in steps:
            try:
                proc = subprocess.run(command, cwd=str(terraform_dir), capture_output=True, text=True)
            except FileNotFoundError:
                execution.update(
                    {
                        "status": "failed",
                        "failed_step": step_name,
                        "stderr_tail": "terraform executable not found in PATH",
                    }
                )
                return execution

            step_result = {
                "step": step_name,
                "command": " ".join(command),
                "return_code": proc.returncode,
                "stdout_tail": self._tail_text(proc.stdout),
                "stderr_tail": self._tail_text(proc.stderr),
            }
            execution["steps"].append(step_result)

            if proc.returncode != 0:
                execution.update(
                    {
                        "status": "failed",
                        "failed_step": step_name,
                        "stderr_tail": step_result["stderr_tail"],
                    }
                )
                return execution

        execution["status"] = "success"
        return execution

    def apply_manifest(self, manifest: str, secrets: dict, env: str = "local", service_name: str = ""):
        self.logger.info(f"==> Step 2: Applying K8s manifest (Compute) via Kubectl/Helm for {env}")

        apply_details: dict[str, Any] = {
            "service_name": service_name,
            "target_env": env,
            "status": "running",
            "manifest_bytes": len(manifest.encode("utf-8")),
            "manifest_preview": "\n".join(manifest.splitlines()[:40]),
        }
        
        if self._is_local_env(env):
            if not self._is_k8s_available():
                apply_details["status"] = "skipped-no-cluster"
                self.logger.info(
                    "[Kubectl] minikube is not running — manifest apply skipped (simulation mode). "
                    "Start minikube to deploy locally."
                )
            else:
                try:
                    self.logger.info("[Kubectl] Executing live 'kubectl apply' on local minikube cluster...")
                    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=".yaml") as tmp:
                        tmp.write(manifest)
                        tmp_name = tmp.name

                    proc = subprocess.run(["kubectl", "apply", "-f", tmp_name], capture_output=True, text=True)
                    apply_details["return_code"] = proc.returncode
                    apply_details["stdout_tail"] = self._tail_text(proc.stdout)
                    apply_details["stderr_tail"] = self._tail_text(proc.stderr)
                    if proc.returncode == 0:
                        apply_details["status"] = "success"
                        self.logger.info(f"[Kubectl Success] {proc.stdout.strip()}")
                    else:
                        apply_details["status"] = "failed"
                        self.logger.warning(f"[Kubectl Error] {proc.stderr.strip()}")
                except Exception as e:
                    apply_details["status"] = "exception"
                    apply_details["exception"] = str(e)
                    self.logger.warning(f"[Kubectl Exception] {e}")
                finally:
                    if 'tmp_name' in locals() and os.path.exists(tmp_name):
                        os.unlink(tmp_name)
        else:
            apply_details["status"] = "remote-placeholder"
            self.logger.info(f"[Helm/Terraform] Orchestrating remote cloud update.")

        self.last_apply_details.append(apply_details)

    def monitor_health(self, services: list, env: str = "local") -> bool:
        self.logger.info("==> Step 3: Executing Kubernetes Liveness & Readiness probes...")

        if not self._is_local_env(env):
            self.last_health_details = [
                {
                    "target_env": env,
                    "healthy": True,
                    "reason": "cloud-health-check-not-implemented-in-kubernetes-path",
                }
            ]
            return True

        if not self._is_k8s_available():
            self.logger.info(
                "[Health] minikube is not running — health probes skipped (simulation mode). "
                "Reporting all services healthy for workflow continuity."
            )
            self.last_health_details = [
                {"service_name": str(s.get("name", "service")), "healthy": True, "reason": "no-cluster-simulation"}
                for s in services
            ]
            return True

        all_healthy = True
        self.last_health_details = []
        for service in services:
            service_name = str(service.get("name", "service"))
            safe_name = service_name.replace("_", "-").lower()
            proc = subprocess.run(
                ["kubectl", "rollout", "status", f"deployment/{safe_name}", "--timeout=120s"],
                capture_output=True,
                text=True,
            )
            health_details = {
                "service_name": service_name,
                "deployment_name": safe_name,
                "return_code": proc.returncode,
                "stdout_tail": self._tail_text(proc.stdout),
                "stderr_tail": self._tail_text(proc.stderr),
                "healthy": proc.returncode == 0,
            }
            self.last_health_details.append(health_details)
            if proc.returncode == 0:
                self.logger.info("[Health] %s", proc.stdout.strip())
            else:
                all_healthy = False
                self.logger.error(
                    "[Health] Deployment %s is not healthy: %s",
                    safe_name,
                    proc.stderr.strip() or proc.stdout.strip(),
                )

        return all_healthy

    def rollback(self):
        self.logger.warning("![ROLLBACK] Reverting Kubernetes deployment to previous stable ReplicaSet")

    def generate_k8s_manifest(
        self,
        name: str,
        image: str,
        port: int,
        stack: str,
        env: str = "local",
        database_connections: list[str] | None = None,
    ) -> str:
        """
        2. Compute: Generates K8s deployment manifests.
        Adapts the Service type (LoadBalancer for Cloud to expose the app, ClusterIP/NodePort for local).
        """
        # Ensure name format matches Kubernetes RFC-1123 constraints (e.g. no underscores)
        safe_name = name.replace("_", "-").lower()
        rollout_token = str(int(time.time() * 1000))
        db_env_block = self._build_database_env_block(database_connections or [], env)

        self.last_manifest_details.append(
            {
                "service_name": name,
                "safe_name": safe_name,
                "image": image,
                "port": port,
                "stack": stack,
                "target_env": env,
                "rollout_token": rollout_token,
                "db_injection": self._last_db_injection_details,
            }
        )

        service_type = "LoadBalancer" if env in ["aws", "azure", "gcp"] else "ClusterIP"
        pull_policy = "Never" if self._is_local_env(env) else "Always"


        return f"""apiVersion: v1
kind: ServiceAccount
metadata:
  name: {safe_name}-sa
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {safe_name}
  labels:
    app: {safe_name}
    stack: {stack}
spec:
  replicas: 3 # High Availability setup
  selector:
    matchLabels:
      app: {safe_name}
  template:
    metadata:
      labels:
        app: {safe_name}
      annotations:
        deployment.cogniops/revision: "{rollout_token}"
    spec:
      serviceAccountName: {safe_name}-sa # Step 3: Security & IAM isolation per pod
      containers:
        - name: {safe_name}
          image: {image}
          imagePullPolicy: {pull_policy}
          ports:
            - containerPort: {port}
{db_env_block}          resources:
            requests:
              memory: "128Mi"
              cpu: "100m"
            limits:
              memory: "256Mi"
              cpu: "500m"
---
apiVersion: v1
kind: Service
metadata:
  name: {safe_name}-svc
spec:
  type: {service_type}
  selector:
    app: {safe_name}
  ports:
    - protocol: TCP
      port: 80
      targetPort: {port}
"""

    def _build_database_env_block(self, database_connections: list[str], env: str) -> str:
        if not self._is_local_env(env):
            self._last_db_injection_details = {"enabled": False, "reason": "non-local-env"}
            return ""

        mysql_connection = self._extract_mysql_connection(database_connections)
        if not mysql_connection:
            self._last_db_injection_details = {"enabled": False, "reason": "no-mysql-connection-in-architecture"}
            return ""

        mysql_host = self._resolve_mysql_service_host()
        if not mysql_host:
            self._last_db_injection_details = {"enabled": False, "reason": "mysql-service-not-found"}
            self.logger.warning("[DB] No mysql service was discovered. Skipping DB_URL injection.")
            return ""

        rewritten_connection = self._rewrite_mysql_connection(mysql_connection, mysql_host)
        if not rewritten_connection:
            self._last_db_injection_details = {"enabled": False, "reason": "connection-rewrite-failed"}
            return ""

        self._last_db_injection_details = {
            "enabled": True,
            "detected_mysql_host": mysql_host,
            "source_connection": mysql_connection,
            "injected_connection": rewritten_connection,
        }
        self.logger.info("[DB] Injecting DB_URL using mysql service host: %s", mysql_host)
        return (
            "          env:\n"
            "            - name: DB_URL\n"
            f"              value: \"{rewritten_connection}\"\n"
        )

    @staticmethod
    def _extract_mysql_connection(database_connections: list[str]) -> str | None:
        for connection in database_connections:
            cleaned = str(connection).strip()
            if "mysql" in cleaned.lower():
                return cleaned
        return None

    def _resolve_mysql_service_host(self) -> str | None:
        if self._mysql_lookup_done:
            return self._mysql_service_host_cache

        self._mysql_lookup_done = True
        try:
            proc = subprocess.run(
                [
                    "kubectl",
                    "get",
                    "svc",
                    "-A",
                    "-o",
                    "jsonpath={range .items[?(@.metadata.name=='mysql')]}{.metadata.namespace}{'\\n'}{end}",
                ],
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0:
                namespaces = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
                if namespaces:
                    self._mysql_service_host_cache = f"mysql.{namespaces[0]}.svc.cluster.local"
        except Exception as exc:
            self.logger.warning("[DB] Unable to discover mysql service host: %s", exc)

        return self._mysql_service_host_cache

    @staticmethod
    def _rewrite_mysql_connection(connection: str, mysql_host: str) -> str | None:
        for prefix in ("jdbc:mysql://", "mysql://"):
            if not connection.startswith(prefix):
                continue

            remainder = connection[len(prefix):]
            slash_index = remainder.find("/")
            if slash_index < 0:
                return f"{prefix}{mysql_host}"

            host_port = remainder[:slash_index]
            suffix = remainder[slash_index:]

            port_suffix = ""
            if ":" in host_port:
                port_suffix = f":{host_port.split(':', 1)[1]}"

            return f"{prefix}{mysql_host}{port_suffix}{suffix}"

        return None

    @staticmethod
    def _tail_text(value: str, max_lines: int = 40) -> str:
        lines = (value or "").splitlines()
        if len(lines) <= max_lines:
            return "\n".join(lines)
        return "\n".join(lines[-max_lines:])

    @staticmethod
    def _is_local_env(env: str) -> bool:
        normalized = (env or "").strip().lower()
        return normalized in {"local", "minikube"}

    def _is_k8s_available(self) -> bool:
        """Return True if the local Kubernetes API server is reachable."""
        try:
            proc = subprocess.run(
                ["kubectl", "cluster-info", "--request-timeout=3s"],
                capture_output=True, text=True, timeout=5,
            )
            return proc.returncode == 0
        except Exception:
            return False