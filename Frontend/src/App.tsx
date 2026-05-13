import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import Index from "./pages/Index";
import Login from "./pages/Login";
import SignUp from "./pages/SignUp";
import VerifyEmail from "./pages/VerifyEmail";
import Dashboard from "./pages/Dashboard";
import Automate from "./pages/Automate";
import Pipeline from "./pages/Pipeline";
import Notifications from "./pages/Notifications";
import AuditHistory from "./pages/AuditHistory";
import HelpSupport from "./pages/HelpSupport";
import Profile from "./pages/Profile";
import Projects from "./pages/Projects";
import ChaosSimulations from "./pages/ChaosSimulations";
import NotFound from "./pages/NotFound";

const queryClient = new QueryClient();

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <Toaster />
      <Sonner />
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Index />} />
          <Route path="/login" element={<Login />} />
          <Route path="/signup" element={<SignUp />} />
          <Route path="/verify-email" element={<VerifyEmail />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/automate" element={<Automate />} />
          <Route path="/pipeline/:id" element={<Pipeline />} />
          <Route path="/notifications" element={<Notifications />} />
          <Route path="/audit" element={<AuditHistory />} />
          <Route path="/help" element={<HelpSupport />} />
          <Route path="/profile" element={<Profile />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/chaos" element={<ChaosSimulations />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </BrowserRouter>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
