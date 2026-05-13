"""
Utility functions for DevOps Multi-Agent System.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any


_ENV_LOADED = False


def load_environment() -> None:
	global _ENV_LOADED
	if _ENV_LOADED:
		return

	env_file = os.getenv("ENV_FILE", ".env")
	env_path = Path(env_file)
	if not env_path.is_absolute():
		env_path = Path.cwd() / env_path

	try:
		from dotenv import load_dotenv
	except ImportError:
		logging.getLogger(__name__).debug("python-dotenv is not installed; skipping .env loading")
		_ENV_LOADED = True
		return

	if env_path.exists():
		load_dotenv(dotenv_path=env_path, override=False)
		logging.getLogger(__name__).info("Loaded environment variables from %s", env_path)
	else:
		logging.getLogger(__name__).debug("No .env file found at %s", env_path)

	_ENV_LOADED = True


def setup_logging(level: str = "INFO") -> None:
	load_environment()
	logging.basicConfig(
		level=getattr(logging, level.upper(), logging.INFO),
		format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
	)
	# Third-party libraries emit hundreds of DEBUG lines (MongoDB heartbeats,
	# HTTP connection pools, LLM token streams, …) that bury application logs
	# and hide interactive prompts.  Clamp them to WARNING regardless of the
	# user's --log-level choice; WARNING/ERROR from these libs still surface.
	_NOISY_LOGGERS = (
		"pymongo",
		"pymongo.topology",
		"pymongo.connection",
		"pymongo.command",
		"pymongo.serverSelection",
		"pymongo.monitor",
		"urllib3",
		"urllib3.connectionpool",
		"httpcore",
		"httpx",
		"openai",
		"anthropic",
		"groq",
		"httpcore.connection",
		"httpcore.http11",
		"charset_normalizer",
	)
	for _name in _NOISY_LOGGERS:
		logging.getLogger(_name).setLevel(logging.WARNING)


def load_json_output(path: Path) -> dict[str, Any]:
	return json.loads(path.read_text(encoding="utf-8"))


def save_json_output(path: Path, data: dict[str, Any]) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def sanitize_name(value: str) -> str:
	return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-").lower()


def truncate_text(value: str, max_len: int = 160) -> str:
	if len(value) <= max_len:
		return value
	return value[: max_len - 3] + "..."


def format_duration(seconds: float) -> str:
	return f"{seconds:.2f}s"


def merge_outputs(*outputs: dict[str, Any]) -> dict[str, Any]:
	merged: dict[str, Any] = {}
	for item in outputs:
		merged.update(item)
	return merged


def extract_services_from_architecture_output(arch_data: dict[str, Any]) -> list[dict[str, Any]]:
	return arch_data.get("services", [])


def extract_dependencies_from_architecture_output(arch_data: dict[str, Any]) -> dict[str, list[str]]:
	return arch_data.get("dependencies", {})


def get_primary_stack(service: dict[str, Any]) -> str:
	return str(service.get("stack", "unknown"))
