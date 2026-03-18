"use client";

import { useEffect, useState } from "react";
import { getSignals, SignalsResponse, Signal } from "@/lib/api";
import clsx from "clsx";

function SignalBadge({ sinyal }: { sinyal: string }) {
  return (
    <span
      className={clsx(
        "px-2 py-0.5 rounded text-xs font-bold",
        sinyal === "LONG"  && "bg-green-900 text-green-300 border border-green-600",
        sinyal === "SHORT" && "bg-red-900 text-red-300 border border-red-600",
        sinyal === "HOLD"  && "bg-yellow-900 text-yellow-300 border border-yellow-600"
      )}
    >
      {sinyal}
    </span>
  );
}

function ScoreBar({ score }: { score: number }) {
  const pct   = Math.round(Math.abs(score) * 100);
  const color = score > 0 ? "bg-green-500" : score < 0 ? "bg-red-500" : "bg-gray-600";
  return (
    <div className="flex items-center gap-2">
      <span className={clsx("text-xs w-14 text-right", score > 0 ? "text-green-400" : score < 0 ? "text-red-400" : "text-gray-400")}>
        {score > 0 ? "+" : ""}{score.toFixed(3)}
      </span>
      <div className="w-24 h-2 bg-gray-800 rounded overflow-hidden">
        <div className={clsx("h-full rounded", color)} style={{ width: `${Math.min(pct * 3, 100)}%` }} />
      </div>
    </div>
  );
}

function SignalRow({ k }: { k: Signal }) {
  const [expanded, setExpanded] = useState(false);
  const hasGamma = k.katmanlar?.gamma;
  const gammaIkon = hasGamma
    ? k.katmanlar.gamma.piyasa_modu === "POZITIF" ? "🟢" : k.katmanlar.gamma.piyasa_modu === "NEGATIF" ? "🔴" : "🟡"
    : "—";

  return (
    <>
      <tr
        className="border-b border-gray-800 hover:bg-gray-800/50 cursor-pointer transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <td className="px-3 py-2 font-bold text-cyan-300">{k.symbol}</td>
        <td className="px-3 py-2"><SignalBadge sinyal={k.final_sinyal} /></td>
        <td className="px-3 py-2 text-xs text-gray-400">{k.guven}</td>
        <td className="px-3 py-2"><ScoreBar score={k.toplam_skor ?? 0} /></td>
        <td className="px-3 py-2 text-right text-gray-200">{k.fiyat != null ? `$${k.fiyat.toFixed(2)}` : "N/A"}</td>
        <td className="px-3 py-2 text-right text-red-400">{k.stop_loss ? `$${k.stop_loss}` : "—"}</td>
        <td className="px-3 py-2 text-right text-green-400">{k.take_profit ? `$${k.take_profit}` : "—"}</td>
        <td className="px-3 py-2 text-center text-xs">{gammaIkon} {hasGamma ? k.katmanlar.gamma.piyasa_modu : "—"}</td>
        <td className="px-3 py-2 text-center text-xs">
          {k.catisma ? <span className="text-yellow-400">⚠️</span> : <span className="text-gray-600">—</span>}
        </td>
      </tr>
      {expanded && (
        <tr className="bg-gray-900/80 border-b border-gray-700">
          <td colSpan={9} className="px-4 py-3">
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 text-xs">
              {/* Teknik */}
              {k.katmanlar?.teknik && (
                <div className="bg-gray-800 rounded p-2">
                  <div className="text-gray-400 mb-1">📊 Teknik</div>
                  <div>Sinyal: <span className="text-cyan-300">{k.katmanlar.teknik.sinyal ?? "N/A"}</span></div>
                  <div>Puan: {k.katmanlar.teknik.puan} | Skor: {k.katmanlar.teknik.skor?.toFixed(3) ?? "N/A"}</div>
                  <div>ATR: {k.atr_kullanildi ? <span className="text-green-400">✅ ${k.atr_degeri}</span> : <span className="text-gray-500">fallback%</span>}</div>
                </div>
              )}
              {/* Efsane */}
              {k.katmanlar?.efsane && (
                <div className="bg-gray-800 rounded p-2">
                  <div className="text-gray-400 mb-1">🏆 Efsane</div>
                  <div className="text-cyan-300">{k.katmanlar.efsane.konsensus ?? "N/A"}</div>
                  <div>Long: {k.katmanlar.efsane.long_oran}% | Short: {k.katmanlar.efsane.short_oran}%</div>
                </div>
              )}
              {/* Insider */}
              {k.katmanlar?.insider && (
                <div className="bg-gray-800 rounded p-2">
                  <div className="text-gray-400 mb-1">🔍 Insider</div>
                  <div>Skor: <span className={(k.katmanlar.insider.skor ?? 0) > 0 ? "text-green-400" : "text-red-400"}>{k.katmanlar.insider.skor?.toFixed(3) ?? "N/A"}</span></div>
                  <div>Çarpan: ×{k.katmanlar.insider.carpan?.toFixed(2) ?? "1.00"}</div>
                  <div className="text-gray-500 truncate">{k.katmanlar.insider.yorum}</div>
                </div>
              )}
              {/* Swan */}
              {k.katmanlar?.swan && (
                <div className="bg-gray-800 rounded p-2">
                  <div className="text-gray-400 mb-1">🦢 Swan</div>
                  <div>VIX: <span className="text-yellow-400">{k.katmanlar.swan.vix ?? "N/A"}</span></div>
                  <div>Risk: <span className={k.katmanlar.swan.risk_adi === "PANIK" || k.katmanlar.swan.risk_adi === "YUKSEK" ? "text-red-400" : "text-green-400"}>{k.katmanlar.swan.risk_adi ?? "N/A"}</span></div>
                  <div>Risk-Off: {k.katmanlar.swan.risk_off ? "🔴 AKTİF" : "🟢 PASİF"}</div>
                </div>
              )}
              {/* Gamma */}
              {hasGamma && k.katmanlar?.gamma && (
                <div className="bg-gray-800 rounded p-2">
                  <div className="text-gray-400 mb-1">⚡ Gamma</div>
                  <div>Skor: <span className={(k.katmanlar.gamma.skor ?? 0) > 0 ? "text-green-400" : "text-red-400"}>{k.katmanlar.gamma.skor?.toFixed(3) ?? "N/A"}</span></div>
                  <div>GEX: {k.katmanlar.gamma.piyasa_modu}</div>
                  <div>UOA: {k.katmanlar.gamma.uoa_var ? "✅" : "—"} | Poz: ×{k.katmanlar.gamma.pozisyon_ayar}</div>
                </div>
              )}
            </div>
            <div className="mt-2 text-xs text-gray-400 italic">{k.aciklama}</div>
          </td>
        </tr>
      )}
    </>
  );
}

