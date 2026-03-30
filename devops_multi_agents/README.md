# DevOps Multi-Agent System

This module contains the full multi-agent architecture, including an integrated architecture analysis agent.

Implemented agents:

- Architecture Analysis Agent (Protocol: MCP)

- DevSecOps Security Agent (Protocol: MCP)
- CI/CD Generation Agent (Protocol: ACP)
- Deployment Agent (Protocol: A2A)
- Chaos Engineering Agent (Protocol: A2A)
- Incident Response Agent (Protocol: Hybrid MCP + ACP)
- AI Orchestrator (workflow coordinator)

## Install

```bash
pip install -r shared/requirements.txt
pip install -r devops_multi_agents/requirements.txt
```

## Configure API keys once (.env)

Create a `.env` file in the workspace root (`application`) from `.env.example` and fill your keys.

Example:

```bash
cp .env.example .env
```

Then set providers and keys in `.env` (OpenRouter, Groq, or Ollama).
All scripts auto-load `.env` at startup via `setup_logging`, so you do not need to export env vars in every terminal session.

Provider selection:

- Global: `LLM_PROVIDER=openrouter|groq|ollama`
- Optional overrides:
  - `ARCH_LLM_PROVIDER`
  - `DEVSECOPS_LLM_PROVIDER`
  - `JUDGE_LLM_PROVIDER`

Ollama note:

- Current project integration uses OpenAI-compatible mode, so use local daemon URL: `OLLAMA_BASE_URL=http://localhost:11434/v1`.
- Ollama Cloud examples with `host=https://ollama.com` use the native `ollama` SDK flow, which is different from this project's current client path.

## Run each agent independently

From the workspace root (`application`):

```bash
python -m devops_multi_agents.run_architecture_analysis gestion_stock --output devops_multi_agents/outputs/architecture-analysis.json --mermaid-output devops_multi_agents/outputs/architecture-diagram.mmd
```

```bash
python -m devops_multi_agents.agents.devsecops_security_agent --repo gestion_stock --arch devops_multi_agents/outputs/architecture-analysis.json --output devops_multi_agents/outputs/devsecops-security.json
```

DevSecOps agent details:

- Uses Dockerized internal tools:
  - Trivy filesystem scan (`aquasec/trivy:0.57.1 fs /repo`)
  - Trivy image scan (`aquasec/trivy:0.57.1 image <service>:latest`)
  - Gitleaks secrets scan (`zricethezav/gitleaks:v8.24.2`)
  - Checkov IaC scan (`bridgecrew/checkov:3.2.368`)
- Tool images are configurable via env vars (recommended for future upgrades):
  - `DEVSECOPS_TRIVY_IMAGE`
  - `DEVSECOPS_GITLEAKS_IMAGE`
  - `DEVSECOPS_CHECKOV_IMAGE`
- Trivy network resilience settings (useful for strict mode on unstable networks):
  - `DEVSECOPS_TRIVY_RETRIES` (default: `2`)
  - `DEVSECOPS_TRIVY_RETRY_DELAY_SEC` (default: `5`)
  - `DEVSECOPS_TRIVY_CACHE_DIR` (default: `.trivy-cache` under workspace)
  - `DEVSECOPS_TRIVY_TIMEOUT` (default: `20m`)
  - `DEVSECOPS_TRIVY_DB_REPOSITORIES` (default: `ghcr.io/aquasecurity/trivy-db,public.ecr.aws/aquasecurity/trivy-db`)
  - `DEVSECOPS_TRIVY_SKIP_DB_UPDATE` (default: `false`)
- Note: Docker tags do not support wildcard syntax like `*`; use explicit tags or update env vars.
- Uses the same LLM model configuration as architecture agent:
  - `OPENROUTER_API_KEY`
  - `OPENROUTER_MODEL` (default: `z-ai/glm-4.5-air:free`)
- Requires Docker daemon running (Docker Desktop on Windows) for Trivy/Gitleaks/Checkov container execution.
- Performs explicit startup prechecks before scans:
  - Docker daemon health (`docker info`)
  - Local tool image availability (`docker image inspect`)
  - Clear messages if daemon is down or images are missing

Disable LLM advisory for DevSecOps agent:

```bash
python -m devops_multi_agents.agents.devsecops_security_agent --repo gestion_stock --arch devops_multi_agents/outputs/architecture-analysis.json --output devops_multi_agents/outputs/devsecops-security.json --disable-llm
```

Require real LLM call (fail if provider/model is unavailable):

```bash
python -m devops_multi_agents.agents.devsecops_security_agent --repo gestion_stock --arch devops_multi_agents/outputs/architecture-analysis.json --output devops_multi_agents/outputs/devsecops-security.json --llm-model z-ai/glm-4.5-air:free --require-real-llm
```

Enable strict tool gate for DevSecOps agent (non-zero exit code if a tool fails):

```bash
python -m devops_multi_agents.agents.devsecops_security_agent --repo gestion_stock --arch devops_multi_agents/outputs/architecture-analysis.json --output devops_multi_agents/outputs/devsecops-security.json --strict-tools
```

