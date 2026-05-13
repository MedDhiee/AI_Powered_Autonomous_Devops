import { useState, useEffect, useRef } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { IncidentResponseDialog, type MockIncident } from "@/components/IncidentResponseDialog";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Bot,
  ArrowLeft,
  CheckCircle2,
  Loader2,
  Clock,
  Play,
  Search,
  Shield,
  Rocket,
  Zap,
  AlertTriangle,
  Timer,
  ExternalLink,
  FileCheck,
  GitBranch,
  Eye,
  SkipForward,
  RefreshCw,
  Terminal,
  PauseCircle,
  ChevronRight,
} from "lucide-react";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface AgentStep {
  id: string;
  name: string;
  icon: React.ElementType;
  status: "pending" | "running" | "completed" | "error";
  duration: number;
  elapsed?: number;
  needsValidation?: boolean;
}

interface PipelineConfig {
  chaosAgent?: boolean;
  incidentAgent?: boolean;
  infra?: string;
  deployType?: string;
  repoUrl?: string;
}

/* ------------------------------------------------------------------ */
/*  Static mock — real vulnerabilities from devsecops-security-model-a */
/* ------------------------------------------------------------------ */

interface Vuln {
  tool: "trivy" | "gitleaks" | "checkov";
  severity: "critical" | "high" | "medium" | "low";
  id: string;
  target: string;
  package: string;
  installedVersion?: string;
  fixedVersion?: string;
  message: string;
}

const STATIC_VULNS: Vuln[] = [
  {
    tool: "trivy",
    severity: "critical",
    id: "CVE-2025-24813",
    target: "gestiondestock/pom.xml",
    package: "org.apache.tomcat.embed:tomcat-embed-core",
    installedVersion: "9.0.58",
    fixedVersion: "9.0.99 / 10.1.35 / 11.0.3",
    message: "Potential RCE and/or information disclosure with partial PUT",
  },
  {
    tool: "trivy",
    severity: "critical",
    id: "CVE-2021-43113",
    target: "gestiondestock/pom.xml",
    package: "com.itextpdf:itextpdf",
    installedVersion: "5.5.13.2",
    fixedVersion: "5.5.13.3",
    message: "iTextPDF: arbitrary code execution via crafted PDF",
  },
  {
    tool: "trivy",
    severity: "high",
    id: "CVE-2025-66035",
    target: "gestion_stock_ui/package-lock.json",
    package: "@angular/common",
    installedVersion: "15.2.10",
    fixedVersion: "21.0.1 / 20.3.14 / 19.2.16",
    message: "Angular HTTP Client XSRF Token Leakage via Protocol-Relative URLs",
  },
  {
    tool: "trivy",
    severity: "high",
    id: "CVE-2025-66412",
    target: "gestion_stock_ui/package-lock.json",
    package: "@angular/compiler",
    installedVersion: "15.2.10",
    fixedVersion: "21.0.2 / 20.3.15",
    message: "Angular Stored XSS via SVG Animation and MathML Attributes",
  },
  {
    tool: "trivy",
    severity: "high",
    id: "CVE-2026-22610",
    target: "gestion_stock_ui/package-lock.json",
    package: "@angular/core",
    installedVersion: "15.2.10",
    fixedVersion: "21.1.0-rc.0 / 21.0.7",
    message: "Angular: Cross-site scripting vulnerability in Template Compiler",
  },
  {
    tool: "trivy",
    severity: "high",
    id: "CVE-2023-6378",
    target: "gestiondestock/pom.xml",
    package: "ch.qos.logback:logback-classic",
    installedVersion: "1.2.10",
    fixedVersion: "1.3.12 / 1.4.12",
    message: "logback: serialization vulnerability in logback receiver",
  },
  {
    tool: "trivy",
    severity: "high",
    id: "CVE-2020-36518",
    target: "gestiondestock/pom.xml",
    package: "com.fasterxml.jackson.core:jackson-databind",
    installedVersion: "2.13.1",
    fixedVersion: "2.13.2.1 / 2.12.6.1",
    message: "jackson-databind: denial of service via deeply nested objects",
  },
  {
    tool: "trivy",
    severity: "high",
    id: "CVE-2023-22102",
    target: "gestiondestock/pom.xml",
    package: "com.mysql:mysql-connector-j",
    installedVersion: "8.0.32",
    fixedVersion: "8.2.0",
    message: "mysql-connector-j: Connector/J unspecified vulnerability (CPU Oct 2023)",
  },
  {
    tool: "trivy",
    severity: "medium",
    id: "CVE-2024-12798",
    target: "gestiondestock/pom.xml",
    package: "ch.qos.logback:logback-core",
    installedVersion: "1.2.10",
    fixedVersion: "1.5.13 / 1.3.15",
    message: "logback-core: arbitrary code execution via JaninoEventEvaluator",
  },
  {
    tool: "trivy",
    severity: "medium",
    id: "CVE-2025-48924",
    target: "gestiondestock/pom.xml",
    package: "org.apache.commons:commons-lang3",
    installedVersion: "3.12.0",
    fixedVersion: "3.18.0",
    message: "commons-lang3: Uncontrolled Recursion vulnerability",
  },
  {
    tool: "gitleaks",
    severity: "high",
    id: "GITLEAKS-001",
    target: "gestiondestock/src/main/resources/application.properties",
    package: "JWT_SECRET",
    message: "Hardcoded JWT secret key detected in application properties",
  },
  {
    tool: "gitleaks",
    severity: "high",
    id: "GITLEAKS-002",
    target: ".env.example",
    package: "DB_PASSWORD",
    message: "Database password exposed in tracked configuration file",
  },
  {
    tool: "gitleaks",
    severity: "medium",
    id: "GITLEAKS-003",
    target: "docker-compose.yml",
    package: "MYSQL_ROOT_PASSWORD",
    message: "MySQL root password hardcoded in docker-compose file",
  },
  {
    tool: "checkov",
    severity: "high",
    id: "CKV_DOCKER_2",
    target: "gestiondestock/Dockerfile",
    package: "Dockerfile",
    message: "Ensure that HEALTHCHECK instructions have been added to container images",
  },
  {
    tool: "checkov",
    severity: "medium",
    id: "CKV_DOCKER_3",
    target: "gestion_stock_ui/Dockerfile",
    package: "Dockerfile",
    message: "Ensure that a user for the container has been created with USER instruction",
  },
];

