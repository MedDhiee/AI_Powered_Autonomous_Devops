import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Bot, ArrowLeft, BookOpen, Play, Search, Shield, Rocket, Zap, AlertTriangle, HelpCircle } from "lucide-react";

const guides = [
  { icon: Search, title: "Architecture Analysis Agent", desc: "Analyse automatiquement la structure de votre projet pour détecter les composants, dépendances et l'architecture globale." },
  { icon: Play, title: "CI/CD Agent", desc: "Génère automatiquement des fichiers de pipeline (GitHub Actions, GitLab CI) adaptés à votre stack technologique." },
  { icon: Shield, title: "DevSecOps Agent", desc: "Exécute des scans de sécurité via Trivy, Gitleaks et Checkov pour détecter les vulnérabilités." },
  { icon: Rocket, title: "Deployment Agent", desc: "Génère et applique les fichiers d'infrastructure (Terraform, Kubernetes manifests) selon le provider choisi." },
  { icon: Zap, title: "Chaos Engineering Agent", desc: "Simule des pannes (suppression de pods, surcharge CPU, latence réseau) pour tester la résilience." },
  { icon: AlertTriangle, title: "Incident Response Agent", desc: "Analyse les logs, propose des corrections et déclenche un redéploiement automatique en cas d'incident." },
];

const faqs = [
  { q: "Comment ajouter un nouveau projet ?", a: "Depuis le Dashboard, cliquez sur 'Automatiser un nouveau projet' et suivez les étapes du wizard." },
  { q: "Qu'est-ce que le Human-in-the-loop ?", a: "C'est un mécanisme de validation humaine qui pause le pipeline pour vous permettre de vérifier les fichiers générés avant de continuer." },
  { q: "Comment fonctionne le Chaos Engineering ?", a: "Sélectionnez un projet existant dans la section Simulations, puis choisissez le type de panne à simuler." },
];

const HelpSupport = () => {
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border px-6 py-4 flex items-center gap-3">
        <Button variant="ghost" size="icon" onClick={() => navigate("/dashboard")}>
          <ArrowLeft className="w-4 h-4" />
        </Button>
        <Bot className="w-5 h-5 text-primary" />
        <span className="font-bold gradient-text">Aide & Support</span>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-8 space-y-8 animate-fade-in">
        <section>
          <h2 className="text-lg font-bold flex items-center gap-2 mb-4">
            <BookOpen className="w-5 h-5 text-primary" /> Guide des Agents
          </h2>
          <div className="grid gap-3">
            {guides.map(g => (
              <div key={g.title} className="glass-card rounded-lg p-4 flex items-start gap-3">
                <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                  <g.icon className="w-4 h-4 text-primary" />
                </div>
                <div>
                  <p className="font-medium text-sm">{g.title}</p>
                  <p className="text-sm text-muted-foreground mt-0.5">{g.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section>
          <h2 className="text-lg font-bold flex items-center gap-2 mb-4">
            <HelpCircle className="w-5 h-5 text-primary" /> FAQ
          </h2>
          <div className="space-y-3">
            {faqs.map((f, i) => (
              <div key={i} className="glass-card rounded-lg p-4">
                <p className="font-medium text-sm">{f.q}</p>
                <p className="text-sm text-muted-foreground mt-1">{f.a}</p>
              </div>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
};

export default HelpSupport;
