import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Bot, ArrowLeft, History, Search, Shield, Rocket, Play, Zap } from "lucide-react";

const auditLogs = [
  { id: 1, action: "Pipeline lancé", agent: "Orchestrateur", project: "api-gateway-v2", date: "2026-04-03 14:30", icon: Play },
  { id: 2, action: "Analyse d'architecture terminée", agent: "Architecture Analysis", project: "api-gateway-v2", date: "2026-04-03 14:32", icon: Search },
  { id: 3, action: "YAML CI/CD généré et validé", agent: "CI/CD Agent", project: "api-gateway-v2", date: "2026-04-03 14:35", icon: Play },
  { id: 4, action: "Scan de sécurité complété", agent: "DevSecOps Agent", project: "api-gateway-v2", date: "2026-04-03 14:37", icon: Shield },
  { id: 5, action: "Terraform appliqué", agent: "Deployment Agent", project: "api-gateway-v2", date: "2026-04-03 14:40", icon: Rocket },
  { id: 6, action: "Test Chaos réussi", agent: "Chaos Engineering", project: "frontend-dashboard", date: "2026-04-02 10:15", icon: Zap },
  { id: 7, action: "Pipeline modifié par l'utilisateur", agent: "Human-in-the-loop", project: "auth-service", date: "2026-03-30 09:20", icon: History },
];

const AuditHistory = () => {
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border px-6 py-4 flex items-center gap-3">
        <Button variant="ghost" size="icon" onClick={() => navigate("/dashboard")}>
          <ArrowLeft className="w-4 h-4" />
        </Button>
        <Bot className="w-5 h-5 text-primary" />
        <span className="font-bold gradient-text">Audit & Historique</span>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-8 animate-fade-in">
        <div className="space-y-2">
          {auditLogs.map(log => (
            <div key={log.id} className="glass-card rounded-lg p-4 flex items-center gap-4">
              <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                <log.icon className="w-4 h-4 text-primary" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="font-medium text-sm">{log.action}</p>
                <p className="text-xs text-muted-foreground">{log.agent} • {log.project}</p>
              </div>
              <Badge variant="outline" className="text-xs shrink-0">{log.date}</Badge>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
};

export default AuditHistory;
