from __future__ import annotations

import logging
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared import save_json_output

from .agents import (
    ArchitectureAnalysisAgent,
    ChaosEngineeringAgent,
    CICDGenerationAgent,
    DeploymentAgent,
    DevSecOpsSecurityAgent,
    IncidentResponseAgent,
)
from .agents.cicd_generation import write_pipeline_files
from .agents.deployment.mongo_integration import MongoDBLogger
from .llm_judge import LLMJudge

LOGGER = logging.getLogger("devops_orchestrator")

# ---------------------------------------------------------------------------
# Best LLM model + provider per agent (from benchmark results).
# Each entry is (model, provider). The orchestrator sets the matching
# *_LLM_PROVIDER env var before invoking the agent so that shared.llm_common
# resolves the right backend.
# ---------------------------------------------------------------------------
BEST_MODELS: dict[str, dict[str, str]] = {
    "architecture": {"model": "z-ai/glm-5.1",          "provider": "nvidia"},
    "devsecops":    {"model": "minimaxai/minimax-m2.7", "provider": "nvidia"},
    "cicd":         {"model": "minimaxai/minimax-m2.7", "provider": "nvidia"},
    "deployment":   {"model": "minimaxai/minimax-m2.7", "provider": "nvidia"},
    "chaos":        {"model": "z-ai/glm-5.1",          "provider": "nvidia"},
    "incident":     {"model": "gpt-oss:120b",           "provider": "ollama"},
}

# Env-var prefix per agent — used to scope provider/model overrides.
_PROVIDER_ENV_KEYS: dict[str, str] = {
    "architecture": "ARCH_LLM_PROVIDER",
    "devsecops":    "DEVSECOPS_LLM_PROVIDER",
    "cicd":         "CICD_LLM_PROVIDER",
    "deployment":   "DEPLOYMENT_LLM_PROVIDER",
    "chaos":        "CHAOS_LLM_PROVIDER",
    "incident":     "INCIDENT_LLM_PROVIDER",
}