const VULN_SUMMARY = {
  total: STATIC_VULNS.length,
  critical: STATIC_VULNS.filter((v) => v.severity === "critical").length,
  high: STATIC_VULNS.filter((v) => v.severity === "high").length,
  medium: STATIC_VULNS.filter((v) => v.severity === "medium").length,
  low: STATIC_VULNS.filter((v) => v.severity === "low").length,
};

const GENERATED_PIPELINES = ["ci-build.yml", "ci-test.yml", "cd-deploy.yml"];

const DEPLOYMENT_FILES = {
  terraform: ["main.tf", "variables.tf", "outputs.tf", "provider.tf"],
  minikube: ["deployment.yaml", "service.yaml", "configmap.yaml", "ingress.yaml"],
};

const TERRAFORM_APPLY_OUTPUT = `\u001b[1mInitializing the backend...\u001b[0m
\u001b[1mInitializing provider plugins...\u001b[0m
- Reusing previous version of hashicorp/google v5.18.0

\u001b[32mTerraform has been successfully initialized!\u001b[0m

\u001b[1mTerraform will perform the following actions:\u001b[0m

  \u001b[32m+\u001b[0m resource "google_compute_network" "vpc_network" {
      + name = "gestion-stock-vpc"
    }

  \u001b[32m+\u001b[0m resource "google_compute_firewall" "default" {
      + name    = "gestion-stock-fw"
      + network = "gestion-stock-vpc"
    }

  \u001b[32m+\u001b[0m resource "google_compute_instance" "app_server" {
      + name         = "gestion-stock-vm"
      + machine_type = "e2-medium"
      + zone         = "europe-west1-b"
    }

\u001b[1mPlan: 3 to add, 0 to change, 0 to destroy.\u001b[0m

google_compute_network.vpc_network: Creating...
google_compute_network.vpc_network: Creation complete after 12s
google_compute_firewall.default: Creating...
google_compute_firewall.default: Creation complete after 8s
google_compute_instance.app_server: Creating...
google_compute_instance.app_server: Creation complete after 25s

\u001b[32m\u001b[1mApply complete! Resources: 3 added, 0 changed, 0 destroyed.\u001b[0m

\u001b[1mOutputs:\u001b[0m
  instance_ip   = "34.76.154.22"
  instance_name = "gestion-stock-vm"`;

const CHAOS_SIMULATIONS = [
  { id: "pod-kill", name: "Suppression de pod aléatoire", desc: "Tue un pod au hasard dans le namespace cible", icon: "💥" },
  { id: "network-latency", name: "Latence réseau (300ms)", desc: "Injecte 300ms de latence sur les communications inter-services", icon: "🌐" },
  { id: "cpu-stress", name: "Surcharge CPU (80%)", desc: "Stress CPU sur les conteneurs de l'application", icon: "⚡" },
  { id: "db-partition", name: "Coupure base de données", desc: "Simule une partition réseau vers MySQL", icon: "🗄️" },
  { id: "service-unavailable", name: "Indisponibilité microservice", desc: "Rend le service Auth inaccessible pendant 60s", icon: "🔌" },
  { id: "packet-loss", name: "Perte de paquets (20%)", desc: "20% de perte de paquets sur le trafic entrant", icon: "📦" },
];

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function detectPlatform(url?: string): "github" | "gitlab" {
  if (url && url.toLowerCase().includes("gitlab")) return "gitlab";
  return "github";
}
function platformLabel(p: "github" | "gitlab") {
  return p === "github" ? "GitHub" : "GitLab";
}
function platformUrl(p: "github" | "gitlab", repoUrl?: string) {
  if (repoUrl) {
    const clean = repoUrl.replace(/\.git$/, "");
    return p === "github" ? `${clean}/actions` : `${clean}/-/pipelines`;
  }
  return p === "github" ? "https://github.com" : "https://gitlab.com";
}

const AGENT_LOGS: Record<string, string[]> = {
  arch: [
    "Clonage du repository...",
    "Analyse de la structure des fichiers...",
    "Détection des stacks technologiques...",
    "Extraction des dépendances...",
    "Construction du graphe de dépendances...",
    "Génération du diagramme Mermaid...",
    "Analyse architecturale terminée.",
  ],
  devsecops: [
    "Démarrage des scans de sécurité...",
    "Trivy : scan du filesystem en cours...",
    "Trivy : 12 vulnérabilités trouvées (2 critiques).",
    "Gitleaks : recherche de secrets exposés...",
    "Gitleaks : 3 secrets détectés.",
    "Checkov : scan IaC en cours...",
    "Checkov : 2 problèmes identifiés.",
    "Génération du rapport de sécurité...",
    "Analyse DevSecOps terminée — 15 vulnérabilités au total.",
  ],
  cicd: [
    "Analyse de l'architecture pour la génération CI/CD...",
    "Détection du provider CI/CD...",
    "Génération du pipeline de build (ci-build.yml)...",
    "Génération du pipeline de tests (ci-test.yml)...",
    "Génération du pipeline de déploiement (cd-deploy.yml)...",
    "Validation de la syntaxe YAML...",
    "Pipelines générés avec succès.",
  ],
  deploy: [
    "Lecture de la configuration d'infrastructure...",
    "Génération des fichiers Terraform / K8s...",
    "Validation des manifests...",
    "Vérification des dépendances cloud...",
    "Fichiers d'infrastructure générés avec succès.",
  ],
  chaos: [
    "Identification des services cibles...",
    "Création des expériences de chaos...",
    "Scénario : suppression de pods aléatoire...",
    "Scénario : latence réseau simulée...",
    "Scénario : surcharge CPU...",
    "Expériences de chaos configurées.",
  ],
  incident: [
    "Analyse des résultats de sécurité et chaos...",
    "Génération des playbooks d'incident...",
    "Création des runbooks de réponse...",
    "Playbooks d'incident générés.",
  ],
};

