import SignalMap       from "@/components/SignalMap";
import RiskGauge       from "@/components/RiskGauge";
import GammaDashboard  from "@/components/GammaDashboard";
import InsiderRadar    from "@/components/InsiderRadar";
import PortfolioPanel  from "@/components/PortfolioPanel";
import AgentStatus     from "@/components/AgentStatus";
import EmergencyStop   from "@/components/EmergencyStop";

export default function WarRoom() {
  return (
    <main className="min-h-screen bg-[#0a0e1a] text-gray-100 p-4 font-mono">
      {/* Header */}
      <header className="flex items-center justify-between mb-6 border-b border-gray-700 pb-4">
        <div>
          <h1 className="text-xl font-bold text-cyan-400 tracking-wider">⚔️ QUANT WAR ROOM</h1>
          <p className="text-xs text-gray-500 mt-0.5">Algoritmik Hedge Fon — Real-time Dashboard</p>
        </div>
        <div className="flex items-center gap-3 text-xs text-gray-500">
          <span className="text-green-400 pulse-green">● LIVE</span>
          <span>Teknik(%35) · Efsane(%25) · Sentiment(%15) · Insider(%15) · Gamma(%10)</span>
        </div>
      </header>

      {/* Row 1: Signals (full width) */}
      <section className="mb-4">
        <SignalMap />
      </section>

      {/* Row 2: Gamma (full width) */}
      <section className="mb-4">
        <GammaDashboard />
      </section>

      {/* Row 3: Risk + Portfolio */}
      <section className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
        <RiskGauge />
        <PortfolioPanel />
      </section>

      {/* Row 4: Insider + Agent Status */}
      <section className="mb-4">
        <InsiderRadar />
      </section>

      <section className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
        <AgentStatus />
        <EmergencyStop />
      </section>

      {/* Footer */}
      <footer className="border-t border-gray-800 pt-3 text-xs text-gray-600 flex justify-between">
        <span>Pipeline: mock → legends → swan → pairs → insider → gamma → state_mgr → alpaca → sheets</span>
        <span>API: localhost:8000 | Next.js 14</span>
      </footer>
    </main>
  );
}
