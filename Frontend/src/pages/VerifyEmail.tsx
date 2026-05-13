import { useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { InputOTP, InputOTPGroup, InputOTPSlot } from "@/components/ui/input-otp";
import { Mail, CheckCircle2 } from "lucide-react";

const VerifyEmail = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const email = (location.state as { email?: string })?.email || "votre email";
  const [otp, setOtp] = useState("");
  const [verified, setVerified] = useState(false);

  const handleVerify = () => {
    if (otp.length === 6) {
      setVerified(true);
      setTimeout(() => navigate("/dashboard"), 1500);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-md animate-fade-in">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-xl bg-primary/10 border border-primary/20 mb-4">
            {verified ? <CheckCircle2 className="w-7 h-7 text-success" /> : <Mail className="w-7 h-7 text-primary" />}
          </div>
          <h1 className="text-2xl font-bold">{verified ? "Email vérifié !" : "Vérifiez votre email"}</h1>
          <p className="text-muted-foreground mt-2 text-sm">
            {verified ? "Redirection vers le tableau de bord..." : `Un code de vérification a été envoyé à ${email}`}
          </p>
        </div>

        {!verified && (
          <div className="glass-card rounded-xl p-6 space-y-6">
            <div className="flex justify-center">
              <InputOTP maxLength={6} value={otp} onChange={setOtp}>
                <InputOTPGroup>
                  <InputOTPSlot index={0} />
                  <InputOTPSlot index={1} />
                  <InputOTPSlot index={2} />
                  <InputOTPSlot index={3} />
                  <InputOTPSlot index={4} />
                  <InputOTPSlot index={5} />
                </InputOTPGroup>
              </InputOTP>
            </div>
            <Button onClick={handleVerify} className="w-full" size="lg" disabled={otp.length !== 6}>
              Vérifier
            </Button>
            <p className="text-center text-sm text-muted-foreground">
              Pas reçu ? <button className="text-primary hover:underline">Renvoyer le code</button>
            </p>
          </div>
        )}
      </div>
    </div>
  );
};

export default VerifyEmail;
