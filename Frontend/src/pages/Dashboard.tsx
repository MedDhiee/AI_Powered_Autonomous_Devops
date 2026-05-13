import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Bot,
  Plus,
  Eye,
  AlertTriangle,
  FileCode,
  Bell,
  History,
  HelpCircle,
  User,
  FolderOpen,
  Zap,
} from "lucide-react";

interface Project {
  id: string;
  name: string;
  status: "completed" | "running" | "failed";
  createdAt: string;
}

const mockProjects: Project[] = [
  {
    id: "1",
    name: "api-gateway-v2",
    status: "completed",
    createdAt: "2026-03-28",
  },
  {
    id: "2",
    name: "frontend-dashboard",
    status: "running",
    createdAt: "2026-04-01",
  },
  { id: "3", name: "auth-service", status: "failed", createdAt: "2026-03-15" },
];

const statusConfig = {
  completed: {
    label: "Completed",
    className: "bg-success/15 text-success border-success/30",
  },
  running: {
    label: "Running",
    className:
      "bg-warning/15 text-warning border-warning/30 animate-pulse-slow",
  },
  failed: {
    label: "Failed",
    className: "bg-destructive/15 text-destructive border-destructive/30",
  },
};

const navItems = [
  { icon: Bell, label: "Notifications", path: "/notifications", badge: 3 },
  { icon: History, label: "Audit & Historique", path: "/audit" },
  { icon: FolderOpen, label: "Projets sauvegardés", path: "/projects" },
  { icon: Zap, label: "Simulations Chaos", path: "/chaos" },
  { icon: User, label: "Profil", path: "/profile" },
  { icon: HelpCircle, label: "Aide / Support", path: "/help" },
];

const Dashboard = () => {
  const navigate = useNavigate();
  const [projects] = useState<Project[]>(mockProjects);

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="border-b border-border/60 px-6 py-1 flex items-center justify-between backdrop-blur-md bg-background/60 sticky top-0 z-50">
        {/* Logo */}
        <div className="flex items-center h-[110px]">
          <img
            src="/logo_hiveops.png"
            alt="HiveOps Logo"
            className="h-full w-auto object-contain"
          />
        </div>

        {/* Navbar Buttons */}
        <div className="flex items-center gap-2">
          {navItems.map((item) => (
            <Button
              key={item.path}
              variant="ghost"
              size="sm"
              onClick={() => navigate(item.path)}
              className="gap-1.5 relative p-8" // supprime le padding interne
            >
              <item.icon className="w-4 h-4" />
              <span className="hidden md:inline text-xs">{item.label}</span>
              {item.badge && (
                <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-destructive text-destructive-foreground text-[10px] flex items-center justify-center font-bold">
                  {item.badge}
                </span>
              )}
            </Button>
          ))}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate("/")}
            className="p-4"
          >
            Déconnexion
          </Button>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-8 space-y-6 animate-fade-in">
        {/* Alerts */}
        <div className="space-y-3">
          <div className="glass-card rounded-lg p-4 flex items-start gap-3 border-l-2 border-l-warning">
            <FileCode className="w-5 h-5 text-warning shrink-0 mt-0.5" />
            <p className="text-sm text-secondary-foreground">
              Vous devez fournir un{" "}
              <span className="font-semibold text-foreground">
                Dockerfile valide
              </span>{" "}
              pour votre application.
            </p>
          </div>
          <div className="glass-card rounded-lg p-4 flex items-start gap-3 border-l-2 border-l-warning">
            <AlertTriangle className="w-5 h-5 text-warning shrink-0 mt-0.5" />
            <p className="text-sm text-secondary-foreground">
              Les outils de sécurité (
              <span className="font-mono text-foreground text-xs">
                Trivy, Gitleaks, Checkov
              </span>
              ) doivent être disponibles pour les analyses automatisées.
            </p>
          </div>
        </div>

        {/* Title + Action */}
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-bold">Mes Projets</h1>
          <Button onClick={() => navigate("/automate")} className="gap-2">
            <Plus className="w-4 h-4" /> Automatiser un nouveau projet
          </Button>
        </div>

        {/* Projects */}
        <div className="grid gap-3">
          {projects.map((project) => {
            const s = statusConfig[project.status];
            return (
              <div
                key={project.id}
                className="glass-card rounded-lg p-4 flex items-center justify-between hover:border-primary/30 transition-colors"
              >
                <div className="flex items-center gap-4">
                  <div className="w-10 h-10 rounded-lg bg-muted flex items-center justify-center">
                    <Bot className="w-5 h-5 text-muted-foreground" />
                  </div>
                  <div>
                    <p className="font-medium font-mono text-sm">
                      {project.name}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {project.createdAt}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <Badge variant="outline" className={s.className}>
                    {s.label}
                  </Badge>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => navigate(`/pipeline/${project.id}`)}
                  >
                    <Eye className="w-4 h-4 mr-1" /> Détails
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      </main>
    </div>
  );
};

export default Dashboard;
