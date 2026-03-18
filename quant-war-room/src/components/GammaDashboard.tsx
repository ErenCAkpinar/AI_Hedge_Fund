"use client";

import { useEffect, useState } from "react";
import { getGamma } from "@/lib/api";
import clsx from "clsx";

interface GexEntry {
  net_gex      : number;
  call_gex     : number;
  put_gex      : number;
  zero_gamma   : number;
  put_wall     : number;
  call_wall    : number;
  piyasa_modu  : string;
}

interface UoaEntry {
  uoa_var    : boolean;
  uoa_yonu   : string;
  sinyal     : number;
  aciklama   : string;
}

interface SkewEntry {
  skew        : number;
  otm_put_iv  : number;
  otm_call_iv : number;
  sinyal      : number;
  yorum       : string;
}

interface SkorEntry {
  final_skor    : number;
  put_wall      : number;
  call_wall     : number;
  zero_gamma    : number;
  piyasa_modu   : string;
  net_gex       : number;
  uoa_var       : boolean;
  uoa_yonu      : string;
  iv_skew       : number;
  tp_ayar       : string;
  pozisyon_ayar : number;
  uyarilar      : string[];
}

interface SymbolGamma {
  sembol  : string;
  spot    : number;
  gex     : GexEntry;
  uoa     : UoaEntry;
  iv_skew : SkewEntry;
  skor    : SkorEntry;
  hata    ?: string;
}

interface GammaResponse {
  tarih    : string;
  versiyon : string;
  varlıklar: Record<string, SymbolGamma>;
}

export default function GammaDashboard() {
  const [data, setData]     = useState<GammaResponse | null>(null);
  const [error, setError]   = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);

  const load = async () => {
    try {
      const raw = await getGamma();
      setData(raw as GammaResponse);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); const id = setInterval(load, 120_000); return () => clearInterval(id); }, []);

  if (loading) return <div className="text-gray-500 p-4 text-sm">Gamma yükleniyor...</div>;
  if (error)   return <div className="text-red-400 p-4 text-sm">⚠️ {error}</div>;
  if (!data)   return null;

  const varlıklar = data.varlıklar ?? {};
  const gecerli   = Object.entries(varlıklar).filter(([, v]) => !v.hata);
  const selectedData = selected ? varlıklar[selected] : null;

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-700 overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
        <span className="text-cyan-400 font-bold">⚡ GAMMA SENTINEL</span>
        <span className="text-xs text-gray-500">{data.tarih}</span>
      </div>

      {/* Grid */}
      <div className="p-3 grid grid-cols-4 sm:grid-cols-6 lg:grid-cols-9 gap-2">
        {gecerli.map(([sem, v]) => {
          const s     = v.skor;
          const isPos = s.final_skor > 0.2;
          const isNeg = s.final_skor < -0.2;
          return (
            <button
              key={sem}
              onClick={() => setSelected(sem === selected ? null : sem)}
              className={clsx(
                "rounded p-2 text-center text-xs transition-all border",
                selected === sem ? "border-cyan-400" : "border-gray-700",
                isPos ? "bg-green-950 hover:bg-green-900"
                      : isNeg ? "bg-red-950 hover:bg-red-900"
                              : "bg-gray-800 hover:bg-gray-700"
              )}
            >
              <div className="font-bold text-gray-200">{sem}</div>
              <div className={clsx("font-mono", isPos ? "text-green-400" : isNeg ? "text-red-400" : "text-gray-400")}>
                {s.final_skor > 0 ? "+" : ""}{s.final_skor.toFixed(2)}
              </div>
              <div className="text-gray-500 text-xs mt-0.5">×{s.pozisyon_ayar}</div>
            </button>
          );
        })}
      </div>

      {/* Detail panel */}
      {selectedData && !selectedData.hata && (
        <div className="border-t border-gray-700 p-4 bg-gray-800/50">
          <div className="flex items-center gap-2 mb-3">
            <span className="text-cyan-300 font-bold text-lg">{selectedData.sembol}</span>
            <span className="text-gray-400 text-sm">${selectedData.spot?.toFixed(2)}</span>
            <span className={clsx(
              "px-2 py-0.5 rounded text-xs font-bold",
              selectedData.skor.piyasa_modu === "POZITIF" ? "bg-green-900 text-green-300" : "bg-red-900 text-red-300"
            )}>
              {selectedData.skor.piyasa_modu} GEX
            </span>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            {/* GEX */}
            <div className="bg-gray-900 rounded p-3">
              <div className="text-gray-400 mb-2 font-semibold">🌊 GEX</div>
              <div>Net: <span className={selectedData.gex?.net_gex > 0 ? "text-green-400" : "text-red-400"}>{selectedData.gex?.net_gex?.toLocaleString()}</span></div>
              <div>Put Wall: <span className="text-red-400">${selectedData.skor.put_wall?.toFixed(2)}</span></div>
              <div>Call Wall: <span className="text-green-400">${selectedData.skor.call_wall?.toFixed(2)}</span></div>
              <div>Zero-γ: <span className="text-cyan-400">${selectedData.skor.zero_gamma?.toFixed(2)}</span></div>
            </div>

            {/* UOA */}
            <div className="bg-gray-900 rounded p-3">
              <div className="text-gray-400 mb-2 font-semibold">🔥 UOA</div>
              <div>Aktif: <span className={selectedData.uoa?.uoa_var ? "text-green-400" : "text-gray-500"}>{selectedData.uoa?.uoa_var ? "✅ Evet" : "— Hayır"}</span></div>
              <div>Yön: <span className={selectedData.uoa?.uoa_yonu === "CALL" ? "text-green-400" : selectedData.uoa?.uoa_yonu === "PUT" ? "text-red-400" : "text-gray-500"}>{selectedData.uoa?.uoa_yonu}</span></div>
              <div className="text-gray-500 mt-1 truncate">{selectedData.uoa?.aciklama}</div>
            </div>

            {/* IV Skew */}
            <div className="bg-gray-900 rounded p-3">
              <div className="text-gray-400 mb-2 font-semibold">📊 IV Skew</div>
              <div>Skew: <span className={selectedData.iv_skew?.skew > 0.05 ? "text-red-400" : "text-green-400"}>{selectedData.iv_skew?.skew > 0 ? "+" : ""}{(selectedData.iv_skew?.skew * 100)?.toFixed(1)}%</span></div>
              <div>Put IV: {(selectedData.iv_skew?.otm_put_iv * 100)?.toFixed(1)}%</div>
              <div>Call IV: {(selectedData.iv_skew?.otm_call_iv * 100)?.toFixed(1)}%</div>
              <div className="text-gray-500 mt-1 truncate">{selectedData.iv_skew?.yorum}</div>
            </div>

            {/* Kompozit Skor */}
            <div className="bg-gray-900 rounded p-3">
              <div className="text-gray-400 mb-2 font-semibold">🎯 Kompozit</div>
              <div>Final Skor: <span className={selectedData.skor.final_skor > 0 ? "text-green-400 font-bold" : "text-red-400 font-bold"}>{selectedData.skor.final_skor > 0 ? "+" : ""}{selectedData.skor.final_skor.toFixed(3)}</span></div>
              <div>Poz. Ayar: <span className="text-yellow-400">×{selectedData.skor.pozisyon_ayar}</span></div>
              <div className="text-cyan-400 mt-1 text-xs">{selectedData.skor.tp_ayar}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