Skip Trivy image scan (useful when images are not locally built yet):

```bash
python -m devops_multi_agents.agents.devsecops_security_agent --repo gestion_stock --arch devops_multi_agents/outputs/architecture-analysis.json --output devops_multi_agents/outputs/devsecops-security.json --strict-tools --skip-image-scan
```

With `--skip-image-scan`, strict mode blocks only on Trivy filesystem + Gitleaks + Checkov.

```bash
python -m devops_multi_agents.agents.cicd_generation_agent --arch devops_multi_agents/outputs/architecture-analysis.json --output devops_multi_agents/outputs/cicd-generation.json
```

When `--provider` is omitted, the script asks interactively which provider to target.
Supported providers: `github`, `gitlab`, `azure-devops`, `bitbucket`.

```bash
python -m devops_multi_agents.agents.cicd_generation_agent --arch devops_multi_agents/outputs/architecture-analysis.json --provider gitlab --output devops_multi_agents/outputs/cicd-generation.json
```

```bash
python -m devops_multi_agents.agents.deployment_agent --arch devops_multi_agents/outputs/architecture-analysis.json --cicd devops_multi_agents/outputs/cicd-generation.json --output devops_multi_agents/outputs/deployment-plan.json
```

```bash
python -m devops_multi_agents.agents.chaos_engineering_agent --arch devops_multi_agents/outputs/architecture-analysis.json --output devops_multi_agents/outputs/chaos-experiments.json
```

```bash
python -m devops_multi_agents.agents.incident_response_agent --arch devops_multi_agents/outputs/architecture-analysis.json --security devops_multi_agents/outputs/devsecops-security.json --chaos devops_multi_agents/outputs/chaos-experiments.json --output devops_multi_agents/outputs/incident-response.json
```

## Run full workflow (orchestrator)

```bash
python -m devops_multi_agents.run_workflow --repo gestion_stock --output-dir devops_multi_agents/outputs
```

Select CI/CD provider in full workflow:

```bash
python -m devops_multi_agents.run_workflow --repo gestion_stock --output-dir devops_multi_agents/outputs --cicd-provider github
```

With optional LLM in architecture phase:

```bash
python -m devops_multi_agents.run_workflow --repo gestion_stock --output-dir devops_multi_agents/outputs --include-llm
```

With an explicit model override for all LLM-enabled agents:

```bash
python -m devops_multi_agents.run_workflow --repo gestion_stock --output-dir devops_multi_agents/outputs --include-llm --llm-model z-ai/glm-4.5-air:free
```

Enable strict security gate in full workflow:

```bash
python -m devops_multi_agents.run_workflow --repo gestion_stock --output-dir devops_multi_agents/outputs --strict-security-tools
```

Skip image scan in full workflow:

```bash
python -m devops_multi_agents.run_workflow --repo gestion_stock --output-dir devops_multi_agents/outputs --strict-security-tools --skip-image-scan
```

## Compare 2 models + LLM-as-judge

Run both models across the full workflow (agent-by-agent outputs), then generate a judge report:

```bash
python -m devops_multi_agents.run_llm_benchmark --repo gestion_stock --model-a z-ai/glm-4.5-air:free --model-b nvidia/nemotron-3-super-120b-a12b:free --judge-model z-ai/glm-4.5-air:free --output-dir devops_multi_agents/outputs/llm-benchmark --strict-security-tools --skip-image-scan
```

You can also pass `--cicd-provider` to benchmark runs.

By default, benchmark requires a real OpenRouter key (`OPENROUTER_API_KEY`).
Use `--allow-fallback` only for dry runs.

To increase output diversity for DevSecOps advisory, you can tune:

- `DEVSECOPS_LLM_TEMPERATURE` (default `0.7`)

Generated artifacts:

- `devops_multi_agents/outputs/llm-benchmark/<model-a-slug>/...`
- `devops_multi_agents/outputs/llm-benchmark/<model-b-slug>/...`
- `devops_multi_agents/outputs/llm-benchmark/llm-comparison-report.json`

The comparison report includes:

- Per-agent metrics for model A and model B
- Per-agent winner and score delta
- Global winner summary
- Judge analysis (LLM-based if available, deterministic fallback otherwise)

## DevSecOps-only two-model benchmark

For focused security-agent comparison (without running all agents):

```bash
python -m devops_multi_agents.run_devsecops_model_benchmark --repo gestion_stock --arch devops_multi_agents/outputs/architecture-analysis.json --model-a minimax-m2.7:cloud --model-b glm-5:cloud --strict-tools --skip-image-scan --require-real-llm --judge-model nvidia/nemotron-3-super-120b-a12b:free --require-real-judge
```

Outputs:

- `devops_multi_agents/outputs/devsecops-security-model-a.json`
- `devops_multi_agents/outputs/devsecops-security-model-b.json`
- `devops_multi_agents/outputs/devsecops-only-judge-report.json`
