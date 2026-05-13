from __future__ import annotations

import ast
import difflib
import json
import logging
import os
import re
import time
from typing import Any

from shared import (
	get_default_model_for_provider,
	get_provider_connection,
	resolve_provider,
	strip_markdown_code_fence,
)

LOGGER = logging.getLogger(__name__)

AGENTS = [
	"architecture_analysis_agent",
	"devsecops_security_agent",
	"cicd_generation_agent",
	"deployment_agent",
	"chaos_engineering_agent",
	"incident_response_agent",
]


class LLMJudge:
	def compare_cicd_with_reference(
		self,
		*,
		model_a: str,
		model_b: str,
		generated_model_a: dict[str, dict[str, str]],
		generated_model_b: dict[str, dict[str, str]],
		reference_pipelines: dict[str, str],
		judge_model: str | None = None,
		judge_provider: str | None = None,
		require_real_judge: bool = False,
		evaluation_type: str = "evaluation_correction_avec_reference",
	) -> dict[str, Any]:
		heuristic = self._compute_cicd_reference_metrics(
			model_a=model_a,
			model_b=model_b,
			generated_model_a=generated_model_a,
			generated_model_b=generated_model_b,
			reference_pipelines=reference_pipelines,
		)

		metrics, workflow_a, workflow_b = self._reference_metrics_to_judge_inputs(
			model_a=model_a,
			model_b=model_b,
			heuristic=heuristic,
			generated_model_a=generated_model_a,
			generated_model_b=generated_model_b,
			reference_pipelines=reference_pipelines,
		)

		llm_judgement = self._run_llm_judge(
			model_a=model_a,
			model_b=model_b,
			metrics=metrics,
			workflow_a=workflow_a,
			workflow_b=workflow_b,
			judge_model=judge_model,
			judge_provider=judge_provider,
			require_real_judge=require_real_judge,
		)

		return {
			"evaluation_type": evaluation_type,
			"decision_source": "reference_metrics_and_llm_judge",
			"models": {"a": model_a, "b": model_b},
			"reference_roles": sorted(list(reference_pipelines.keys())),
			"reference_metrics": heuristic,
			"judge": llm_judgement,
		}

	def _reference_metrics_to_judge_inputs(
		self,
		*,
		model_a: str,
		model_b: str,
		heuristic: dict[str, Any],
		generated_model_a: dict[str, dict[str, str]],
		generated_model_b: dict[str, dict[str, str]],
		reference_pipelines: dict[str, str],
	) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
		global_block = heuristic.get("global", {}) if isinstance(heuristic, dict) else {}
		avg_a = float(global_block.get("model_a_average", 0.0) or 0.0)
		avg_b = float(global_block.get("model_b_average", 0.0) or 0.0)

		delta = avg_a - avg_b
		if abs(delta) < 1e-9:
			winner = "tie"
		else:
			winner = model_a if delta > 0 else model_b

		metrics = {
			"agents": {
				"cicd_generation_agent": {
					"model_a": {"overall_score": round(avg_a, 3)},
					"model_b": {"overall_score": round(avg_b, 3)},
					"winner": winner,
					"score_delta": round(abs(delta), 3),
				}
			},
			"global": {
				"wins": {
					model_a: 1 if winner == model_a else 0,
					model_b: 1 if winner == model_b else 0,
					"tie": 1 if winner == "tie" else 0,
				},
				"overall_winner": winner,
			},
		}

		workflow_a = {
			"agents": {
				"cicd_generation_agent": {
					"success": True,
					"data": {
						"provider": "github",
						"pipelines": generated_model_a,
						"reference_roles": sorted(list(reference_pipelines.keys())),
					},
					"duration_seconds": 0.0,
				}
			}
		}
		workflow_b = {
			"agents": {
				"cicd_generation_agent": {
					"success": True,
					"data": {
						"provider": "github",
						"pipelines": generated_model_b,
						"reference_roles": sorted(list(reference_pipelines.keys())),
					},
					"duration_seconds": 0.0,
				}
			}
		}

		return metrics, workflow_a, workflow_b

	def compare_models(
		self,
		model_a: str,
		model_b: str,
		workflow_a: dict[str, Any],
		workflow_b: dict[str, Any],
		judge_model: str | None = None,
		judge_provider: str | None = None,
		require_real_judge: bool = False,
	) -> dict[str, Any]:
		metrics = self._compute_metrics(model_a, model_b, workflow_a, workflow_b)
		llm_judgement = self._run_llm_judge(
			model_a=model_a,
			model_b=model_b,
			metrics=metrics,
			workflow_a=workflow_a,
			workflow_b=workflow_b,
			judge_model=judge_model,
			judge_provider=judge_provider,
			require_real_judge=require_real_judge,
		)
		return {
			"models": {"a": model_a, "b": model_b},
			"metrics": metrics,
			"judge": llm_judgement,
		}

	@staticmethod
	def _resolve_judge_provider(judge_provider: str | None) -> str:
		if judge_provider is None:
			return resolve_provider("JUDGE_LLM_PROVIDER", default_provider="ollama")

		normalized = str(judge_provider).strip().lower()
		if normalized in {"openrouter", "groq", "ollama"}:
			return normalized

		raise ValueError(
			f"Unsupported judge provider '{judge_provider}'. Supported providers: openrouter, groq, ollama"
		)

	def _parse_llm_json_object(self, raw_content: str) -> dict[str, Any]:
		value = strip_markdown_code_fence(str(raw_content or "")).strip()
		if not value:
			raise ValueError("LLM returned empty content")

		candidates: list[str] = [value]
		start = value.find("{")
		end = value.rfind("}")
		if start != -1 and end != -1 and end > start:
			extracted = value[start : end + 1].strip()
			if extracted and extracted not in candidates:
				candidates.append(extracted)

		for candidate in candidates:
			try:
				parsed = json.loads(candidate)
				if isinstance(parsed, dict):
					return parsed
			except Exception:
				pass

		for candidate in candidates:
			try:
				parsed = ast.literal_eval(candidate)
				if isinstance(parsed, dict):
					return parsed
			except Exception:
				pass

		raise ValueError("LLM output is not a valid JSON object")

	def _compute_cicd_reference_metrics(
		self,
		*,
		model_a: str,
		model_b: str,
		generated_model_a: dict[str, dict[str, str]],
		generated_model_b: dict[str, dict[str, str]],
		reference_pipelines: dict[str, str],
	) -> dict[str, Any]:
		roles = sorted(set(reference_pipelines.keys()) | set(generated_model_a.keys()) | set(generated_model_b.keys()))
		per_role: dict[str, Any] = {}
		total_a = 0.0
		total_b = 0.0

		for role in roles:
			reference_yaml = str(reference_pipelines.get(role, "") or "")
			candidate_a = generated_model_a.get(role, {})
			candidate_b = generated_model_b.get(role, {})
			yaml_a = str(candidate_a.get("yaml", "") or "")
			yaml_b = str(candidate_b.get("yaml", "") or "")

			score_a = self._cicd_reference_score(reference_yaml, yaml_a)
			score_b = self._cicd_reference_score(reference_yaml, yaml_b)

			if abs(score_a["overall_score"] - score_b["overall_score"]) < 1e-9:
				winner = "tie"
			else:
				winner = model_a if score_a["overall_score"] > score_b["overall_score"] else model_b

			per_role[role] = {
				"winner": winner,
				"similarity_threshold": score_a["similarity_threshold"],
				"model_a": {
					"score": score_a["overall_score"],
					"similarity_pass": score_a["similarity_pass"],
					"stage_coverage": score_a["stage_coverage"],
					"trigger_coverage": score_a["trigger_coverage"],
					"security_coverage": score_a["security_coverage"],
					"docker_coverage": score_a["docker_coverage"],
					"text_similarity": score_a["text_similarity"],
					"pipeline_path": candidate_a.get("path"),
				},
				"model_b": {
					"score": score_b["overall_score"],
					"similarity_pass": score_b["similarity_pass"],
					"stage_coverage": score_b["stage_coverage"],
					"trigger_coverage": score_b["trigger_coverage"],
					"security_coverage": score_b["security_coverage"],
					"docker_coverage": score_b["docker_coverage"],
					"text_similarity": score_b["text_similarity"],
					"pipeline_path": candidate_b.get("path"),
				},
			}

			total_a += float(score_a["overall_score"])
			total_b += float(score_b["overall_score"])

		count = max(1, len(roles))
		avg_a = round(total_a / count, 3)
		avg_b = round(total_b / count, 3)

		if abs(avg_a - avg_b) < 1e-9:
			global_winner = "tie"
		else:
			global_winner = model_a if avg_a > avg_b else model_b

		return {
			"per_role": per_role,
			"global": {
				"model_a_average": avg_a,
				"model_b_average": avg_b,
				"overall_winner": global_winner,
				"similarity_threshold": float(os.getenv("CICD_REFERENCE_SIMILARITY_THRESHOLD", "0.30")),
			},
		}


	def _cicd_reference_score(self, reference_yaml: str, candidate_yaml: str) -> dict[str, float]:
		candidate = (candidate_yaml or "").lower()
		reference = (reference_yaml or "").lower()
		similarity_threshold = float(os.getenv("CICD_REFERENCE_SIMILARITY_THRESHOLD", "0.30"))

		if not candidate.strip():
			return {
				"overall_score": 0.0,
				"similarity_pass": False,
				"similarity_threshold": round(similarity_threshold, 3),
				"stage_coverage": 0.0,
				"trigger_coverage": 0.0,
				"security_coverage": 0.0,
				"docker_coverage": 0.0,
				"text_similarity": 0.0,
			}

		stage_markers = ["test", "build", "package", "artifact", "security", "deploy"]
		stage_hits = sum(1 for token in stage_markers if token in candidate)
		stage_coverage = stage_hits / len(stage_markers)

		trigger_tokens = ["push", "pull_request", "merge_request", "pr:"]
		trigger_coverage = 1.0 if ("push" in candidate and any(tok in candidate for tok in trigger_tokens[1:])) else 0.5 if "push" in candidate else 0.0

		security_coverage = 1.0 if any(tok in candidate for tok in ["trivy", "snyk", "security", "scan"]) else 0.0
		docker_coverage = 1.0 if any(tok in candidate for tok in ["docker", "image", "build-push"]) else 0.0

		similarity = 0.0
		if reference.strip():
			similarity = difflib.SequenceMatcher(
				None,
				reference[:50000],
				candidate[:50000],
			).ratio()

		overall_score_raw = round(
			100.0
			* (
				0.35 * similarity
				+ 0.30 * stage_coverage
				+ 0.15 * trigger_coverage
				+ 0.10 * security_coverage
				+ 0.10 * docker_coverage
			),
			3,
		)

		similarity_pass = similarity >= similarity_threshold
		overall_score = overall_score_raw if similarity_pass else 0.0

		return {
			"overall_score": overall_score,
			"similarity_pass": similarity_pass,
			"similarity_threshold": round(similarity_threshold, 3),
			"stage_coverage": round(stage_coverage, 3),
			"trigger_coverage": round(trigger_coverage, 3),
			"security_coverage": round(security_coverage, 3),
			"docker_coverage": round(docker_coverage, 3),
			"text_similarity": round(similarity, 3),
		}




	def _compute_metrics(
		self,
		model_a: str,
		model_b: str,
		workflow_a: dict[str, Any],
		workflow_b: dict[str, Any],
	) -> dict[str, Any]:
		report: dict[str, Any] = {"agents": {}}
		agent_names = sorted(
			set(workflow_a.get("agents", {}).keys()) | set(workflow_b.get("agents", {}).keys())
		)
		if not agent_names:
			agent_names = AGENTS

		for agent in agent_names:
			output_a = workflow_a.get("agents", {}).get(agent, {})
			output_b = workflow_b.get("agents", {}).get(agent, {})

			metrics_a = self._agent_metrics(output_a, agent)
			metrics_b = self._agent_metrics(output_b, agent)

			delta = metrics_a["overall_score"] - metrics_b["overall_score"]
			if abs(delta) < 1e-9:
				winner = "tie"
			else:
				winner = model_a if delta > 0 else model_b
			report["agents"][agent] = {
				"model_a": metrics_a,
				"model_b": metrics_b,
				"winner": winner,
				"score_delta": round(abs(delta), 3),
			}

		wins_a = sum(1 for item in report["agents"].values() if item["winner"] == model_a)
		wins_b = sum(1 for item in report["agents"].values() if item["winner"] == model_b)
		ties = sum(1 for item in report["agents"].values() if item["winner"] == "tie")
		report["global"] = {
			"wins": {model_a: wins_a, model_b: wins_b, "tie": ties},
			"overall_winner": "tie" if wins_a == wins_b else (model_a if wins_a > wins_b else model_b),
		}
		return report

	def _agent_metrics(self, agent_output: dict[str, Any], agent_name: str) -> dict[str, Any]:
		success = bool(agent_output.get("success", False))
		data = agent_output.get("data", {}) if isinstance(agent_output.get("data", {}), dict) else {}
		data = self._normalize_agent_data(agent_name, data)

		completeness = self._structure_completeness(data, agent_name)
		actionability = self._actionability_score(data)
		consistency = self._consistency_score(data)
		architecture_llm_quality = self._architecture_llm_quality(data) if agent_name == "architecture_analysis_agent" else 0.0
		devsecops_llm_quality = self._devsecops_llm_quality(data) if agent_name == "devsecops_security_agent" else 0.0
		latency_raw = agent_output.get("duration_seconds")
		latency: float | None
		try:
			latency = float(latency_raw) if latency_raw is not None else None
		except Exception:
			latency = None
		efficiency = 0.5 if (latency is None or latency <= 0) else max(0.1, min(1.0, 60.0 / max(latency, 1.0)))

		if agent_name == "architecture_analysis_agent":
			overall_score = (
				(30 if success else 0)
				+ (20 * completeness)
				+ (10 * actionability)
				+ (10 * consistency)
				+ (5 * efficiency)
				+ (25 * architecture_llm_quality)
			)
		elif agent_name == "devsecops_security_agent":
			overall_score = (
				(30 if success else 0)
				+ (20 * completeness)
				+ (10 * actionability)
				+ (10 * consistency)
				+ (5 * efficiency)
				+ (25 * devsecops_llm_quality)
			)
		else:
			overall_score = (
				(35 if success else 0)
				+ (30 * completeness)
				+ (20 * actionability)
				+ (10 * consistency)
				+ (5 * efficiency)
			)

		metrics = {
			"success": success,
			"structure_completeness": round(completeness, 3),
			"actionability": round(actionability, 3),
			"consistency": round(consistency, 3),
			"latency_seconds": round(latency, 4) if latency is not None else None,
			"efficiency": round(efficiency, 3),
			"overall_score": round(overall_score, 3),
		}
		if agent_name == "architecture_analysis_agent":
			metrics["architecture_llm_quality"] = round(architecture_llm_quality, 3)
		if agent_name == "devsecops_security_agent":
			metrics["devsecops_llm_quality"] = round(devsecops_llm_quality, 3)
		return metrics

	def _normalize_agent_data(self, agent_name: str, data: dict[str, Any]) -> dict[str, Any]:
		if not isinstance(data, dict):
			return {}

		nested = data.get("data")
		if not isinstance(nested, dict):
			return data

		expected_keys_map = {
			"architecture_analysis_agent": ["services", "dependencies", "graph", "mermaid_diagram"],
			"devsecops_security_agent": ["summary", "risk_score", "tool_status", "findings"],
			"cicd_generation_agent": ["summary", "pipelines"],
			"deployment_agent": ["summary", "deployment_plan", "generated_manifests"],
			"chaos_engineering_agent": ["summary", "experiments"],
			"incident_response_agent": ["summary", "runbook", "incident_actions"],
		}
		expected = expected_keys_map.get(agent_name, ["summary"])

		has_expected_top = any(key in data for key in expected)
		has_expected_nested = any(key in nested for key in expected)
		if has_expected_nested and not has_expected_top:
			return nested

		wrapper_keys = {"agent_name", "protocol", "success", "data", "error", "duration_seconds"}
		if set(data.keys()).issubset(wrapper_keys) and nested:
			return nested

		return data

	def _structure_completeness(self, data: dict[str, Any], agent_name: str) -> float:
		required_map = {
			"architecture_analysis_agent": ["services", "dependencies", "graph", "mermaid_diagram"],
			"devsecops_security_agent": ["summary", "risk_score", "tool_status", "findings"],
			"cicd_generation_agent": ["summary", "pipelines"],
			"deployment_agent": ["summary", "deployment_plan", "generated_manifests"],
			"chaos_engineering_agent": ["summary", "experiments"],
			"incident_response_agent": ["summary", "runbook", "incident_actions"],
		}
		required = required_map.get(agent_name, ["summary"])
		present = sum(1 for key in required if key in data and data[key] is not None)
		return present / len(required)

	def _actionability_score(self, data: dict[str, Any]) -> float:
		llm_output = data.get("llm_output", {}) if isinstance(data.get("llm_output"), dict) else {}
		candidate_lists = [
			data.get("recommendations", []),
			data.get("remediation_priorities", []),
			llm_output.get("recommendations", []),
			llm_output.get("remediation_priorities", []),
			llm_output.get("compliance_notes", []),
			data.get("deployment_plan", []),
			data.get("runbook", []),
			data.get("incident_actions", []),
			data.get("experiments", []),
		]
		items = 0
		for value in candidate_lists:
			if isinstance(value, list):
				items += len(value)
		return min(1.0, items / 12.0)

	def _architecture_llm_quality(self, data: dict[str, Any]) -> float:
		llm_output = data.get("llm_output", {}) if isinstance(data.get("llm_output"), dict) else {}
		summary = str(llm_output.get("architecture_summary", ""))
		diagram_desc = str(llm_output.get("system_diagram_description", ""))
		recommendations = llm_output.get("recommendations", [])

		if not isinstance(recommendations, list):
			recommendations = []

		rec_text = "\n".join(str(item) for item in recommendations)
		text_blob = f"{summary}\n{diagram_desc}\n{rec_text}".lower()
		services = data.get("services", []) if isinstance(data.get("services"), list) else []
		dependencies = data.get("dependencies", []) if isinstance(data.get("dependencies"), list) else []

		service_names = [str(svc.get("name", "")).lower() for svc in services if svc.get("name")]
		service_mentions = sum(1 for name in service_names if name and name in text_blob)
		service_coverage = 1.0 if not service_names else min(1.0, service_mentions / len(service_names))

		ports: list[str] = []
		for svc in services:
			for port in svc.get("exposed_ports", []) if isinstance(svc.get("exposed_ports"), list) else []:
				ports.append(str(port))
		port_mentions = sum(1 for port in set(ports) if port in text_blob)
		port_coverage = 1.0 if not ports else min(1.0, port_mentions / len(set(ports)))

		has_db = any(
			isinstance(svc.get("database_connections"), list) and len(svc.get("database_connections", [])) > 0
			for svc in services
		)
		db_coverage = 1.0 if (not has_db or any(token in text_blob for token in ["mysql", "jdbc", "database", "db"])) else 0.0

		dep_tokens: set[str] = set()
		for dep in dependencies:
			if isinstance(dep, dict):
				for key in ["from", "to", "source", "target", "name", "type"]:
					value = dep.get(key)
					if value:
						dep_tokens.update(re.findall(r"[a-z0-9_\-]+", str(value).lower()))
		dep_tokens = {token for token in dep_tokens if len(token) >= 4}
		dep_mentions = sum(1 for token in dep_tokens if token in text_blob)
		dep_coverage = 1.0 if not dep_tokens else min(1.0, dep_mentions / len(dep_tokens))

		specificity_tokens = [
			"cve-",
			"port",
			"kubernetes",
			"docker",
			"api gateway",
			"reverse proxy",
			"prometheus",
			"grafana",
			"health",
		]
		specific_recs = 0
		for rec in recommendations:
			rec_text = str(rec).lower()
			if any(token in rec_text for token in specificity_tokens) or any(ch.isdigit() for ch in rec_text):
				specific_recs += 1
		rec_specificity = 0.0 if not recommendations else min(1.0, specific_recs / len(recommendations))

		words = re.findall(r"[a-z0-9_\-]+", text_blob)
		word_count = len(words)
		unique_words = len(set(words))
		detail_depth = min(1.0, word_count / 220.0)
		lexical_diversity = 0.0 if word_count == 0 else min(1.0, unique_words / word_count)

		return (
			0.24 * service_coverage
			+ 0.14 * port_coverage
			+ 0.1 * db_coverage
			+ 0.2 * dep_coverage
			+ 0.2 * rec_specificity
			+ 0.07 * detail_depth
			+ 0.05 * lexical_diversity
		)

	def _devsecops_llm_quality(self, data: dict[str, Any]) -> float:
		llm_output = data.get("llm_output", {}) if isinstance(data.get("llm_output"), dict) else {}
		summary = str(llm_output.get("security_summary", ""))
		remediation = llm_output.get("remediation_priorities", [])
		compliance = llm_output.get("compliance_notes", [])
		findings = data.get("findings", []) if isinstance(data.get("findings"), list) else []

		if not isinstance(remediation, list):
			remediation = []
		if not isinstance(compliance, list):
			compliance = []

		rem_text = "\n".join(str(item) for item in remediation)
		compliance_text = "\n".join(str(item) for item in compliance)
		text_blob = f"{summary}\n{rem_text}\n{compliance_text}".lower()

		severity_counts = self._severity_counts(findings)
		critical = severity_counts.get("critical", 0)
		high = severity_counts.get("high", 0)
		medium = severity_counts.get("medium", 0)

		# Reward explicit risk framing when severe findings exist.
		risk_tokens = ["critical", "high", "rce", "exposed", "secret", "xss", "cve"]
		risk_framing = min(1.0, sum(1 for token in risk_tokens if token in text_blob) / 5.0)
		if (critical + high + medium) == 0:
			risk_framing = 1.0

		ids = [
			str(item.get("id", "")).lower()
			for item in findings
			if isinstance(item, dict) and item.get("id")
		]
		unique_ids: list[str] = []
		for vuln_id in ids:
			if vuln_id not in unique_ids:
				unique_ids.append(vuln_id)
		id_pool = unique_ids[:20]
		id_mentions = sum(1 for vuln_id in id_pool if vuln_id and vuln_id in text_blob)
		evidence_coverage = 1.0 if not id_pool else min(1.0, id_mentions / len(id_pool))

		secret_count = sum(
			1 for item in findings
			if isinstance(item, dict) and str(item.get("type", "")).lower() == "secret"
		)
		secret_awareness = 1.0 if secret_count == 0 else (1.0 if "secret" in text_blob else 0.0)

		rec_specific = 0
		for rec in remediation:
			rec_text = str(rec).lower()
			if any(marker in rec_text for marker in ["cve-", "ghsa-", "upgrade", "rotate", "patch", "spring", "tomcat", "angular"]):
				rec_specific += 1
		rec_specificity = 0.0 if not remediation else min(1.0, rec_specific / len(remediation))

		compliance_specific = 0
		for note in compliance:
			note_text = str(note).lower()
			if any(marker in note_text for marker in ["pci", "gdpr", "owasp", "soc 2", "iso", "nist", "cis"]):
				compliance_specific += 1
		compliance_quality = 0.0 if not compliance else min(1.0, compliance_specific / len(compliance))

		depth = min(1.0, (len(remediation) + len(compliance)) / 14.0)

		return (
			0.2 * risk_framing
			+ 0.25 * evidence_coverage
			+ 0.15 * secret_awareness
			+ 0.2 * rec_specificity
			+ 0.1 * compliance_quality
			+ 0.1 * depth
		)

	def _severity_counts(self, findings: list[dict[str, Any]]) -> dict[str, int]:
		counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
		for finding in findings:
			if not isinstance(finding, dict):
				continue
			sev = str(finding.get("severity", "")).lower()
			if sev in counts:
				counts[sev] += 1
		return counts

	def _consistency_score(self, data: dict[str, Any]) -> float:
		score = 1.0
		risk_score = data.get("risk_score")
		if risk_score is not None:
			try:
				n = int(risk_score)
				if n < 0 or n > 100:
					score -= 0.3
			except Exception:
				score -= 0.4

		findings = data.get("findings")
		if findings is not None and not isinstance(findings, list):
			score -= 0.3

		summary = data.get("summary")
		if summary is not None and not isinstance(summary, str):
			score -= 0.2
		return max(0.0, score)

	def _run_llm_judge(
		self,
		model_a: str,
		model_b: str,
		metrics: dict[str, Any],
		workflow_a: dict[str, Any],
		workflow_b: dict[str, Any],
		judge_model: str | None,
		judge_provider: str | None,
		require_real_judge: bool,
	) -> dict[str, Any]:
		provider = self._resolve_judge_provider(judge_provider)
		base_url, api_key = get_provider_connection(provider)
		if not api_key:
			if require_real_judge:
				raise RuntimeError(f"API key is required for provider '{provider}' when require_real_judge=True")
			return self._fallback_judgement(metrics, f"No API key configured for provider '{provider}'")

		try:
			from openai import OpenAI
		except ImportError:
			if require_real_judge:
				raise RuntimeError("openai package missing and require_real_judge=True")
			return self._fallback_judgement(metrics, "openai package missing")

		primary_model = (
			judge_model
			or os.getenv("JUDGE_MODEL")
			or get_default_model_for_provider(
				provider,
				openrouter_env="OPENROUTER_JUDGE_MODEL",
				openrouter_default="z-ai/glm-4.5-air:free",
				groq_env="GROQ_JUDGE_MODEL",
				groq_default=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
				ollama_env="OLLAMA_JUDGE_MODEL",
				ollama_default=os.getenv("OLLAMA_MODEL", "nemotron-3-super"),
			)
		)
		provider_fallback_env = {
			"openrouter": "OPENROUTER_JUDGE_FALLBACK_MODELS",
			"groq": "GROQ_JUDGE_FALLBACK_MODELS",
			"ollama": "OLLAMA_JUDGE_FALLBACK_MODELS",
		}.get(provider, "JUDGE_FALLBACK_MODELS")

		fallback_models = [
			item.strip()
			for item in ",".join(
				[
					os.getenv("JUDGE_FALLBACK_MODELS", ""),
					os.getenv(provider_fallback_env, ""),
				]
			).split(",")
			if item.strip()
		]
		models_to_try: list[str] = []
		for candidate in [primary_model, *fallback_models]:
			if candidate and candidate not in models_to_try:
				models_to_try.append(candidate)

		max_retries = int(os.getenv("LLM_JUDGE_MAX_RETRIES", "3"))
		retry_delay = float(os.getenv("LLM_JUDGE_RETRY_DELAY_SEC", "2"))
		client = OpenAI(base_url=base_url, api_key=api_key)

		prompt_payload = {
			"models": {"a": model_a, "b": model_b},
			"metrics": metrics,
			"output_samples": {
				"model_a": self._summarize_workflow(workflow_a),
				"model_b": self._summarize_workflow(workflow_b),
			},
		}

		prompt = f"""
You are an impartial LLM evaluator for DevOps agents.
Compare model A vs model B for each agent and return strict JSON with keys:
- evaluation_summary (string)
- per_agent (object: agent_name -> {{winner, rationale, confidence_0_to_1, metrics_comment}})
- global_winner (string)
- recommendations (array of short strings)

Use provided metrics and output samples. Prefer factual, concise reasoning.
Payload:
{json.dumps(prompt_payload, ensure_ascii=True)}
"""

		attempt_errors: list[str] = []
		for model in models_to_try:
			for attempt in range(1, max_retries + 1):
				try:
					response = client.chat.completions.create(
						model=model,
						messages=[{"role": "user", "content": prompt}],
					)
					content = str(response.choices[0].message.content or "")
					parsed = self._parse_llm_json_object(content)
					parsed.setdefault("judge_model_used", model)
					parsed.setdefault("judge_attempt", attempt)
					return parsed
				except Exception as exc:
					err = f"model={model}, attempt={attempt}/{max_retries}: {exc}"
					attempt_errors.append(err)
					LOGGER.warning("LLM judge failed on provider %s: %s", provider, err)
					if attempt < max_retries:
						time.sleep(max(retry_delay, 0.0) * attempt)

		reason = " | ".join(attempt_errors[-3:]) if attempt_errors else "Unknown judge failure"
		if require_real_judge:
			raise RuntimeError(f"LLM judge unavailable after retries: {reason}")
		return self._fallback_judgement(metrics, reason)

	def _summarize_workflow(self, workflow: dict[str, Any]) -> dict[str, Any]:
		out: dict[str, Any] = {}
		for agent_name, result in workflow.get("agents", {}).items():
			if not isinstance(result, dict):
				continue
			data = result.get("data", {}) if isinstance(result.get("data"), dict) else {}
			out[agent_name] = {
				"success": result.get("success"),
				"duration_seconds": result.get("duration_seconds"),
				"summary": data.get("summary") or data.get("security_summary") or data.get("architecture_summary"),
				"top_keys": sorted(list(data.keys()))[:12],
			}
		return out

	def _fallback_judgement(self, metrics: dict[str, Any], reason: str) -> dict[str, Any]:
		agents = metrics.get("agents", {})
		per_agent: dict[str, Any] = {}
		for agent_name, values in agents.items():
			winner = values.get("winner", "unknown")
			delta = values.get("score_delta", 0)
			per_agent[agent_name] = {
				"winner": winner,
				"rationale": f"Higher weighted metric score with delta {delta}.",
				"confidence_0_to_1": round(min(0.95, 0.5 + (float(delta) / 100.0)), 2),
				"metrics_comment": "Rule-based fallback judgement (LLM judge unavailable).",
			}

		global_winner = metrics.get("global", {}).get("overall_winner", "unknown")
		return {
			"evaluation_summary": "Fallback judgement generated from deterministic metrics.",
			"per_agent": per_agent,
			"global_winner": global_winner,
			"recommendations": [
				"Use the same benchmark prompt/template for repeated experiments.",
				"Track score trends over multiple runs instead of one-shot comparison.",
				f"Judge fallback used: {reason}",
			],
		}