export default function SignalMap() {
  const [data, setData]     = useState<SignalsResponse | null>(null);
  const [error, setError]   = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    try {
      const d = await getSignals();
      setData(d);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 60_000); // 60s refresh
    return () => clearInterval(id);
  }, []);

  if (loading) return <div className="text-gray-500 p-4">Sinyaller yükleniyor...</div>;
  if (error)   return <div className="text-red-400 p-4">⚠️ {error}</div>;
  if (!data)   return null;

  const { ozet, kararlar, tarih } = data;

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-700 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
        <div className="flex items-center gap-3">
          <span className="text-cyan-400 font-bold">📡 SİNYAL HARİTASI</span>
          <span className="text-xs text-gray-500">{tarih}</span>
        </div>
        <div className="flex gap-4 text-xs">
          <span className="text-green-400">🟢 {ozet.long} LONG</span>
          <span className="text-red-400">🔴 {ozet.short} SHORT</span>
          <span className="text-yellow-400">🟡 {ozet.hold} HOLD</span>
          {ozet.catisma > 0 && <span className="text-orange-400">⚠️ {ozet.catisma} Çatışma</span>}
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-gray-500 border-b border-gray-700">
              <th className="px-3 py-2 text-left">SEMBOL</th>
              <th className="px-3 py-2 text-left">SİNYAL</th>
              <th className="px-3 py-2 text-left">GÜVEN</th>
              <th className="px-3 py-2 text-left">SKOR</th>
              <th className="px-3 py-2 text-right">FİYAT</th>
              <th className="px-3 py-2 text-right">STOP</th>
              <th className="px-3 py-2 text-right">TP</th>
              <th className="px-3 py-2 text-center">GAMMA</th>
              <th className="px-3 py-2 text-center">ÇATIŞMA</th>
            </tr>
          </thead>
          <tbody>
            {kararlar.map((k) => <SignalRow key={k.symbol} k={k} />)}
          </tbody>
        </table>
      </div>
    </div>
  );
}
