from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import sys
from urllib.parse import urlparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared import save_json_output, setup_logging

from devops_multi_agents.orchestrator import DevOpsOrchestrator


def detect_provider_from_url(repo_url: str) -> str:
    domain = urlparse(repo_url).netloc.lower()
    if 'gitlab' in domain:
        return 'gitlab'
    elif 'bitbucket' in domain:
        return 'bitbucket'
    elif 'dev.azure' in domain or 'visualstudio' in domain:
        return 'azure-devops'
    return 'github'


def prompt_jenkins_choice() -> bool:
    """Ask user if they want to use Jenkins for CI/CD."""
    print("\nDo you want to use Jenkins for CI/CD?")
    print("  1) No, use provider detected from repo URL (default)")
    print("  2) Yes, use Jenkins")
    try:
        answer = input("Selection [1/2] (default: 1): ").strip()
    except EOFError:
        return False
    return answer == "2"


def prompt_chaos_choice(non_interactive: bool = False) -> bool:
    """Ask user whether to run the Chaos Engineering Agent."""
    if non_interactive or not sys.stdin.isatty():
        return False
    print("\nDo you want to run the Chaos Engineering Agent?")
    print("  1) No, skip chaos experiments (default)")
    print("  2) Yes, run chaos experiments after deployment")
    try:
        answer = input("Selection [1/2] (default: 1): ").strip()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer == "2"


def prompt_chaos_backend(default: str = "simulation") -> str:
    """Ask which chaos backend to use."""
    if not sys.stdin.isatty():
        return default
    print("\nChoose chaos engineering backend:")
    print("  1) simulation (fast, no real containers)  [default]")
    print("  2) docker      (targets real running Docker containers)")
    print("  3) kubernetes  (targets pods in a cluster)")
    try:
        answer = input("Selection [1-3] (default: 1): ").strip()
    except (EOFError, KeyboardInterrupt):
        return default
    return {"1": "simulation", "2": "docker", "3": "kubernetes"}.get(answer, default)


def parse_container_map(raw: str | None) -> dict[str, str] | None:
    """Parse 'svc1=ctr1,svc2=ctr2' into a dict."""
    if not raw:
        return None
    mapping: dict[str, str] = {}
    for token in raw.split(","):
        token = token.strip()
        if "=" in token:
            k, v = token.split("=", 1)
            mapping[k.strip()] = v.strip()
    return mapping or None


def resolve_cicd_provider(repo_url: str | None, use_jenkins: bool, cli_provider: str | None) -> str:
    """Determine CI/CD provider: Jenkins override > CLI flag > URL detection > github default."""
    if use_jenkins:
        return "jenkins"
    if cli_provider:
        return cli_provider
    if repo_url:
        return detect_provider_from_url(repo_url)
    return "github"


def resolve_target_env(target_env: str | None, non_interactive: bool = False) -> str:
    if target_env:
        normalized = target_env.strip().lower()
        return "minikube" if normalized == "local" else normalized

    import sys
    if non_interactive or not sys.stdin.isatty():
        return "minikube"

    print("\nChoose deployment target environment:")
    print("  1) minikube")
    print("  2) aws")
    print("  3) azure")
    print("  4) gcp")
    try:
        answer = input("Selection [1-4] (default: 1): ").strip()
    except (EOFError, KeyboardInterrupt):
        answer = ""

    mapping = {
        "1": "minikube",
        "2": "aws",
        "3": "azure",
        "4": "gcp",
        "minikube": "minikube",
        "aws": "aws",
        "azure": "azure",
        "gcp": "gcp",
        "local": "minikube",
    }
    return mapping.get(answer.lower(), "minikube")


