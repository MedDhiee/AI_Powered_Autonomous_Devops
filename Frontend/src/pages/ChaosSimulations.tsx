import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Bot, ArrowLeft, Zap, Play, CheckCircle2, Cpu, Wifi, Trash2 } from "lucide-react";

const projects = [
  { id: "1", name: "api-gateway-v2" },
  { id: "2", name: "frontend-dashboard" },
  { id: "4", name: "payment-api" },
];

const simTypes = [
  { id: "pod-kill", label: "Suppression de Pods", icon: Trash2, desc: "Supprime aléatoirement des pods pour tester le self-healing." },
  { id: "cpu-stress", label: "Surcharge CPU", icon: Cpu, desc: "Simule une charge CPU élevée pour tester l'autoscaling." },
  { id: "network-latency", label: "Latence Réseau", icon: Wifi, desc: "Injecte de la latence réseau entre les services." },
];

interface SimResult {
  type: string;
  project: string;
  status: "success" | "failed";
  duration: string;
  date: string;
}

const pastResults: SimResult[] = [
  { type: "Suppression de Pods", project: "api-gateway-v2", status: "success", duration: "2m 15s", date: "2026-04-02 14:30" },
  { type: "Surcharge CPU", project: "api-gateway-v2", status: "success", duration: "3m 45s", date: "2026-04-01 10:00" },
  { type: "Latence Réseau", project: "frontend-dashboard", status: "success", duration: "1m 50s", date: "2026-03-30 16:20" },
];

const ChaosSimulations = () => {
  const navigate = useNavigate();
  const [selectedProject, setSelectedProject] = useState("");
  const [selectedSim, setSelectedSim] = useState("");
  const [running, setRunning] = useState(false);
  const [done, setDone] = useState(false);

  const handleLaunch = () => {
    setRunning(true);
    setDone(false);
    setTimeout(() => {
      setRunning(false);
      setDone(true);
    }, 3000);
  };

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border px-6 py-4 flex items-center gap-3">
        <Button variant="ghost" size="icon" onClick={() => navigate("/dashboard")}>
          <ArrowLeft className="w-4 h-4" />
        </Button>
        <Bot className="w-5 h-5 text-primary" />
        <span className="font-bold gradient-text">Simulations Chaos Engineering</span>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-8 space-y-6 animate-fade-in">
        {/* Launch simulation */}
        <div className="glass-card rounded-xl p-6 space-y-4">
          <h2 className="font-semibold flex items-center gap-2">
            <Zap className="w-5 h-5 text-warning" /> Lancer une simulation
          </h2>

          <div className="space-y-3">
            <div className="space-y-1">
              <label className="text-sm font-medium">Projet cible</label>
              <Select value={selectedProject} onValueChange={setSelectedProject}>
                <SelectTrigger><SelectValue placeholder="Sélectionner un projet" /></SelectTrigger>
                <SelectContent>
                  {projects.map(p => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>

            <div className="grid gap-2">
              {simTypes.map(s => (
                <button
                  key={s.id}
                  onClick={() => setSelectedSim(s.id)}
                  className={`p-3 rounded-lg border text-left flex items-start gap-3 transition-all ${
                    selectedSim === s.id ? "border-primary bg-primary/5" : "border-border hover:border-muted-foreground"
                  }`}
                >
                  <s.icon className={`w-5 h-5 mt-0.5 shrink-0 ${selectedSim === s.id ? "text-primary" : "text-muted-foreground"}`} />
                  <div>
                    <p className="font-medium text-sm">{s.label}</p>
                    <p className="text-xs text-muted-foreground">{s.desc}</p>
                  </div>
                </button>
              ))}
            </div>

            <Button onClick={handleLaunch} disabled={!selectedProject || !selectedSim || running} className="gap-2">
              {running ? <><Zap className="w-4 h-4 animate-pulse" /> Simulation en cours...</> :
               done ? <><CheckCircle2 className="w-4 h-4" /> Simulation réussie !</> :
               <><Play className="w-4 h-4" /> Lancer la simulation</>}
            </Button>
          </div>
        </div>

        {/* Past results */}
        <div className="space-y-3">
          <h2 className="font-semibold text-sm">Historique des simulations</h2>
          {pastResults.map((r, i) => (
            <div key={i} className="glass-card rounded-lg p-4 flex items-center justify-between">
              <div>
                <p className="font-medium text-sm">{r.type}</p>
                <p className="text-xs text-muted-foreground">{r.project} • {r.date}</p>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">{r.duration}</span>
                <Badge variant="outline" className="bg-success/15 text-success border-success/30">Réussi</Badge>
              </div>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
};

export default ChaosSimulations;
