from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from shared import (
    first_choice_text,
    get_default_model_for_provider,
    get_provider_connection,
    resolve_provider,
    strip_markdown_code_fence,
)

SUPPORTED_LLM_PROVIDERS = {"openrouter", "groq", "ollama"}
SUPPORTED_TERRAFORM_PROVIDERS = {"aws", "azure", "gcp"}


class TerraformLLMGenerator:
    def __init__(self) -> None:
        self.logger = logging.getLogger("TerraformLLMGenerator")

    def generate_files(
        self,
        *,
        cloud_provider: str,
        architecture_data: dict[str, Any],
        output_dir: Path,
        llm_model: str | None,
        llm_provider_override: str | None,
        require_real_llm: bool,
    ) -> dict[str, Any]:
        provider = cloud_provider.strip().lower()
        if provider not in SUPPORTED_TERRAFORM_PROVIDERS:
            raise ValueError(f"Unsupported cloud provider for terraform generation: {provider}")

        output_dir.mkdir(parents=True, exist_ok=True)
        llm_provider = self._resolve_llm_provider(llm_provider_override)
        model = llm_model or get_default_model_for_provider(
            llm_provider,
            openrouter_env="OPENROUTER_TERRAFORM_MODEL",
            openrouter_default=os.getenv("OPENROUTER_MODEL", "z-ai/glm-4.5-air:free"),
            groq_env="GROQ_TERRAFORM_MODEL",
            groq_default=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
            ollama_env="OLLAMA_TERRAFORM_MODEL",
            ollama_default=os.getenv("OLLAMA_MODEL", "llama3.2"),
        )

        used_llm = True
        generation_error: str | None = None
        try:
            terraform_files = self._generate_via_llm(
                cloud_provider=provider,
                architecture_data=architecture_data,
                llm_provider=llm_provider,
                llm_model=model,
            )
        except Exception as exc:
            if require_real_llm:
                raise RuntimeError(f"Terraform LLM generation failed for provider {provider}: {exc}") from exc
            used_llm = False
            generation_error = str(exc)
            self.logger.warning(
                "Terraform LLM generation failed for provider '%s' (%s). Falling back to static templates.",
                provider,
                exc,
            )
            terraform_files = self._fallback_terraform_files(provider)

        written_files: list[str] = []
        for name, content in terraform_files.items():
            file_path = output_dir / name
            file_path.write_text(content.strip() + "\n", encoding="utf-8")
            written_files.append(str(file_path))

        return {
            "cloud_provider": provider,
            "llm_provider": llm_provider,
            "llm_model": model,
            "used_llm": used_llm,
            "generation_error": generation_error,
            "terraform_dir": str(output_dir),
            "files": written_files,
        }

    def _generate_via_llm(
        self,
        *,
        cloud_provider: str,
        architecture_data: dict[str, Any],
        llm_provider: str,
        llm_model: str,
    ) -> dict[str, str]:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("The 'openai' package is required for Terraform LLM generation") from exc

        base_url, api_key = get_provider_connection(llm_provider)
        if not api_key:
            raise RuntimeError(f"No API key configured for provider '{llm_provider}'")

        timeout_seconds = float(os.getenv("TERRAFORM_LLM_TIMEOUT_SEC", "45"))
        max_retries = int(os.getenv("TERRAFORM_LLM_MAX_RETRIES", "1"))
        client = OpenAI(
          base_url=base_url,
          api_key=api_key,
          timeout=timeout_seconds,
          max_retries=max_retries,
        )
        temperature = float(os.getenv("TERRAFORM_LLM_TEMPERATURE", "0.1"))
        prompt = self._build_prompt(cloud_provider, architecture_data)

        self.logger.info(
            "Generating Terraform via LLM for provider '%s' with provider '%s' and model '%s'",
            cloud_provider,
            llm_provider,
            llm_model,
        )
        response = client.chat.completions.create(
            model=llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )

        raw_content = strip_markdown_code_fence(first_choice_text(response))
        payload = self._parse_json_object(raw_content)
        return self._normalize_terraform_payload(payload)

    def _resolve_llm_provider(self, provider_override: str | None) -> str:
        if provider_override and provider_override.strip():
            normalized = provider_override.strip().lower()
            if normalized not in SUPPORTED_LLM_PROVIDERS:
                supported = ", ".join(sorted(SUPPORTED_LLM_PROVIDERS))
                raise ValueError(f"Unsupported llm_provider '{provider_override}'. Supported values: {supported}")
            return normalized
        return resolve_provider("DEPLOYMENT_LLM_PROVIDER", default_provider="ollama")

    @staticmethod
    def _architecture_summary(architecture_data: dict[str, Any]) -> str:
        services = architecture_data.get("services", [])
        if not isinstance(services, list):
            return "No services available"

        lines: list[str] = []
        for service in services:
            if not isinstance(service, dict):
                continue
            lines.append(
                json.dumps(
                    {
                        "name": service.get("name"),
                        "stack": service.get("stack"),
                        "path": str(service.get("path", "")).replace("\\", "/"),
                        "ports": service.get("exposed_ports", []),
                        "db": service.get("database_connections", []),
                    }
                )
            )
        return "\n".join(lines) if lines else "No service details available"

    def _build_prompt(self, cloud_provider: str, architecture_data: dict[str, Any]) -> str:
        architecture_summary = self._architecture_summary(architecture_data)
        return f"""
You are a senior cloud platform engineer.
Generate Terraform files for cloud provider '{cloud_provider}'.

Rules:
- Return ONLY a JSON object (no markdown, no comments outside JSON).
- JSON keys must be exactly: providers.tf, variables.tf, main.tf, outputs.tf
- Values must be valid Terraform HCL strings.
- Do not include backticks.
- Use placeholders and variables for credentials/secrets.
- Resources should be realistic minimal network + compute baseline for container orchestration bootstrap.

Architecture context:
{architecture_summary}

Output example shape:
{{
  "providers.tf": "...",
  "variables.tf": "...",
  "main.tf": "...",
  "outputs.tf": "..."
}}
""".strip()

    @staticmethod
    def _parse_json_object(raw_content: str) -> dict[str, Any]:
        candidate = (raw_content or "").strip()
        if not candidate:
            raise ValueError("LLM returned empty content")

        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            maybe_json = candidate[start : end + 1]
            parsed = json.loads(maybe_json)
            if isinstance(parsed, dict):
                return parsed

        raise ValueError("LLM output is not a JSON object")

    @staticmethod
    def _normalize_terraform_payload(payload: dict[str, Any]) -> dict[str, str]:
        normalized: dict[str, str] = {}

        direct_keys = {"providers.tf", "variables.tf", "main.tf", "outputs.tf"}
        if all(key in payload for key in direct_keys):
            for key in direct_keys:
                normalized[key] = str(payload.get(key, ""))
            return normalized

        alias_map = {
            "providers_tf": "providers.tf",
            "variables_tf": "variables.tf",
            "main_tf": "main.tf",
            "outputs_tf": "outputs.tf",
        }
        for source_key, target_key in alias_map.items():
            if source_key in payload:
                normalized[target_key] = str(payload[source_key])

        missing = [k for k in ["providers.tf", "variables.tf", "main.tf", "outputs.tf"] if k not in normalized]
        if missing:
            raise ValueError(f"Missing terraform file keys in LLM output: {', '.join(missing)}")

        return normalized

    @staticmethod
    def _fallback_terraform_files(cloud_provider: str) -> dict[str, str]:
        if cloud_provider == "aws":
            return {
                "providers.tf": """
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}
""",
                "variables.tf": """
variable "project_name" {
  type    = string
  default = "cogniops"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "aws_region" {
  type    = string
  default = "eu-west-1"
}
""",
                "main.tf": """
resource "aws_vpc" "main" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_hostnames = true
  tags = {
    Name = "${var.project_name}-${var.environment}-vpc"
  }
}

resource "aws_subnet" "public_a" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.42.1.0/24"
  map_public_ip_on_launch = true
  availability_zone       = "${var.aws_region}a"
  tags = {
    Name = "${var.project_name}-${var.environment}-public-a"
  }
}
""",
                "outputs.tf": """
output "vpc_id" {
  value = aws_vpc.main.id
}

output "public_subnet_id" {
  value = aws_subnet.public_a.id
}
""",
            }

        if cloud_provider == "azure":
            return {
                "providers.tf": """
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
}

provider "azurerm" {
  features {}
}
""",
                "variables.tf": """
variable "project_name" {
  type    = string
  default = "cogniops"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "location" {
  type    = string
  default = "westeurope"
}
""",
                "main.tf": """
resource "azurerm_resource_group" "main" {
  name     = "${var.project_name}-${var.environment}-rg"
  location = var.location
}

resource "azurerm_virtual_network" "main" {
  name                = "${var.project_name}-${var.environment}-vnet"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  address_space       = ["10.42.0.0/16"]
}

resource "azurerm_subnet" "default" {
  name                 = "default"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.42.1.0/24"]
}
""",
                "outputs.tf": """
output "resource_group_name" {
  value = azurerm_resource_group.main.name
}

output "subnet_id" {
  value = azurerm_subnet.default.id
}
""",
            }

        # gcp fallback
        return {
            "providers.tf": """
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.gcp_project_id
  region  = var.gcp_region
}
""",
            "variables.tf": """
variable "project_name" {
  type    = string
  default = "cogniops"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "gcp_project_id" {
  type = string
}

variable "gcp_region" {
  type    = string
  default = "europe-west1"
}
""",
            "main.tf": """
resource "google_compute_network" "main" {
  name                    = "${var.project_name}-${var.environment}-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "default" {
  name          = "${var.project_name}-${var.environment}-subnet"
  ip_cidr_range = "10.42.1.0/24"
  region        = var.gcp_region
  network       = google_compute_network.main.id
}
""",
            "outputs.tf": """
output "network_id" {
  value = google_compute_network.main.id
}

output "subnet_id" {
  value = google_compute_subnetwork.default.id
}
""",
        }