def prompt_model_choice(label: str, options: list[str], default_value: str) -> str:
    print(f"\n{label}")
    for idx, option in enumerate(options, start=1):
        print(f"  {idx}) {option}")
    print(f"  {len(options) + 1}) custom")

    try:
        answer = input(f"Selection [1-{len(options) + 1}] (default: {default_value}): ").strip()
    except EOFError:
        return default_value

    if not answer:
        return default_value

    if answer.isdigit():
        index = int(answer)
        if 1 <= index <= len(options):
            return options[index - 1]
        if index == len(options) + 1:
            try:
                custom = input("Custom model id: ").strip()
            except EOFError:
                custom = ""
            return custom or default_value

    return answer

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run full DevOps multi-agent workflow")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--repo", help="Path to local repository to analyze")
    group.add_argument("--repo-url", help="Remote repository URL (e.g. https://github.com/user/repo.git)")
    parser.add_argument("--output-dir", default="devops_multi_agents/outputs", help="Output directory")
    parser.add_argument("--include-llm", action="store_true", help="Enable LLM in architecture analysis phase")
    parser.add_argument(
        "--llm-model",
        default=None,
        help="Override LLM model for all agents that use LLM",
    )
    parser.add_argument(
        "--strict-security-tools",
        action="store_true",
        help="Fail workflow if Trivy/Gitleaks/Checkov execution fails",
    )
    parser.add_argument(
        "--skip-image-scan",
        action="store_true",
        help="Skip Trivy image scan in security phase",
    )
    parser.add_argument(
        "--cicd-provider",
        default=None,
        help="CI/CD provider for generated templates (github, gitlab, azure-devops, bitbucket, jenkins)",
    )
    parser.add_argument(
        "--use-jenkins",
        action="store_true",
        help="Use Jenkins as CI/CD provider (overrides --cicd-provider and URL detection)",
    )
    parser.add_argument(
        "--cicd-llm-provider",
        default=None,
        help="Optional CI/CD LLM provider override (openrouter, groq, ollama)",
    )
    parser.add_argument(
        "--push-cicd-pipelines",
        action="store_true",
        help="Commit and push generated CI/CD pipeline files to remote",
    )
    parser.add_argument(
        "--cicd-human-validation",
        action="store_true",
        help="Require user approval before CI/CD pipeline push",
    )
    parser.add_argument(
        "--auto-approve-cicd",
        action="store_true",
        help="Auto-approve CI/CD pipelines for push (useful for non-interactive runs)",
    )
    parser.add_argument(
        "--cicd-push-commit-message",
        default=None,
        help="Custom git commit message for generated CI/CD pipeline push",
    )
    parser.add_argument(
        "--cicd-model-a",
        default=None,
        help="Optional model A for CI/CD LLM-as-judge comparison",
    )
    parser.add_argument(
        "--cicd-model-b",
        default=None,
        help="Optional model B for CI/CD LLM-as-judge comparison",
    )
    parser.add_argument(
        "--cicd-judge-model",
        default=None,
        help="Optional judge model override for CI/CD comparison",
    )
    parser.add_argument(
        "--cicd-judge-provider",
        default="ollama",
        help="Judge provider for CI/CD comparison (openrouter, groq, ollama)",
    )
    parser.add_argument(
        "--require-real-cicd-llm",
        action="store_true",
        help="Fail if CI/CD LLM generation cannot produce a real response",
    )
    parser.add_argument(
        "--require-real-cicd-judge",
        action="store_true",
        help="Fail if CI/CD LLM judge cannot produce a real response",
    )
    parser.add_argument(
        "--target-env",
        default=None,
        choices=["minikube", "aws", "azure", "gcp", "local"],
        help="Deployment environment. If omitted, a terminal prompt asks for minikube/aws/azure/gcp.",
    )
    parser.add_argument(
        "--auto-approve-deployment",
        action="store_true",
        help="Skip deployment human validation step",
    )
    parser.add_argument(
        "--deployment-llm-model",
        default=None,
        help="LLM model used by deployment Terraform generation (cloud env only)",
    )
    parser.add_argument(
        "--deployment-llm-provider",
        default="ollama",
        help="LLM provider for deployment Terraform generation (cloud env only)",
    )
    parser.add_argument(
        "--require-real-deployment-llm",
        action="store_true",
        help="Fail deployment if Terraform LLM generation cannot produce a real response",
    )
    parser.add_argument(
        "--auto-approve-terraform",
        action="store_true",
        help="Skip manual approval step before terraform plan/apply",
    )
    parser.add_argument(
        "--skip-terraform-apply",
        action="store_true",
        help="Generate Terraform and run approval flow, but skip terraform apply execution",
    )
    parser.add_argument(
        "--interactive-model-selection",
        action="store_true",
        help="Prompt in terminal to select deployment LLM model for cloud targets",
    )
    parser.add_argument(
        "--no-human-in-the-loop",
        action="store_true",
        help="Disable human-in-the-loop validation between agents",
    )
    # ── Pipeline monitoring ──────────────────────────────────────────────────
    parser.add_argument(
        "--monitor-pipelines",
        action="store_true",
        help=(
            "After pushing pipelines, watch GitHub Actions runs and automatically "
            "store failures as incidents in MongoDB then invoke the Incident Response Agent."
        ),
    )
    parser.add_argument(
        "--github-token",
        default=None,
        help="GitHub personal access token with repo+actions:read scopes. "
             "Falls back to GITHUB_TOKEN env var.",
    )
    parser.add_argument(
        "--github-repo",
        default=None,
        help="Target repository in 'owner/repo' format. Falls back to GITHUB_REPO env var.",
    )
    parser.add_argument(
        "--monitor-poll-interval",
        type=int,
        default=30,
        help="Seconds between GitHub Actions status polls (default: 30).",
    )
    parser.add_argument(
        "--monitor-timeout",
        type=int,
        default=1800,
        help="Max seconds to wait for a single workflow run (default: 1800 = 30 min).",
    )
    parser.add_argument(
        "--auto-approve-fix",
        action="store_true",
        help=(
            "Skip the human-in-the-loop prompt before applying auto-detected "
            "fixes to workflow files. Default: prompt the user before each fix."
        ),
    )
    parser.add_argument(
        "--auto-apply-fix",
        action="store_true",
        help=(
            "Execute fix commands from the RAG database automatically (not just write "
            "them to the .sh script). Implies --auto-approve-fix. Use when you trust "
            "the stored fix commands and want full-auto remediation."
        ),
    )
    # ── Chaos Engineering (opt-in) ───────────────────────────────────────
    chaos_group = parser.add_mutually_exclusive_group()
    chaos_group.add_argument(
        "--enable-chaos",
        action="store_true",
        help="Run the Chaos Engineering Agent after deployment.",
    )
    chaos_group.add_argument(
        "--no-chaos",
        action="store_true",
        help="Explicitly skip the Chaos Engineering Agent (suppresses the prompt).",
    )
    parser.add_argument(
        "--chaos-backend",
        choices=["simulation", "docker", "kubernetes"],
        default=None,
        help="Chaos backend (asked interactively if omitted with --enable-chaos).",
    )
    parser.add_argument("--chaos-dry-run", action="store_true", help="Log chaos commands without executing them.")
    parser.add_argument("--chaos-prometheus-url", default=None, help="Prometheus URL for chaos metrics.")
    parser.add_argument("--chaos-grafana-url", default=None, help="Grafana URL for chaos annotations.")
    parser.add_argument("--chaos-grafana-api-key", default=None, help="Grafana service-account token.")
    parser.add_argument("--chaos-experiment-duration", type=int, default=60, help="Seconds to hold each failure.")
    parser.add_argument("--chaos-observation-window", type=int, default=30, help="Post-recovery observation seconds.")
    parser.add_argument("--chaos-max-experiments", type=int, default=50, help="Maximum number of experiments.")
    parser.add_argument(
        "--chaos-experiment-types",
        default=None,
        help="Comma-separated experiment types (pod-failure,network-latency,...).",
    )
    parser.add_argument("--chaos-namespace", default="default", help="K8s namespace (kubernetes backend only).")
    parser.add_argument(
        "--chaos-container-map",
        default=None,
        help="Service-to-container mapping for docker backend, e.g. 'svc1=ctr1,svc2=ctr2'.",
    )
    parser.add_argument("--chaos-llm-model", default=None, help="Override chaos LLM model (default: glm-5:cloud).")
    parser.add_argument("--chaos-llm-provider", default=None, help="Override chaos LLM provider (default: ollama).")

    # ── Incident Response (auto-triggered on each agent's incidents) ─────
    parser.add_argument(
        "--disable-incident-response",
        action="store_true",
        help="Disable automatic incident-response invocation between agents.",
    )
    parser.add_argument("--incident-similarity-threshold", type=float, default=0.7, help="Min RAG cosine similarity.")
    parser.add_argument("--incident-top-k", type=int, default=3, help="Number of RAG candidates to retrieve.")
    parser.add_argument("--incident-max-incidents", type=int, default=20, help="Max incidents pulled from MongoDB.")
    parser.add_argument(
        "--incident-auto-apply",
        action="store_true",
        help="Auto-execute incident fix commands (and approve patches).",
    )
    parser.add_argument("--incident-llm-model", default=None, help="Override incident LLM model (default: openai/gpt-oss-120b).")
    parser.add_argument("--incident-llm-provider", default=None, help="Override incident LLM provider (default: groq).")

    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    has_model_a = bool(args.cicd_model_a)
    has_model_b = bool(args.cicd_model_b)
    if has_model_a != has_model_b:
        parser.error("--cicd-model-a and --cicd-model-b must be provided together")

    return args


