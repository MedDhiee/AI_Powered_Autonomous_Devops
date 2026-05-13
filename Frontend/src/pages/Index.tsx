import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { ArrowRight, Shield, Zap, GitBranch, Activity } from "lucide-react";

const features = [
  {
    icon: GitBranch,
    title: "CI/CD Intelligent",
    desc: "Génération automatique de pipelines",
  },
  { icon: Shield, title: "DevSecOps", desc: "Scans de sécurité intégrés" },
  {
    icon: Zap,
    title: "Chaos Engineering",
    desc: "Tests de résilience automatisés",
  },
  {
    icon: Activity,
    title: "Self-Healing",
    desc: "Réponse aux incidents en temps réel",
  },
];

const Index = () => {
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-background flex flex-col">
      <header className="border-b border-border/60 px-6 py-0 flex items-center justify-between backdrop-blur-md bg-background/60 sticky top-0 z-50">
        <div className="flex items-center h-[110px]">
          <img
            src="/logo_hiveops.png"
            alt="HiveOps Logo"
            className="h-full w-auto object-contain"
          />
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" size="sm" onClick={() => navigate("/login")}>
            Se connecter
          </Button>
          <Button size="sm" onClick={() => navigate("/signup")}>
            Commencer
          </Button>
        </div>
      </header>

      <main className="flex-1 flex flex-col items-center justify-center px-6 py-20">
        <div className="max-w-2xl text-center space-y-6 animate-fade-in">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-primary/20 bg-primary/5 text-primary text-sm">
            <Zap className="w-3.5 h-3.5" /> Orchestration DevOps par agents IA
          </div>

          <h1 className="text-4xl md:text-5xl font-extrabold leading-tight">
            Déployez vos projets avec des{" "}
            <span className="gradient-text">agents intelligents</span>
          </h1>

          <p className="text-muted-foreground text-lg max-w-xl mx-auto">
            Pipeline CI/CD, sécurité, infrastructure — tout automatisé avec
            validation humaine à chaque étape critique.
          </p>

          <Button
            size="lg"
            onClick={() => navigate("/dashboard")}
            className="gap-2 text-base px-8"
          >
            Commencer gratuitement <ArrowRight className="w-4 h-4" />
          </Button>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-16 max-w-3xl w-full animate-fade-in">
          {features.map((f) => (
            <div
              key={f.title}
              className="glass-card rounded-xl p-4 text-center space-y-2 hover:border-primary/30 transition-colors"
            >
              <div className="inline-flex w-10 h-10 rounded-lg bg-primary/10 items-center justify-center mx-auto">
                <f.icon className="w-5 h-5 text-primary" />
              </div>
              <p className="font-medium text-sm">{f.title}</p>
              <p className="text-xs text-muted-foreground">{f.desc}</p>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
};

export default Index;
