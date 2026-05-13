import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Bot, ArrowLeft, ArrowRight, CheckCircle2, GitBranch, Shield, Cpu, Zap, Cloud, Database, Network, Activity, Monitor, ServerCog } from "lucide-react";

const steps = ["Repository", "Agents Premium", "Infrastructure", "Configuration"];

type Infra = "gcp" | "aws" | "azure" | "minikube" | "";
type DeployType = "vm" | "kubernetes" | "serverless" | "database" | "";

const Automate = () => {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [repoUrl, setRepoUrl] = useState("");
  const [repoVerified, setRepoVerified] = useState(false);
  const [useJenkins, setUseJenkins] = useState(false);
  const [chaosAgent, setChaosAgent] = useState(false);
  const [incidentAgent, setIncidentAgent] = useState(false);
  const [infra, setInfra] = useState<Infra>("");
  const [deployType, setDeployType] = useState<DeployType>("");

  // GCP Config state
  const [projectId, setProjectId] = useState("");
  const [region, setRegion] = useState("");
  const [zone, setZone] = useState("");
  // VM
  const [vmMachineType, setVmMachineType] = useState("e2-medium");
  const [vmCpu, setVmCpu] = useState(2);
  const [vmRam, setVmRam] = useState(4);
  const [vmDiskSize, setVmDiskSize] = useState(50);
  const [vmDiskType, setVmDiskType] = useState("ssd");
  const [vmOsImage, setVmOsImage] = useState("debian-11");
  const [vmExternalIp, setVmExternalIp] = useState("yes");
  const [vmNetwork, setVmNetwork] = useState("default");
  // K8s
  const [clusterName, setClusterName] = useState("");
  const [nodeCount, setNodeCount] = useState(3);
  const [machineType, setMachineType] = useState("e2-medium");
  const [autoscaling, setAutoscaling] = useState(false);
  const [minNodes, setMinNodes] = useState(1);
  const [maxNodes, setMaxNodes] = useState(5);
  const [k8sMode, setK8sMode] = useState("zonal");
  // Serverless
  const [serviceName, setServiceName] = useState("");
  const [containerImage, setContainerImage] = useState("");
  const [minInstances, setMinInstances] = useState(0);
  const [maxInstances, setMaxInstances] = useState(10);
  const [accessType, setAccessType] = useState("public");
  // DB
  const [dbType, setDbType] = useState("postgresql");
  const [dbVersion, setDbVersion] = useState("");
  const [dbCpu, setDbCpu] = useState(2);
  const [dbRam, setDbRam] = useState(4);
  const [dbStorage, setDbStorage] = useState(10);
  const [dbPassword, setDbPassword] = useState("");
  // Network
  const [vpc, setVpc] = useState("default");
  const [firewallHttp, setFirewallHttp] = useState(true);
  const [firewallHttps, setFirewallHttps] = useState(true);
  const [firewallSsh, setFirewallSsh] = useState(false);
  const [privateIp, setPrivateIp] = useState(false);
  // DevSecOps
  const [scanDocker, setScanDocker] = useState(true);
  const [scanDeps, setScanDeps] = useState(true);
  // Monitoring
  const [logging, setLogging] = useState(true);
  const [monitoring, setMonitoring] = useState(true);

  const verifyRepo = () => {
    if (repoUrl.trim()) setRepoVerified(true);
  };

  const canNext = () => {
    if (step === 0) return repoVerified;
    if (step === 1) return true;
    if (step === 2) return !!infra;
    return true;
  };

  // On step 0, once repo is verified the two inline buttons handle navigation —
  // hide the footer "Suivant" to avoid confusion.
  const showFooterNext = !(step === 0 && repoVerified);

  const handleLaunch = () => {
    navigate("/pipeline/new", {
      state: { chaosAgent, incidentAgent, infra, deployType, repoUrl, useJenkins },
    });
  };

  const infraOptions = [
    { value: "gcp", label: "Google Cloud (GCP)", icon: Cloud },
    { value: "aws", label: "Amazon Web Services", icon: Cloud },
    { value: "azure", label: "Microsoft Azure", icon: Cloud },
    { value: "minikube", label: "Minikube (Local)", icon: Cpu },
  ];

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border px-6 py-4 flex items-center gap-3">
        <Button variant="ghost" size="icon" onClick={() => navigate("/dashboard")}>
          <ArrowLeft className="w-4 h-4" />
        </Button>
        <Bot className="w-5 h-5 text-primary" />
        <span className="font-bold gradient-text">Nouveau Projet</span>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-8 animate-fade-in">
        {/* Stepper */}
        <div className="flex items-center justify-between mb-8">
          {steps.map((s, i) => (
            <div key={s} className="flex items-center gap-2">
              <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold border transition-all ${
                i < step ? "bg-primary border-primary text-primary-foreground" :
                i === step ? "border-primary text-primary glow-border" :
                "border-border text-muted-foreground"
              }`}>
                {i < step ? <CheckCircle2 className="w-4 h-4" /> : i + 1}
              </div>
              <span className={`text-sm hidden sm:inline ${i === step ? "text-foreground font-medium" : "text-muted-foreground"}`}>{s}</span>
              {i < steps.length - 1 && <div className={`w-8 lg:w-16 h-px ${i < step ? "bg-primary" : "bg-border"}`} />}
            </div>
          ))}
        </div>

        {/* Step 0: Repository */}
        {step === 0 && (
          <div className="glass-card rounded-xl p-6 space-y-5">
            <div className="flex items-center gap-2">
              <GitBranch className="w-5 h-5 text-primary" />
              <h2 className="text-lg font-semibold">Repository Git</h2>
            </div>

            <div className="space-y-2">
              <Label>URL du repository</Label>
              <div className="flex gap-2">
                <Input
                  placeholder="https://github.com/user/repo.git"
                  value={repoUrl}
                  onChange={e => { setRepoUrl(e.target.value); setRepoVerified(false); setUseJenkins(false); }}
                />
                <Button onClick={verifyRepo} disabled={!repoUrl.trim()}>Vérifier</Button>
              </div>
            </div>

            {repoVerified && (
              <div className="space-y-4 animate-fade-in">
                <div className="flex items-center gap-2 text-success text-sm">
                  <CheckCircle2 className="w-4 h-4" /> Repository vérifié avec succès
                </div>

                {/* Jenkins option */}
                <div className={`rounded-xl border p-4 space-y-3 transition-all ${useJenkins ? "border-orange-500/50 bg-orange-500/5" : "border-border/50 bg-muted/40"}`}>
                  <div className="flex items-center gap-3">
                    <div className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ${useJenkins ? "bg-orange-500/15 border border-orange-500/30" : "bg-muted border border-border"}`}>
                      <ServerCog className={`w-5 h-5 ${useJenkins ? "text-orange-400" : "text-muted-foreground"}`} />
                    </div>
                    <div className="flex-1">
                      <p className={`text-sm font-semibold ${useJenkins ? "text-orange-400" : "text-muted-foreground"}`}>
                        Utiliser Jenkins pour le CI/CD ?
                      </p>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        Génère un <code className="font-mono">Jenkinsfile</code> à la place des pipelines GitHub/GitLab natifs.
                      </p>
                    </div>
                  </div>

                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      onClick={() => { setUseJenkins(true); setStep(s => s + 1); }}
                      className={`gap-2 transition-all ${useJenkins ? "bg-orange-500 hover:bg-orange-600 text-white shadow-[0_0_16px_hsl(25_95%_55%/0.35)]" : "bg-muted text-muted-foreground border border-border hover:bg-muted/80 hover:text-foreground shadow-none"}`}
                      style={{ boxShadow: useJenkins ? undefined : "none" }}
                    >
                      <ServerCog className="w-4 h-4" />
                      Oui, utiliser Jenkins
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => { setUseJenkins(false); setStep(s => s + 1); }}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      Non, continuer sans Jenkins
                    </Button>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Step 1: Premium Agents */}
        {step === 1 && (
          <div className="space-y-4">
            <div className="glass-card rounded-xl p-6 space-y-3">
              <div className="flex items-start gap-3">
                <Checkbox checked={chaosAgent} onCheckedChange={v => setChaosAgent(!!v)} id="chaos" />
                <div>
                  <label htmlFor="chaos" className="font-medium flex items-center gap-2 cursor-pointer">
                    <Zap className="w-4 h-4 text-warning" /> Chaos Engineering Agent
                  </label>
                  <p className="text-sm text-muted-foreground mt-1">Système capable de simuler des pannes (suppression de pods, surcharge CPU, latence réseau) afin de tester la résilience de l'infrastructure.</p>
                </div>
              </div>
            </div>
            <div className="glass-card rounded-xl p-6 space-y-3">
              <div className="flex items-start gap-3">
                <Checkbox checked={incidentAgent} onCheckedChange={v => setIncidentAgent(!!v)} id="incident" />
                <div>
                  <label htmlFor="incident" className="font-medium flex items-center gap-2 cursor-pointer">
                    <Shield className="w-4 h-4 text-destructive" /> Incident Response Agent
                  </label>
                  <p className="text-sm text-muted-foreground mt-1">Système de self-healing capable d'analyser les logs, proposer des corrections et déclencher un redéploiement automatique en cas d'incident.</p>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Step 2: Infrastructure */}
        {step === 2 && (
          <div className="glass-card rounded-xl p-6 space-y-4">
            <h2 className="text-lg font-semibold">Choisissez votre infrastructure</h2>
            <div className="grid grid-cols-2 gap-3">
              {infraOptions.map(o => (
                <button
                  key={o.value}
                  onClick={() => setInfra(o.value as Infra)}
                  className={`p-4 rounded-lg border text-left transition-all ${
                    infra === o.value ? "border-primary bg-primary/5 glow-border" : "border-border hover:border-muted-foreground"
                  }`}
                >
                  <o.icon className={`w-5 h-5 mb-2 ${infra === o.value ? "text-primary" : "text-muted-foreground"}`} />
                  <p className="font-medium text-sm">{o.label}</p>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Step 3: Configuration */}
        {step === 3 && (
          <div className="space-y-4">
            {infra === "gcp" ? (
              <>
                {/* General */}
                <Section icon={Cloud} title="Général">
                  <div className="grid grid-cols-3 gap-3">
                    <div className="space-y-1">
                      <Label className="text-xs">Project ID</Label>
                      <Input value={projectId} onChange={e => setProjectId(e.target.value)} placeholder="my-project" />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs">Region</Label>
                      <Select value={region} onValueChange={setRegion}>
                        <SelectTrigger><SelectValue placeholder="Région" /></SelectTrigger>
                        <SelectContent>
                          {["us-central1", "europe-west1", "asia-east1"].map(r => <SelectItem key={r} value={r}>{r}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs">Zone</Label>
                      <Select value={zone} onValueChange={setZone}>
                        <SelectTrigger><SelectValue placeholder="Zone" /></SelectTrigger>
                        <SelectContent>
                          {["a", "b", "c"].map(z => <SelectItem key={z} value={`${region || "us-central1"}-${z}`}>{`${region || "us-central1"}-${z}`}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                </Section>

                {/* Deploy Type */}
                <Section icon={Cpu} title="Type de déploiement">
                  <div className="grid grid-cols-2 gap-2">
                    {(["vm", "kubernetes", "serverless", "database"] as const).map(dt => (
                      <button key={dt} onClick={() => setDeployType(dt)}
                        className={`p-3 rounded-lg border text-sm font-medium transition-all ${deployType === dt ? "border-primary bg-primary/5" : "border-border hover:border-muted-foreground"}`}>
                        {dt === "vm" ? "🖥️ VM" : dt === "kubernetes" ? "☸️ Kubernetes (GKE)" : dt === "serverless" ? "⚡ Serverless (Cloud Run)" : "🗄️ Base de données"}
                      </button>
                    ))}
                  </div>
                </Section>

                {/* VM */}
                {deployType === "vm" && (
                  <Section icon={Monitor} title="VM (Compute Engine)">
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <Label className="text-xs">Machine type</Label>
                        <Select value={vmMachineType} onValueChange={setVmMachineType}>
                          <SelectTrigger><SelectValue /></SelectTrigger>
                          <SelectContent>
                            {["e2-micro", "e2-medium", "n1-standard-1"].map(m => <SelectItem key={m} value={m}>{m}</SelectItem>)}
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">Nombre de CPUs</Label>
                        <Input type="number" value={vmCpu} onChange={e => setVmCpu(+e.target.value)} />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">RAM (GB)</Label>
                        <Input type="number" value={vmRam} onChange={e => setVmRam(+e.target.value)} />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">Disk size (GB)</Label>
                        <Input type="number" value={vmDiskSize} onChange={e => setVmDiskSize(+e.target.value)} />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">Disk type</Label>
                        <Select value={vmDiskType} onValueChange={setVmDiskType}>
                          <SelectTrigger><SelectValue /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value="ssd">SSD</SelectItem>
                            <SelectItem value="hdd">HDD</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">OS Image</Label>
                        <Select value={vmOsImage} onValueChange={setVmOsImage}>
                          <SelectTrigger><SelectValue /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value="debian-11">Debian 11</SelectItem>
                            <SelectItem value="ubuntu-2204">Ubuntu 22.04</SelectItem>
                            <SelectItem value="cos">Container-Optimized OS</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">External IP</Label>
                        <div className="flex gap-3 mt-1">
                          {["yes", "no"].map(v => (
                            <label key={v} className="flex items-center gap-1.5 text-sm cursor-pointer">
                              <input type="radio" name="vmExternalIp" value={v} checked={vmExternalIp === v} onChange={() => setVmExternalIp(v)} className="accent-primary" />
                              {v === "yes" ? "Oui" : "Non"}
                            </label>
                          ))}
                        </div>
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">Network</Label>
                        <Select value={vmNetwork} onValueChange={setVmNetwork}>
                          <SelectTrigger><SelectValue /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value="default">Default</SelectItem>
                            <SelectItem value="custom">Custom</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                    </div>
                  </Section>
                )}

                {/* Kubernetes */}
                {deployType === "kubernetes" && (
                  <Section icon={Cpu} title="Kubernetes (GKE)">
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <Label className="text-xs">Cluster name</Label>
                        <Input value={clusterName} onChange={e => setClusterName(e.target.value)} />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">Nombre de nodes</Label>
                        <Input type="number" value={nodeCount} onChange={e => setNodeCount(+e.target.value)} />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">Machine type</Label>
                        <Select value={machineType} onValueChange={setMachineType}>
                          <SelectTrigger><SelectValue /></SelectTrigger>
                          <SelectContent>
                            {["e2-micro", "e2-small", "e2-medium", "n1-standard-1", "n1-standard-2"].map(m => <SelectItem key={m} value={m}>{m}</SelectItem>)}
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">Mode</Label>
                        <div className="flex gap-3 mt-1">
                          {["zonal", "regional"].map(m => (
                            <label key={m} className="flex items-center gap-1.5 text-sm cursor-pointer">
                              <input type="radio" name="k8sMode" value={m} checked={k8sMode === m} onChange={() => setK8sMode(m)} className="accent-primary" />
                              {m.charAt(0).toUpperCase() + m.slice(1)}
                            </label>
                          ))}
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 mt-3">
                      <Switch checked={autoscaling} onCheckedChange={setAutoscaling} />
                      <Label className="text-sm">Autoscaling</Label>
                    </div>
                    {autoscaling && (
                      <div className="grid grid-cols-2 gap-3 mt-2">
                        <div className="space-y-1"><Label className="text-xs">Min nodes</Label><Input type="number" value={minNodes} onChange={e => setMinNodes(+e.target.value)} /></div>
                        <div className="space-y-1"><Label className="text-xs">Max nodes</Label><Input type="number" value={maxNodes} onChange={e => setMaxNodes(+e.target.value)} /></div>
                      </div>
                    )}
                  </Section>
                )}

                {/* Serverless */}
                {deployType === "serverless" && (
                  <Section icon={Zap} title="Serverless (Cloud Run)">
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1"><Label className="text-xs">Service name</Label><Input value={serviceName} onChange={e => setServiceName(e.target.value)} /></div>
                      <div className="space-y-1"><Label className="text-xs">Container image</Label><Input value={containerImage} onChange={e => setContainerImage(e.target.value)} /></div>
                      <div className="space-y-1"><Label className="text-xs">Min instances</Label><Input type="number" value={minInstances} onChange={e => setMinInstances(+e.target.value)} /></div>
                      <div className="space-y-1"><Label className="text-xs">Max instances</Label><Input type="number" value={maxInstances} onChange={e => setMaxInstances(+e.target.value)} /></div>
                    </div>
                    <div className="mt-3">
                      <Label className="text-xs">Access</Label>
                      <div className="flex gap-3 mt-1">
                        {["public", "private"].map(a => (
                          <label key={a} className="flex items-center gap-1.5 text-sm cursor-pointer">
                            <input type="radio" name="access" value={a} checked={accessType === a} onChange={() => setAccessType(a)} className="accent-primary" />
                            {a.charAt(0).toUpperCase() + a.slice(1)}
                          </label>
                        ))}
                      </div>
                    </div>
                  </Section>
                )}

                {/* Database */}
                {deployType === "database" && (
                  <Section icon={Database} title="Base de données (Cloud SQL)">
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <Label className="text-xs">Type</Label>
                        <div className="flex gap-3 mt-1">
                          {["postgresql", "mysql"].map(t => (
                            <label key={t} className="flex items-center gap-1.5 text-sm cursor-pointer">
                              <input type="radio" name="dbType" value={t} checked={dbType === t} onChange={() => setDbType(t)} className="accent-primary" />
                              {t === "postgresql" ? "PostgreSQL" : "MySQL"}
                            </label>
                          ))}
                        </div>
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">Version</Label>
                        <Select value={dbVersion} onValueChange={setDbVersion}>
                          <SelectTrigger><SelectValue placeholder="Version" /></SelectTrigger>
                          <SelectContent>
                            {(dbType === "postgresql" ? ["14", "15", "16"] : ["5.7", "8.0"]).map(v => <SelectItem key={v} value={v}>{v}</SelectItem>)}
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="space-y-1"><Label className="text-xs">CPU</Label><Input type="number" value={dbCpu} onChange={e => setDbCpu(+e.target.value)} /></div>
                      <div className="space-y-1"><Label className="text-xs">RAM (GB)</Label><Input type="number" value={dbRam} onChange={e => setDbRam(+e.target.value)} /></div>
                      <div className="space-y-1"><Label className="text-xs">Storage (GB)</Label><Input type="number" value={dbStorage} onChange={e => setDbStorage(+e.target.value)} /></div>
                      <div className="space-y-1"><Label className="text-xs">Mot de passe (Vault)</Label><Input type="password" value={dbPassword} onChange={e => setDbPassword(e.target.value)} placeholder="Géré par Vault" /></div>
                    </div>
                  </Section>
                )}

                {/* Network */}
                <Section icon={Network} title="Réseau & Sécurité">
                  <div className="space-y-3">
                    <div className="space-y-1">
                      <Label className="text-xs">VPC</Label>
                      <Select value={vpc} onValueChange={setVpc}>
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="default">Default</SelectItem>
                          <SelectItem value="custom">Custom</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-2">
                      <Label className="text-xs">Firewall</Label>
                      <div className="flex gap-4">
                        <label className="flex items-center gap-1.5 text-sm"><Checkbox checked={firewallHttp} onCheckedChange={v => setFirewallHttp(!!v)} />HTTP</label>
                        <label className="flex items-center gap-1.5 text-sm"><Checkbox checked={firewallHttps} onCheckedChange={v => setFirewallHttps(!!v)} />HTTPS</label>
                        <label className="flex items-center gap-1.5 text-sm"><Checkbox checked={firewallSsh} onCheckedChange={v => setFirewallSsh(!!v)} />SSH</label>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <Switch checked={privateIp} onCheckedChange={setPrivateIp} />
                      <Label className="text-sm">Private IP</Label>
                    </div>
                  </div>
                </Section>

                {/* DevSecOps */}
                <Section icon={Shield} title="DevSecOps">
                  <div className="flex gap-4">
                    <label className="flex items-center gap-1.5 text-sm"><Checkbox checked={scanDocker} onCheckedChange={v => setScanDocker(!!v)} />Scan Docker image</label>
                    <label className="flex items-center gap-1.5 text-sm"><Checkbox checked={scanDeps} onCheckedChange={v => setScanDeps(!!v)} />Scan dépendances</label>
                  </div>
                </Section>

                {/* Monitoring */}
                <Section icon={Activity} title="Monitoring & Logs">
                  <div className="flex gap-4">
                    <label className="flex items-center gap-1.5 text-sm"><Checkbox checked={logging} onCheckedChange={v => setLogging(!!v)} />Logging</label>
                    <label className="flex items-center gap-1.5 text-sm"><Checkbox checked={monitoring} onCheckedChange={v => setMonitoring(!!v)} />Monitoring</label>
                  </div>
                </Section>
              </>
            ) : (
              <div className="glass-card rounded-xl p-6 text-center text-muted-foreground">
                <p>Configuration pour <span className="text-foreground font-semibold">{infra?.toUpperCase() || "cette infrastructure"}</span> — formulaire simplifié.</p>
                <p className="text-sm mt-1">Le formulaire complet est disponible pour GCP.</p>
              </div>
            )}
          </div>
        )}

        {/* Navigation */}
        <div className="flex justify-between mt-8">
          <Button variant="outline" onClick={() => setStep(s => s - 1)} disabled={step === 0}>
            <ArrowLeft className="w-4 h-4 mr-1" /> Précédent
          </Button>
          {step < 3 ? (
            showFooterNext && (
              <Button onClick={() => setStep(s => s + 1)} disabled={!canNext()}>
                Suivant <ArrowRight className="w-4 h-4 ml-1" />
              </Button>
            )
          ) : (
            <Button onClick={handleLaunch}>
              🚀 Lancer l'automatisation
            </Button>
          )}
        </div>
      </main>
    </div>
  );
};

const Section = ({ icon: Icon, title, children }: { icon: React.ElementType; title: string; children: React.ReactNode }) => (
  <div className="glass-card rounded-xl p-5 space-y-3">
    <div className="flex items-center gap-2">
      <Icon className="w-4 h-4 text-primary" />
      <h3 className="font-semibold text-sm">{title}</h3>
    </div>
    {children}
  </div>
);

export default Automate;
