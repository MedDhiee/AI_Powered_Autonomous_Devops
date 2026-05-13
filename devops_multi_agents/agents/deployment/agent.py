from __future__ import annotations

import logging
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any

from shared import BaseAgent, ProtocolType

from .docker_manager import DockerManager
from .infra_manager import InfrastructureManager
from .vault_integration import VaultManager
from .mongo_integration import MongoDBLogger
from .feedback_integration import EventNotifier
from .human_validation import HumanInTheLoop

class UnifiedDeploymentAgent(BaseAgent):
    """A2A agent for robust deployment."""

    def __init__(self) -> None:
        super().__init__(agent_name="deployment_agent", protocol=ProtocolType.A2A.value)
        self.vault = VaultManager()
        self.mongo = MongoDBLogger()
        self.docker = DockerManager()
        self.infra = InfrastructureManager()
        self.notifier = EventNotifier()
        self.hitl = HumanInTheLoop()

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        architecture_data: dict[str, Any] = kwargs["architecture_data"]
        cicd_data: dict[str, Any] = kwargs.get("cicd_data", {})
        target_env: str = kwargs.get("target_env", "local")  # local, aws, azure, gcp
        require_approval: bool = kwargs.get("require_approval", True)
        repository_path = kwargs.get("repository_path")
        llm_model = kwargs.get("llm_model")
        llm_provider = kwargs.get("llm_provider")
        require_real_llm: bool = bool(kwargs.get("require_real_llm", False))
        auto_approve_terraform: bool = bool(kwargs.get("auto_approve_terraform", False))
        skip_terraform_apply: bool = bool(kwargs.get("skip_terraform_apply", False))
        execution_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)
        event_logs: list[dict[str, Any]] = []
        service_reports: list[dict[str, Any]] = []

        def log_event(level: str, stage: str, message: str, **extra: Any) -> None:
            event_logs.append(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "level": level,
                    "stage": stage,
                    "message": message,
                    "extra": extra,
                }
            )

        self.logger.info("Starting deployment analysis and execution")
        log_event("INFO", "start", "Starting deployment analysis and execution", target_env=target_env)

        # 1. A2A & Human-in-the-loop
        if require_approval:
            approved = self.hitl.request_approval(architecture_data, target_env)
            if not approved:
                log_event("WARNING", "approval", "Deployment aborted by operator")
                return {"protocol": self.protocol, "status": "aborted_by_user", "msg": "Deployment aborted by operator."}
            log_event("INFO", "approval", "Deployment approved by operator")
        else:
            log_event("INFO", "approval", "Deployment auto-approved")

        # 2. Extract Secrets via Vault
        secrets = self.vault.fetch_secrets(target_env)
        log_event("INFO", "secrets", "Secrets fetched", secret_keys=list(secrets.keys()))

        services = architecture_data.get("services", [])
        deployment_plan = []
        manifests = {}

        results = []

        try:
            # 3. Create Infrastructure (Terraform/Helm)
            infra_status = self.infra.provision(
                target_env,
                secrets,
                architecture_data=architecture_data,
                repository_path=repository_path,
                llm_model=llm_model,
                llm_provider=llm_provider,
                require_real_llm=require_real_llm,
                auto_approve_terraform=auto_approve_terraform,
                skip_terraform_apply=skip_terraform_apply,
            )
            deployment_plan.append(f"Provisioned infrastructure: {infra_status}")
            log_event("INFO", "provision", "Infrastructure provisioned", infra_status=infra_status)

            for service in services:
                service_name = service.get("name", "service")
                stack = service.get("stack", "unknown")
                service_path = service.get("path", "")
                database_connections = service.get("database_connections", [])
                log_event(
                    "INFO",
                    "service-start",
                    "Processing service",
                    service_name=service_name,
                    stack=stack,
                    path=service_path,
                )
                
                ports = service.get("exposed_ports", [80])
                port = ports[0] if ports else 80

                # Docker build en passant le chemin exact
                image = self.docker.build_and_push(
                    service_name,
                    target_env,
                    secrets,
                    service_path,
                    stack,
                    repository_path=repository_path,
                )
                deployment_plan.append(f"Built & pushed image {image}")
                log_event("INFO", "build", "Image built", service_name=service_name, image=image)

                # Deploy service (K8s/Helm)
                manifest = self.infra.generate_k8s_manifest(
                    service_name,
                    image,
                    port,
                    stack,
                    target_env,
                    database_connections,
                )
                manifests[service_name] = manifest
                self.infra.apply_manifest(manifest, secrets, target_env, service_name=service_name)
                deployment_plan.append(f"Deployed {service_name} on {target_env}")
                log_event("INFO", "deploy", "Manifest applied", service_name=service_name, target_env=target_env)

                service_reports.append(
                    {
                        "service_name": service_name,
                        "stack": stack,
                        "path": service_path,
                        "port": port,
                        "image": image,
                        "build_details": dict(self.docker.last_build_details),
                        "manifest_details": dict(self.infra.last_manifest_details[-1]) if self.infra.last_manifest_details else {},
                        "apply_details": dict(self.infra.last_apply_details[-1]) if self.infra.last_apply_details else {},
                    }
                )

            # 4. Validate CI/CD & Deploy
            # 5. Monitor Health & Rollback
            health = self.infra.monitor_health(services, env=target_env)
            log_event("INFO", "health", "Health monitoring completed", healthy=health)
            if not health:
                self.logger.error("Health check failed, rolling back!")
                self.infra.rollback()
                log_event("ERROR", "health", "Health check failed, rollback initiated")
                raise RuntimeError("Deployment health degraded. Rollback initiated.")

            status = "success"
            
        except Exception as e:
            self.logger.error(f"Deployment failed: {e}")
            status = "failed"
            log_event(
                "ERROR",
                "exception",
                "Deployment failed",
                error=str(e),
                traceback=traceback.format_exc(),
            )
            # 6. Notify Incident Response and Chaos Engineering
            self.notifier.notify_incident(str(e))
            self.notifier.notify_chaos(services)
            results.append({"error": str(e)})

        finished_at = datetime.now(timezone.utc)
        execution_context = {
            "execution_id": execution_id,
            "agent": "deployment_agent",
            "protocol": self.protocol,
            "status": status,
            "target_env": target_env,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": (finished_at - started_at).total_seconds(),
            "repository_path": str(repository_path) if repository_path else "",
            "service_count": len(services),
            "services": service_reports,
            "health_checks": list(self.infra.last_health_details),
            "terraform": dict(self.infra.last_terraform_details),
            "events": event_logs,
            "error_results": results,
            "cicd_summary": {
                "success": cicd_data.get("success") if isinstance(cicd_data, dict) else None,
                "provider": (cicd_data.get("data") or {}).get("provider") if isinstance(cicd_data, dict) else None,
            },
        }

        # Log to MongoDB
        mongo_write = self.mongo.log_deployment(target_env, status, deployment_plan, results, execution_context)

        return {
            "protocol": self.protocol,
            "summary": f"Completed deployment to {target_env} with status {status}",
            "deployment_plan": deployment_plan,
            "generated_manifests": manifests,
            "status": status,
            "execution_id": execution_id,
            "mongo_write": mongo_write,
            "terraform": dict(self.infra.last_terraform_details),
        }
