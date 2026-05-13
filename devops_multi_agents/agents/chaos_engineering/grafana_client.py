from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger("chaos_engineering.grafana")


class GrafanaClient:
    """Grafana HTTP API client — annotations, Loki log queries, and datasource inspection."""

    def __init__(
        self,
        url: str = "http://localhost:3000",
        api_key: str | None = None,
        username: str = "admin",
        password: str = "admin",
        loki_datasource_uid: str = "loki",
        timeout: int = 10,
    ) -> None:
        self.url = url.rstrip("/")
        self.loki_uid = loki_datasource_uid
        self.timeout = timeout
        self._session = requests.Session()
        if api_key:
            self._session.headers["Authorization"] = f"Bearer {api_key}"
        else:
            self._session.auth = (username, password)
        self._session.headers["Content-Type"] = "application/json"
        self._available: bool | None = None

    # ── availability ──────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            resp = self._session.get(f"{self.url}/api/health", timeout=5)
            self._available = resp.status_code == 200
        except Exception:
            self._available = False
        if not self._available:
            logger.warning("Grafana not reachable at %s — annotation/log features disabled", self.url)
        return self._available

    # ── annotations ───────────────────────────────────────────────────────────

    def create_annotation(
        self,
        text: str,
        tags: list[str],
        dashboard_uid: str | None = None,
    ) -> int | None:
        """Create a point annotation and return its ID, or None if unavailable."""
        if not self.is_available():
            return None
        payload: dict[str, Any] = {
            "text": text,
            "tags": tags,
            "time": int(time.time() * 1000),
        }
        if dashboard_uid:
            payload["dashboardUID"] = dashboard_uid
        try:
            resp = self._session.post(
                f"{self.url}/api/annotations", json=payload, timeout=self.timeout
            )
            resp.raise_for_status()
            ann_id: int = resp.json()["id"]
            logger.info("Grafana annotation created id=%d: %s", ann_id, text)
            return ann_id
        except Exception as exc:
            logger.warning("Failed to create Grafana annotation: %s", exc)
            return None

    def update_annotation(
        self,
        annotation_id: int,
        text: str,
        tags: list[str],
    ) -> bool:
        """Patch an existing annotation (e.g. to mark experiment end and set timeEnd)."""
        if not self.is_available():
            return False
        payload: dict[str, Any] = {
            "text": text,
            "tags": tags,
            "timeEnd": int(time.time() * 1000),
        }
        try:
            resp = self._session.patch(
                f"{self.url}/api/annotations/{annotation_id}",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            logger.info("Grafana annotation %d updated", annotation_id)
            return True
        except Exception as exc:
            logger.warning("Failed to update Grafana annotation %d: %s", annotation_id, exc)
            return False

    def delete_annotation(self, annotation_id: int) -> bool:
        if not self.is_available():
            return False
        try:
            resp = self._session.delete(
                f"{self.url}/api/annotations/{annotation_id}", timeout=self.timeout
            )
            return resp.status_code == 200
        except Exception:
            return False

    # ── Loki log queries ──────────────────────────────────────────────────────

    def query_loki_logs(
        self,
        service: str,
        start_ns: int,
        end_ns: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query Loki logs via Grafana datasource proxy.

        Returns a list of ``{"timestamp": float, "service": str, "message": str, "labels": dict}``
        sorted chronologically.
        """
        if not self.is_available():
            return []

        loki_query = f'{{job="{service}"}}'
        try:
            resp = self._session.get(
                f"{self.url}/api/datasources/proxy/uid/{self.loki_uid}/loki/api/v1/query_range",
                params={"query": loki_query, "start": start_ns, "end": end_ns, "limit": limit},
                timeout=max(self.timeout, 20),
            )
            resp.raise_for_status()
            entries: list[dict[str, Any]] = []
            for stream in resp.json().get("data", {}).get("result", []):
                labels = stream.get("stream", {})
                for ts_str, line in stream.get("values", []):
                    entries.append(
                        {
                            "timestamp": int(ts_str) / 1e9,
                            "service": service,
                            "message": line,
                            "labels": labels,
                        }
                    )
            return sorted(entries, key=lambda e: e["timestamp"])
        except Exception as exc:
            logger.debug("Loki query failed for %s: %s", service, exc)
            return []

    def search_logs_for_errors(
        self,
        service: str,
        start_ns: int,
        end_ns: int,
        limit: int = 50,
    ) -> list[str]:
        """Convenience: return only error-level log lines for a service."""
        if not self.is_available():
            return []
        error_query = f'{{job="{service}"}} |= "error" | logfmt | level="error"'
        try:
            resp = self._session.get(
                f"{self.url}/api/datasources/proxy/uid/{self.loki_uid}/loki/api/v1/query_range",
                params={"query": error_query, "start": start_ns, "end": end_ns, "limit": limit},
                timeout=max(self.timeout, 20),
            )
            resp.raise_for_status()
            lines: list[str] = []
            for stream in resp.json().get("data", {}).get("result", []):
                for _, line in stream.get("values", []):
                    lines.append(line)
            return lines
        except Exception as exc:
            logger.debug("Loki error log query failed for %s: %s", service, exc)
            return []

    # ── datasource inspection ─────────────────────────────────────────────────

    def list_datasources(self) -> list[dict[str, Any]]:
        """Return all configured datasources (name, type, uid)."""
        if not self.is_available():
            return []
        try:
            resp = self._session.get(f"{self.url}/api/datasources", timeout=self.timeout)
            resp.raise_for_status()
            return [
                {"name": ds.get("name"), "type": ds.get("type"), "uid": ds.get("uid")}
                for ds in resp.json()
            ]
        except Exception as exc:
            logger.debug("Failed to list Grafana datasources: %s", exc)
            return []

    def find_loki_uid(self) -> str | None:
        """Auto-detect the Loki datasource UID."""
        for ds in self.list_datasources():
            if ds.get("type") == "loki":
                uid: str = ds["uid"]
                logger.info("Auto-detected Loki datasource uid=%s", uid)
                self.loki_uid = uid
                return uid
        return None
