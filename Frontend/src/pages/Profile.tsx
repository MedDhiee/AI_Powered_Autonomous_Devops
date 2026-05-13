import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Bot, ArrowLeft, User, Save } from "lucide-react";

const Profile = () => {
  const navigate = useNavigate();
  const [name, setName] = useState("John Doe");
  const [email, setEmail] = useState("john@deploypilot.io");
  const [role] = useState("Admin");
  const [password, setPassword] = useState("");

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border px-6 py-4 flex items-center gap-3">
        <Button variant="ghost" size="icon" onClick={() => navigate("/dashboard")}>
          <ArrowLeft className="w-4 h-4" />
        </Button>
        <Bot className="w-5 h-5 text-primary" />
        <span className="font-bold gradient-text">Profil Utilisateur</span>
      </header>

      <main className="max-w-xl mx-auto px-6 py-8 animate-fade-in">
        <div className="glass-card rounded-xl p-6 space-y-6">
          <div className="flex items-center gap-4">
            <div className="w-16 h-16 rounded-full bg-primary/10 border border-primary/20 flex items-center justify-center">
              <User className="w-8 h-8 text-primary" />
            </div>
            <div>
              <p className="font-bold text-lg">{name}</p>
              <Badge variant="outline">{role}</Badge>
            </div>
          </div>

          <div className="space-y-4">
            <div className="space-y-1">
              <Label>Nom complet</Label>
              <Input value={name} onChange={e => setName(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label>Email</Label>
              <Input type="email" value={email} onChange={e => setEmail(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label>Rôle</Label>
              <Input value={role} disabled className="opacity-60" />
            </div>
            <div className="space-y-1">
              <Label>Nouveau mot de passe</Label>
              <Input type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="Laisser vide pour ne pas changer" />
            </div>
          </div>

          <Button className="gap-2">
            <Save className="w-4 h-4" /> Sauvegarder
          </Button>
        </div>
      </main>
    </div>
  );
};

export default Profile;