def _collect_github_credentials(
    token: str | None,
    repo: str | None,
) -> tuple[str, str]:
    """
    Interactively ask the user for GITHUB_TOKEN and GITHUB_REPO when they
    are not already supplied via CLI flag or environment variable.
    """
    import sys

    # Resolve from env first so the user is not asked for something already set
    token = token or os.getenv("GITHUB_TOKEN", "")
    repo = repo or os.getenv("GITHUB_REPO", "")

    is_tty = sys.stdin.isatty()

    print()
    print("  " + "═" * 56)
    print("   GitHub Actions Monitoring — Credentials Required")
    print("  " + "═" * 56)
    print("  These values are used ONLY to poll GitHub Actions runs")
    print("  and are never written to disk.\n")

    if not token:
        if is_tty:
            try:
                import getpass
                token = getpass.getpass(
                    "  GitHub Personal Access Token (repo + actions:read): "
                ).strip()
            except (EOFError, KeyboardInterrupt):
                token = ""
        if not token:
            print("  ⚠  No token provided — pipeline monitoring will be skipped.")

    if not repo:
        if is_tty:
            try:
                repo = input("  Repository (owner/repo, e.g. acme/my-app): ").strip()
            except (EOFError, KeyboardInterrupt):
                repo = ""
        if not repo:
            print("  ⚠  No repository provided — pipeline monitoring will be skipped.")

    if token and repo:
        print(f"\n  ✅  Monitoring configured for repo: {repo}\n")

    return token, repo


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)
    target_env = resolve_target_env(args.target_env, non_interactive=args.no_human_in_the_loop)
    deployment_llm_model = args.deployment_llm_model
    if target_env in {"aws", "azure", "gcp"}:
        if args.interactive_model_selection:
            deployment_llm_model = prompt_model_choice(
                "Choose Terraform generation model",
                ["glm-5:cloud", "minimax-m2.7:cloud", "qwen3:14b", "qwen2.5:14b-instruct-q6_K"],
                deployment_llm_model or "minimax-m2.7:cloud",
            )
        elif not deployment_llm_model:
            deployment_llm_model = "minimax-m2.7:cloud"
            print("No deployment LLM model specified for cloud target; defaulting to minimax-m2.7:cloud")

    # Collect GitHub credentials interactively if monitoring is requested
    github_token = args.github_token
    github_repo = args.github_repo
    if args.monitor_pipelines:
        github_token, github_repo = _collect_github_credentials(github_token, github_repo)
        # If neither token nor repo could be obtained, disable monitoring silently
        if not github_token or not github_repo:
            args.monitor_pipelines = False

    # Determine Jenkins preference (interactive or CLI flag)
    use_jenkins = args.use_jenkins
    clone_dir = None

    if args.repo_url:
        # Interactive flow: ask about Jenkins before cloning
        if not use_jenkins and not args.cicd_provider:
            use_jenkins = prompt_jenkins_choice()

        print(f"Cloning repository from {args.repo_url}...")
        clone_dir = tempfile.mkdtemp(prefix="devops_multi_agents_repo_")
        subprocess.run(["git", "clone", args.repo_url, clone_dir], check=True)
        args.repo = clone_dir
        # Only auto-enable push/validation if not already set via CLI flags
        if not args.push_cicd_pipelines:
            args.push_cicd_pipelines = True
        if not args.cicd_human_validation and not args.auto_approve_cicd:
            args.cicd_human_validation = True

    cicd_provider = resolve_cicd_provider(args.repo_url, use_jenkins, args.cicd_provider)
    print(f"CI/CD provider: {cicd_provider}")

    # ── Resolve Chaos Engineering opt-in ────────────────────────────────
    if args.no_chaos:
        enable_chaos = False
    elif args.enable_chaos:
        enable_chaos = True
    else:
        enable_chaos = prompt_chaos_choice(non_interactive=args.no_human_in_the_loop)

    chaos_backend = args.chaos_backend
    if enable_chaos and chaos_backend is None:
        chaos_backend = prompt_chaos_backend(default="simulation")
    elif chaos_backend is None:
        chaos_backend = "simulation"

    chaos_experiment_types = (
        [t.strip() for t in args.chaos_experiment_types.split(",") if t.strip()]
        if args.chaos_experiment_types
        else None
    )
    chaos_container_map = parse_container_map(args.chaos_container_map)

    enable_incident_response = not args.disable_incident_response

    try:
        workspace_root = Path(__file__).resolve().parents[1]
        orchestrator = DevOpsOrchestrator(workspace_root=workspace_root)
        summary = orchestrator.run_full_workflow(
            repository_path=Path(args.repo),
            output_dir=Path(args.output_dir),
            include_llm=args.include_llm,
            strict_security_tools=args.strict_security_tools,
            skip_image_scan=args.skip_image_scan,
            llm_model=args.llm_model,
            cicd_provider=cicd_provider,
            cicd_llm_provider=args.cicd_llm_provider,
            push_cicd_pipelines=args.push_cicd_pipelines,
            cicd_human_validation=args.cicd_human_validation,
            auto_approve_cicd=args.auto_approve_cicd,
            cicd_push_commit_message=args.cicd_push_commit_message,
            cicd_compare_model_a=args.cicd_model_a,
            cicd_compare_model_b=args.cicd_model_b,
            cicd_judge_model=args.cicd_judge_model,
            cicd_judge_provider=args.cicd_judge_provider,
            require_real_cicd_llm=args.require_real_cicd_llm,
            require_real_cicd_judge=args.require_real_cicd_judge,
            target_env=target_env,
            auto_approve_deployment=args.auto_approve_deployment,
            deployment_llm_model=deployment_llm_model,
            deployment_llm_provider=args.deployment_llm_provider,
            require_real_deployment_llm=args.require_real_deployment_llm,
            auto_approve_terraform=args.auto_approve_terraform,
            skip_terraform_apply=args.skip_terraform_apply,
            human_in_the_loop=not args.no_human_in_the_loop,
            monitor_pipelines=args.monitor_pipelines,
            github_token=github_token,
            github_repo=github_repo,
            monitor_poll_interval=args.monitor_poll_interval,
            monitor_timeout=args.monitor_timeout,
            auto_approve_fix=args.auto_approve_fix or args.auto_apply_fix,
            auto_apply_fix=args.auto_apply_fix,
            # ── Chaos engineering ─────────────────────────────────────
            enable_chaos=enable_chaos,
            chaos_backend=chaos_backend,
            chaos_dry_run=args.chaos_dry_run,
            chaos_prometheus_url=args.chaos_prometheus_url,
            chaos_grafana_url=args.chaos_grafana_url,
            chaos_grafana_api_key=args.chaos_grafana_api_key,
            chaos_experiment_duration=args.chaos_experiment_duration,
            chaos_observation_window=args.chaos_observation_window,
            chaos_max_experiments=args.chaos_max_experiments,
            chaos_experiment_types=chaos_experiment_types,
            chaos_namespace=args.chaos_namespace,
            chaos_container_map=chaos_container_map,
            chaos_llm_model=args.chaos_llm_model,
            chaos_llm_provider=args.chaos_llm_provider,
            # ── Incident response (auto-trigger between agents) ───────
            enable_incident_response=enable_incident_response,
            incident_similarity_threshold=args.incident_similarity_threshold,
            incident_top_k=args.incident_top_k,
            incident_max_incidents=args.incident_max_incidents,
            incident_auto_apply=args.incident_auto_apply,
            incident_llm_model=args.incident_llm_model,
            incident_llm_provider=args.incident_llm_provider,
        )
        save_json_output(Path(args.output_dir) / "workflow-summary.json", summary)
    finally:
        pass
        # Note: If you want to delete the cloned repository after the run, you can uncomment this.
        # if clone_dir:
        #     shutil.rmtree(clone_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
