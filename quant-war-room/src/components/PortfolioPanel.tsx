"use client";

import { useEffect, useState } from "react";
import { getPortfolio, PortfolioResponse } from "@/lib/api";
import clsx from "clsx";

export default function PortfolioPanel() {
  const [data, setData]       = useState<PortfolioResponse | null>(null);
  const [error, setError]     = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    try {
      setData(await getPortfolio());
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); const id = setInterval(load, 30_000); return () => clearInterval(id); }, []);

  if (loading) return <div className="text-gray-500 p-4 text-sm">Portföy yükleniyor...</div>;
  if (error)   return <div className="text-yellow-400 p-4 text-sm">⚠️ Alpaca: {error}</div>;
  if (!data)   return null;

  const plColor = data.day_pl >= 0 ? "text-green-400" : "text-red-400";
  const plSign  = data.day_pl >= 0 ? "+" : "";

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-700 p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-cyan-400 font-bold">💼 PORTFÖY</span>
        <span className="text-xs text-gray-500 pulse-green">● CANLI</span>
      </div>

      {/* Ana metrikler */}
      <div className="grid grid-cols-2 gap-3 mb-4">
        <div className="bg-gray-800 rounded p-3">
          <div className="text-xs text-gray-400">Equity</div>
          <div className="text-xl font-bold text-white">${data.equity.toLocaleString("en-US", { maximumFractionDigits: 0 })}</div>
        </div>
        <div className="bg-gray-800 rounded p-3">
          <div className="text-xs text-gray-400">Günlük P&L</div>
          <div className={clsx("text-xl font-bold", plColor)}>
            {plSign}${Math.abs(data.day_pl).toLocaleString("en-US", { maximumFractionDigits: 0 })}
          </div>
          <div className={clsx("text-xs", plColor)}>{plSign}{data.day_pl_pct.toFixed(2)}%</div>
        </div>
        <div className="bg-gray-800 rounded p-3">
          <div className="text-xs text-gray-400">Cash</div>
          <div className="text-white font-semibold">${data.cash.toLocaleString("en-US", { maximumFractionDigits: 0 })}</div>
        </div>
        <div className="bg-gray-800 rounded p-3">
          <div className="text-xs text-gray-400">Buying Power</div>
          <div className="text-white font-semibold">${data.buying_power.toLocaleString("en-US", { maximumFractionDigits: 0 })}</div>
        </div>
      </div>

      {/* Açık pozisyonlar */}
      {data.pozisyonlar.length > 0 ? (
        <div>
          <div className="text-xs text-gray-400 mb-2">Açık Pozisyonlar ({data.acik_pozisyon})</div>
          <div className="space-y-1">
            {data.pozisyonlar.map((p) => {
              const pl      = p.unrealized_pl;
              const plPct   = p.unrealized_plpc * 100;
              const isLong  = p.side === "long";
              return (
                <div key={p.symbol} className="flex items-center justify-between bg-gray-800 rounded px-3 py-2 text-xs">
                  <div className="flex items-center gap-2">
                    <span className={isLong ? "text-green-400" : "text-red-400"}>{isLong ? "↑" : "↓"}</span>
                    <span className="font-bold text-gray-200">{p.symbol}</span>
                    <span className="text-gray-500">{p.qty} adet</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-gray-400">${p.current_price.toFixed(2)}</span>
                    <span className={pl >= 0 ? "text-green-400" : "text-red-400"}>
                      {pl >= 0 ? "+" : ""}${pl.toFixed(2)} ({plPct >= 0 ? "+" : ""}{plPct.toFixed(2)}%)
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="text-center text-gray-500 text-sm py-2">Açık pozisyon yok</div>
      )}
    </div>
  );
}
