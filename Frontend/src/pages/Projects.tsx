import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Bot, ArrowLeft, Eye, Copy, Trash2 } from "lucide-react";

const savedProjects = [
  { id: "1", name: "api-gateway-v2", infra: "GCP", status: "completed" as const, date: "2026-03-28" },
  { id: "2", name: "frontend-dashboard", infra: "GCP", status: "running" as const, date: "2026-04-01" },
  { id: "3", name: "auth-service", infra: "AWS", status: "failed" as const, date: "2026-03-15" },
  { id: "4", name: "payment-api", infra: "Minikube", status: "completed" as const, date: "2026-03-10" },
];

const statusConfig = {
  completed: { label: "Completed", className: "bg-success/15 text-success border-success/30" },
  running: { label: "Running", className: "bg-warning/15 text-warning border-warning/30" },
  failed: { label: "Failed", className: "bg-destructive/15 text-destructive border-destructive/30" },
};

const Projects = () => {
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border px-6 py-4 flex items-center gap-3">
        <Button variant="ghost" size="icon" onClick={() => navigate("/dashboard")}>
          <ArrowLeft className="w-4 h-4" />
        </Button>
        <Bot className="w-5 h-5 text-primary" />
        <span className="font-bold gradient-text">Projets Sauvegardés</span>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-8 space-y-3 animate-fade-in">
        {savedProjects.map(p => {
          const s = statusConfig[p.status];
          return (
            <div key={p.id} className="glass-card rounded-lg p-4 flex items-center justify-between">
              <div className="flex items-center gap-4">
                <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center">
                  <Bot className="w-5 h-5 text-primary" />
                </div>
                <div>
                  <p className="font-medium font-mono text-sm">{p.name}</p>
                  <p className="text-xs text-muted-foreground">{p.infra} • {p.date}</p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Badge variant="outline" className={s.className}>{s.label}</Badge>
                <Button variant="ghost" size="icon" onClick={() => navigate(`/pipeline/${p.id}`)} title="Voir">
                  <Eye className="w-4 h-4" />
                </Button>
                <Button variant="ghost" size="icon" title="Cloner">
                  <Copy className="w-4 h-4" />
                </Button>
                <Button variant="ghost" size="icon" title="Supprimer" className="text-destructive hover:text-destructive">
                  <Trash2 className="w-4 h-4" />
                </Button>
              </div>
            </div>
          );
        })}
      </main>
    </div>
  );
};

export default Projects;
