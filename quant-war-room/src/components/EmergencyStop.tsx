"use client";

import { useState } from "react";
import { emergencyStop } from "@/lib/api";

type Phase = "idle" | "confirm1" | "confirm2" | "loading" | "done" | "error";

export default function EmergencyStop() {
  const [phase, setPhase]   = useState<Phase>("idle");
  const [secret, setSecret] = useState("");
  const [reason, setReason] = useState("");
  const [result, setResult] = useState<string>("");

  const handleStop = async () => {
    setPhase("loading");
    try {
      const res = await emergencyStop(secret, reason || "War Room emergency stop");
      setResult(res.mesaj ?? JSON.stringify(res));
      setPhase("done");
    } catch (e: unknown) {
      setResult(e instanceof Error ? e.message : String(e));
      setPhase("error");
    }
  };

  if (phase === "done") {
    return (
      <div className="bg-gray-900 rounded-xl border border-green-800 p-4">
        <div className="text-green-400 font-bold mb-2">✅ Emergency Stop Tamamlandı</div>
        <div className="text-gray-300 text-sm">{result}</div>
        <button className="mt-3 text-xs text-gray-500 hover:text-gray-300" onClick={() => { setPhase("idle"); setSecret(""); setReason(""); }}>
          Sıfırla
        </button>
      </div>
    );
  }

  return (
    <div className="bg-gray-900 rounded-xl border border-red-900 p-4">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-red-400 font-bold">🚨 EMERGENCY STOP</span>
        <span className="text-xs text-gray-500">Tüm pozisyonları kapat</span>
      </div>

      {phase === "idle" && (
        <button
          onClick={() => setPhase("confirm1")}
          className="w-full py-2 bg-red-900 hover:bg-red-800 text-red-300 font-bold rounded border border-red-700 text-sm transition-colors"
        >
          ⛔ DURDUR
        </button>
      )}

      {phase === "confirm1" && (
        <div className="space-y-3">
          <div className="text-yellow-400 text-sm">⚠️ Tüm açık pozisyonlar kapatılacak. Bu işlem geri alınamaz!</div>
          <div className="flex gap-2">
            <button onClick={() => setPhase("confirm2")} className="flex-1 py-2 bg-red-800 hover:bg-red-700 text-white rounded text-sm font-bold">
              Devam Et
            </button>
            <button onClick={() => setPhase("idle")} className="flex-1 py-2 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded text-sm">
              İptal
            </button>
          </div>
        </div>
      )}

      {phase === "confirm2" && (
        <div className="space-y-3">
          <div className="text-red-400 text-sm font-bold">🔐 Son onay: Emergency secret girin</div>
          <input
            type="password"
            placeholder="Emergency secret..."
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-red-500"
          />
          <input
            type="text"
            placeholder="Sebep (opsiyonel)..."
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-gray-500"
          />
          <div className="flex gap-2">
            <button
              onClick={handleStop}
              disabled={!secret}
              className="flex-1 py-2 bg-red-700 hover:bg-red-600 disabled:bg-gray-700 disabled:text-gray-500 text-white rounded text-sm font-bold"
            >
              🚨 DURDUR
            </button>
            <button onClick={() => setPhase("idle")} className="flex-1 py-2 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded text-sm">
              İptal
            </button>
          </div>
        </div>
      )}

      {phase === "loading" && (
        <div className="text-yellow-400 text-sm animate-pulse">⏳ Pozisyonlar kapatılıyor...</div>
      )}

      {phase === "error" && (
        <div className="space-y-2">
          <div className="text-red-400 text-sm">❌ Hata: {result}</div>
          <button onClick={() => setPhase("idle")} className="text-xs text-gray-500 hover:text-gray-300">Sıfırla</button>
        </div>
      )}
    </div>
  );
}
