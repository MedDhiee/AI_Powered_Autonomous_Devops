from __future__ import annotations

import os
from typing import Any

from shared.utils import load_environment


def resolve_provider(component_provider_env: str, default_provider: str = "openrouter") -> str:
    load_environment()
    provider = (os.getenv(component_provider_env) or os.getenv("LLM_PROVIDER") or default_provider).strip().lower()
    if provider in {"openrouter", "groq", "ollama", "nvidia"}:
        return provider
    return default_provider


def get_provider_connection(provider: str) -> tuple[str, str | None]:
    if provider == "openrouter":
        return (
            os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            os.getenv("OPENROUTER_API_KEY"),
        )
    if provider == "groq":
        return (
            os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
            os.getenv("GROQ_API_KEY"),
        )
    if provider == "ollama":
        return (
            os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            os.getenv("OLLAMA_API_KEY", "ollama"),
        )
    if provider == "nvidia":
        # NVIDIA NIM (integrate.api.nvidia.com) is OpenAI-compatible.
        return (
            os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
            os.getenv("NVIDIA_API_KEY"),
        )
    return (
        os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        os.getenv("OPENROUTER_API_KEY"),
    )


def get_default_model_for_provider(
    provider: str,
    *,
    openrouter_env: str,
    openrouter_default: str,
    groq_env: str = "GROQ_MODEL",
    groq_default: str = "llama-3.1-8b-instant",
    ollama_env: str = "OLLAMA_MODEL",
    ollama_default: str = "llama3.2",
    nvidia_env: str = "NVIDIA_MODEL",
    nvidia_default: str = "minimaxai/minimax-m2.7",
) -> str:
    if provider == "openrouter":
        return os.getenv(openrouter_env, openrouter_default)
    if provider == "groq":
        return os.getenv(groq_env, groq_default)
    if provider == "ollama":
        return os.getenv(ollama_env, ollama_default)
    if provider == "nvidia":
        return os.getenv(nvidia_env, nvidia_default)
    return os.getenv(openrouter_env, openrouter_default)


def strip_markdown_code_fence(text: str) -> str:
    value = (text or "").strip()
    if not value.startswith("```"):
        return value
    lines = value.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def first_choice_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if not choices:
        return ""
    first_choice = choices[0]
    message = getattr(first_choice, "message", None)
    content = getattr(message, "content", "")
    return str(content or "").strip()
