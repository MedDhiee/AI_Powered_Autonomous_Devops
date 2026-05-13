import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Bot, ArrowLeft, Bell, AlertTriangle, CheckCircle2, Zap } from "lucide-react";

const notifications = [
  { id: 1, type: "cicd" as const, title: "Pipeline CI/CD terminé", desc: "Le pipeline pour api-gateway-v2 a été complété avec succès.", time: "Il y a 5 min", read: false },
  { id: 2, type: "incident" as const, title: "Incident détecté", desc: "Un pod a été redémarré automatiquement sur le cluster GKE.", time: "Il y a 20 min", read: false },
  { id: 3, type: "chaos" as const, title: "Test de résilience terminé", desc: "Chaos Engineering : test de latence réseau réussi.", time: "Il y a 1h", read: false },
  { id: 4, type: "cicd" as const, title: "Déploiement réussi", desc: "frontend-dashboard déployé en production.", time: "Il y a 3h", read: true },
  { id: 5, type: "incident" as const, title: "Alerte sécurité", desc: "Vulnérabilité critique détectée dans les dépendances.", time: "Hier", read: true },
];

const typeConfig = {
  cicd: { icon: CheckCircle2, color: "text-success" },
  incident: { icon: AlertTriangle, color: "text-destructive" },
  chaos: { icon: Zap, color: "text-warning" },
};

const Notifications = () => {
  const navigate = useNavigate();
  const unreadCount = notifications.filter(n => !n.read).length;

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border px-6 py-4 flex items-center gap-3">
        <Button variant="ghost" size="icon" onClick={() => navigate("/dashboard")}>
          <ArrowLeft className="w-4 h-4" />
        </Button>
        <Bot className="w-5 h-5 text-primary" />
        <span className="font-bold gradient-text">Notifications</span>
        {unreadCount > 0 && <Badge className="bg-destructive text-destructive-foreground">{unreadCount}</Badge>}
      </header>

      <main className="max-w-3xl mx-auto px-6 py-8 space-y-3 animate-fade-in">
        {notifications.map(n => {
          const cfg = typeConfig[n.type];
          const Icon = cfg.icon;
          return (
            <div key={n.id} className={`glass-card rounded-lg p-4 flex items-start gap-3 transition-all ${!n.read ? "border-l-2 border-l-primary" : "opacity-70"}`}>
              <Icon className={`w-5 h-5 mt-0.5 shrink-0 ${cfg.color}`} />
              <div className="flex-1">
                <p className="font-medium text-sm">{n.title}</p>
                <p className="text-sm text-muted-foreground mt-0.5">{n.desc}</p>
                <p className="text-xs text-muted-foreground mt-1">{n.time}</p>
              </div>
              {!n.read && <div className="w-2 h-2 rounded-full bg-primary mt-2 shrink-0" />}
            </div>
          );
        })}
      </main>
    </div>
  );
};

export default Notifications;
