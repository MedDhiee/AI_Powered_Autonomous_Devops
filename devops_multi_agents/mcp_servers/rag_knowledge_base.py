"""
RAG knowledge base for DevOps incident solutions.

Uses ChromaDB as vector store with a pre-seeded catalogue of common
deployment / Kubernetes / security incidents and their remediation steps.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import chromadb
    from chromadb.utils import embedding_functions

    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False
    logger.warning("chromadb not installed – RAG knowledge base disabled.")

# ---------------------------------------------------------------------------
# Seed data: known DevOps incident patterns + remediation playbooks
# ---------------------------------------------------------------------------
SEED_INCIDENTS: list[dict[str, Any]] = [
    # ── Kubernetes ──────────────────────────────────────────────────────────
    {
        "id": "k8s-crashloop",
        "title": "CrashLoopBackOff",
        "description": "Pod keeps restarting in CrashLoopBackOff. Container exits immediately after start.",
        "category": "kubernetes",
        "severity": "high",
        "solution": (
            "1. Inspect recent logs: kubectl logs <pod> --previous\n"
            "2. Describe the pod: kubectl describe pod <pod>\n"
            "3. Verify liveness/readiness probe config is correct\n"
            "4. Check entrypoint / CMD in the Dockerfile\n"
            "5. Confirm environment variables and mounted secrets exist\n"
            "6. Restart deployment: kubectl rollout restart deployment/<name>"
        ),
        "auto_commands": [
            "kubectl logs {pod_name} --previous --tail=50",
            "kubectl describe pod {pod_name}",
        ],
        "fix_commands": [
            "kubectl rollout restart deployment/{service_name}",
        ],
    },
    {
        "id": "k8s-imagepull",
        "title": "ImagePullBackOff / ErrImageNeverPull",
        "description": "Kubernetes cannot pull the container image. ErrImageNeverPull or ImagePullBackOff status.",
        "category": "kubernetes",
        "severity": "high",
        "solution": (
            "1. Verify the image tag exists in the registry\n"
            "2. Check imagePullPolicy (set to IfNotPresent for local images)\n"
            "3. Create or verify imagePullSecret: kubectl create secret docker-registry\n"
            "4. If using local Minikube: eval $(minikube docker-env) && rebuild image\n"
            "5. kubectl set image deployment/<name> <container>=<correct-image>"
        ),
        "auto_commands": [
            "kubectl describe pod {pod_name}",
            "kubectl get events --sort-by=.lastTimestamp",
        ],
        "fix_commands": [
            "kubectl rollout restart deployment/{service_name}",
        ],
    },
    {
        "id": "k8s-oomkilled",
        "title": "OOMKilled – out of memory",
        "description": "Container was killed because it exceeded its memory limit (OOMKilled).",
        "category": "kubernetes",
        "severity": "high",
        "solution": (
            "1. Check current limits: kubectl describe pod <pod> | grep -A3 Limits\n"
            "2. Increase memory limit in deployment manifest (resources.limits.memory)\n"
            "3. Profile the app for memory leaks\n"
            "4. Apply updated manifest: kubectl apply -f deployment.yaml\n"
            "5. Monitor: kubectl top pod <pod>"
        ),
        "auto_commands": [
            "kubectl describe pod {pod_name}",
            "kubectl top pod {pod_name}",
        ],
        "fix_commands": [
            "kubectl apply -f {manifest_path}",
        ],
    },
    {
        "id": "k8s-pending",
        "title": "Pod stuck in Pending state",
        "description": "Pod remains Pending – scheduler cannot place it on any node.",
        "category": "kubernetes",
        "severity": "medium",
        "solution": (
            "1. kubectl describe pod <pod> – look at Events section\n"
            "2. Check resource requests vs. available node capacity: kubectl describe nodes\n"
            "3. Check for taints/tolerations mismatch\n"
            "4. Scale node pool or reduce resource requests in manifest"
        ),
        "auto_commands": [
            "kubectl describe pod {pod_name}",
            "kubectl describe nodes",
        ],
        "fix_commands": [],
    },
    {
        "id": "k8s-probe-fail",
        "title": "Liveness / Readiness probe failing",
        "description": "Container health probe keeps failing, causing restarts or traffic removal.",
        "category": "kubernetes",
        "severity": "medium",
        "solution": (
            "1. kubectl logs <pod> to see what the app returns\n"
            "2. Verify probe path / port matches actual application endpoint\n"
            "3. Increase initialDelaySeconds if the app is slow to start\n"
            "4. kubectl edit deployment/<name> to adjust probe parameters\n"
            "5. kubectl rollout restart deployment/<name>"
        ),
        "auto_commands": [
            "kubectl logs {pod_name} --tail=100",
            "kubectl describe pod {pod_name}",
        ],
        "fix_commands": [
            "kubectl rollout restart deployment/{service_name}",
        ],
    },
    # ── Docker build ─────────────────────────────────────────────────────────
    {
        "id": "docker-build-fail",
        "title": "Docker image build failed",
        "description": "Docker build command failed. Build step exited with non-zero status or stderr contains error.",
        "category": "docker",
        "severity": "high",
        "solution": (
            "1. Review build stderr for the exact failing layer\n"
            "2. Ensure base image tag exists: docker pull <base-image>\n"
            "3. Check network access during build (package registry)\n"
            "4. Validate Dockerfile syntax\n"
            "5. Try building with --no-cache to skip stale layers\n"
            "6. docker build --no-cache -t <image> ."
        ),
        "auto_commands": [
            "docker images | grep {service_name}",
            "docker system df",
        ],
        "fix_commands": [
            "docker build --no-cache -t {service_name}:latest {service_path}",
        ],
    },
    {
        "id": "docker-push-fail",
        "title": "Docker image push to registry failed",
        "description": "docker push returned an error. Authentication or network issue with the container registry.",
        "category": "docker",
        "severity": "high",
        "solution": (
            "1. Re-authenticate: docker login <registry>\n"
            "2. Check registry URL and tag format\n"
            "3. Verify network connectivity to the registry host\n"
            "4. Retry: docker push <image>"
        ),
        "auto_commands": [],
        "fix_commands": [
            "docker push {image_name}",
        ],
    },
    # ── Terraform / Infrastructure ────────────────────────────────────────────
    {
        "id": "terraform-apply-fail",
        "title": "Terraform apply failed",
        "description": "terraform apply exited with an error. State may be partially applied.",
        "category": "infrastructure",
        "severity": "high",
        "solution": (
            "1. Run terraform plan to see the diff\n"
            "2. Check error message for resource conflict or permission issue\n"
            "3. If state is locked: terraform force-unlock <lock-id>\n"
            "4. Fix the offending resource block in .tf files\n"
            "5. Re-run: terraform apply -auto-approve"
        ),
        "auto_commands": [
            "terraform plan -out=tfplan",
            "terraform show",
        ],
        "fix_commands": [
            "terraform apply -auto-approve",
        ],
    },
    {
        "id": "terraform-state-lock",
        "title": "Terraform state lock",
        "description": "Terraform state is locked by a previous run that did not complete.",
        "category": "infrastructure",
        "severity": "medium",
        "solution": (
            "1. Identify lock ID from error message\n"
            "2. terraform force-unlock <lock-id>\n"
            "3. Ensure previous terraform process is terminated\n"
            "4. Retry the operation"
        ),
        "auto_commands": [
            "terraform show",
        ],
        "fix_commands": [
            "terraform force-unlock {lock_id}",
        ],
    },
    # ── Health checks ─────────────────────────────────────────────────────────
    {
        "id": "health-check-fail",
        "title": "Deployment health check failed",
        "description": "Post-deployment health check returned unhealthy status or did not respond.",
        "category": "deployment",
        "severity": "high",
        "solution": (
            "1. Check service logs for startup errors\n"
            "2. Verify the health-check endpoint path and port are correct\n"
            "3. kubectl rollout status deployment/<name>\n"
            "4. If degraded, roll back: kubectl rollout undo deployment/<name>\n"
            "5. Investigate root cause before re-deploying"
        ),
        "auto_commands": [
            "kubectl rollout status deployment/{service_name}",
            "kubectl logs deployment/{service_name} --tail=100",
        ],
        "fix_commands": [
            "kubectl rollout undo deployment/{service_name}",
        ],
    },
    # ── Trivy scanner issues (from trivy.pdf knowledge base) ─────────────────
    {
        "id": "trivy-db-download-fail",
        "title": "Trivy vulnerability DB download failure",
        "description": (
            "Trivy fails to download the vulnerability database. "
            "FATAL failed to download vulnerability DB. "
            "context deadline exceeded. connection timed out ghcr.io aquasecurity trivy-db. "
            "FATAL failed to download vulnerability DB OCI artifact not found."
        ),
        "category": "security-tooling",
        "severity": "medium",
        "solution": (
            "1. Increase timeout: trivy image --timeout 30m <image>\n"
            "2. Set GitHub token to avoid rate limiting: export GITHUB_TOKEN=<token>\n"
            "3. Configure HTTP proxy if behind a firewall: export HTTP_PROXY=http://proxy:port\n"
            "4. Clear corrupted cache and retry: rm -rf ~/.cache/trivy/db/ && trivy image <image>\n"
            "5. Use a mirror registry: trivy image --db-repository your-registry.com/trivy-db <image>\n"
            "6. Skip DB update to use cached version: trivy image --skip-db-update <image>\n"
            "7. Pre-download DB once: trivy image --download-db-only"
        ),
        "auto_commands": [],
        "fix_commands": [
            "rm -rf ~/.cache/trivy/db/",
            "trivy image --timeout 30m --skip-db-update {image_name}",
        ],
    },
    {
        "id": "trivy-image-pull-fail",
        "title": "Trivy image pull failure",
        "description": (
            "Trivy FATAL failed to pull image. "
            "Authentication error pulling private registry image for scanning. "
            "unauthorized access to container registry during Trivy scan."
        ),
        "category": "security-tooling",
        "severity": "medium",
        "solution": (
            "1. Verify the image exists: docker pull <image>\n"
            "2. Authenticate to private registry: "
            "export TRIVY_USERNAME=<user> && export TRIVY_PASSWORD=<pass>\n"
            "3. Use Docker credentials stored locally\n"
            "4. Scan a local tar instead: docker save <image> -o image.tar && trivy image --input image.tar\n"
            "5. Skip TLS verification (dev only): trivy image --insecure <registry>/<image>"
        ),
        "auto_commands": [],
        "fix_commands": [],
    },
    # ── CI/CD pipeline issues (from ci_cd.pdf knowledge base) ────────────────
    {
        "id": "cicd-runner-not-found",
        "title": "CI/CD runner offline or not found",
        "description": (
            "GitHub Actions self-hosted runner offline. "
            "No runner matching the specified labels was found. "
            "jobs stuck in queued state pending runner. "
            "Runner not found Agent polling error. "
            "CircleCI job waiting no runner available. "
            "GitLab runner XX not found job pending queue."
        ),
        "category": "cicd",
        "severity": "high",
        "solution": (
            "1. Check runner status in the UI: Settings > Actions > Runners (GitHub) "
            "or Settings > CI/CD > Runners (GitLab)\n"
            "2. SSH into the runner host and restart the runner service:\n"
            "   GitHub: cd actions-runner && ./run.sh\n"
            "   GitLab: sudo systemctl restart gitlab-runner\n"
            "3. Verify runner labels match those required by the job\n"
            "4. Register a new runner if the existing one is permanently offline\n"
            "5. As a temporary workaround, switch to hosted runners "
            "(remove 'self-hosted' label from the job)\n"
            "6. Check for resource exhaustion on the runner host (disk, memory)"
        ),
        "auto_commands": [],
        "fix_commands": [],
    },
    {
        "id": "cicd-yaml-syntax-error",
        "title": "CI/CD pipeline YAML syntax error",
        "description": (
            "Pipeline fails immediately at startup with YAML syntax error. "
            "Jenkinsfile parse error. .gitlab-ci.yml invalid indentation. "
            "GitHub Actions workflow file parse error. "
            "Azure Pipelines YAML validation failed."
        ),
        "category": "cicd",
        "severity": "high",
        "solution": (
            "1. Validate YAML locally: python -c \"import yaml; yaml.safe_load(open('.gitlab-ci.yml'))\"\n"
            "2. Use the provider's lint tool: gitlab-ci-lint / act (GitHub Actions)\n"
            "3. Check for tab characters (YAML requires spaces)\n"
            "4. Verify the file is in the correct branch and correctly named\n"
            "5. Compare indentation with the provider's documentation examples"
        ),
        "auto_commands": [],
        "fix_commands": [],
    },
    {
        "id": "cicd-secret-missing",
        "title": "CI/CD secret or environment variable missing",
        "description": (
            "Pipeline fails with missing environment variable or secret. "
            "Secret not found in CI context. "
            "API key undefined in pipeline job. "
            "Error: required secret not set."
        ),
        "category": "cicd",
        "severity": "high",
        "solution": (
            "1. Add the missing secret in the CI provider UI: "
            "Settings > Secrets (GitHub) / Variables > CI/CD (GitLab)\n"
            "2. Reference it correctly in the YAML: ${{ secrets.MY_SECRET }} (GitHub) "
            "/ $MY_SECRET (GitLab)\n"
            "3. Never hard-code secrets – use Vault, AWS Secrets Manager, or Azure Key Vault\n"
            "4. Verify the secret scope (repo vs. org vs. environment level)"
        ),
        "auto_commands": [],
        "fix_commands": [],
    },
    # ── Gitleaks / secret scanning issues (from gitleaks.pdf) ─────────────────
    {
        "id": "gitleaks-false-positive",
        "title": "Gitleaks false positive on generated or test files",
        "description": (
            "Gitleaks reports a secret leak in auto-generated code or test fixtures. "
            "WRN leaks found exit code 1 on generated file. "
            "generic-api-key detected in RcppExports or test file. "
            "False positive secret detection in build artifacts."
        ),
        "category": "security",
        "severity": "low",
        "solution": (
            "1. Add the fingerprint to .gitleaksignore:\n"
            "   echo 'path/to/file:rule-id:line' >> .gitleaksignore\n"
            "2. Or add an allowlist in .gitleaks.toml:\n"
            "   [[rules.allowlists]]\n"
            "   paths = ['^tests/.*', '^generated/.*']\n"
            "3. Re-run scan to confirm the false positive is suppressed: "
            "gitleaks detect --no-git --source .\n"
            "4. Do NOT use #gitleaks:allow in generated files (ineffective)"
        ),
        "auto_commands": [],
        "fix_commands": [],
    },
    {
        "id": "gitleaks-secret-in-history",
        "title": "Gitleaks detected real secret committed to git history",
        "description": (
            "Gitleaks found a real API key, password, or credential in git commit history. "
            "WRN leaks found AWS secret access key hardcoded in source. "
            "Gitleaks detected hardcoded token in repository. "
            "Secret exposed in git log or previous commit."
        ),
        "category": "security",
        "severity": "high",
        "solution": (
            "1. IMMEDIATELY rotate the exposed credential in your cloud provider console\n"
            "2. Remove from git history: git filter-repo --path <file> --invert-paths\n"
            "3. Add to .gitignore and .gitleaksignore\n"
            "4. Force-push cleaned history (coordinate with team): git push --force-with-lease\n"
            "5. Move all secrets to Vault / AWS Secrets Manager / env vars\n"
            "6. Re-scan after remediation: gitleaks detect --source ."
        ),
        "auto_commands": [],
        "fix_commands": [],
    },
    # ── Security ──────────────────────────────────────────────────────────────
    {
        "id": "secret-exposed",
        "title": "Secret / credential exposed in code or logs",
        "description": "Gitleaks or Trivy detected a hardcoded secret, API key, or password in the repository.",
        "category": "security",
        "severity": "high",
        "solution": (
            "1. Immediately rotate the exposed credential in your provider console\n"
            "2. Remove the secret from the codebase and commit history (git filter-repo)\n"
            "3. Add the secret name to .gitignore / .gitleaksignore\n"
            "4. Move secrets to Vault or environment variables\n"
            "5. Re-scan after remediation: gitleaks detect --source ."
        ),
        "auto_commands": [],
        "fix_commands": [],
    },
    {
        "id": "cve-critical",
        "title": "Critical CVE found in container image or dependency",
        "description": "Trivy or security scan reports a CRITICAL severity CVE in a package or OS layer.",
        "category": "security",
        "severity": "high",
        "solution": (
            "1. Identify the affected package from the scan report\n"
            "2. Update the base image to a patched version\n"
            "3. Upgrade the vulnerable package in requirements/package.json\n"
            "4. Rebuild and re-scan the image\n"
            "5. Pin to a safe version and document in SECURITY.md"
        ),
        "auto_commands": [],
        "fix_commands": [
            "docker build --no-cache -t {service_name}:latest {service_path}",
        ],
    },
    # ── Network ───────────────────────────────────────────────────────────────
    {
        "id": "network-timeout",
        "title": "Service connection timeout",
        "description": "Service cannot reach a dependency (database, API, message broker) – connection timeout.",
        "category": "network",
        "severity": "medium",
        "solution": (
            "1. Verify the dependency service is running: kubectl get pods\n"
            "2. Check Kubernetes Service DNS resolution inside pod\n"
            "3. Inspect NetworkPolicy rules\n"
            "4. Confirm env vars for host/port are correct\n"
            "5. Test connectivity: kubectl exec -it <pod> -- curl http://<svc>:<port>/health"
        ),
        "auto_commands": [
            "kubectl get pods -A",
            "kubectl get svc",
        ],
        "fix_commands": [],
    },
    {
        "id": "service-unavailable",
        "title": "Upstream service unavailable (503)",
        "description": "HTTP 503 or connection refused from a dependent microservice.",
        "category": "network",
        "severity": "medium",
        "solution": (
            "1. Check the upstream service pod status: kubectl get pod -l app=<svc>\n"
            "2. Review its logs: kubectl logs -l app=<svc>\n"
            "3. Check HPA / replica count: kubectl get hpa\n"
            "4. Scale if needed: kubectl scale deployment/<svc> --replicas=2\n"
            "5. Restart if stuck: kubectl rollout restart deployment/<svc>"
        ),
        "auto_commands": [
            "kubectl get pods",
            "kubectl get hpa",
        ],
        "fix_commands": [
            "kubectl rollout restart deployment/{service_name}",
        ],
    },
]

# ---------------------------------------------------------------------------
# Knowledge base class
# ---------------------------------------------------------------------------

_DEFAULT_PERSIST_DIR = str(
    Path(__file__).parent.parent.parent / "devops_multi_agents" / "outputs" / "rag_store"
)


class IncidentKnowledgeBase:
    """Thin wrapper around ChromaDB for incident RAG retrieval."""

    COLLECTION_NAME = "incident_solutions"

    def __init__(self, persist_directory: str | None = None) -> None:
        self.persist_dir = persist_directory or os.getenv(
            "RAG_PERSIST_DIR", _DEFAULT_PERSIST_DIR
        )
        self.client: Any = None
        self.collection: Any = None

        if not CHROMADB_AVAILABLE:
            logger.error("chromadb not installed – install it with: pip install chromadb")
            return

        self._init_db()

    # ------------------------------------------------------------------
    def _init_db(self) -> None:
        Path(self.persist_dir).mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=self.persist_dir)

        # Use the built-in default embedding function (all-MiniLM-L6-v2)
        ef = embedding_functions.DefaultEmbeddingFunction()

        existing = [c.name for c in self.client.list_collections()]
        if self.COLLECTION_NAME not in existing:
            self.collection = self.client.create_collection(
                name=self.COLLECTION_NAME,
                embedding_function=ef,
                metadata={"hnsw:space": "cosine"},
            )
            self._seed()
            logger.info("RAG knowledge base created and seeded with %d incidents.", len(SEED_INCIDENTS))
        else:
            self.collection = self.client.get_collection(
                name=self.COLLECTION_NAME,
                embedding_function=ef,
            )
            logger.info("RAG knowledge base loaded (%d entries).", self.collection.count())

    # ------------------------------------------------------------------
    def _seed(self) -> None:
        """Insert the built-in incident catalogue."""
        documents, metadatas, ids = [], [], []
        for inc in SEED_INCIDENTS:
            # Embed title + description + solution for richer similarity
            text = f"{inc['title']}\n{inc['description']}\n{inc['solution']}"
            documents.append(text)
            metadatas.append(
                {
                    "id": inc["id"],
                    "title": inc["title"],
                    "category": inc["category"],
                    "severity": inc["severity"],
                    "solution": inc["solution"],
                    "auto_commands": "\n".join(inc.get("auto_commands", [])),
                    "fix_commands": "\n".join(inc.get("fix_commands", [])),
                }
            )
            ids.append(inc["id"])

        self.collection.add(documents=documents, metadatas=metadatas, ids=ids)

    # ------------------------------------------------------------------
    def search(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        """Return the top_k most similar incidents from the knowledge base."""
        if self.collection is None:
            return []

        results = self.collection.query(
            query_texts=[query],
            n_results=min(top_k, self.collection.count()),
            include=["documents", "metadatas", "distances"],
        )

        hits: list[dict[str, Any]] = []
        for i, meta in enumerate(results["metadatas"][0]):
            distance = results["distances"][0][i]
            # ChromaDB cosine distance: 0 = identical, 2 = opposite.
            # Convert to similarity in [0, 1].
            similarity = round(1.0 - distance / 2.0, 4)
            hits.append(
                {
                    "rank": i + 1,
                    "similarity": similarity,
                    "id": meta.get("id"),
                    "title": meta.get("title"),
                    "category": meta.get("category"),
                    "severity": meta.get("severity"),
                    "solution": meta.get("solution"),
                    "auto_commands": [
                        c for c in meta.get("auto_commands", "").splitlines() if c
                    ],
                    "fix_commands": [
                        c for c in meta.get("fix_commands", "").splitlines() if c
                    ],
                }
            )
        return hits

    # ------------------------------------------------------------------
    def add_incident(
        self,
        incident_id: str,
        title: str,
        description: str,
        solution: str,
        category: str = "custom",
        severity: str = "medium",
    ) -> bool:
        """Add a new incident+solution pair to the knowledge base."""
        if self.collection is None:
            return False
        try:
            text = f"{title}\n{description}\n{solution}"
            self.collection.add(
                documents=[text],
                metadatas=[
                    {
                        "id": incident_id,
                        "title": title,
                        "category": category,
                        "severity": severity,
                        "solution": solution,
                        "auto_commands": "",
                        "fix_commands": "",
                    }
                ],
                ids=[incident_id],
            )
            return True
        except Exception as exc:
            logger.error("Failed to add incident to knowledge base: %s", exc)
            return False

    # ------------------------------------------------------------------
    def count(self) -> int:
        if self.collection is None:
            return 0
        return self.collection.count()
