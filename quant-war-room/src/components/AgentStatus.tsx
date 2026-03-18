"use client";

import { useEffect, useState } from "react";
import { getAgents, AgentsResponse } from "@/lib/api";
import clsx from "clsx";

const AGENT_LABELS: Record<string, string> = {
  mock_agent     : "📊 Teknik",
  legends_agent  : "🏆 Efsane",
  swan_agent     : "🦢 Swan",
  pairs_agent    : "🔗 Pairs",
  insider_agent  : "🔍 Insider",
  gamma_agent    : "⚡ Gamma",
  state_manager  : "🎯 State Mgr",
  sentiment_agent: "📰 Sentiment",
};

function timeSince(dateStr: string | null): string {
  if (!dateStr) return "—";
  const diff  = Date.now() - new Date(dateStr).getTime();
  const mins  = Math.floor(diff / 60000);
  const hours = Math.floor(mins / 60);
  if (hours > 0)  return `${hours}s önce`;
  if (mins  > 0)  return `${mins}dk önce`;
  return "az önce";
}

export default function AgentStatus() {
  const [data, setData]       = useState<AgentsResponse | null>(null);
  const [error, setError]     = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    try {
      setData(await getAgents());
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); const id = setInterval(load, 60_000); return () => clearInterval(id); }, []);

  if (loading) return <div className="text-gray-500 p-4 text-sm">Ajan durumları yükleniyor...</div>;
  if (error)   return <div className="text-red-400 p-4 text-sm">⚠️ {error}</div>;
  if (!data)   return null;

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-700 p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-cyan-400 font-bold">🤖 AJAN DURUMU</span>
        <span className="text-xs text-gray-500">{data.aktif_sayisi}/{data.toplam_ajan} aktif</span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {Object.entries(data.ajanlar).map(([key, agent]) => {
          const label = AGENT_LABELS[key] ?? key;
          const isOk  = agent.mevcut;
          const diff  = agent.son_guncelleme
            ? (Date.now() - new Date(agent.son_guncelleme).getTime()) / 60000
            : Infinity;
          const isStale = diff > 60; // 1 saatten eski = stale

          return (
            <div
              key={key}
              className={clsx(
                "rounded p-2 text-xs border",
                isOk && !isStale ? "bg-green-950 border-green-800"
                  : isOk && isStale ? "bg-yellow-950 border-yellow-800"
                  : "bg-gray-800 border-gray-700"
              )}
            >
              <div className="flex items-center gap-1 mb-1">
                <span className={isOk ? (isStale ? "text-yellow-400" : "text-green-400") : "text-gray-500"}>
                  {isOk ? (isStale ? "⚠" : "●") : "○"}
                </span>
                <span className="text-gray-200 font-semibold">{label}</span>
              </div>
              <div className={clsx("text-xs", isOk ? "text-gray-400" : "text-gray-600")}>
                {isOk ? timeSince(agent.son_guncelleme) : "veri yok"}
              </div>
              {isOk && (
                <div className="text-gray-600 text-xs">{agent.boyut_kb} KB</div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
