"use client";

import { useEffect, useState } from "react";
import { getRisk, RiskResponse } from "@/lib/api";
import { RadialBarChart, RadialBar, ResponsiveContainer } from "recharts";

function GaugeColor(score: number): string {
  if (score >= 70) return "#ff3b5c";
  if (score >= 50) return "#ffd700";
  if (score >= 30) return "#00d4ff";
  return "#00ff88";
}

/** null, undefined veya NaN gelirse "N/A" döner */
function fmtUSD(val: number | null | undefined): string {
  if (val == null || isNaN(val)) return "N/A";
  return `$${val.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

function fmtPct(val: number | null | undefined, multiply = 100): string {
  if (val == null || isNaN(val)) return "N/A";
  return `%${(val * multiply).toFixed(1)}`;
}

export default function RiskGauge() {
  const [data, setData]       = useState<RiskResponse | null>(null);
  const [error, setError]     = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    try {
      setData(await getRisk());
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 120_000);
    return () => clearInterval(id);
  }, []);

  if (loading) return <div className="text-gray-500 p-4 text-sm">Risk yükleniyor...</div>;
  if (error)   return <div className="text-red-400 p-4 text-sm">⚠️ {error}</div>;
  if (!data)   return null;

  const score     = data.risk_skoru ?? 0;
  const color     = GaugeColor(score);
  const gaugeData = [{ value: score, fill: color }];

  const riskIcon = { DUSUK: "🟢", ORTA: "🟡", YUKSEK: "🔴", PANIK: "🚨" }[data.risk_adi] ?? "⚪";

  const mc           = data.monte_carlo;
  const iflasOlasiligi = mc?.iflas_olasiligi;
  const iflasGecerli  = iflasOlasiligi != null && !isNaN(iflasOlasiligi);

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-700 p-4">
      <div className="text-cyan-400 font-bold mb-3">🦢 BLACK SWAN RİSK</div>

      {/* Gauge */}
      <div className="flex items-center gap-4">
        <div className="w-32 h-32 relative">
          <ResponsiveContainer width="100%" height="100%">
            <RadialBarChart
              cx="50%" cy="50%"
              innerRadius="60%" outerRadius="90%"
              startAngle={225} endAngle={-45}
              data={gaugeData}
              barSize={12}
            >
              <RadialBar dataKey="value" cornerRadius={6} background={{ fill: "#1f2937" }} />
            </RadialBarChart>
          </ResponsiveContainer>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-2xl font-bold" style={{ color }}>{score}</span>
            <span className="text-xs text-gray-500">/100</span>
          </div>
        </div>

        <div className="flex-1 space-y-2 text-sm">
          <div className="flex items-center gap-2">
            <span className="text-gray-400">Durum</span>
            <span className="font-bold" style={{ color }}>{riskIcon} {data.risk_adi}</span>
          </div>
          <div>
            <span className="text-gray-400">VIX: </span>
            <span className="text-yellow-400 font-bold">{data.vix?.deger ?? "N/A"}</span>
          </div>
          <div>
            <span className="text-gray-400">Risk-Off: </span>
            <span className={data.risk_off ? "text-red-400 font-bold" : "text-green-400"}>
              {data.risk_off ? "🔴 AKTİF" : "🟢 PASİF"}
            </span>
          </div>
          <div className="text-xs text-gray-500 italic">{data.tavsiye}</div>
        </div>
      </div>

      {/* Monte Carlo */}
      {mc && (
        <div className="mt-4 grid grid-cols-2 gap-2 text-xs">
          <div className="bg-gray-800 rounded p-2">
            <div className="text-gray-400">MC Medyan</div>
            <div className="text-cyan-300 font-bold">{fmtUSD(mc.median_sonuc)}</div>
          </div>
          <div className="bg-gray-800 rounded p-2">
            <div className="text-gray-400">İflas Olasılığı</div>
            <div className={`font-bold ${iflasGecerli && iflasOlasiligi! > 0.15 ? "text-red-400" : "text-green-400"}`}>
              {iflasGecerli ? fmtPct(iflasOlasiligi) : "N/A"}
            </div>
          </div>
          <div className="bg-gray-800 rounded p-2">
            <div className="text-gray-400">VaR 95%</div>
            <div className="text-orange-400 font-bold">{fmtUSD(mc.var_95)}</div>
          </div>
          <div className="bg-gray-800 rounded p-2">
            <div className="text-gray-400">En Kötü Kriz</div>
            <div className="text-red-400 font-bold text-xs">{data.en_kotu_kriz ?? "N/A"}</div>
          </div>
        </div>
      )}

      {/* Kriz Stres */}
      {data.kriz_stres && (
        <div className="mt-3">
          <div className="text-xs text-gray-500 mb-1">Tarihi Kriz Stres Testi</div>
          <div className="space-y-1">
            {Object.entries(data.kriz_stres).map(([kriz, v]) => {
              const vals = v as { kayip_pct: number | null; kalan: number };
              const raw  = vals.kayip_pct;
              const pct  = raw != null && !isNaN(raw) ? Math.abs(raw) * 100 : 0;
              return (
                <div key={kriz} className="flex items-center gap-2 text-xs">
                  <span className="text-gray-400 w-16 truncate">{kriz}</span>
                  <div className="flex-1 h-1.5 bg-gray-800 rounded overflow-hidden">
                    <div className="h-full bg-red-600 rounded" style={{ width: `${Math.min(pct * 1.5, 100)}%` }} />
                  </div>
                  <span className="text-red-400 w-14 text-right">
                    {raw != null && !isNaN(raw) ? `-${pct.toFixed(0)}%` : "N/A"}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
