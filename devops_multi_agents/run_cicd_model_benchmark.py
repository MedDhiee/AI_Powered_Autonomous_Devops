from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared import save_json_output, setup_logging

from devops_multi_agents.agents import CICDGenerationAgent
from devops_multi_agents.agents.cicd_generation_agent import write_pipeline_files
from devops_multi_agents.llm_judge import LLMJudge


FRONTEND_STACK_HINTS = {
    "node",
    "nodejs",
    "javascript",
    "typescript",
    "angular",
    "react",
    "vue",
    "frontend",
    "web",
    "ui",
}


def default_judge_model_for_provider(provider: str) -> str:
    normalized = (provider or "").strip().lower()
    if normalized == "ollama":
        return "nemotron-3-super"
    if normalized == "groq":
        return "llama-3.1-8b-instant"
    return "z-ai/glm-4.5-air:free"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run CI/CD generation agent for two model labels and compare them with LLMJudge"
    )
    parser.add_argument("--arch", required=True, help="Architecture JSON path")
    parser.add_argument("--model-a", required=True, help="Model A id (label for comparison report)")
    parser.add_argument("--model-b", required=True, help="Model B id (label for comparison report)")
    parser.add_argument(
        "--provider",
        default="github",
        help="CI/CD provider (github, gitlab, azure-devops, bitbucket)",
    )
    parser.add_argument(
        "--llm-provider",
        default=None,
        help="Optional CI/CD LLM provider override (openrouter, groq, ollama)",
    )
    parser.add_argument(
        "--judge-provider",
        default="ollama",
        help="Optional judge LLM provider override (openrouter, groq, ollama)",
    )
    parser.add_argument(
        "--output-a",
        default="devops_multi_agents/outputs/cicd-generation-model-a.json",
        help="Output file for model A CI/CD generation",
    )
    parser.add_argument(
        "--output-b",
        default="devops_multi_agents/outputs/cicd-generation-model-b.json",
        help="Output file for model B CI/CD generation",
    )
    parser.add_argument(
        "--judge-output",
        default="devops_multi_agents/outputs/cicd-only-judge-report.json",
        help="Output file for CI/CD-only judge report",
    )
    parser.add_argument(
        "--judge-type",
        default="default",
        choices=["default", "evaluation-correction-avec-reference"],
        help="Judge strategy: default metrics comparison or reference-based evaluation correction",
    )
    parser.add_argument(
        "--reference-backend-pipeline",
        default=None,
        help="Path to the real backend pipeline YAML reference",
    )
    parser.add_argument(
        "--reference-frontend-pipeline",
        default=None,
        help="Path to the real frontend pipeline YAML reference",
    )
    parser.add_argument(
        "--pipelines-root-a",
        default="devops_multi_agents/outputs/generated-pipelines/model-a",
        help="Directory where model A pipeline YAML files are written",
    )
    parser.add_argument(
        "--pipelines-root-b",
        default="devops_multi_agents/outputs/generated-pipelines/model-b",
        help="Directory where model B pipeline YAML files are written",
    )
    parser.add_argument(
        "--require-real-cicd-llm",
        action="store_true",
        help="Fail if CI/CD generation cannot use a real LLM response",
    )
    parser.add_argument("--judge-model", default=None, help="Judge model override (example: gpt-oss)")
    parser.add_argument("--require-real-judge", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    if args.judge_type == "evaluation-correction-avec-reference":
        if not args.reference_backend_pipeline or not args.reference_frontend_pipeline:
            parser.error(
                "--reference-backend-pipeline and --reference-frontend-pipeline are required when --judge-type evaluation-correction-avec-reference"
            )

    return args


def _safe_slug(raw_value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", raw_value.strip().lower())
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "service"


def _service_role(stack: str) -> str:
    normalized = stack.strip().lower()
    if normalized in FRONTEND_STACK_HINTS:
        return "frontend"
    return "backend"


def _align_pipelines_by_role(
    architecture_data: dict[str, Any],
    pipelines: dict[str, str],
) -> dict[str, dict[str, str]]:
    aligned: dict[str, dict[str, str]] = {}

    slug_roles: dict[str, str] = {}
    for service in architecture_data.get("services", []):
        if not isinstance(service, dict):
            continue
        name = str(service.get("name", "service"))
        stack = str(service.get("stack", "unknown"))
        slug_roles[_safe_slug(name)] = _service_role(stack)

    for pipeline_path, yaml_text in pipelines.items():
        lower_path = pipeline_path.lower()
        role: str | None = None

        for slug, candidate_role in slug_roles.items():
            if slug and slug in lower_path:
                role = candidate_role
                break

        if role is None:
            if any(token in lower_path for token in ["frontend", "front", "ui", "web"]):
                role = "frontend"
            elif any(token in lower_path for token in ["backend", "back", "api", "service"]):
                role = "backend"
            elif "frontend" not in aligned:
                role = "frontend"
            else:
                role = "backend"

        aligned[role] = {"path": pipeline_path, "yaml": yaml_text}

    return aligned

def run_cicd_once(
    *,
    architecture_data: dict[str, Any],
    provider: str,
    llm_model: str,
    llm_provider: str | None,
    require_real_llm: bool,
    reference_pipelines: dict[str, str] | None = None,
) -> dict[str, Any]:
    agent = CICDGenerationAgent()
    result = agent.run(
        architecture_data=architecture_data,
        provider=provider,
        include_llm=True,
        llm_model=llm_model,
        llm_provider=llm_provider,
        require_real_llm=require_real_llm,
        reference_pipelines=reference_pipelines or {},
        ask_provider=False,
    )
    return result.__dict__


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)

    arch_path = Path(args.arch)
    if not arch_path.exists():
        raise FileNotFoundError(f"Architecture file not found: {arch_path}")
    architecture_data = json.loads(arch_path.read_text(encoding="utf-8"))

    reference_pipelines_for_generation: dict[str, str] = {}
    if args.judge_type == "evaluation-correction-avec-reference":
        ref_backend_path = Path(args.reference_backend_pipeline)
        ref_frontend_path = Path(args.reference_frontend_pipeline)
        if not ref_backend_path.exists():
            raise FileNotFoundError(f"Backend reference pipeline not found: {ref_backend_path}")
        if not ref_frontend_path.exists():
            raise FileNotFoundError(f"Frontend reference pipeline not found: {ref_frontend_path}")

        reference_pipelines_for_generation = {
            "backend": ref_backend_path.read_text(encoding="utf-8"),
            "frontend": ref_frontend_path.read_text(encoding="utf-8"),
        }

    result_a = run_cicd_once(
        architecture_data=architecture_data,
        provider=args.provider,
        llm_model=args.model_a,
        llm_provider=args.llm_provider,
        require_real_llm=args.require_real_cicd_llm,
        reference_pipelines=reference_pipelines_for_generation,
    )
    result_b = run_cicd_once(
        architecture_data=architecture_data,
        provider=args.provider,
        llm_model=args.model_b,
        llm_provider=args.llm_provider,
        require_real_llm=args.require_real_cicd_llm,
        reference_pipelines=reference_pipelines_for_generation,
    )

    if result_a.get("success") and isinstance(result_a.get("data"), dict):
        pipelines_a = result_a["data"].get("pipelines", {})
        if isinstance(pipelines_a, dict):
            result_a["data"]["written_pipeline_files"] = write_pipeline_files(
                pipelines_a,
                Path(args.pipelines_root_a),
            )

    if result_b.get("success") and isinstance(result_b.get("data"), dict):
        pipelines_b = result_b["data"].get("pipelines", {})
        if isinstance(pipelines_b, dict):
            result_b["data"]["written_pipeline_files"] = write_pipeline_files(
                pipelines_b,
                Path(args.pipelines_root_b),
            )

    save_json_output(Path(args.output_a), result_a)
    save_json_output(Path(args.output_b), result_b)

    workflow_a = {
        "agents": {
            "cicd_generation_agent": {
                "success": bool(result_a.get("success", False)),
                "data": result_a.get("data", {}),
                "duration_seconds": result_a.get("duration_seconds"),
            }
        }
    }
    workflow_b = {
        "agents": {
            "cicd_generation_agent": {
                "success": bool(result_b.get("success", False)),
                "data": result_b.get("data", {}),
                "duration_seconds": result_b.get("duration_seconds"),
            }
        }
    }

    judge = LLMJudge()

    if args.judge_type == "evaluation-correction-avec-reference":
        reference_pipelines = reference_pipelines_for_generation

        pipelines_a_raw = result_a.get("data", {}).get("pipelines", {}) if isinstance(result_a.get("data", {}), dict) else {}
        pipelines_b_raw = result_b.get("data", {}).get("pipelines", {}) if isinstance(result_b.get("data", {}), dict) else {}

        aligned_a = _align_pipelines_by_role(architecture_data, pipelines_a_raw if isinstance(pipelines_a_raw, dict) else {})
        aligned_b = _align_pipelines_by_role(architecture_data, pipelines_b_raw if isinstance(pipelines_b_raw, dict) else {})

        report = judge.compare_cicd_with_reference(
            model_a=args.model_a,
            model_b=args.model_b,
            generated_model_a=aligned_a,
            generated_model_b=aligned_b,
            reference_pipelines=reference_pipelines,
            judge_model=args.judge_model or default_judge_model_for_provider(args.judge_provider),
            judge_provider=args.judge_provider,
            require_real_judge=args.require_real_judge,
            evaluation_type="evaluation_correction_avec_reference",
        )
    else:
        report = judge.compare_models(
            model_a=args.model_a,
            model_b=args.model_b,
            workflow_a=workflow_a,
            workflow_b=workflow_b,
            judge_model=args.judge_model,
            judge_provider=args.judge_provider,
            require_real_judge=args.require_real_judge,
        )

    save_json_output(Path(args.judge_output), report)


if __name__ == "__main__":
    main()
