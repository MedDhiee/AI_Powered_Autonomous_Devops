from __future__ import annotations

import json
import logging
from typing import Any

from shared import (
    first_choice_text,
    get_default_model_for_provider,
    get_provider_connection,
    resolve_provider,
    strip_markdown_code_fence,
)

LOGGER = logging.getLogger(__name__)


class LLMArchitectureAdvisor:
    """
    Sends structured JSON to an LLM (No raw code sent).
    Uses OpenAI-compatible clients (OpenRouter, Groq, Ollama).
    """

    def generate(self, structured_output: dict[str, Any], llm_model: str | None = None) -> dict[str, Any]:
        provider = resolve_provider("ARCH_LLM_PROVIDER", default_provider="openrouter")
        base_url, api_key = get_provider_connection(provider)

        # If no key is configured, keep pipeline usable with local summary.
        if not api_key:
            LOGGER.warning("No API key found for provider '%s'. Falling back to local summary generation.", provider)
            return self._local_fallback_summary(structured_output)

        return self._call_real_llm(
            api_key,
            structured_output,
            llm_model=llm_model,
            provider=provider,
            base_url=base_url,
        )

    def _call_real_llm(
        self,
        api_key: str,
        structured_output: dict[str, Any],
        llm_model: str | None = None,
        provider: str = "openrouter",
        base_url: str = "https://openrouter.ai/api/v1",
    ) -> dict[str, Any]:
        try:
            from openai import OpenAI
        except ImportError:
            LOGGER.error("The 'openai' package is required. Run: pip install openai")
            return {"error": "Missing openai package"}

        model = llm_model or get_default_model_for_provider(
            provider,
            openrouter_env="OPENROUTER_MODEL",
            openrouter_default="z-ai/glm-4.5-air:free",
        )
        client = OpenAI(base_url=base_url, api_key=api_key)
        
        # We only send the lightweight JSON, not the code!
        payload = json.dumps(structured_output, indent=2)
        
        prompt = f"""
        You are an expert DevOps Architect. I have analyzed a deployment repository.
        Here is the structured architecture metadata (JSON):
        {payload}
        
        Provide your response in raw JSON format with NO markdown wrapping block, and with these three exact keys:
        {{
          "architecture_summary": "A high-level description of the system",
          "recommendations": ["An array of strings", "with DevOps/Architecture improvements"],
          "system_diagram_description": "A textual representation or explanation of the data flow"
        }}
        """

        LOGGER.info("Sending structured metadata to provider '%s' model '%s'...", provider, model)
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}]
            )
            content = first_choice_text(response)
            if not content:
                raise RuntimeError(f"Provider {provider} returned empty or non-text content")
        except Exception as exc:
            err_text = str(exc)
            if "429" in err_text:
                status = "fallback_quota_limit"
                LOGGER.warning("Provider %s quota/rate limit reached (%s). Using local fallback.", provider, exc)
            else:
                status = "fallback_error"
                LOGGER.warning("Provider %s request failed (%s). Using local fallback.", provider, exc)
            fallback = self._local_fallback_summary(structured_output)
            fallback["llm_status"] = status
            fallback["llm_error"] = str(exc)
            fallback["llm_provider"] = provider
            return fallback
        
        # Strip potential markdown formatting (e.g. ```json \n ... \n```)
        content = strip_markdown_code_fence(content)

        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                parsed.setdefault("llm_provider", provider)
                parsed.setdefault("llm_model", model)
            return parsed
        except json.JSONDecodeError as e:
            LOGGER.error("Failed to parse JSON from LLM: %s", e)
            return {"error": "Invalid format returned by LLM", "raw_response": content}

    def _local_fallback_summary(self, structured_output: dict[str, Any]) -> dict[str, Any]:
        services = structured_output.get("services", [])
        dependencies = structured_output.get("dependencies", {})

        service_count = len(services)
        stacks = sorted({svc.get("stack", "unknown") for svc in services})

        summary = (
            f"Detected {service_count} service(s) using stacks: {', '.join(stacks) if stacks else 'none'}. "
            "Dependency graph and runtime indicators were extracted from build tooling outputs and config metadata."
        )

        recommendations: list[str] = []
        if service_count >= 5:
            recommendations.append("Add service-level SLOs and alert routing per domain to reduce incident noise.")
        if any(svc.get("stack") == "unknown" for svc in services):
            recommendations.append("Some services could not be classified; add explicit build/config markers for better observability.")
        if not any(svc.get("exposed_ports") for svc in services):
            recommendations.append("No exposed ports were detected from Dockerfiles; validate container network contracts.")
        if not recommendations:
            recommendations.append("Architecture metadata quality looks good; next step is integrating ownership and deployment topology tags.")

        diagram = self._build_diagram_text(services, dependencies)

        LOGGER.info("Generated LLM-ready architecture summary from structured metadata")
        return {
            "architecture_summary": summary,
            "recommendations": recommendations,
            "system_diagram_description": diagram,
        }

    def _build_diagram_text(self, services: list[dict[str, Any]], dependencies: dict[str, list[str]]) -> str:
        lines = ["System diagram (logical):"]
        for service in services:
            name = service.get("name", "unknown")
            stack = service.get("stack", "unknown")
            ports = service.get("exposed_ports", [])
            line = f"- {name} [{stack}]"
            if ports:
                line += f" exposes {ports}"
            lines.append(line)

            targets = dependencies.get(name, [])
            for target in targets:
                lines.append(f"  -> depends on {target}")

        return "\n".join(lines)
