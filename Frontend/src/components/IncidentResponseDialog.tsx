import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronRight,
  Crown,
  Database,
  Loader2,
  SkipForward,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  Wrench,
  XCircle,
  Brain,
} from "lucide-react";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface MockIncident {
  id: string;
  name: string;
  summary: string;
  severity: "critical" | "high" | "medium" | "low";
  service: string;
}

interface RAGSolution {
  id: string;
  title: string;
  confidence: number;
  rootCause: string;
  steps: string[];
  commands: string[];
  source: "knowledge_base";
}

interface LLMSolution {
  rootCauseHypothesis: string;
  confidence: "high" | "medium" | "low";
  proposedFix: {
    summary: string;
    steps: string[];
    commands: string[];
  };
  riskLevel: "low" | "medium" | "high" | "critical";
  validationSteps: string[];
}

type DialogStep =
  | "alert"
  | "rag_loading"
  | "rag_result"
  | "llm_loading"
  | "llm_result"
  | "resolved"
  | "rejected"
  | "manual";

interface Props {
  open: boolean;
  incident: MockIncident | null;
  onClose: (outcome: "manual" | "resolved" | "rejected" | "dismissed") => void;
  isPremium?: boolean;
}

/* ------------------------------------------------------------------ */
/*  Mock data factory                                                  */
/* ------------------------------------------------------------------ */

function getMockRAGSolutions(incident: MockIncident): RAGSolution[] {
  const base: Record<string, RAGSolution[]> = {
    oom: [
      {
        id: "rag-oom-1",
        title: "OOMKilled: Pod Memory Limit Exceeded",
        confidence: 0.905,
        rootCause: "JVM heap size exceeds the Kubernetes memory limit configured for the pod.",
        steps: [
          "Augmenter la limite mémoire du pod à 512Mi dans le manifest Kubernetes.",
          "Ajouter le flag JVM -Xmx256m pour plafonner le heap Java.",
          "Activer le Vertical Pod Autoscaler (VPA) pour ajustement automatique.",
          "Redémarrer le déploiement après modification.",
        ],
        commands: [
          "kubectl set resources deployment gestion-stock-api --limits=memory=512Mi",
          "kubectl rollout restart deployment/gestion-stock-api",
        ],
        source: "knowledge_base",
      },
      {
        id: "rag-oom-2",
        title: "CrashLoopBackOff causé par dépassement mémoire",
        confidence: 0.847,
        rootCause: "Fuite mémoire dans le module de cache ou boucle infinie lors de la pagination.",
        steps: [
          "Activer le profiling JVM avec -Xss512k et analyser les heap dumps.",
          "Réduire la taille du cache applicatif (Spring Cache TTL à 60s).",
          "Vérifier les requêtes N+1 dans le repository JPA.",
        ],
        commands: [
          "kubectl exec -it <pod> -- jcmd 1 GC.heap_info",
          "kubectl logs <pod> --previous | grep -i 'OutOfMemory'",
        ],
        source: "knowledge_base",
      },
    ],
    secret: [
      {
        id: "rag-secret-1",
        title: "Secret exposé dans l'historique Git",
        confidence: 0.921,
        rootCause: "Clé d'API ou mot de passe committé directement dans le code source.",
        steps: [
          "Révoquer immédiatement la clé compromise.",
          "Utiliser git-filter-repo pour purger l'historique.",
          "Stocker les secrets dans Vault ou GitHub Secrets.",
          "Ajouter .env au .gitignore et un pre-commit hook Gitleaks.",
        ],
        commands: [
          "git filter-repo --path .env --invert-paths",
          "gh secret set MY_API_KEY --body '<new_value>'",
        ],
        source: "knowledge_base",
      },
    ],
    network: [
      {
        id: "rag-net-1",
        title: "Latence réseau inter-services > 300ms",
        confidence: 0.872,
        rootCause: "Absence de circuit breaker ou connexion saturée vers MySQL.",
        steps: [
          "Activer Resilience4J CircuitBreaker sur les appels MySQL.",
          "Vérifier le pool de connexions HikariCP (max-pool-size).",
          "Analyser les slow queries MySQL avec EXPLAIN ANALYZE.",
        ],
        commands: [
          "kubectl exec -it <mysql-pod> -- mysql -e 'SHOW PROCESSLIST;'",
          "kubectl top pods -n production",
        ],
        source: "knowledge_base",
      },
    ],
  };

  if (incident.id.includes("oom") || incident.name.toLowerCase().includes("memory") || incident.name.toLowerCase().includes("oom")) {
    return base.oom;
  }
  if (incident.id.includes("secret") || incident.name.toLowerCase().includes("secret") || incident.name.toLowerCase().includes("gitleaks")) {
    return base.secret;
  }
  if (incident.id.includes("network") || incident.name.toLowerCase().includes("latence") || incident.name.toLowerCase().includes("réseau")) {
    return base.network;
  }
  // default: OOM
  return base.oom;
}