class DevOpsOrchestrator:
    """Workflow orchestrator coordinating all DevOps agents."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.architecture_agent = ArchitectureAnalysisAgent()
        self.security_agent = DevSecOpsSecurityAgent()
        self.cicd_agent = CICDGenerationAgent()
        self.deployment_agent = DeploymentAgent()
        # Chaos + incident-response are constructed lazily because they
        # depend on runtime config (Prometheus URL, output dir, etc.).
        self.chaos_agent: ChaosEngineeringAgent | None = None
        self.incident_agent: IncidentResponseAgent | None = None
        self.mongo = MongoDBLogger()

    # -------------------------------------------------------------------
    # Helpers — scope LLM provider env var to a single agent invocation
    # -------------------------------------------------------------------

    @staticmethod
    def _scoped_env(env_key: str | None, value: str | None):
        """Context manager-style helper: temporarily set env_key=value."""
        class _Scope:
            def __enter__(self_inner):
                self_inner._prev = os.environ.get(env_key) if env_key else None
                if env_key and value:
                    os.environ[env_key] = value
                return self_inner

            def __exit__(self_inner, *exc):
                if not env_key:
                    return False
                if self_inner._prev is None:
                    os.environ.pop(env_key, None)
                else:
                    os.environ[env_key] = self_inner._prev
                return False

        return _Scope()

    def run_full_workflow(
        self,
        repository_path: Path,
        output_dir: Path,
        include_llm: bool = False,
        strict_security_tools: bool = False,
        skip_image_scan: bool = False,
        llm_model: str | None = None,
        cicd_provider: str = "github",
        cicd_llm_provider: str | None = None,
        push_cicd_pipelines: bool = False,
        cicd_human_validation: bool = False,
        auto_approve_cicd: bool = False,
        cicd_push_commit_message: str | None = None,
        cicd_compare_model_a: str | None = None,
        cicd_compare_model_b: str | None = None,
        cicd_judge_model: str | None = None,
        cicd_judge_provider: str | None = "ollama",
        require_real_cicd_llm: bool = False,
        require_real_cicd_judge: bool = False,
        # Pipeline monitoring — watch GitHub Actions runs and auto-resolve failures
        monitor_pipelines: bool = False,
        github_token: str | None = None,
        github_repo: str | None = None,
        monitor_poll_interval: int = 30,
        monitor_timeout: int = 1800,
        auto_approve_fix: bool = False,
        auto_apply_fix: bool = False,
        target_env: str = "local",
        auto_approve_deployment: bool = False,
        deployment_llm_model: str | None = None,
        deployment_llm_provider: str | None = None,
        require_real_deployment_llm: bool = False,
        auto_approve_terraform: bool = False,
        skip_terraform_apply: bool = False,
        human_in_the_loop: bool = True,
        # ── Chaos engineering (opt-in) ────────────────────────────────────
        enable_chaos: bool = False,
        chaos_backend: str = "simulation",
        chaos_dry_run: bool = False,
        chaos_prometheus_url: str | None = None,
        chaos_grafana_url: str | None = None,
        chaos_grafana_api_key: str | None = None,
        chaos_experiment_duration: int = 60,
        chaos_observation_window: int = 30,
        chaos_max_experiments: int = 50,
        chaos_experiment_types: list[str] | None = None,
        chaos_namespace: str = "default",
        chaos_container_map: dict[str, str] | None = None,
        chaos_llm_model: str | None = None,
        chaos_llm_provider: str | None = None,
        # ── Incident response (auto-triggered on agent incidents) ─────────
        enable_incident_response: bool = True,
        incident_similarity_threshold: float = 0.7,
        incident_top_k: int = 3,
        incident_max_incidents: int = 20,
        incident_auto_apply: bool = False,
        incident_llm_model: str | None = None,
        incident_llm_provider: str | None = None,
    ) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        repository_path = repository_path.resolve()
        execution_id = str(uuid.uuid4())

        # Resolve per-agent (model, provider). Explicit overrides win;
        # otherwise fall back to BEST_MODELS.
        arch_model     = llm_model            or BEST_MODELS["architecture"]["model"]
        arch_provider  = BEST_MODELS["architecture"]["provider"]
        devsecops_model    = llm_model        or BEST_MODELS["devsecops"]["model"]
        devsecops_provider = BEST_MODELS["devsecops"]["provider"]
        cicd_model     = llm_model            or BEST_MODELS["cicd"]["model"]
        cicd_prov      = cicd_llm_provider    or BEST_MODELS["cicd"]["provider"]
        deploy_model   = deployment_llm_model or BEST_MODELS["deployment"]["model"]
        deploy_prov    = deployment_llm_provider or BEST_MODELS["deployment"]["provider"]
        chaos_model    = chaos_llm_model      or BEST_MODELS["chaos"]["model"]
        chaos_prov     = chaos_llm_provider   or BEST_MODELS["chaos"]["provider"]
        inc_model      = incident_llm_model   or BEST_MODELS["incident"]["model"]
        inc_prov       = incident_llm_provider or BEST_MODELS["incident"]["provider"]

        # Track all incident responses produced during the workflow,
        # one entry per agent that produced incidents.
        incident_runs: list[dict[str, Any]] = []

        # ---------------------------------------------------------------
        # 1. Architecture Analysis (LLM: glm-5:cloud / ollama)
        # ---------------------------------------------------------------
        LOGGER.info("[architecture] model=%s provider=%s", arch_model, arch_provider)
        architecture_output_path = output_dir / "architecture-analysis.json"
        architecture_data = self._run_architecture_agent(
            repository_path,
            include_llm=include_llm,
            llm_model=arch_model,
            llm_provider=arch_provider,
        )
        save_json_output(architecture_output_path, architecture_data)
        if architecture_data.get("mermaid_diagram"):
            (output_dir / "architecture-diagram.mmd").write_text(
                architecture_data["mermaid_diagram"], encoding="utf-8"
            )

        arch_incidents = self._log_agent_output(
            agent_name="architecture_analysis_agent",
            status="success",
            data=architecture_data,
            execution_id=execution_id,
            repository_path=str(repository_path),
        )
        self._maybe_run_incident_response(
            triggered_by="architecture_analysis_agent",
            incidents_count=arch_incidents,
            enable_incident_response=enable_incident_response,
            architecture_data=architecture_data,
            output_dir=output_dir,
            repository_path=repository_path,
            execution_id=execution_id,
            similarity_threshold=incident_similarity_threshold,
            top_k=incident_top_k,
            max_incidents=incident_max_incidents,
            auto_apply=incident_auto_apply,
            llm_model=inc_model,
            llm_provider=inc_prov,
            incident_runs=incident_runs,
        )

        LOGGER.info("Architecture analysis complete.")

        # ---------------------------------------------------------------
        # 2. DevSecOps Security (LLM: minimax-m2.7:cloud / ollama)
        # ---------------------------------------------------------------
        LOGGER.info("[devsecops] model=%s provider=%s", devsecops_model, devsecops_provider)
        with self._scoped_env(_PROVIDER_ENV_KEYS["devsecops"], devsecops_provider):
            security_result = self.security_agent.run(
                repository_path=repository_path,
                architecture_data=architecture_data,
                include_llm=include_llm,
                strict_tools=strict_security_tools,
                skip_image_scan=skip_image_scan,
                llm_model=devsecops_model,
            )
        save_json_output(output_dir / "devsecops-security.json", security_result.__dict__)

        sec_incidents = self._log_agent_output(
            agent_name="devsecops_security_agent",
            status="success" if security_result.success else "failed",
            data=security_result.__dict__,
            execution_id=execution_id,
            repository_path=str(repository_path),
        )
        self._maybe_run_incident_response(
            triggered_by="devsecops_security_agent",
            incidents_count=sec_incidents,
            enable_incident_response=enable_incident_response,
            architecture_data=architecture_data,
            security_data=security_result.__dict__,
            output_dir=output_dir,
            repository_path=repository_path,
            execution_id=execution_id,
            similarity_threshold=incident_similarity_threshold,
            top_k=incident_top_k,
            max_incidents=incident_max_incidents,
            auto_apply=incident_auto_apply,
            llm_model=inc_model,
            llm_provider=inc_prov,
            incident_runs=incident_runs,
        )

        if strict_security_tools and not security_result.success:
            raise RuntimeError(
                f"Security gate failed in strict mode: {security_result.error or 'unknown error'}"
            )

        # Human-in-the-loop: review security findings
        if human_in_the_loop and security_result.success:
            action = self._prompt_security_review(security_result.__dict__)
            if action == "rescan":
                LOGGER.info("User requested rescan after fixing vulnerabilities.")
                with self._scoped_env(_PROVIDER_ENV_KEYS["devsecops"], devsecops_provider):
                    security_result = self.security_agent.run(
                        repository_path=repository_path,
                        architecture_data=architecture_data,
                        include_llm=include_llm,
                        strict_tools=strict_security_tools,
                        skip_image_scan=skip_image_scan,
                        llm_model=devsecops_model,
                    )
                save_json_output(output_dir / "devsecops-security.json", security_result.__dict__)
                self._log_agent_output(
                    agent_name="devsecops_security_agent",
                    status="success" if security_result.success else "failed",
                    data=security_result.__dict__,
                    execution_id=execution_id,
                    repository_path=str(repository_path),
                    extra={"rescan": True},
                )

        # ---------------------------------------------------------------
        # 3. CI/CD Generation (LLM: minimax-m2.7:cloud / ollama)
        # ---------------------------------------------------------------
        LOGGER.info("[cicd] model=%s provider=%s", cicd_model, cicd_prov)
        # Detect the repo's actual default branch so generated pipeline
        # triggers match the branch that will be pushed.
        repo_branch_result = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repository_path)
        repo_default_branch = repo_branch_result.stdout.strip() if repo_branch_result.returncode == 0 else "main"
        LOGGER.info("Detected repository default branch: %s", repo_default_branch)

        with self._scoped_env(_PROVIDER_ENV_KEYS["cicd"], cicd_prov):
            cicd_result = self.cicd_agent.run(
                repository_path=repository_path,
                architecture_data=architecture_data,
                provider=cicd_provider,
                include_llm=include_llm,
                llm_model=cicd_model,
                llm_provider=cicd_prov,
                require_real_llm=require_real_cicd_llm,
                # Pass the detected branch so templates use the correct trigger
                main_branch=repo_default_branch,
            )

        written_pipeline_files: list[str] = []
        output_pipeline_files: list[str] = []
        output_pipeline_dir = output_dir / "generated-pipelines"
        if cicd_result.success:
            pipelines = cicd_result.data.get("pipelines", {}) if isinstance(cicd_result.data, dict) else {}
            if isinstance(pipelines, dict) and pipelines:
                # Write to output_dir — single source of truth
                output_pipeline_files = write_pipeline_files(pipelines, output_pipeline_dir)
                LOGGER.info("Pipeline files saved to: %s", str(output_pipeline_dir.resolve()))
                print(f"\nPipeline files saved to: {output_pipeline_dir.resolve()}")
                for f in output_pipeline_files:
                    print(f"  -> {output_pipeline_dir / f}")

                # Copy from outputs into the cloned repo so git push picks them up
                written_pipeline_files = self._copy_pipelines_to_repo(
                    output_pipeline_dir=output_pipeline_dir,
                    pipeline_files=output_pipeline_files,
                    repository_path=repository_path,
                )

        push_result: dict[str, Any] = {
            "enabled": push_cicd_pipelines,
            "human_validation_required": cicd_human_validation,
            "approved": None,
            "pushed": False,
            "github_actions_trigger_expected": False,
            "status": "not-requested",
            "message": "Push step was not requested.",
        }

        if push_cicd_pipelines:
            approved = self._approve_cicd_push(
                cicd_human_validation=cicd_human_validation,
                auto_approve_cicd=auto_approve_cicd,
                pipeline_files=written_pipeline_files,
            )
            push_result["approved"] = approved

            if not cicd_result.success:
                push_result.update(
                    {
                        "status": "skipped-generation-failed",
                        "message": "CI/CD generation failed; push skipped.",
                    }
                )
            elif not written_pipeline_files:
                push_result.update(
                    {
                        "status": "skipped-no-files",
                        "message": "No generated pipeline files to push.",
                    }
                )
            elif not approved:
                push_result.update(
                    {
                        "status": "skipped-not-approved",
                        "message": "Human validation rejected or not confirmed. Push skipped.",
                    }
                )
            else:
                push_result = self._commit_and_push_pipeline_files(
                    repository_path=repository_path,
                    pipeline_files=written_pipeline_files,
                    commit_message=cicd_push_commit_message,
                )
                # Log push outcome so nothing is silent
                push_status = push_result.get("status", "unknown")
                if push_result.get("pushed"):
                    LOGGER.info("Pipeline push succeeded (status=%s)", push_status)
                else:
                    LOGGER.warning(
                        "Pipeline push did not complete (status=%s): %s",
                        push_status,
                        push_result.get("message", ""),
                    )

        # ── Pipeline monitoring ──────────────────────────────────────────
        # If the push succeeded and monitoring is enabled, watch the triggered
        # GitHub Actions runs.  Failures are stored in MongoDB and forwarded
        # to the Incident Response Agent automatically.
        monitor_results: list[dict[str, Any]] = []

        # When push is blocked by secret scanning, fixes generated by the
        # incident agent will hit the same wall — so skip monitoring entirely
        # and surface a single clear instruction to the user.
        if monitor_pipelines and push_result.get("status") == "push-protection-blocked":
            bypass_url = push_result.get("bypass_url", "")
            print("\n" + "=" * 64)
            print("  MONITORING SKIPPED — fix the push protection first.")
            print("  Pipeline files are queued locally but cannot reach GitHub,")
            print("  so any fix the agent generates would also be blocked.")
            print("")
            print("  ACTION REQUIRED:")
            print(f"  1. Open: {bypass_url}")
            print("  2. Click 'Allow secret' on the page.")
            print("  3. Re-run this command — push will succeed and monitoring will start.")
            print("=" * 64 + "\n")
            LOGGER.warning(
                "Skipping pipeline monitoring: push is blocked by secret scanning. "
                "Bypass URL: %s",
                bypass_url,
            )
            monitor_results = [{
                "status": "skipped-push-protection",
                "bypass_url": bypass_url,
                "message": "Push blocked by GitHub secret scanning — visit the bypass URL and re-run.",
            }]
        elif (
            monitor_pipelines
            and push_result.get("github_actions_trigger_expected")
            and push_result.get("pushed")
        ):
            _token = github_token or os.getenv("GITHUB_TOKEN", "")
            _repo = github_repo or os.getenv("GITHUB_REPO", "")

            if not _token or not _repo:
                LOGGER.warning(
                    "Pipeline monitoring requested but GITHUB_TOKEN or GITHUB_REPO "
                    "is not set — skipping monitor step."
                )
            else:
                from .pipeline_monitor import PipelineMonitor

                from datetime import timedelta
                # Backdate by 2 min to absorb clock-skew between local clock and GitHub.
                push_ts = (
                    datetime.now(timezone.utc) - timedelta(minutes=2)
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
                branch = push_result.get("branch", "main")

                # Derive workflow display names from the pipeline file paths
                pipeline_names = [
                    Path(f).stem.replace("-pipeline", "").replace("_pipeline", "")
                    for f in written_pipeline_files
                ]

                try:
                    monitor = PipelineMonitor(
                        github_token=_token,
                        github_repo=_repo,
                        poll_interval=monitor_poll_interval,
                        timeout=monitor_timeout,
                        auto_approve_fix=auto_approve_fix,
                        auto_apply=auto_apply_fix,
                    )
                except RuntimeError as exc:
                    LOGGER.error(
                        "Pipeline monitoring disabled: %s", exc
                    )
                    print(f"\n  ❌  Pipeline monitoring disabled: {exc}\n")
                    monitor = None

                if monitor is None:
                    monitor_results = []
                else:
                    monitor_results = monitor.monitor_and_resolve(
                        branch=branch,
                        pipeline_names=pipeline_names,
                        push_timestamp=push_ts,
                        architecture_data=architecture_data,
                        output_dir=output_dir / "incident-fixes",
                        repository_path=repository_path,
                    )
                    save_json_output(
                        output_dir / "pipeline-monitor-results.json",
                        {"runs": monitor_results},
                    )

        if isinstance(cicd_result.data, dict):
            cicd_result.data["written_pipeline_files"] = written_pipeline_files
            cicd_result.data["output_pipeline_files"] = output_pipeline_files
            cicd_result.data["push_result"] = push_result
            cicd_result.data["monitor_results"] = monitor_results

        save_json_output(output_dir / "cicd-generation.json", cicd_result.__dict__)

        cicd_incidents = self._log_agent_output(
            agent_name="cicd_generation_agent",
            status="success" if cicd_result.success else "failed",
            data=cicd_result.__dict__,
            execution_id=execution_id,
            repository_path=str(repository_path),
            extra={"push_result": push_result},
        )
        self._maybe_run_incident_response(
            triggered_by="cicd_generation_agent",
            incidents_count=cicd_incidents,
            enable_incident_response=enable_incident_response,
            architecture_data=architecture_data,
            security_data=security_result.__dict__,
            output_dir=output_dir,
            repository_path=repository_path,
            execution_id=execution_id,
            similarity_threshold=incident_similarity_threshold,
            top_k=incident_top_k,
            max_incidents=incident_max_incidents,
            auto_apply=incident_auto_apply,
            llm_model=inc_model,
            llm_provider=inc_prov,
            incident_runs=incident_runs,
        )

        # CI/CD benchmark comparison (optional)
        cicd_benchmark_report: dict[str, Any] | None = None
        if cicd_compare_model_a and cicd_compare_model_b:
            result_a, result_b, cicd_benchmark_report = self._run_cicd_judge_comparison(
                architecture_data=architecture_data,
                provider=cicd_provider,
                model_a=cicd_compare_model_a,
                model_b=cicd_compare_model_b,
                llm_provider=cicd_llm_provider,
                judge_model=cicd_judge_model,
                judge_provider=cicd_judge_provider,
                require_real_cicd_llm=require_real_cicd_llm,
                require_real_cicd_judge=require_real_cicd_judge,
                output_dir=output_dir,
            )
            save_json_output(output_dir / "cicd-generation-model-a.json", result_a)
            save_json_output(output_dir / "cicd-generation-model-b.json", result_b)
            save_json_output(output_dir / "cicd-only-judge-report.json", cicd_benchmark_report)

        # ---------------------------------------------------------------
        # 4. Deployment (LLM: minimax-m2.7:cloud / ollama)
        # ---------------------------------------------------------------
        LOGGER.info("[deployment] model=%s provider=%s target=%s", deploy_model, deploy_prov, target_env)
        with self._scoped_env(_PROVIDER_ENV_KEYS["deployment"], deploy_prov):
            deployment_result = self.deployment_agent.run(
                architecture_data=architecture_data,
                cicd_data=cicd_result.__dict__,
                target_env=target_env,
                require_approval=not auto_approve_deployment and human_in_the_loop,
                repository_path=str(repository_path),
                llm_model=deploy_model,
                llm_provider=deploy_prov,
                require_real_llm=require_real_deployment_llm,
                auto_approve_terraform=auto_approve_terraform,
                skip_terraform_apply=skip_terraform_apply,
            )
        save_json_output(output_dir / "deployment-plan.json", deployment_result.__dict__)

        deploy_incidents = self._log_agent_output(
            agent_name="deployment_agent",
            status="success" if deployment_result.success else "failed",
            data=deployment_result.__dict__,
            execution_id=execution_id,
            repository_path=str(repository_path),
            extra={"target_env": target_env},
        )
        self._maybe_run_incident_response(
            triggered_by="deployment_agent",
            incidents_count=deploy_incidents,
            enable_incident_response=enable_incident_response,
            architecture_data=architecture_data,
            security_data=security_result.__dict__,
            output_dir=output_dir,
            repository_path=repository_path,
            execution_id=execution_id,
            similarity_threshold=incident_similarity_threshold,
            top_k=incident_top_k,
            max_incidents=incident_max_incidents,
            auto_apply=incident_auto_apply,
            llm_model=inc_model,
            llm_provider=inc_prov,
            incident_runs=incident_runs,
        )

        # ---------------------------------------------------------------
        # 5. Chaos Engineering (opt-in — LLM: glm-5:cloud / ollama)
        # ---------------------------------------------------------------
        chaos_result_dict: dict[str, Any] | None = None
        if enable_chaos:
            LOGGER.info(
                "[chaos] backend=%s model=%s provider=%s prom=%s grafana=%s",
                chaos_backend, chaos_model, chaos_prov,
                chaos_prometheus_url, chaos_grafana_url,
            )
            chaos_result_dict = self._run_chaos_agent(
                architecture_data=architecture_data,
                output_dir=output_dir,
                backend=chaos_backend,
                dry_run=chaos_dry_run,
                prometheus_url=chaos_prometheus_url,
                grafana_url=chaos_grafana_url,
                grafana_api_key=chaos_grafana_api_key,
                experiment_duration=chaos_experiment_duration,
                observation_window=chaos_observation_window,
                max_experiments=chaos_max_experiments,
                experiment_types=chaos_experiment_types,
                namespace=chaos_namespace,
                container_map=chaos_container_map,
                llm_model=chaos_model,
                llm_provider=chaos_prov,
            )
            save_json_output(output_dir / "chaos-experiments.json", chaos_result_dict)

            chaos_incidents = self._log_agent_output(
                agent_name="chaos_engineering_agent",
                status="success" if (chaos_result_dict or {}).get("success", True) else "failed",
                data=chaos_result_dict or {},
                execution_id=execution_id,
                repository_path=str(repository_path),
                extra={"backend": chaos_backend},
            )
            self._maybe_run_incident_response(
                triggered_by="chaos_engineering_agent",
                incidents_count=chaos_incidents,
                enable_incident_response=enable_incident_response,
                architecture_data=architecture_data,
                security_data=security_result.__dict__,
                chaos_data=chaos_result_dict,
                output_dir=output_dir,
                repository_path=repository_path,
                execution_id=execution_id,
                similarity_threshold=incident_similarity_threshold,
                top_k=incident_top_k,
                max_incidents=incident_max_incidents,
                auto_apply=incident_auto_apply,
                llm_model=inc_model,
                llm_provider=inc_prov,
                incident_runs=incident_runs,
            )
        else:
            LOGGER.info("[chaos] skipped (enable_chaos=False)")

        # ---------------------------------------------------------------
        # Workflow Summary
        # ---------------------------------------------------------------
        workflow_summary = {
            "execution_id": execution_id,
            "repository": str(repository_path),
            "architecture_output": str(architecture_output_path),
            "best_models": BEST_MODELS,
            "agents": {
                "architecture_analysis_agent": {
                    "success": True,
                    "protocol": "MCP",
                    "output": str(architecture_output_path),
                    "llm_model": arch_model,
                    "llm_provider": arch_provider,
                },
                "devsecops_security_agent": security_result.__dict__,
                "cicd_generation_agent": cicd_result.__dict__,
                "deployment_agent": deployment_result.__dict__,
                "chaos_engineering_agent": chaos_result_dict
                if enable_chaos
                else {"skipped": True, "reason": "user opted out"},
            },
            "incident_response_runs": incident_runs,
        }
        if cicd_benchmark_report is not None:
            workflow_summary["cicd_judge"] = cicd_benchmark_report

        save_json_output(output_dir / "workflow-summary.json", workflow_summary)

        self._log_agent_output(
            agent_name="orchestrator",
            status="success",
            data=workflow_summary,
            execution_id=execution_id,
            repository_path=str(repository_path),
        )

        return workflow_summary

    # -------------------------------------------------------------------
    # Architecture agent
    # -------------------------------------------------------------------

    def _run_architecture_agent(
        self,
        repository_path: Path,
        include_llm: bool,
        llm_model: str | None = None,
        llm_provider: str | None = None,
    ) -> dict[str, Any]:
        LOGGER.info("Triggering integrated architecture analysis agent")
        provider = llm_provider or BEST_MODELS["architecture"]["provider"]
        with self._scoped_env(_PROVIDER_ENV_KEYS["architecture"], provider):
            return self.architecture_agent.analyze_repository(
                repository_path,
                include_llm=include_llm,
                llm_model=llm_model,
            )

    # -------------------------------------------------------------------
    # Chaos Engineering invocation
    # -------------------------------------------------------------------

    def _run_chaos_agent(
        self,
        *,
        architecture_data: dict[str, Any],
        output_dir: Path,  # noqa: ARG002 — kept for future per-experiment artifacts
        backend: str,
        dry_run: bool,
        prometheus_url: str | None,
        grafana_url: str | None,
        grafana_api_key: str | None,
        experiment_duration: int,
        observation_window: int,
        max_experiments: int,
        experiment_types: list[str] | None,
        namespace: str,
        container_map: dict[str, str] | None,
        llm_model: str | None,
        llm_provider: str | None,
    ) -> dict[str, Any]:
        if self.chaos_agent is None:
            self.chaos_agent = ChaosEngineeringAgent(
                prometheus_url=prometheus_url or os.getenv("PROMETHEUS_URL", "http://localhost:9090"),
                grafana_url=grafana_url or os.getenv("GRAFANA_URL", "http://localhost:3000"),
                grafana_api_key=grafana_api_key or os.getenv("GRAFANA_API_KEY"),
                backend=backend,
                dry_run=dry_run,
                experiment_duration=experiment_duration,
                observation_window=observation_window,
                k8s_namespace=namespace,
                container_map=container_map,
            )

        # Provider env scoping in case chaos plugins later use shared.llm_common.
        with self._scoped_env(_PROVIDER_ENV_KEYS["chaos"], llm_provider):
            # Surface model selection for downstream tooling that reads env.
            with self._scoped_env("CHAOS_LLM_MODEL", llm_model):
                result = self.chaos_agent.run(
                    architecture_data=architecture_data,
                    experiment_types=experiment_types,
                    max_experiments=max_experiments,
                )
        return result.__dict__

    # -------------------------------------------------------------------
    # Incident Response — auto-trigger when an agent produces incidents
    # -------------------------------------------------------------------

    def _maybe_run_incident_response(
        self,
        *,
        triggered_by: str,
        incidents_count: int,
        enable_incident_response: bool,
        architecture_data: dict[str, Any],
        output_dir: Path,
        repository_path: Path,
        execution_id: str,
        similarity_threshold: float,
        top_k: int,
        max_incidents: int,
        auto_apply: bool,
        llm_model: str | None,
        llm_provider: str | None,
        incident_runs: list[dict[str, Any]],
        security_data: dict[str, Any] | None = None,
        chaos_data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not enable_incident_response or incidents_count <= 0:
            return None

        LOGGER.info(
            "[incident-response] triggered by %s (%d incident(s)) — model=%s provider=%s",
            triggered_by, incidents_count, llm_model, llm_provider,
        )

        if self.incident_agent is None:
            self.incident_agent = IncidentResponseAgent(
                similarity_threshold=similarity_threshold,
                auto_apply=auto_apply,
                output_dir=str(output_dir / "incident-fixes"),
                repository_path=str(repository_path),
                auto_approve_fix=auto_apply,
            )

        # Scope env so any downstream call to shared.llm_common picks the
        # right provider/model for the incident reasoner.
        with self._scoped_env(_PROVIDER_ENV_KEYS["incident"], llm_provider):
            with self._scoped_env("INCIDENT_LLM_MODEL", llm_model):
                try:
                    result = self.incident_agent.run(
                        architecture_data=architecture_data,
                        security_data=security_data or {},
                        chaos_data=chaos_data or {},
                        top_k=top_k,
                        max_incidents=max_incidents,
                        output_dir=str(output_dir / "incident-fixes"),
                        repository_path=str(repository_path),
                    )
                except Exception as exc:
                    LOGGER.error("Incident response invocation failed: %s", exc)
                    return None

        result_dict = result.__dict__ if hasattr(result, "__dict__") else dict(result)
        run_record = {
            "triggered_by": triggered_by,
            "execution_id": execution_id,
            "incidents_in_batch": incidents_count,
            "result": result_dict,
        }
        incident_runs.append(run_record)

        # Persist a per-trigger snapshot so users can see exactly what the
        # incident agent did in response to which upstream agent.
        save_json_output(
            output_dir / f"incident-response-{triggered_by}.json",
            run_record,
        )

        # Log the incident agent's own run too, so it shows up in MongoDB
        # like every other agent.
        self._log_agent_output(
            agent_name="incident_response_agent",
            status="success",
            data=result_dict,
            execution_id=execution_id,
            repository_path=str(repository_path),
            extra={"triggered_by": triggered_by, "incidents_in_batch": incidents_count},
        )
        return result_dict

    # -------------------------------------------------------------------
    # Human-in-the-loop: Security review
    # -------------------------------------------------------------------

    def _prompt_security_review(self, security_data: dict[str, Any]) -> str:
        """Prompt user to review security findings. Returns 'continue' or 'rescan'."""
        # findings live at security_data["data"]["findings"]
        inner = security_data.get("data") or {}
        if not isinstance(inner, dict):
            inner = {}
        findings: list = inner.get("findings", [])
        if not isinstance(findings, list):
            findings = []

        risk_score = inner.get("risk_score", 0)
        high = sum(1 for f in findings if str(f.get("severity", "")).lower() == "high")
        critical = sum(1 for f in findings if str(f.get("severity", "")).lower() == "critical")

        print(f"\n{'='*60}")
        print("DevSecOps Security Scan Complete")
        print(f"{'='*60}")
        print(f"  Total findings : {len(findings)}")
        print(f"  Critical        : {critical}")
        print(f"  High            : {high}")
        print(f"  Risk score      : {risk_score}/100")
        print("\nOptions:")
        print("  1) Continue to next agent (CI/CD generation)")
        print("  2) Rescan after fixing vulnerabilities")

        import sys
        if not sys.stdin.isatty():
            return "continue"

        try:
            answer = input("Selection [1/2] (default: 1): ").strip()
        except (EOFError, KeyboardInterrupt):
            return "continue"

        if answer == "2":
            return "rescan"
        return "continue"

    # -------------------------------------------------------------------
    # CI/CD push approval
    # -------------------------------------------------------------------

    def _approve_cicd_push(
        self,
        *,
        cicd_human_validation: bool,
        auto_approve_cicd: bool,
        pipeline_files: list[str],
    ) -> bool:
        if auto_approve_cicd:
            LOGGER.info("Auto-approve enabled: CI/CD pipelines approved for push.")
            return True
        if not cicd_human_validation:
            return True

        print("\nGenerated CI/CD pipeline files:")
        for path in pipeline_files:
            print(f" - {path}")
        print("\nHuman-in-the-loop validation is required before git push.")
        try:
            answer = input("Approve generated pipelines and push to the remote repository? [y/N]: ").strip().lower()
        except EOFError:
            LOGGER.warning("No interactive input available; CI/CD push rejected by default.")
            return False
        return answer in {"y", "yes"}

    # -------------------------------------------------------------------
    # CI/CD Judge comparison
    # -------------------------------------------------------------------

    def _run_cicd_judge_comparison(
        self,
        *,
        architecture_data: dict[str, Any],
        provider: str,
        model_a: str,
        model_b: str,
        llm_provider: str | None,
        judge_model: str | None,
        judge_provider: str | None,
        require_real_cicd_llm: bool,
        require_real_cicd_judge: bool,
        output_dir: Path,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        agent_a = CICDGenerationAgent()
        result_a = agent_a.run(
            architecture_data=architecture_data,
            provider=provider,
            include_llm=True,
            llm_model=model_a,
            llm_provider=llm_provider,
            require_real_llm=require_real_cicd_llm,
            ask_provider=False,
        )
        agent_b = CICDGenerationAgent()
        result_b = agent_b.run(
            architecture_data=architecture_data,
            provider=provider,
            include_llm=True,
            llm_model=model_b,
            llm_provider=llm_provider,
            require_real_llm=require_real_cicd_llm,
            ask_provider=False,
        )

        generated_root = output_dir / "generated-pipelines"
        model_a_root = generated_root / "model-a"
        model_b_root = generated_root / "model-b"

        if result_a.success and isinstance(result_a.data, dict):
            pipelines_a = result_a.data.get("pipelines", {})
            if isinstance(pipelines_a, dict):
                result_a.data["written_pipeline_files"] = write_pipeline_files(pipelines_a, model_a_root)
        if result_b.success and isinstance(result_b.data, dict):
            pipelines_b = result_b.data.get("pipelines", {})
            if isinstance(pipelines_b, dict):
                result_b.data["written_pipeline_files"] = write_pipeline_files(pipelines_b, model_b_root)

        workflow_a = {
            "agents": {
                "cicd_generation_agent": {
                    "success": bool(result_a.success),
                    "data": result_a.data if isinstance(result_a.data, dict) else {},
                    "duration_seconds": result_a.duration_seconds,
                }
            }
        }
        workflow_b = {
            "agents": {
                "cicd_generation_agent": {
                    "success": bool(result_b.success),
                    "data": result_b.data if isinstance(result_b.data, dict) else {},
                    "duration_seconds": result_b.duration_seconds,
                }
            }
        }

        report = LLMJudge().compare_models(
            model_a=model_a,
            model_b=model_b,
            workflow_a=workflow_a,
            workflow_b=workflow_b,
            judge_model=judge_model,
            judge_provider=judge_provider,
            require_real_judge=require_real_cicd_judge,
        )
        return result_a.__dict__, result_b.__dict__, report

    # -------------------------------------------------------------------
    # Git push helpers
    # -------------------------------------------------------------------

    def _copy_pipelines_to_repo(
        self,
        *,
        output_pipeline_dir: Path,
        pipeline_files: list[str],
        repository_path: Path,
    ) -> list[str]:
        """
        Copy pipeline files from output_dir/generated-pipelines/ into the
        cloned repository so they can be git-committed and pushed.
        Returns the list of relative paths written inside repository_path.
        """
        import shutil
        written: list[str] = []
        for relative in pipeline_files:
            src = (output_pipeline_dir / relative).resolve()
            dst = (repository_path / relative).resolve()
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            written.append(relative)
            LOGGER.debug("Copied pipeline %s -> %s", src, dst)
        return written

    def _commit_and_push_pipeline_files(
        self,
        *,
        repository_path: Path,
        pipeline_files: list[str],
        commit_message: str | None,
    ) -> dict[str, Any]:
        if not pipeline_files:
            return {
                "enabled": True,
                "approved": True,
                "pushed": False,
                "github_actions_trigger_expected": False,
                "status": "skipped-no-files",
                "message": "No pipeline files were provided for push.",
            }

        repo_check = self._run_git(["rev-parse", "--is-inside-work-tree"], cwd=repository_path)
        if repo_check.returncode != 0:
            return {
                "enabled": True,
                "approved": True,
                "pushed": False,
                "github_actions_trigger_expected": False,
                "status": "failed-not-git-repo",
                "message": repo_check.stderr.strip() or "Repository path is not a git repository.",
            }

        # Show the remote URL so the user can verify the target repo
        remote_result = self._run_git(["remote", "get-url", "origin"], cwd=repository_path)
        remote_url = remote_result.stdout.strip() if remote_result.returncode == 0 else "(no remote)"
        LOGGER.info("Pushing pipelines to remote: %s", remote_url)
        print(f"\n  → Git remote: {remote_url}")

        if not remote_url or remote_url == "(no remote)":
            return {
                "enabled": True,
                "approved": True,
                "pushed": False,
                "github_actions_trigger_expected": False,
                "status": "failed-no-remote",
                "message": (
                    "Repository has no 'origin' remote. "
                    "Add one with: git remote add origin https://github.com/owner/repo.git"
                ),
            }

        add_result = self._run_git(["add", "--", *pipeline_files], cwd=repository_path)
        if add_result.returncode != 0:
            msg = add_result.stderr.strip() or "Failed to stage generated pipeline files."
            LOGGER.error("git add failed: %s", msg)
            print(f"\n  [ERROR] git add failed: {msg}\n")
            return {
                "enabled": True,
                "approved": True,
                "pushed": False,
                "github_actions_trigger_expected": False,
                "status": "failed-git-add",
                "message": msg,
            }

        diff_result = self._run_git(["diff", "--cached", "--quiet", "--", *pipeline_files], cwd=repository_path)
        if diff_result.returncode == 0:
            # Files already committed — touch them with a generation timestamp so
            # git sees a real change and GitHub Actions gets a new trigger event.
            LOGGER.info("No staged changes: touching pipeline files with generation timestamp.")
            print("  → Pipeline files unchanged — adding generation timestamp to force re-push.")
            self._touch_pipeline_files(pipeline_files, repository_path)
            self._run_git(["add", "--", *pipeline_files], cwd=repository_path)

        branch_result = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repository_path)
        branch_name = branch_result.stdout.strip() if branch_result.returncode == 0 else "main"
        LOGGER.info("Current branch: %s", branch_name)
        print(f"  → Branch: {branch_name}")

        message = commit_message or f"chore(cicd): regenerate workflow pipelines [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC]"
        print(f"  → Committing: {message}")
        commit_result = self._run_git(["commit", "-m", message], cwd=repository_path)
        if commit_result.returncode != 0:
            msg = commit_result.stderr.strip() or commit_result.stdout.strip() or "Failed to commit generated pipeline files."
            LOGGER.error("git commit failed: %s", msg)
            print(f"\n  [ERROR] git commit failed: {msg}\n")
            return {
                "enabled": True,
                "approved": True,
                "pushed": False,
                "github_actions_trigger_expected": False,
                "status": "failed-git-commit",
                "message": msg,
            }
        LOGGER.info("Commit created: %s", message)

        push_result = self._run_git(["push", "origin", branch_name], cwd=repository_path)
        if push_result.returncode != 0:
            push_result = self._run_git(["push", "-u", "origin", branch_name], cwd=repository_path)

        if push_result.returncode != 0:
            stderr = push_result.stderr.strip()
            # Detect GitHub push protection (secret scanning) block
            if "GH013" in stderr or "push protection" in stderr.lower() or "secret scanning" in stderr.lower():
                import re as _re
                bypass_match = _re.search(r"https://\S+unblock-secret\S*", stderr)
                bypass_url = bypass_match.group(0) if bypass_match else "https://github.com/<owner>/<repo>/security/secret-scanning"
                print("\n" + "=" * 64)
                print("  PUSH BLOCKED BY GITHUB SECRET SCANNING")
                print("  A secret was detected in your repository history.")
                print("  Visit this URL to allow the push:")
                print(f"  {bypass_url}")
                print("=" * 64 + "\n")
                LOGGER.warning("Push blocked by GitHub push protection. Bypass: %s", bypass_url)
                return {
                    "enabled": True,
                    "approved": True,
                    "pushed": False,
                    "github_actions_trigger_expected": True,
                    "status": "push-protection-blocked",
                    "bypass_url": bypass_url,
                    "message": f"Push blocked by GitHub secret scanning. Visit {bypass_url} to unblock.",
                    "branch": branch_name,
                    "remote": remote_url,
                }
            LOGGER.error("git push failed: %s", stderr)
            print(f"\n  [ERROR] git push failed: {stderr}\n")
            return {
                "enabled": True,
                "approved": True,
                "pushed": False,
                "github_actions_trigger_expected": False,
                "status": "failed-git-push",
                "message": stderr or "Failed to push generated pipeline files.",
                "branch": branch_name,
                "remote": remote_url,
            }

        actions_url = remote_url.rstrip(".git").replace("git@github.com:", "https://github.com/") + "/actions"
        print(f"\n  ✅ Pipelines pushed! Check GitHub Actions at:\n     {actions_url}\n")

        return {
            "enabled": True,
            "approved": True,
            "pushed": True,
            "github_actions_trigger_expected": True,
            "status": "pushed",
            "message": "Generated pipeline files were pushed successfully. GitHub Actions should trigger on push.",
            "branch": branch_name,
            "remote": remote_url,
            "actions_url": actions_url,
        }

    def _touch_pipeline_files(self, pipeline_files: list[str], repository_path: Path) -> None:
        """Add a generation timestamp comment to each pipeline file so git sees a diff."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        for relative in pipeline_files:
            path = repository_path / relative
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            # Replace existing generated-at line or prepend a new one
            import re as _re
            marker = "# generated-at:"
            new_line = f"# generated-at: {ts}"
            if marker in content:
                content = _re.sub(r"# generated-at:.*", new_line, content)
            else:
                content = new_line + "\n" + content
            path.write_text(content, encoding="utf-8")

    @staticmethod
    def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            text=True,
            capture_output=True,
            check=False,
        )

    # -------------------------------------------------------------------
    # MongoDB logging for all agents
    # -------------------------------------------------------------------

    _FAILURE_KEYWORDS = frozenset({
        "error", "fail", "failed", "failure", "exception", "critical",
        "fatal", "crash", "rollback", "timeout", "unauthorized", "denied",
        "refused", "unhealthy", "degraded", "abort",
    })
    _WARNING_KEYWORDS = frozenset({
        "warn", "warning", "deprecated", "skipped", "missing", "leak",
        "vulnerability", "vuln", "cve", "high", "medium",
    })

    @staticmethod
    def _text_has_keywords(text: str, keywords: frozenset[str]) -> bool:
        lowered = text.lower()
        return any(kw in lowered for kw in keywords)

    def _extract_log_entries(
        self,
        agent_name: str,
        execution_id: str,
        pipeline_result_id: Any,
        data: dict[str, Any],
        top_status: str,
    ) -> list[dict[str, Any]]:
        """
        Walk the agent output and produce one log document per notable event.
        Always produces at least one summary entry.
        """
        now = self.mongo._now()
        entries: list[dict[str, Any]] = []

        def _make_entry(level: str, stage: str, message: str, details: dict | None = None) -> dict:
            return {
                "timestamp": now,
                "level": level,
                "source": agent_name,
                "execution_id": execution_id,
                "pipeline_result_id": pipeline_result_id,
                "stage": stage,
                "message": message,
                "details": details or {},
            }

        # --- Summary entry (always present) ---
        summary_level = "INFO" if top_status == "success" else "ERROR"
        entries.append(_make_entry(summary_level, "summary",
                                   f"{agent_name} finished with status: {top_status}",
                                   {"status": top_status}))

        # --- Top-level error field ---
        if data.get("error"):
            entries.append(_make_entry("ERROR", "agent-error",
                                       str(data["error"]), {"error": str(data["error"])}))

        # --- DevSecOps: individual findings ---
        inner = data.get("data") or {}
        if isinstance(inner, dict):
            findings = inner.get("findings", [])
            if isinstance(findings, list):
                for f in findings:
                    if not isinstance(f, dict):
                        continue
                    sev = str(f.get("severity", "info")).upper()
                    level = "ERROR" if sev in {"HIGH", "CRITICAL"} else "WARNING"
                    msg = (
                        f"[{f.get('tool','?')}] {sev} - {f.get('id','?')} "
                        f"in {f.get('target','?')}: {f.get('message','')}"
                    )
                    entries.append(_make_entry(level, "security-finding", msg, f))

            # Tool errors inside tool_status
            tool_status = inner.get("tool_status", {})
            if isinstance(tool_status, dict):
                for tool_name, tool_info in tool_status.items():
                    # trivy_images is a list of per-image results
                    if isinstance(tool_info, list):
                        for img_result in tool_info:
                            if isinstance(img_result, dict) and not img_result.get("success", True):
                                entries.append(_make_entry(
                                    "ERROR", "tool-trivy-image",
                                    f"Trivy image scan failed for {img_result.get('image','?')}",
                                    img_result,
                                ))
                        continue
                    if not isinstance(tool_info, dict):
                        continue
                    if tool_info.get("error") and not tool_info.get("success", True):
                        entries.append(_make_entry(
                            "ERROR", f"tool-{tool_name}",
                            f"Tool {tool_name} failed: {str(tool_info.get('error',''))[:500]}",
                            {"tool": tool_name},
                        ))

        # --- Deployment: event_logs inside execution_context or data ---
        for key in ("execution_context", "data"):
            ctx = data.get(key) or {}
            if not isinstance(ctx, dict):
                continue
            events = ctx.get("events", [])
            if isinstance(events, list):
                for ev in events:
                    if not isinstance(ev, dict):
                        continue
                    ev_level = str(ev.get("level", "INFO")).upper()
                    if ev_level not in {"INFO", "WARNING", "WARN", "ERROR", "CRITICAL", "DEBUG"}:
                        ev_level = "INFO"
                    if ev_level in {"WARN"}:
                        ev_level = "WARNING"
                    entries.append(_make_entry(
                        ev_level,
                        ev.get("stage", "event"),
                        str(ev.get("message", ""))[:2000],
                        ev.get("extra", {}),
                    ))

        # --- CI/CD: push result ---
        push_result = (data.get("data") or {}) if isinstance(data.get("data"), dict) else {}
        push = push_result.get("push_result") or data.get("push_result") or {}
        if isinstance(push, dict) and push.get("status") not in {None, "not-requested", "pushed", "skipped-no-changes"}:
            push_status = push.get("status", "")
            level = "ERROR" if "failed" in push_status else "WARNING"
            entries.append(_make_entry(level, "cicd-push",
                                       f"CI/CD push result: {push_status} — {push.get('message','')}",
                                       push))

        # --- Scan any string values in data for failure/warning keywords ---
        for field_name in ("summary", "msg", "message"):
            val = str(data.get(field_name) or "")
            if val and self._text_has_keywords(val, self._FAILURE_KEYWORDS):
                entries.append(_make_entry("ERROR", f"data-{field_name}", val))
            elif val and self._text_has_keywords(val, self._WARNING_KEYWORDS):
                entries.append(_make_entry("WARNING", f"data-{field_name}", val))

        return entries

    def _extract_incidents(
        self,
        agent_name: str,
        execution_id: str,
        pipeline_result_id: Any,
        data: dict[str, Any],
        top_status: str,
        log_entries: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Build incident documents:
        - One incident if the agent failed entirely
        - One incident per ERROR/WARNING log entry that looks actionable
        """
        now = self.mongo._now()
        incidents: list[dict[str, Any]] = []

        def _make_incident(severity: str, summary: str, details: dict) -> dict:
            return {
                "signature": f"{agent_name}::{summary[:120]}",
                "status": "open",
                "severity": severity,
                "environment": "workflow",
                "agent_name": agent_name,
                "execution_id": execution_id,
                "pipeline_result_id": pipeline_result_id,
                "created_at": now,
                "updated_at": now,
                "occurrences": 1,
                "summary": summary,
                "details": details,
            }

        # Agent-level failure
        if top_status != "success":
            error_msg = str(data.get("error") or data.get("msg") or f"{agent_name} failed")
            incidents.append(_make_incident("high", f"{agent_name} failed: {error_msg[:200]}", data))

        # Per-log incidents for ERROR or WARNING entries
        seen: set[str] = set()
        for entry in log_entries:
            level = entry.get("level", "INFO")
            if level not in {"ERROR", "WARNING"}:
                continue
            stage = entry.get("stage", "")
            # Skip the generic summary entry to avoid duplicates with agent-level failure
            if stage == "summary":
                continue
            msg = entry.get("message", "")[:200]
            key = f"{agent_name}::{stage}::{msg}"
            if key in seen:
                continue
            seen.add(key)
            severity = "high" if level == "ERROR" else "medium"
            incidents.append(_make_incident(severity, msg, entry.get("details", {})))

        return incidents

    def _log_agent_output(
        self,
        *,
        agent_name: str,
        status: str,
        data: dict[str, Any],
        execution_id: str,
        repository_path: str,
        extra: dict[str, Any] | None = None,
    ) -> int:
        """
        Persist agent output to MongoDB across all four collections:
          pipeline_results  — one document per agent run (full output)
          logs              — one document per event/finding/warning/error
          agent_metadata    — upserted counters per agent name
          incidents         — one document per failure or actionable warning

        Returns the number of incident documents that were extracted (0 when
        MongoDB is unavailable or when nothing actionable was detected). The
        orchestrator uses this count to decide whether to invoke the
        Incident Response Agent.
        """
        # We always compute incidents locally (even without MongoDB) so the
        # incident-response trigger still works in offline mode.
        incidents_count = 0
        if self.mongo.client is None:
            LOGGER.debug("MongoDB unavailable, skipping persist for %s", agent_name)
            # Still derive incidents in-memory so callers can react.
            try:
                local_logs = self._extract_log_entries(
                    agent_name=agent_name,
                    execution_id=execution_id,
                    pipeline_result_id=None,
                    data=data,
                    top_status=status,
                )
                incidents_count = len(self._extract_incidents(
                    agent_name=agent_name,
                    execution_id=execution_id,
                    pipeline_result_id=None,
                    data=data,
                    top_status=status,
                    log_entries=local_logs,
                ))
            except Exception:
                pass
            return incidents_count

        now = self.mongo._now()

        # ---- pipeline_results ------------------------------------------------
        record: dict[str, Any] = {
            "timestamp": now,
            "agent": agent_name,
            "execution_id": execution_id,
            "repository_path": repository_path,
            "status": status,
            "data": data,
        }
        if extra:
            record["extra"] = extra

        try:
            result_id = self.mongo.pipeline_results.insert_one(record).inserted_id
        except Exception as exc:
            LOGGER.warning("pipeline_results insert failed for %s: %s", agent_name, exc)
            return 0

        # ---- logs ------------------------------------------------------------
        log_entries = self._extract_log_entries(
            agent_name=agent_name,
            execution_id=execution_id,
            pipeline_result_id=result_id,
            data=data,
            top_status=status,
        )
        try:
            if log_entries:
                self.mongo.logs_col.insert_many(log_entries)
            LOGGER.debug("Logged %d log entries for %s", len(log_entries), agent_name)
        except Exception as exc:
            LOGGER.warning("logs insert failed for %s: %s", agent_name, exc)

        # ---- agent_metadata --------------------------------------------------
        try:
            if self.mongo.agent_metadata is not None:
                inc_success = 1 if status == "success" else 0
                inc_failure = 0 if status == "success" else 1
                self.mongo.agent_metadata.update_one(
                    {"agent_name": agent_name},
                    {
                        "$set": {
                            "last_execution_id": execution_id,
                            "last_status": status,
                            "last_seen": now,
                            "last_repository_path": repository_path,
                        },
                        "$inc": {
                            "execution_count": 1,
                            "success_count": inc_success,
                            "failure_count": inc_failure,
                        },
                        "$setOnInsert": {"created_at": now, "agent_name": agent_name},
                    },
                    upsert=True,
                )
        except Exception as exc:
            LOGGER.warning("agent_metadata upsert failed for %s: %s", agent_name, exc)

        # ---- incidents -------------------------------------------------------
        incidents_count = 0
        try:
            if self.mongo.incidents is not None:
                incident_docs = self._extract_incidents(
                    agent_name=agent_name,
                    execution_id=execution_id,
                    pipeline_result_id=result_id,
                    data=data,
                    top_status=status,
                    log_entries=log_entries,
                )
                incidents_count = len(incident_docs)
                if incident_docs:
                    self.mongo.incidents.insert_many(incident_docs)
                    LOGGER.info(
                        "Saved %d incident(s) for %s (status=%s)",
                        incidents_count, agent_name, status,
                    )
        except Exception as exc:
            LOGGER.warning("incidents insert failed for %s: %s", agent_name, exc)

        LOGGER.debug(
            "MongoDB write complete for %s — pipeline_result=%s, logs=%d, incidents=%d",
            agent_name, result_id, len(log_entries), incidents_count,
        )
        return incidents_count
