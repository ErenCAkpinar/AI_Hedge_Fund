"use client";

import { useEffect, useState } from "react";
import { getInsider } from "@/lib/api";
import clsx from "clsx";

interface InsiderSkor {
  final_skor : number;
  carpan     : number;
  yorum      : string;
}

interface InsiderVarlık {
  skor     : InsiderSkor;
  katmanlar?: Record<string, { sinyal: number; veri: unknown }>;
  hata    ?: string;
}

interface InsiderResponse {
  tarih    : string;
  varlıklar: Record<string, InsiderVarlık>;
}

export default function InsiderRadar() {
  const [data, setData]       = useState<InsiderResponse | null>(null);
  const [error, setError]     = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    try {
      const raw = await getInsider();
      setData(raw as InsiderResponse);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); const id = setInterval(load, 300_000); return () => clearInterval(id); }, []);

  if (loading) return <div className="text-gray-500 p-4 text-sm">Insider yükleniyor...</div>;
  if (error)   return <div className="text-red-400 p-4 text-sm">⚠️ {error}</div>;
  if (!data)   return null;

  const varlıklar = data.varlıklar ?? {};
  const gecerli   = Object.entries(varlıklar).filter(([, v]) => v.skor && !v.hata);

  // En güçlü insider sinyalleri (skor bazında sırala)
  const sorted = gecerli.sort(([, a], [, b]) => Math.abs(b.skor.final_skor) - Math.abs(a.skor.final_skor));

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-700 overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
        <span className="text-cyan-400 font-bold">🔍 PROJECT GÖZCÜ — INSIDER RADAR</span>
        <span className="text-xs text-gray-500">{data.tarih}</span>
      </div>

      <div className="p-3 space-y-1 max-h-80 overflow-y-auto">
        {sorted.map(([sem, v]) => {
          const skor  = v.skor.final_skor;
          const isPos = skor > 0.3;
          const isNeg = skor < -0.3;
          const pctBar = Math.min(Math.abs(skor) * 100, 100);

          return (
            <div key={sem} className="flex items-center gap-3 bg-gray-800 rounded px-3 py-2 text-xs">
              <span className="font-bold text-gray-200 w-12">{sem}</span>

              {/* Skor bar */}
              <div className="flex-1 h-2 bg-gray-700 rounded overflow-hidden">
                <div
                  className={clsx("h-full rounded", isPos ? "bg-green-500" : isNeg ? "bg-red-500" : "bg-gray-500")}
                  style={{ width: `${pctBar}%` }}
                />
              </div>

              <span className={clsx(
                "w-16 text-right font-mono font-bold",
                isPos ? "text-green-400" : isNeg ? "text-red-400" : "text-gray-400"
              )}>
                {skor > 0 ? "+" : ""}{skor.toFixed(3)}
              </span>

              <span className="text-gray-500 w-6 text-center">×{v.skor.carpan.toFixed(1)}</span>

              <span className="text-gray-500 flex-1 truncate hidden md:block">{v.skor.yorum}</span>
            </div>
          );
        })}

        {sorted.length === 0 && (
          <div className="text-center text-gray-500 py-4">Insider verisi yok</div>
        )}
      </div>

      {/* Lejant */}
      <div className="px-4 py-2 border-t border-gray-700 flex gap-4 text-xs text-gray-500">
        <span>6 Katman: Form4 · Kongre · Dark Pool · 13F · Whale · Short Interest</span>
      </div>
    </div>
  );
}