function getMockLLMSolution(incident: MockIncident): LLMSolution {
  return {
    rootCauseHypothesis: `Analyse LLM : Le service ${incident.service} présente une instabilité liée à ${incident.summary.slice(0, 80)}. La cause probable est une configuration inadaptée à la charge actuelle.`,
    confidence: "medium",
    proposedFix: {
      summary: "Augmentation des ressources allouées et optimisation de la configuration.",
      steps: [
        "Analyser les métriques de ressources sur les 24 dernières heures.",
        "Appliquer les limites recommandées selon les pics observés.",
        "Activer les health checks et le liveness probe.",
        "Mettre en place une alerte PagerDuty sur le seuil de déclenchement.",
      ],
      commands: [
        `kubectl describe pod -l app=${incident.service} | grep -A5 'Limits'`,
        "kubectl top pods --sort-by=memory",
        `kubectl patch deployment ${incident.service} --patch '{"spec":{"template":{"spec":{"containers":[{"name":"app","resources":{"limits":{"memory":"512Mi","cpu":"500m"}}}]}}}}'`,
      ],
    },
    riskLevel: "medium",
    validationSteps: [
      "Vérifier que le pod redémarre sans CrashLoopBackOff.",
      "Surveiller les métriques pendant 10 minutes après le déploiement.",
      "Confirmer que les endpoints de santé répondent en < 200ms.",
    ],
  };
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const confidenceColor = (c: number) => {
  if (c >= 0.9) return "text-emerald-400";
  if (c >= 0.8) return "text-yellow-400";
  return "text-orange-400";
};

const confidenceBg = (c: number) => {
  if (c >= 0.9) return "bg-emerald-500/15 border-emerald-500/30";
  if (c >= 0.8) return "bg-yellow-500/15 border-yellow-500/30";
  return "bg-orange-500/15 border-orange-500/30";
};

const severityColor = (s: string) => {
  switch (s) {
    case "critical": return "bg-red-500/15 text-red-400 border-red-500/30";
    case "high":     return "bg-orange-500/15 text-orange-400 border-orange-500/30";
    case "medium":   return "bg-yellow-500/15 text-yellow-400 border-yellow-500/30";
    default:         return "bg-blue-500/15 text-blue-400 border-blue-500/30";
  }
};

const riskColor = (r: string) => {
  switch (r) {
    case "critical": return "text-red-400";
    case "high":     return "text-orange-400";
    case "medium":   return "text-yellow-400";
    default:         return "text-emerald-400";
  }
};

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export const IncidentResponseDialog = ({ open, incident, onClose, isPremium = true }: Props) => {
  const [step, setStep] = useState<DialogStep>("alert");
  const [ragSolutions, setRagSolutions] = useState<RAGSolution[]>([]);
  const [ragIndex, setRagIndex] = useState(0);
  const [llmSolution, setLlmSolution] = useState<LLMSolution | null>(null);
  const [storedInRag, setStoredInRag] = useState(false);

  const reset = () => {
    setStep("alert");
    setRagSolutions([]);
    setRagIndex(0);
    setLlmSolution(null);
    setStoredInRag(false);
  };

  const handleClose = (outcome: "manual" | "resolved" | "rejected" | "dismissed") => {
    reset();
    onClose(outcome);
  };

  /* ---- RAG search ---- */
  const startRAGSearch = () => {
    if (!incident) return;
    setStep("rag_loading");
    setTimeout(() => {
      const solutions = getMockRAGSolutions(incident);
      const filtered = solutions.filter((s) => s.confidence >= 0.8);
      setRagSolutions(filtered);
      setRagIndex(0);
      setStep(filtered.length > 0 ? "rag_result" : "llm_loading");
      if (filtered.length === 0) {
        // Go straight to LLM
        setTimeout(() => {
          setLlmSolution(getMockLLMSolution(incident!));
          setStep("llm_result");
        }, 2500);
      }
    }, 2000);
  };

  /* ---- Skip RAG solution (go to next or LLM) ---- */
  const skipRAGSolution = () => {
    if (!incident) return;
    const nextIdx = ragIndex + 1;
    if (nextIdx < ragSolutions.length) {
      setRagIndex(nextIdx);
    } else {
      setStep("llm_loading");
      setTimeout(() => {
        setLlmSolution(getMockLLMSolution(incident));
        setStep("llm_result");
      }, 2500);
    }
  };

  /* ---- Approve RAG solution ---- */
  const approveRAGSolution = () => {
    setStep("resolved");
  };

  /* ---- Validate LLM (store in RAG) ---- */
  const validateLLM = () => {
    setStoredInRag(true);
    setTimeout(() => setStep("resolved"), 500);
  };

  /* ---- Reject LLM ---- */
  const rejectLLM = () => {
    setStep("rejected");
  };

  if (!incident) return null;

  const currentRAG = ragSolutions[ragIndex];

  return (
    <Dialog open={open} onOpenChange={() => {}}>
      <DialogContent className="sm:max-w-xl max-h-[92vh] flex flex-col">

        {/* ---- STEP: alert ---- */}
        {step === "alert" && (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2 text-orange-400">
                <AlertTriangle className="w-5 h-5" />
                Incident détecté — Agent interrompu
              </DialogTitle>
              <DialogDescription>
                Une erreur a interrompu l'exécution du pipeline. Choisissez comment y remédier.
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-4 py-2">
              {/* Incident card */}
              <div className="rounded-xl border border-orange-500/30 bg-orange-500/5 p-4 space-y-2">
                <div className="flex items-center gap-2 flex-wrap">
                  <Badge variant="outline" className={severityColor(incident.severity)}>
                    {incident.severity.toUpperCase()}
                  </Badge>
                  <code className="text-xs font-mono text-primary">{incident.id}</code>
                  <span className="text-xs text-muted-foreground">— {incident.service}</span>
                </div>
                <p className="text-sm font-semibold text-foreground">{incident.name}</p>
                <p className="text-xs text-muted-foreground">{incident.summary}</p>
              </div>

              {/* Options */}
              <div className="grid grid-cols-1 gap-3">
                <button
                  onClick={() => handleClose("manual")}
                  className="flex items-start gap-3 rounded-xl border border-border/60 bg-muted/40 p-4 text-left hover:border-border transition-all hover:bg-muted/60"
                >
                  <Wrench className="w-5 h-5 text-muted-foreground mt-0.5 shrink-0" />
                  <div>
                    <p className="text-sm font-medium">Corriger manuellement</p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      Pause le pipeline et ouvre l'éditeur de configuration pour une correction manuelle.
                    </p>
                  </div>
                </button>

                <button
                  onClick={isPremium ? startRAGSearch : undefined}
                  className={`flex items-start gap-3 rounded-xl border p-4 text-left transition-all ${
                    isPremium
                      ? "border-primary/40 bg-primary/5 hover:border-primary/60 hover:bg-primary/10 cursor-pointer"
                      : "border-border/40 bg-muted/20 cursor-not-allowed opacity-60"
                  }`}
                >
                  <div className="relative shrink-0">
                    <Bot className="w-5 h-5 text-primary mt-0.5" />
                    <Crown className="w-3 h-3 text-yellow-400 absolute -top-1 -right-1" />
                  </div>
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-medium">Incident Response Agent</p>
                      <Badge variant="outline" className="text-[10px] px-1.5 border-yellow-400/40 text-yellow-400">
                        Premium
                      </Badge>
                    </div>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      Recherche dans la base de connaissance RAG puis consulte le LLM pour une recommandation intelligente.
                    </p>
                    {!isPremium && (
                      <p className="text-xs text-yellow-400 mt-1">⚠ Fonctionnalité Premium — abonnement requis</p>
                    )}
                  </div>
                </button>
              </div>
            </div>

            <DialogFooter>
              <Button variant="ghost" size="sm" onClick={() => handleClose("dismissed")}>
                Ignorer pour l'instant
              </Button>
            </DialogFooter>
          </>
        )}

        {/* ---- STEP: rag_loading ---- */}
        {step === "rag_loading" && (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <Database className="w-5 h-5 text-primary" />
                Recherche dans la base de connaissance…
              </DialogTitle>
              <DialogDescription>
                L'agent interroge la base RAG (ChromaDB) pour trouver des solutions similaires.
              </DialogDescription>
            </DialogHeader>

            <div className="py-8 flex flex-col items-center gap-4">
              <Loader2 className="w-10 h-10 text-primary animate-spin" />
              <div className="w-full max-w-xs space-y-2">
                <Progress value={undefined} className="h-1.5 animate-pulse" />
                <p className="text-xs text-center text-muted-foreground">
                  Calcul des embeddings et similarité cosinus…
                </p>
              </div>
            </div>
          </>
        )}

        {/* ---- STEP: rag_result ---- */}
        {step === "rag_result" && currentRAG && (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <Database className="w-5 h-5 text-primary" />
                Solution trouvée dans la RAG
                <Badge variant="outline" className="ml-auto text-[10px]">
                  {ragIndex + 1} / {ragSolutions.length}
                </Badge>
              </DialogTitle>
              <DialogDescription>
                Solution extraite de la base de connaissance. Vérifiez-la avant d'approuver.
              </DialogDescription>
            </DialogHeader>

            <div className="flex-1 overflow-y-auto space-y-4 min-h-0 py-1">
              {/* Confidence badge */}
              <div className={`flex items-center gap-3 rounded-lg border p-3 ${confidenceBg(currentRAG.confidence)}`}>
                <Sparkles className={`w-5 h-5 shrink-0 ${confidenceColor(currentRAG.confidence)}`} />
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold">{currentRAG.title}</span>
                  </div>
                  <div className="flex items-center gap-2 mt-1">
                    <span className="text-xs text-muted-foreground">Score de confiance :</span>
                    <span className={`text-sm font-bold ${confidenceColor(currentRAG.confidence)}`}>
                      {(currentRAG.confidence * 100).toFixed(1)}%
                    </span>
                    <Progress
                      value={currentRAG.confidence * 100}
                      className="h-1.5 w-20"
                    />
                  </div>
                </div>
              </div>

              {/* Root cause */}
              <div className="rounded-lg bg-muted/50 p-3 space-y-1">
                <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Cause racine identifiée</p>
                <p className="text-sm">{currentRAG.rootCause}</p>
              </div>

              {/* Steps */}
              <div className="space-y-2">
                <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Étapes de correction</p>
                <ol className="space-y-1.5">
                  {currentRAG.steps.map((s, i) => (
                    <li key={i} className="flex items-start gap-2 text-sm">
                      <span className="text-primary font-bold shrink-0">{i + 1}.</span>
                      <span>{s}</span>
                    </li>
                  ))}
                </ol>
              </div>

              {/* Commands */}
              {currentRAG.commands.length > 0 && (
                <div className="space-y-1.5">
                  <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Commandes</p>
                  {currentRAG.commands.map((cmd, i) => (
                    <code key={i} className="block text-xs bg-black/40 border border-border/40 rounded-lg px-3 py-2 font-mono text-green-400">
                      $ {cmd}
                    </code>
                  ))}
                </div>
              )}
            </div>

            <DialogFooter className="flex flex-wrap gap-2 pt-2 border-t border-border/50">
              <Button variant="outline" onClick={skipRAGSolution} className="gap-1.5">
                <SkipForward className="w-4 h-4" />
                {ragIndex + 1 < ragSolutions.length ? "Solution suivante" : "Passer au LLM"}
              </Button>
              <Button onClick={approveRAGSolution} className="gap-1.5 bg-emerald-600 hover:bg-emerald-500">
                <ThumbsUp className="w-4 h-4" /> Appliquer cette solution
              </Button>
            </DialogFooter>
          </>
        )}

        {/* ---- STEP: llm_loading ---- */}
        {step === "llm_loading" && (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <Brain className="w-5 h-5 text-purple-400" />
                Aucune solution RAG validée — Consultation du LLM…
              </DialogTitle>
              <DialogDescription>
                L'agent appelle le modèle GPT-OSS-120B via Groq pour une recommandation avancée.
              </DialogDescription>
            </DialogHeader>

            <div className="py-8 flex flex-col items-center gap-4">
              <div className="relative">
                <Loader2 className="w-10 h-10 text-purple-400 animate-spin" />
                <Brain className="w-4 h-4 text-purple-300 absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2" />
              </div>
              <div className="w-full max-w-xs space-y-2">
                <Progress value={undefined} className="h-1.5 animate-pulse" />
                <p className="text-xs text-center text-muted-foreground">
                  Raisonnement en cours (streaming)…
                </p>
              </div>
            </div>
          </>
        )}

        {/* ---- STEP: llm_result ---- */}
        {step === "llm_result" && llmSolution && (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <Brain className="w-5 h-5 text-purple-400" />
                Recommandation LLM
                <Badge variant="outline" className="ml-auto text-[10px] border-purple-400/40 text-purple-400">
                  GPT-OSS-120B
                </Badge>
              </DialogTitle>
              <DialogDescription>
                Validez cette recommandation pour l'appliquer et l'enregistrer dans la base RAG.
              </DialogDescription>
            </DialogHeader>

            <div className="flex-1 overflow-y-auto space-y-4 min-h-0 py-1">
              {/* Confidence + risk */}
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-lg border border-purple-400/20 bg-purple-500/5 p-3 text-center">
                  <p className="text-xs text-muted-foreground">Confiance LLM</p>
                  <p className="text-base font-bold text-purple-400 capitalize mt-1">{llmSolution.confidence}</p>
                </div>
                <div className="rounded-lg border border-border/40 bg-muted/40 p-3 text-center">
                  <p className="text-xs text-muted-foreground">Niveau de risque</p>
                  <p className={`text-base font-bold capitalize mt-1 ${riskColor(llmSolution.riskLevel)}`}>
                    {llmSolution.riskLevel}
                  </p>
                </div>
              </div>

              {/* Hypothesis */}
              <div className="rounded-lg bg-muted/50 p-3 space-y-1">
                <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Hypothèse de cause</p>
                <p className="text-sm">{llmSolution.rootCauseHypothesis}</p>
              </div>

              {/* Fix summary */}
              <div className="space-y-1">
                <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Correction proposée</p>
                <p className="text-sm text-foreground">{llmSolution.proposedFix.summary}</p>
              </div>

              {/* Steps */}
              <div className="space-y-2">
                <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Étapes</p>
                <ol className="space-y-1.5">
                  {llmSolution.proposedFix.steps.map((s, i) => (
                    <li key={i} className="flex items-start gap-2 text-sm">
                      <span className="text-purple-400 font-bold shrink-0">{i + 1}.</span>
                      <span>{s}</span>
                    </li>
                  ))}
                </ol>
              </div>

              {/* Commands */}
              {llmSolution.proposedFix.commands.length > 0 && (
                <div className="space-y-1.5">
                  <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Commandes</p>
                  {llmSolution.proposedFix.commands.map((cmd, i) => (
                    <code key={i} className="block text-xs bg-black/40 border border-border/40 rounded-lg px-3 py-2 font-mono text-purple-300">
                      $ {cmd}
                    </code>
                  ))}
                </div>
              )}

              {/* Validation steps */}
              <div className="rounded-lg border border-blue-400/20 bg-blue-500/5 p-3 space-y-1.5">
                <p className="text-xs font-semibold text-blue-400 uppercase tracking-wide">Étapes de validation</p>
                {llmSolution.validationSteps.map((s, i) => (
                  <p key={i} className="text-xs text-muted-foreground">• {s}</p>
                ))}
              </div>

              <div className="rounded-lg border border-yellow-400/20 bg-yellow-500/5 p-2.5 text-xs text-yellow-400">
                💡 En validant, cette solution sera enregistrée dans la base RAG pour les incidents futurs.
              </div>
            </div>

            <DialogFooter className="flex flex-wrap gap-2 pt-2 border-t border-border/50">
              <Button
                variant="outline"
                onClick={rejectLLM}
                className="gap-1.5 border-red-400/30 text-red-400 hover:bg-red-400/10"
              >
                <ThumbsDown className="w-4 h-4" /> Rejeter
              </Button>
              <Button onClick={validateLLM} className="gap-1.5 bg-purple-600 hover:bg-purple-500">
                <ThumbsUp className="w-4 h-4" /> Valider — Enregistrer dans RAG
              </Button>
            </DialogFooter>
          </>
        )}

        {/* ---- STEP: resolved ---- */}
        {step === "resolved" && (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2 text-emerald-400">
                <CheckCircle2 className="w-5 h-5" /> Incident résolu
              </DialogTitle>
              <DialogDescription>
                La solution a été appliquée avec succès.
              </DialogDescription>
            </DialogHeader>

            <div className="py-6 flex flex-col items-center gap-4 text-center">
              <div className="w-16 h-16 rounded-full bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center">
                <CheckCircle2 className="w-8 h-8 text-emerald-400" />
              </div>
              <div className="space-y-1.5">
                <p className="text-sm font-semibold">L'incident <span className="text-primary">{incident.name}</span> a été traité.</p>
                {storedInRag && (
                  <p className="text-xs text-muted-foreground">
                    ✅ La solution LLM a été enregistrée dans la base RAG pour les futurs incidents similaires.
                  </p>
                )}
                <p className="text-xs text-muted-foreground">Le pipeline peut reprendre son exécution.</p>
              </div>
            </div>

            <DialogFooter>
              <Button onClick={() => handleClose("resolved")} className="gap-1.5">
                <ChevronRight className="w-4 h-4" /> Reprendre le pipeline
              </Button>
            </DialogFooter>
          </>
        )}

        {/* ---- STEP: rejected ---- */}
        {step === "rejected" && (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2 text-red-400">
                <XCircle className="w-5 h-5" /> Recommandation rejetée
              </DialogTitle>
              <DialogDescription>
                La solution LLM a été rejetée. Le pipeline est mis en pause.
              </DialogDescription>
            </DialogHeader>

            <div className="py-6 flex flex-col items-center gap-4 text-center">
              <div className="w-16 h-16 rounded-full bg-red-500/10 border border-red-500/20 flex items-center justify-center">
                <XCircle className="w-8 h-8 text-red-400" />
              </div>
              <div className="space-y-1.5">
                <p className="text-sm font-semibold">La solution n'a pas résolu le problème.</p>
                <p className="text-xs text-muted-foreground">
                  Le pipeline est mis en pause. Vous pouvez le corriger manuellement ou escalader à l'équipe on-call.
                </p>
              </div>
            </div>

            <DialogFooter className="flex gap-2">
              <Button variant="outline" onClick={() => handleClose("manual")} className="gap-1.5">
                <Wrench className="w-4 h-4" /> Corriger manuellement
              </Button>
              <Button variant="outline" onClick={() => handleClose("dismissed")} className="gap-1.5 border-red-400/30 text-red-400">
                Escalader à l'équipe
              </Button>
            </DialogFooter>
          </>
        )}

        {/* ---- STEP: manual ---- */}
        {step === "manual" && (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <Wrench className="w-5 h-5 text-warning" /> Correction manuelle
              </DialogTitle>
            </DialogHeader>
            <div className="py-4 text-sm text-muted-foreground">
              Le pipeline est mis en pause. Corrigez l'erreur manuellement puis reprenez.
            </div>
            <DialogFooter>
              <Button onClick={() => handleClose("manual")} className="gap-1.5">
                <ChevronRight className="w-4 h-4" /> Fermer
              </Button>
            </DialogFooter>
          </>
        )}

      </DialogContent>
    </Dialog>
  );
};

export default IncidentResponseDialog;
