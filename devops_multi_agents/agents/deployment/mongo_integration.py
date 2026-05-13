from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any

try:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    PYMONGO_AVAILABLE = True
except ImportError:
    MongoClient = None
    PyMongoError = Exception
    PYMONGO_AVAILABLE = False


class MongoDBLogger:
    def __init__(self):
        self.logger = logging.getLogger("MongoDBLogger")
        self.mongo_uri = os.environ.get("MONGO_URI") or os.environ.get("MOONGO_URI", "mongodb://localhost:27017/")
        self.db_name = os.environ.get("MONGO_DB_NAME", "CogniOps")

        self.client = None
        self.db = None
        self.pipeline_results = None
        self.logs_col = None
        self.agent_metadata = None
        self.incidents = None

        if not PYMONGO_AVAILABLE:
            self.logger.warning("pymongo is not installed. Mongo persistence is disabled.")
            return

        try:
            self.client = MongoClient(self.mongo_uri, serverSelectionTimeoutMS=3000)
            self.client.admin.command("ping")
            self.db = self.client[self.db_name]

            # Expected collections for incident-ready persistence
            self.pipeline_results = self.db["pipeline_results"]
            self.logs_col = self.db["logs"]
            self.agent_metadata = self.db["agent_metadata"]
            self.incidents = self.db["incidents"]

            self.logger.info("Successfully connected to MongoDB at %s", self.mongo_uri)
        except Exception as exc:
            self.logger.warning("Could not connect to MongoDB: %s. Falling back to logging only.", exc)
            self.client = None

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _truncate_text(value: str, max_chars: int = 8000) -> str:
        if len(value) <= max_chars:
            return value
        return value[-max_chars:]

    def _extract_error_payload(self, results: list, execution_context: dict[str, Any]) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []

        for item in results or []:
            if isinstance(item, dict) and item.get("error"):
                errors.append({"source": "results", "error": str(item.get("error"))})

        for svc in execution_context.get("services", []) or []:
            if not isinstance(svc, dict):
                continue
            service_name = str(svc.get("service_name", "service"))

            build_details = svc.get("build_details") or {}
            if isinstance(build_details, dict) and build_details.get("status") in {"failed", "exception"}:
                errors.append(
                    {
                        "source": "build",
                        "service": service_name,
                        "status": build_details.get("status"),
                        "stderr_tail": self._truncate_text(str(build_details.get("stderr_tail", ""))),
                    }
                )

            apply_details = svc.get("apply_details") or {}
            if isinstance(apply_details, dict) and apply_details.get("status") in {"failed", "exception"}:
                errors.append(
                    {
                        "source": "k8s-apply",
                        "service": service_name,
                        "status": apply_details.get("status"),
                        "stderr_tail": self._truncate_text(str(apply_details.get("stderr_tail", ""))),
                    }
                )

        for health in execution_context.get("health_checks", []) or []:
            if not isinstance(health, dict):
                continue
            if not bool(health.get("healthy", True)):
                errors.append(
                    {
                        "source": "health-check",
                        "service": health.get("service_name"),
                        "deployment": health.get("deployment_name"),
                        "stderr_tail": self._truncate_text(str(health.get("stderr_tail", ""))),
                    }
                )

        for event in execution_context.get("events", []) or []:
            if not isinstance(event, dict):
                continue
            if str(event.get("level", "")).upper() == "ERROR":
                errors.append(
                    {
                        "source": "event-log",
                        "stage": event.get("stage"),
                        "message": self._truncate_text(str(event.get("message", ""))),
                        "extra": event.get("extra", {}),
                    }
                )

        return errors

    @staticmethod
    def _compute_incident_severity(error_payload: list[dict[str, Any]]) -> str:
        if not error_payload:
            return "none"

        text = " ".join(str(item).lower() for item in error_payload)
        if any(keyword in text for keyword in ["crashloop", "errimageneverpull", "failed", "timeout"]):
            return "high"
        return "medium"

    def _build_incident_signature(self, env: str, execution_context: dict[str, Any], error_payload: list[dict[str, Any]]) -> str:
        service_names = sorted(
            str(svc.get("service_name", "service"))
            for svc in (execution_context.get("services", []) or [])
            if isinstance(svc, dict)
        )
        first_error = str(error_payload[0]) if error_payload else "no-error"
        raw = f"{env}|{'|'.join(service_names)}|{first_error}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _upsert_agent_metadata(self, *, status: str, execution_context: dict[str, Any]) -> str | None:
        if self.agent_metadata is None:
            return None

        now = self._now()
        inc_success = 1 if status == "success" else 0
        inc_failure = 1 if status != "success" else 0

        self.agent_metadata.update_one(
            {"agent_name": "deployment_agent"},
            {
                "$set": {
                    "protocol": execution_context.get("protocol", "A2A"),
                    "last_execution_id": execution_context.get("execution_id"),
                    "last_status": status,
                    "last_target_env": execution_context.get("target_env"),
                    "last_seen": now,
                    "last_repository_path": execution_context.get("repository_path", ""),
                    "supported_collections": ["pipeline_results", "agent_metadata", "logs", "incidents"],
                },
                "$inc": {
                    "execution_count": 1,
                    "success_count": inc_success,
                    "failure_count": inc_failure,
                },
                "$setOnInsert": {
                    "created_at": now,
                    "agent_type": "A2A",
                },
            },
            upsert=True,
        )
        return "updated"

    def _create_or_update_incident(
        self,
        *,
        env: str,
        status: str,
        execution_context: dict[str, Any],
        error_payload: list[dict[str, Any]],
        pipeline_result_id: Any,
    ) -> dict[str, Any]:
        if self.incidents is None:
            return {"status": "skipped", "reason": "incidents-collection-unavailable"}

        if status == "success" and not error_payload:
            return {"status": "skipped", "reason": "no-incident-detected"}

        now = self._now()
        signature = self._build_incident_signature(env, execution_context, error_payload)
        severity = self._compute_incident_severity(error_payload)

        incident = self.incidents.find_one(
            {
                "signature": signature,
                "status": {"$in": ["open", "investigating"]},
            }
        )

        if incident:
            self.incidents.update_one(
                {"_id": incident["_id"]},
                {
                    "$set": {
                        "updated_at": now,
                        "last_seen_execution_id": execution_context.get("execution_id"),
                        "last_pipeline_result_id": pipeline_result_id,
                        "severity": severity,
                        "status": "investigating",
                        "last_error_payload": error_payload[:20],
                    },
                    "$inc": {"occurrences": 1},
                    "$push": {
                        "evidence": {
                            "ts": now,
                            "execution_id": execution_context.get("execution_id"),
                            "pipeline_result_id": pipeline_result_id,
                            "error_sample": error_payload[:5],
                        }
                    },
                },
            )
            return {"status": "updated", "incident_id": str(incident["_id"]), "signature": signature}

        new_incident = {
            "signature": signature,
            "status": "open",
            "severity": severity,
            "environment": env,
            "created_at": now,
            "updated_at": now,
            "first_seen_execution_id": execution_context.get("execution_id"),
            "last_seen_execution_id": execution_context.get("execution_id"),
            "service_names": [
                str(svc.get("service_name", "service"))
                for svc in (execution_context.get("services", []) or [])
                if isinstance(svc, dict)
            ],
            "summary": "Deployment execution generated actionable failures for incident handling",
            "occurrences": 1,
            "last_pipeline_result_id": pipeline_result_id,
            "last_error_payload": error_payload[:20],
            "evidence": [
                {
                    "ts": now,
                    "execution_id": execution_context.get("execution_id"),
                    "pipeline_result_id": pipeline_result_id,
                    "error_sample": error_payload[:5],
                }
            ],
        }
        inserted_id = self.incidents.insert_one(new_incident).inserted_id
        return {"status": "created", "incident_id": str(inserted_id), "signature": signature}

    def log_deployment(
        self,
        env: str,
        status: str,
        plan: list,
        results: list,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.logger.info("Writing deployment log to MongoDB => Env %s, Status %s", env, status)

        context = execution_context or {}
        error_payload = self._extract_error_payload(results, context)
        now = self._now()

        deployment_record = {
            "timestamp": now,
            "agent": "deployment_agent",
            "environment": env,
            "status": status,
            "deployment_plan": plan,
            "execution_results": results,
            "execution_context": context,
            "error_payload": error_payload,
        }

        write_summary: dict[str, Any] = {
            "connected": bool(self.client),
            "pipeline_result_id": None,
            "log_ids": [],
            "agent_metadata": None,
            "incident": None,
        }

        if self.client is None:
            self.logger.warning("Mongo client unavailable. Returning in-memory write summary only.")
            write_summary["reason"] = "mongo-client-unavailable"
            return write_summary

        try:
            pipeline_result_id = self.pipeline_results.insert_one(deployment_record).inserted_id
            write_summary["pipeline_result_id"] = str(pipeline_result_id)

            log_documents: list[dict[str, Any]] = []
            for event in context.get("events", []) or []:
                if not isinstance(event, dict):
                    continue
                log_documents.append(
                    {
                        "timestamp": now,
                        "level": str(event.get("level", "INFO")),
                        "source": "deployment_agent",
                        "execution_id": context.get("execution_id"),
                        "pipeline_result_id": pipeline_result_id,
                        "stage": event.get("stage"),
                        "message": self._truncate_text(str(event.get("message", ""))),
                        "details": event.get("extra", {}),
                    }
                )

            log_documents.append(
                {
                    "timestamp": now,
                    "level": "INFO" if status == "success" else "ERROR",
                    "source": "deployment_agent",
                    "execution_id": context.get("execution_id"),
                    "pipeline_result_id": pipeline_result_id,
                    "stage": "summary",
                    "message": f"Deployment to {env} finished with status: {status}",
                    "details": {
                        "service_count": context.get("service_count"),
                        "duration_seconds": context.get("duration_seconds"),
                        "error_count": len(error_payload),
                    },
                }
            )

            if log_documents:
                inserted_logs = self.logs_col.insert_many(log_documents).inserted_ids
                write_summary["log_ids"] = [str(log_id) for log_id in inserted_logs]

            write_summary["agent_metadata"] = self._upsert_agent_metadata(status=status, execution_context=context)
            write_summary["incident"] = self._create_or_update_incident(
                env=env,
                status=status,
                execution_context=context,
                error_payload=error_payload,
                pipeline_result_id=pipeline_result_id,
            )

            self.logger.info(
                "Mongo write completed. pipeline_results=%s, logs=%s",
                write_summary["pipeline_result_id"],
                len(write_summary["log_ids"]),
            )
            return write_summary

        except PyMongoError as exc:
            self.logger.error("Failed to write into MongoDB: %s", exc)
            write_summary["write_error"] = str(exc)
            return write_summary