/* ------------------------------------------------------------------ */
/*  Per-agent incident definitions (fires mid-run, stops the pipeline) */
/* ------------------------------------------------------------------ */

const AGENT_INCIDENTS: Partial<Record<string, MockIncident>> = {
  deploy: {
    id: "INC-oom-001",
    name: "OOMKilled — gestion-stock-api",
    summary:
      "Le pod gestion-stock-api a été tué par OOMKilled durant le déploiement. Le JVM heap dépasse la limite mémoire Kubernetes configurée à 256Mi.",
    severity: "high",
    service: "gestion-stock-api",
  },
  chaos: {
    id: "INC-network-002",
    name: "Latence réseau critique (>300ms)",
    summary:
      "La simulation de latence a révélé une absence de circuit breaker. Les timeouts en cascade paralysent l'API et la base de données.",
    severity: "high",
    service: "gestion-stock-api",
  },
  devsecops: {
    id: "INC-secret-003",
    name: "Secret exposé — AWS_ACCESS_KEY détecté",
    summary:
      "Gitleaks a détecté une clé AWS hardcodée dans application.properties. Le scan DevSecOps est interrompu en attente de remédiation.",
    severity: "critical",
    service: "gestion-stock-api",
  },
};

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

const Pipeline = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const config = (location.state as PipelineConfig) || {};
  const platform = detectPlatform(config.repoUrl);
  const infraType = config.infra === "minikube" ? "minikube" : "terraform";
  const isGcp = config.infra === "gcp";

  const [agents, setAgents] = useState<AgentStep[]>([
    { id: "arch", name: "Architecture Analysis Agent", icon: Search, status: "pending", duration: 90 },
    { id: "devsecops", name: "DevSecOps Agent", icon: Shield, status: "pending", duration: 120, needsValidation: true },
    { id: "cicd", name: "CI/CD Agent", icon: Play, status: "pending", duration: 95, needsValidation: true },
    { id: "deploy", name: "Deployment Agent", icon: Rocket, status: "pending", duration: 150, needsValidation: true },
    ...(config.chaosAgent
      ? [{ id: "chaos", name: "Chaos Engineering Agent", icon: Zap, status: "pending" as const, duration: 110, needsValidation: true }]
      : []),
    ...(config.incidentAgent
      ? [{ id: "incident", name: "Incident Response Agent", icon: AlertTriangle, status: "pending" as const, duration: 85 }]
      : []),
  ]);

  const [currentIdx, setCurrentIdx] = useState(0);
  const [logs, setLogs] = useState<string[]>([]);
  const [waitingValidation, setWaitingValidation] = useState(false);
  const agentsRef = useRef(agents);
  // tracks which agent ids have already fired an incident (to avoid re-triggering on retry)
  const incidentFiredRef = useRef<Set<string>>(new Set());

  /* ---- DevSecOps dialog ---- */
  const [showDevsecopsDialog, setShowDevsecopsDialog] = useState(false);
  const [devsecopsView, setDevsecopsView] = useState<"summary" | "details">("summary");

  /* ---- CI/CD dialog ---- */
  const [showCicdDialog, setShowCicdDialog] = useState(false);
  const [cicdPushing, setCicdPushing] = useState(false);
  const [cicdPushed, setCicdPushed] = useState(false);

  /* ---- Deploy dialog ---- */
  const [showDeployDialog, setShowDeployDialog] = useState(false);
  const [deployPaused, setDeployPaused] = useState(false);
  const [terraformRunning, setTerraformRunning] = useState(false);
  const [terraformOutput, setTerraformOutput] = useState<string | null>(null);

  /* ---- Chaos dialog ---- */
  const [showChaosDialog, setShowChaosDialog] = useState(false);
  const [selectedSims, setSelectedSims] = useState<Set<string>>(new Set());
  const [chaosRunning, setChaosRunning] = useState(false);
  const [chaosResults, setChaosResults] = useState<Record<string, string> | null>(null);

  /* ---- Incident Response dialog ---- */
  const [showIncidentDialog, setShowIncidentDialog] = useState(false);
  const [activeIncident, setActiveIncident] = useState<MockIncident | null>(null);

  const logsEndRef = useRef<HTMLDivElement>(null);

  const addLog = (msg: string) =>
    setLogs((prev) => [...prev, `[${new Date().toLocaleTimeString()}] ${msg}`]);

  const formatDuration = (s: number) =>
    `${Math.floor(s / 60)}m ${(s % 60).toString().padStart(2, "0")}s`;

  useEffect(() => { agentsRef.current = agents; }, [agents]);
  useEffect(() => { logsEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [logs]);

  /* ---- Main execution engine ---- */
  useEffect(() => {
    if (waitingValidation) return;
    const agent = agentsRef.current[currentIdx];
    if (!agent) return;

    setAgents((prev) =>
      prev.map((a, i) => (i === currentIdx ? { ...a, status: "running" } : a))
    );
    addLog(`▶ ${agent.name} démarré...`);

    const agentLogs = AGENT_LOGS[agent.id] || [];
    const interval = (agent.duration * 1000) / (agentLogs.length + 1);
    let logIdx = 0;
    const logTimer = setInterval(() => {
      if (logIdx < agentLogs.length) addLog(`  ${agentLogs[logIdx++]}`);
    }, interval);

    const realDelay = Math.max(3000, (agent.duration / 10) * 1000);

    // Mid-run incident: fires at ~45% of realDelay for agents that have a defined incident
    // and only once per agent (incidentFiredRef tracks already-triggered agents)
    const incidentDef = AGENT_INCIDENTS[agent.id];
    let incidentTimer: ReturnType<typeof setTimeout> | null = null;

    if (incidentDef && !incidentFiredRef.current.has(agent.id)) {
      incidentTimer = setTimeout(() => {
        // Stop log emission and the completion timer
        clearInterval(logTimer);
        clearTimeout(completeTimer); // eslint-disable-line @typescript-eslint/no-use-before-define

        incidentFiredRef.current.add(agent.id);

        setAgents((prev) =>
          prev.map((a, i) => (i === currentIdx ? { ...a, status: "error" } : a))
        );
        addLog(`🚨 INCIDENT — ${agent.name} interrompu : ${incidentDef.name}`);
        addLog("⏹ Pipeline stoppé — en attente de résolution.");

        setWaitingValidation(true);
        setActiveIncident(incidentDef);
        setShowIncidentDialog(true);
      }, realDelay * 0.45);
    }

    const completeTimer = setTimeout(() => {
      clearInterval(logTimer);
      if (incidentTimer) clearTimeout(incidentTimer);
      while (logIdx < agentLogs.length) addLog(`  ${agentLogs[logIdx++]}`);

      setAgents((prev) =>
        prev.map((a, i) =>
          i === currentIdx ? { ...a, status: "completed", elapsed: a.duration } : a
        )
      );
      addLog(`✅ ${agent.name} terminé — ${formatDuration(agent.duration)}`);

      if (agent.needsValidation) {
        setWaitingValidation(true);
        if (agent.id === "devsecops") {
          addLog("⏸ En attente de validation de l'utilisateur (DevSecOps)...");
          setDevsecopsView("summary");
          setShowDevsecopsDialog(true);
        } else if (agent.id === "cicd") {
          addLog("⏸ En attente de validation de l'utilisateur (CI/CD)...");
          setShowCicdDialog(true);
        } else if (agent.id === "deploy") {
          addLog("⏸ En attente de validation de l'utilisateur (Déploiement)...");
          setShowDeployDialog(true);
        } else if (agent.id === "chaos") {
          addLog("⏸ En attente de sélection des simulations de chaos...");
          setShowChaosDialog(true);
        }
      } else {
        setCurrentIdx((prev) => prev + 1);
      }
    }, realDelay);

    return () => {
      clearInterval(logTimer);
      clearTimeout(completeTimer);
      if (incidentTimer) clearTimeout(incidentTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentIdx, waitingValidation]);

  const proceedToNext = () => {
    setWaitingValidation(false);
    setCurrentIdx((prev) => prev + 1);
  };

  /* ---- DevSecOps handlers ---- */
  const handleDevsecopsRescan = () => {
    setShowDevsecopsDialog(false);
    addLog("🔄 Relance de l'analyse DevSecOps après correction...");
    // Reset this agent and re-run it
    const idx = agentsRef.current.findIndex((a) => a.id === "devsecops");
    setAgents((prev) =>
      prev.map((a, i) => (i === idx ? { ...a, status: "pending", elapsed: undefined } : a))
    );
    setCurrentIdx(idx);
    setWaitingValidation(false);
  };

  const handleDevsecopsContinue = () => {
    setShowDevsecopsDialog(false);
    addLog("✔ Validation DevSecOps confirmée — passage à l'agent suivant.");
    proceedToNext();
  };

  /* ---- CI/CD handlers ---- */
  const handleCicdValidate = () => {
    setCicdPushing(true);
    addLog("📤 Push des pipelines vers le repository...");
    setTimeout(() => {
      setCicdPushing(false);
      setCicdPushed(true);
      addLog(`✅ Pipelines poussés avec succès vers ${platformLabel(platform)}.`);
    }, 2500);
  };

  const handleCicdClose = () => {
    setShowCicdDialog(false);
    setCicdPushed(false);
    addLog("✔ Validation CI/CD confirmée — passage à l'agent suivant.");
    proceedToNext();
  };

  /* ---- Deploy handlers ---- */
  const handleDeployManual = () => {
    setDeployPaused(true);
    addLog("⏸ Pipeline en pause — en attente de la correction manuelle des fichiers...");
  };

  const handleTerraformApply = () => {
    setTerraformRunning(true);
    setTerraformOutput(null);
    addLog("🚀 Lancement de terraform apply...");
    // Simulate progressive output
    setTimeout(() => {
      setTerraformRunning(false);
      setTerraformOutput(TERRAFORM_APPLY_OUTPUT);
      addLog("✅ terraform apply terminé — 3 ressources créées.");
    }, 4000);
  };

  const handleDeployClose = () => {
    setShowDeployDialog(false);
    setDeployPaused(false);
    setTerraformOutput(null);
    addLog("✔ Validation Déploiement confirmée — passage à l'agent suivant.");
    proceedToNext();
  };

  /* ---- Chaos handlers ---- */
  const toggleSim = (id: string) => {
    setSelectedSims((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const runChaos = (all: boolean) => {
    const toRun = all
      ? CHAOS_SIMULATIONS.map((s) => s.id)
      : [...selectedSims];
    if (toRun.length === 0) return;
    setChaosRunning(true);
    addLog(`🧪 Lancement de ${toRun.length} simulation(s) de chaos...`);
    setTimeout(() => {
      const results: Record<string, string> = {};
      toRun.forEach((id) => {
        const sim = CHAOS_SIMULATIONS.find((s) => s.id === id)!;
        results[id] = Math.random() > 0.25
          ? `✅ ${sim.name} — Résilience confirmée (service récupéré en ${Math.floor(Math.random() * 20) + 5}s)`
          : `⚠️ ${sim.name} — Dégradation détectée`;
        addLog(`  ${results[id]}`);
      });
      setChaosRunning(false);
      setChaosResults(results);
    }, 4000);
  };

  const handleChaosClose = () => {
    setShowChaosDialog(false);
    setChaosResults(null);
    setSelectedSims(new Set());
    addLog("✔ Chaos Engineering validé — passage à l'agent suivant.");
    proceedToNext();
  };

  /* ---- Incident Response handlers ---- */
  const handleIncidentClose = (outcome: "manual" | "resolved" | "rejected" | "dismissed") => {
    setShowIncidentDialog(false);
    setActiveIncident(null);

    if (outcome === "resolved") {
      addLog("✅ Incident résolu — reprise de l'agent en cours...");
      // Reset agent to pending so the engine re-runs it (this time no incident fires)
      setAgents((prev) =>
        prev.map((a, i) =>
          i === currentIdx ? { ...a, status: "pending", elapsed: undefined } : a
        )
      );
      setWaitingValidation(false);
    } else if (outcome === "manual") {
      addLog("⏸ Correction manuelle sélectionnée — pipeline en pause. Corrigez puis relancez.");
      // Stay blocked — waitingValidation remains true; user must use a resume button
    } else if (outcome === "rejected") {
      addLog("⚠ Recommandation LLM rejetée — escalade à l'équipe on-call. Pipeline en pause.");
      // Stay blocked
    } else {
      // dismissed — just resume (user ignores the incident)
      addLog("ℹ Incident ignoré — reprise de l'agent.");
      setAgents((prev) =>
        prev.map((a, i) =>
          i === currentIdx ? { ...a, status: "pending", elapsed: undefined } : a
        )
      );
      setWaitingValidation(false);
    }
  };

  /* ---- Derived ---- */
  const allDone = agents.length > 0 && agents.every((a) => a.status === "completed") && !waitingValidation;
  const totalTime = agents.reduce((sum, a) => sum + (a.elapsed || 0), 0);

  const statusIcon = (status: AgentStep["status"]) => {
    switch (status) {
      case "completed": return <CheckCircle2 className="w-5 h-5 text-success" />;
      case "running":   return <Loader2 className="w-5 h-5 text-primary animate-spin" />;
      case "error":     return <AlertTriangle className="w-5 h-5 text-orange-400 animate-pulse" />;
      default:          return <Clock className="w-5 h-5 text-muted-foreground" />;
    }
  };

  const severityColor = (sev: string) => {
    switch (sev) {
      case "critical": return "bg-red-500/15 text-red-400 border-red-500/30";
      case "high":     return "bg-orange-500/15 text-orange-400 border-orange-500/30";
      case "medium":   return "bg-yellow-500/15 text-yellow-400 border-yellow-500/30";
      case "low":      return "bg-blue-500/15 text-blue-400 border-blue-500/30";
      default:         return "bg-muted text-muted-foreground";
    }
  };

  const toolColor = (tool: string) => {
    switch (tool) {
      case "trivy":    return "bg-cyan-500/15 text-cyan-400 border-cyan-500/30";
      case "gitleaks": return "bg-purple-500/15 text-purple-400 border-purple-500/30";
      case "checkov":  return "bg-amber-500/15 text-amber-400 border-amber-500/30";
      default:         return "bg-muted text-muted-foreground";
    }
  };

  /* ---------------------------------------------------------------- */
  /*  Render                                                           */
  /* ---------------------------------------------------------------- */

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="border-b border-border/60 px-6 py-4 flex items-center gap-3 backdrop-blur-md bg-background/60 sticky top-0 z-50">
        <Button variant="ghost" size="icon" onClick={() => navigate("/dashboard")}>
          <ArrowLeft className="w-4 h-4" />
        </Button>
        <Bot className="w-5 h-5 text-primary" />
        <span className="font-bold gradient-text">Pipeline d'exécution</span>
        {allDone && (
          <Badge className="ml-auto bg-success/15 text-success border-success/30">
            ✅ Terminé en {formatDuration(totalTime)}
          </Badge>
        )}
      </header>

      <main className="max-w-5xl mx-auto px-6 py-8 animate-fade-in">
        <div className="grid lg:grid-cols-5 gap-6">
          {/* Pipeline column */}
          <div className="lg:col-span-3 space-y-3">
            {agents.map((agent, i) => (
              <div
                key={agent.id}
                className={`glass-card rounded-lg p-4 flex items-center gap-4 transition-all ${
                  agent.status === "running" ? "border-primary/40 glow-border" :
                  agent.status === "error"   ? "border-orange-400/50 bg-orange-500/5" : ""
                }`}
              >
                <div className="relative">
                  {statusIcon(agent.status)}
                  {i < agents.length - 1 && (
                    <div className={`absolute left-1/2 top-full w-px h-3 -translate-x-1/2 ${agent.status === "completed" ? "bg-success/50" : "bg-border"}`} />
                  )}
                </div>
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <agent.icon className="w-4 h-4 text-muted-foreground" />
                    <span className="font-medium text-sm">{agent.name}</span>
                    {agent.needsValidation && (
                      <Badge variant="outline" className="text-[10px] px-1.5 py-0 border-warning/40 text-warning">
                        validation
                      </Badge>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    Durée estimée : {formatDuration(agent.duration)}
                  </p>
                </div>
                {agent.status === "completed" && agent.elapsed && (
                  <div className="flex items-center gap-1 text-xs text-muted-foreground">
                    <Timer className="w-3 h-3" /> {formatDuration(agent.elapsed)}
                  </div>
                )}
                {agent.status === "running" && (
                  <span className="text-xs text-primary animate-pulse">En cours...</span>
                )}
                {agent.status === "error" && (
                  <span className="text-xs text-orange-400 animate-pulse">Interrompu</span>
                )}
              </div>
            ))}

            {/* Pipeline paused due to unresolved incident */}
            {waitingValidation && agents.some((a) => a.status === "error") && !showIncidentDialog && (
              <div className="glass-card rounded-xl p-4 border-orange-400/40 bg-orange-500/5 space-y-3 animate-fade-in">
                <div className="flex items-center gap-2">
                  <AlertTriangle className="w-5 h-5 text-orange-400" />
                  <h3 className="font-semibold text-orange-400">Pipeline en pause — incident non résolu</h3>
                </div>
                <p className="text-sm text-muted-foreground">
                  Un incident a interrompu l'exécution. Résolvez-le pour reprendre le pipeline.
                </p>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    className="gap-1.5 border-orange-400/40 text-orange-400 hover:bg-orange-400/10"
                    onClick={() => {
                      const errorAgent = agents.find((a) => a.status === "error");
                      if (errorAgent) {
                        setActiveIncident(AGENT_INCIDENTS[errorAgent.id] || null);
                        setShowIncidentDialog(true);
                      }
                    }}
                  >
                    <Bot className="w-4 h-4" /> Rouvrir l'Incident Response Agent
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setAgents((prev) =>
                        prev.map((a) => a.status === "error" ? { ...a, status: "pending", elapsed: undefined } : a)
                      );
                      setWaitingValidation(false);
                    }}
                  >
                    <RefreshCw className="w-4 h-4 mr-1.5" /> Reprendre quand même
                  </Button>
                </div>
              </div>
            )}

            {allDone && (
              <div className="glass-card rounded-xl p-5 border-success/30 space-y-3 animate-fade-in">
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="w-5 h-5 text-success" />
                  <h3 className="font-semibold">Pipeline terminé avec succès</h3>
                </div>
                <p className="text-sm text-muted-foreground">
                  Tous les agents ont terminé. Temps total :{" "}
                  <span className="font-semibold text-foreground">{formatDuration(totalTime)}</span>
                </p>
                <Button onClick={() => navigate("/dashboard")} variant="outline">
                  Retour au Dashboard
                </Button>
              </div>
            )}
          </div>

          {/* Logs column */}
          <div className="lg:col-span-2">
            <div className="glass-card rounded-xl p-4 sticky top-24">
              <h3 className="font-semibold text-sm mb-3 flex items-center gap-2">
                <Play className="w-4 h-4 text-primary" /> Logs en temps réel
              </h3>
              <div className="bg-muted rounded-lg p-3 h-80 overflow-y-auto font-mono text-xs space-y-1">
                {logs.length === 0 ? (
                  <p className="text-muted-foreground">En attente...</p>
                ) : (
                  logs.map((log, i) => (
                    <div key={i} className="text-secondary-foreground">{log}</div>
                  ))
                )}
                <div ref={logsEndRef} />
              </div>
            </div>
          </div>
        </div>
      </main>

      {/* ============================================================ */}
      {/*  DIALOG: DevSecOps                                            */}
      {/* ============================================================ */}
      <Dialog open={showDevsecopsDialog} onOpenChange={() => {}}>
        <DialogContent className="sm:max-w-2xl max-h-[90vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Shield className="w-5 h-5 text-primary" />
              Rapport de sécurité — DevSecOps Agent
            </DialogTitle>
            <DialogDescription>
              {devsecopsView === "summary"
                ? "L'analyse est terminée. Consultez le résumé ou vérifiez les détails avant de continuer."
                : "Détail des vulnérabilités détectées par Trivy, Gitleaks et Checkov."}
            </DialogDescription>
          </DialogHeader>

          <div className="flex-1 overflow-y-auto space-y-4 min-h-0">
            {devsecopsView === "summary" ? (
              <>
                {/* Summary cards */}
                <div className="flex items-center justify-between p-3 rounded-lg bg-muted">
                  <span className="font-medium text-sm">Total des vulnérabilités</span>
                  <Badge variant="outline" className="text-base font-bold">{VULN_SUMMARY.total}</Badge>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  {(["critical", "high", "medium", "low"] as const).map((sev) => (
                    <div key={sev} className={`flex items-center justify-between p-2.5 rounded-lg border ${severityColor(sev)}`}>
                      <span className="text-sm font-medium capitalize">{sev}</span>
                      <span className="text-sm font-bold">{VULN_SUMMARY[sev]}</span>
                    </div>
                  ))}
                </div>
                <div className="grid grid-cols-3 gap-2">
                  {["trivy", "gitleaks", "checkov"].map((tool) => (
                    <div key={tool} className={`flex flex-col items-center p-2.5 rounded-lg border ${toolColor(tool)}`}>
                      <span className="text-xs font-bold uppercase">{tool}</span>
                      <span className="text-lg font-bold">{STATIC_VULNS.filter((v) => v.tool === tool).length}</span>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              /* Details table */
              <div className="space-y-2">
                {STATIC_VULNS.map((v, i) => (
                  <div key={i} className="glass-card rounded-lg p-3 space-y-1.5">
                    <div className="flex items-start justify-between gap-2 flex-wrap">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Badge variant="outline" className={`text-[10px] px-1.5 ${toolColor(v.tool)}`}>
                          {v.tool}
                        </Badge>
                        <Badge variant="outline" className={`text-[10px] px-1.5 ${severityColor(v.severity)}`}>
                          {v.severity}
                        </Badge>
                        <code className="text-xs font-mono text-primary">{v.id}</code>
                      </div>
                    </div>
                    <p className="text-xs font-semibold text-foreground">{v.package}</p>
                    <p className="text-xs text-muted-foreground">{v.message}</p>
                    <div className="flex items-center gap-3 text-xs text-muted-foreground flex-wrap">
                      <span className="truncate max-w-[200px]" title={v.target}>📂 {v.target.split("/").pop()}</span>
                      {v.installedVersion && <span>v{v.installedVersion} → <span className="text-success">{v.fixedVersion}</span></span>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <DialogFooter className="flex flex-wrap gap-2 pt-2 border-t border-border/50">
            {devsecopsView === "summary" ? (
              <>
                <Button variant="outline" onClick={() => setDevsecopsView("details")} className="gap-1.5">
                  <Eye className="w-4 h-4" /> Vérifier les détails
                </Button>
                <Button variant="outline" onClick={handleDevsecopsRescan} className="gap-1.5 border-warning/40 text-warning hover:bg-warning/10">
                  <RefreshCw className="w-4 h-4" /> Relancer après correction
                </Button>
                <Button onClick={handleDevsecopsContinue} className="gap-1.5">
                  <ChevronRight className="w-4 h-4" /> Continuer
                </Button>
              </>
            ) : (
              <>
                <Button variant="outline" onClick={() => setDevsecopsView("summary")} className="gap-1.5">
                  <ArrowLeft className="w-4 h-4" /> Retour au résumé
                </Button>
                <Button variant="outline" onClick={handleDevsecopsRescan} className="gap-1.5 border-warning/40 text-warning hover:bg-warning/10">
                  <RefreshCw className="w-4 h-4" /> Relancer après correction
                </Button>
                <Button onClick={handleDevsecopsContinue} className="gap-1.5">
                  <ChevronRight className="w-4 h-4" /> Continuer
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ============================================================ */}
      {/*  DIALOG: CI/CD                                               */}
      {/* ============================================================ */}
      <Dialog open={showCicdDialog} onOpenChange={() => {}}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Play className="w-5 h-5 text-primary" /> Validation des pipelines CI/CD
            </DialogTitle>
            <DialogDescription>
              Les pipelines ont été générés. Vérifiez-les avant de les pousser.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="space-y-1.5">
              <p className="text-sm font-medium">Fichiers générés :</p>
              {GENERATED_PIPELINES.map((f) => (
                <div key={f} className="flex items-center gap-2 p-2 rounded-lg bg-muted text-sm">
                  <FileCheck className="w-4 h-4 text-success" />
                  <code className="font-mono text-xs">
                    .{platform === "github" ? "github/workflows" : "gitlab-ci.d"}/{f}
                  </code>
                </div>
              ))}
            </div>
            <div className="flex items-center gap-2 p-3 rounded-lg border border-primary/20 bg-primary/5">
              <GitBranch className="w-4 h-4 text-primary" />
              <span className="text-sm">Projet détecté : <span className="font-semibold">{platformLabel(platform)}</span></span>
            </div>
            {cicdPushed && (
              <div className="flex items-center gap-2 p-3 rounded-lg border border-success/30 bg-success/5 animate-fade-in">
                <CheckCircle2 className="w-4 h-4 text-success" />
                <span className="text-sm">
                  Pipelines poussés !{" "}
                  <a href={platformUrl(platform, config.repoUrl)} target="_blank" rel="noopener noreferrer" className="text-primary underline inline-flex items-center gap-1">
                    Suivre sur {platformLabel(platform)} <ExternalLink className="w-3 h-3" />
                  </a>
                </span>
              </div>
            )}
          </div>

          <DialogFooter className="flex gap-2">
            {!cicdPushed ? (
              <>
                <Button variant="outline" onClick={handleCicdClose} className="gap-1.5">
                  <SkipForward className="w-4 h-4" /> Passer sans pousser
                </Button>
                <Button onClick={handleCicdValidate} disabled={cicdPushing} className="gap-1.5">
                  {cicdPushing ? <><Loader2 className="w-4 h-4 animate-spin" /> Push...</> : <><GitBranch className="w-4 h-4" /> Valider et pousser</>}
                </Button>
              </>
            ) : (
              <Button onClick={handleCicdClose} className="gap-1.5">
                <ChevronRight className="w-4 h-4" /> Continuer
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ============================================================ */}
      {/*  DIALOG: Deployment                                           */}
      {/* ============================================================ */}
      <Dialog open={showDeployDialog} onOpenChange={() => {}}>
        <DialogContent className="sm:max-w-xl max-h-[90vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Rocket className="w-5 h-5 text-primary" /> Validation — Deployment Agent
            </DialogTitle>
            <DialogDescription>
              {deployPaused
                ? "Pipeline en pause. Corrigez vos fichiers, puis lancez terraform apply."
                : `Les fichiers ${infraType === "terraform" ? "Terraform" : "Kubernetes"} ont été générés.`}
            </DialogDescription>
          </DialogHeader>

          <div className="flex-1 overflow-y-auto space-y-4 min-h-0">
            {!deployPaused ? (
              <>
                <div className="space-y-1.5">
                  <p className="text-sm font-medium">
                    Fichiers {infraType === "terraform" ? "Terraform" : "Kubernetes"} générés :
                  </p>
                  {DEPLOYMENT_FILES[infraType].map((f) => (
                    <div key={f} className="flex items-center gap-2 p-2 rounded-lg bg-muted">
                      <FileCheck className="w-4 h-4 text-success" />
                      <code className="font-mono text-xs">{infraType === "terraform" ? `infra/${f}` : `k8s/${f}`}</code>
                    </div>
                  ))}
                </div>
                <div className="p-3 rounded-lg border border-warning/30 bg-warning/5 text-sm">
                  Vérifiez les fichiers avant de continuer. Si des modifications sont nécessaires, cliquez sur &quot;Modifier manuellement&quot;.
                </div>
              </>
            ) : (
              <>
                {/* Paused state */}
                <div className="flex items-center gap-3 p-4 rounded-lg border border-orange-500/30 bg-orange-500/5">
                  <PauseCircle className="w-5 h-5 text-orange-400 shrink-0" />
                  <div>
                    <p className="text-sm font-semibold text-orange-400">Pipeline en pause</p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      Modifiez vos fichiers {infraType === "terraform" ? "Terraform dans <code>infra/</code>" : "Kubernetes dans <code>k8s/</code>"} puis lancez l&apos;application.
                    </p>
                  </div>
                </div>

                <div className="space-y-1.5">
                  {DEPLOYMENT_FILES[infraType].map((f) => (
                    <div key={f} className="flex items-center gap-2 p-2 rounded-lg bg-muted border border-border/50">
                      <FileCheck className="w-4 h-4 text-muted-foreground" />
                      <code className="font-mono text-xs">{infraType === "terraform" ? `infra/${f}` : `k8s/${f}`}</code>
                      <Badge variant="outline" className="ml-auto text-[10px] text-warning border-warning/30">à modifier</Badge>
                    </div>
                  ))}
                </div>

                {/* Terraform apply section — GCP only */}
                {isGcp && infraType === "terraform" && (
                  <div className="space-y-3">
                    <div className="flex items-center gap-2 border-t border-border/50 pt-3">
                      <Terminal className="w-4 h-4 text-primary" />
                      <span className="text-sm font-medium">Lancer l'application Terraform</span>
                    </div>

                    {!terraformOutput ? (
                      <Button
                        onClick={handleTerraformApply}
                        disabled={terraformRunning}
                        className="w-full gap-2"
                        variant="outline"
                      >
                        {terraformRunning ? (
                          <><Loader2 className="w-4 h-4 animate-spin" /> terraform apply en cours...</>
                        ) : (
                          <><Terminal className="w-4 h-4" /> Lancer terraform apply</>
                        )}
                      </Button>
                    ) : (
                      <div className="space-y-2 animate-fade-in">
                        <div className="flex items-center gap-2 text-success text-sm font-medium">
                          <CheckCircle2 className="w-4 h-4" /> Apply terminé avec succès
                        </div>
                        <pre className="bg-black/60 border border-border/40 rounded-lg p-3 text-xs font-mono text-green-400 overflow-x-auto whitespace-pre-wrap max-h-52 overflow-y-auto">
                          {TERRAFORM_APPLY_OUTPUT.replace(/\u001b\[[0-9;]*m/g, "")}
                        </pre>
                      </div>
                    )}
                  </div>
                )}
              </>
            )}
          </div>

          <DialogFooter className="flex flex-wrap gap-2 pt-2 border-t border-border/50">
            {!deployPaused ? (
              <>
                <Button variant="outline" onClick={handleDeployManual} className="gap-1.5 border-warning/40 text-warning hover:bg-warning/10">
                  <Eye className="w-4 h-4" /> Modifier manuellement
                </Button>
                <Button onClick={handleDeployClose} className="gap-1.5">
                  <CheckCircle2 className="w-4 h-4" /> Valider et continuer
                </Button>
              </>
            ) : (
              <Button
                onClick={handleDeployClose}
                disabled={isGcp && infraType === "terraform" && !terraformOutput}
                className="gap-1.5"
              >
                <ChevronRight className="w-4 h-4" />
                {agents[currentIdx + 1] ? "Passer à l'agent suivant" : "Terminer le pipeline"}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ============================================================ */}
      {/*  DIALOG: Incident Response Agent                              */}
      {/* ============================================================ */}
      <IncidentResponseDialog
        open={showIncidentDialog}
        incident={activeIncident}
        onClose={handleIncidentClose}
        isPremium={true}
      />

      {/* ============================================================ */}
      {/*  DIALOG: Chaos Engineering                                    */}
      {/* ============================================================ */}
      <Dialog open={showChaosDialog} onOpenChange={() => {}}>
        <DialogContent className="sm:max-w-lg max-h-[90vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Zap className="w-5 h-5 text-accent" /> Chaos Engineering — Simulations de pannes
            </DialogTitle>
            <DialogDescription>
              {chaosResults
                ? "Résultats des simulations. Vérifiez la résilience de votre système."
                : "Choisissez les simulations à exécuter ou lancez-les toutes."}
            </DialogDescription>
          </DialogHeader>

          <div className="flex-1 overflow-y-auto space-y-3 min-h-0">
            {!chaosResults ? (
              CHAOS_SIMULATIONS.map((sim) => (
                <label
                  key={sim.id}
                  className={`glass-card rounded-lg p-3 flex items-start gap-3 cursor-pointer transition-all hover:border-accent/40 ${
                    selectedSims.has(sim.id) ? "border-accent/50 bg-accent/5" : ""
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={selectedSims.has(sim.id)}
                    onChange={() => toggleSim(sim.id)}
                    className="mt-0.5 accent-primary"
                  />
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-base">{sim.icon}</span>
                      <span className="text-sm font-medium">{sim.name}</span>
                    </div>
                    <p className="text-xs text-muted-foreground mt-0.5">{sim.desc}</p>
                  </div>
                </label>
              ))
            ) : (
              Object.entries(chaosResults).map(([id, result]) => (
                <div key={id} className={`rounded-lg p-3 border text-xs font-mono ${result.startsWith("✅") ? "border-success/30 bg-success/5 text-success" : "border-warning/30 bg-warning/5 text-warning"}`}>
                  {result}
                </div>
              ))
            )}
          </div>

          <DialogFooter className="flex flex-wrap gap-2 pt-2 border-t border-border/50">
            {!chaosResults ? (
              <>
                <Button
                  variant="outline"
                  onClick={() => runChaos(false)}
                  disabled={selectedSims.size === 0 || chaosRunning}
                  className="gap-1.5"
                >
                  {chaosRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                  Lancer la sélection ({selectedSims.size})
                </Button>
                <Button
                  onClick={() => runChaos(true)}
                  disabled={chaosRunning}
                  className="gap-1.5"
                >
                  {chaosRunning ? (
                    <><Loader2 className="w-4 h-4 animate-spin" /> En cours...</>
                  ) : (
                    <><Zap className="w-4 h-4" /> Lancer toutes les simulations</>
                  )}
                </Button>
              </>
            ) : (
              <Button onClick={handleChaosClose} className="gap-1.5">
                <ChevronRight className="w-4 h-4" />
                {agents[currentIdx + 1] ? "Passer à l'agent suivant" : "Terminer le pipeline"}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default Pipeline;
