"""
fastapi_bridge.py  [V1 — Quant War Room API]
=============================================
Algoritmik Hedge Fon — FastAPI Backend

JSON raporlarını HTTP endpoint'lerine dönüştürür.
Next.js quant-war-room/ dashboard bu API'yi kullanır.

Endpoint'ler:
    GET  /api/v1/health           → Sistem sağlık kontrolü
    GET  /api/v1/signals          → final_karar.json (tüm sinyaller)
    GET  /api/v1/portfolio        → Alpaca hesap bilgisi
    GET  /api/v1/risk             → swan_rapor.json (VIX + MC + Kriz)
    GET  /api/v1/insider          → insider_rapor.json (6 katman)
    GET  /api/v1/gamma            → gamma_rapor.json (GEX + UOA + Skew)
    GET  /api/v1/sentiment        → sentiment_rapor.json
    GET  /api/v1/agents           → Tüm ajanların son çalışma durumu
    POST /api/v1/emergency-stop   → Tüm pozisyonları kapat (double-confirm)

Çalıştırma:
    pip install fastapi uvicorn
    uvicorn fastapi_bridge:app --host 0.0.0.0 --port 8000 --reload

CORS: Next.js dev (localhost:3000) ve prod için açık.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

# ─────────────────────────────────────────────
# APP
# ─────────────────────────────────────────────
app = FastAPI(
    title       = "Quant War Room API",
    description = "Algoritmik Hedge Fon — Real-time sinyal ve risk dashboard",
    version     = "1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["http://localhost:3000", "http://127.0.0.1:3000", "*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
DOSYALAR = {
    "signals"   : "final_karar.json",
    "risk"      : "swan_rapor.json",
    "insider"   : "insider_rapor.json",
    "gamma"     : "gamma_rapor.json",
    "sentiment" : "sentiment_rapor.json",
    "pairs"     : "pairs_rapor.json",
}

EMERGENCY_SECRET = os.getenv("EMERGENCY_STOP_SECRET", "hedge-emergency-2025")


# ─────────────────────────────────────────────
# YARDIMCI FONKSİYONLAR
# ─────────────────────────────────────────────
def json_oku(dosya: str) -> dict | list:
    """JSON dosyasını okur. Bulunamazsa HTTPException fırlatır."""
    path = Path(dosya)
    if not path.exists():
        raise HTTPException(
            status_code = 404,
            detail      = f"{dosya} bulunamadı. Önce ilgili ajanı çalıştırın.",
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"JSON parse hatası: {e}")


def dosya_durumu(dosya: str) -> dict:
    """Dosya var mı, ne zaman güncellendi?"""
    path = Path(dosya)
    if not path.exists():
        return {"mevcut": False, "son_guncelleme": None, "boyut_kb": 0}
    stat = path.stat()
    return {
        "mevcut"         : True,
        "son_guncelleme" : datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "boyut_kb"       : round(stat.st_size / 1024, 1),
    }


# ─────────────────────────────────────────────
# ENDPOINT 1: HEALTH
# ─────────────────────────────────────────────
@app.get("/api/v1/health")
def health_check() -> dict:
    """Sistem sağlık kontrolü — tüm JSON dosyalarının durumu."""
    return {
        "status"    : "ok",
        "timestamp" : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dosyalar"  : {ad: dosya_durumu(yol) for ad, yol in DOSYALAR.items()},
        "versiyon"  : "Quant War Room V1",
    }


# ─────────────────────────────────────────────
# ENDPOINT 2: SİNYALLER
# ─────────────────────────────────────────────
@app.get("/api/v1/signals")
def get_signals() -> dict:
    """
    final_karar.json → tüm semboller için final sinyal, skor, SL/TP, katmanlar.
    Next.js SignalMap bileşeni bu endpoint'i kullanır.
    """
    veri = json_oku(DOSYALAR["signals"])

    # Özet istatistik ekle
    kararlar = veri.get("kararlar", [])
    ozet = {
        "toplam"  : len(kararlar),
        "long"    : sum(1 for k in kararlar if k.get("final_sinyal") == "LONG"),
        "short"   : sum(1 for k in kararlar if k.get("final_sinyal") == "SHORT"),
        "hold"    : sum(1 for k in kararlar if k.get("final_sinyal") == "HOLD"),
        "catisma" : sum(1 for k in kararlar if k.get("catisma")),
    }

    return {**veri, "ozet": ozet}


# ─────────────────────────────────────────────
# ENDPOINT 3: PORTFÖY
# ─────────────────────────────────────────────
@app.get("/api/v1/portfolio")
def get_portfolio() -> dict:
    """
    Alpaca paper hesabından gerçek portföy bilgisi çeker.
    Bağlantı yoksa son bilinen değerleri döndürür.
    """
    try:
        import alpaca_trade_api as tradeapi

        api = tradeapi.REST(
            os.getenv("ALPACA_API_KEY"),
            os.getenv("ALPACA_SECRET_KEY"),
            os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
        )
        hesap    = api.get_account()
        pozlar   = api.list_positions()

        pozisyonlar = []
        for p in pozlar:
            pozisyonlar.append({
                "symbol"        : p.symbol,
                "qty"           : float(p.qty),
                "avg_entry"     : float(p.avg_entry_price),
                "current_price" : float(p.current_price),
                "market_value"  : float(p.market_value),
                "unrealized_pl" : float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
                "side"          : p.side,
            })

        return {
            "timestamp"       : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "equity"          : float(hesap.equity),
            "cash"            : float(hesap.cash),
            "buying_power"    : float(hesap.buying_power),
            "portfolio_value" : float(hesap.portfolio_value),
            "day_pl"          : float(hesap.equity) - float(hesap.last_equity),
            "day_pl_pct"      : round(
                (float(hesap.equity) / float(hesap.last_equity) - 1) * 100, 3
            ) if float(hesap.last_equity) > 0 else 0.0,
            "pozisyonlar"     : pozisyonlar,
            "acik_pozisyon"   : len(pozisyonlar),
        }

    except ImportError:
        raise HTTPException(status_code=503, detail="alpaca_trade_api kurulu değil.")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Alpaca bağlantı hatası: {e}")


# ─────────────────────────────────────────────
# ENDPOINT 4: RİSK (BLACK SWAN)
# ─────────────────────────────────────────────
@app.get("/api/v1/risk")
def get_risk() -> dict:
    """
    swan_rapor.json → VIX, Monte Carlo, tarihi kriz stres testi, Risk-Off durumu.
    Next.js RiskGauge bileşeni bu endpoint'i kullanır.
    """
    return json_oku(DOSYALAR["risk"])


# ─────────────────────────────────────────────
# ENDPOINT 5: INSIDER
# ─────────────────────────────────────────────
@app.get("/api/v1/insider")
def get_insider() -> dict:
    """
    insider_rapor.json → Form4, Kongre, Dark Pool, 13F, Whale, Short Interest.
    Next.js InsiderRadar bileşeni bu endpoint'i kullanır.
    """
    return json_oku(DOSYALAR["insider"])


# ─────────────────────────────────────────────
# ENDPOINT 6: GAMMA
# ─────────────────────────────────────────────
@app.get("/api/v1/gamma")
def get_gamma() -> dict:
    """
    gamma_rapor.json → GEX, UOA, IV Skew, Put/Call Wall, Zero Gamma.
    Next.js GammaDashboard bileşeni bu endpoint'i kullanır.
    """
    return json_oku(DOSYALAR["gamma"])


# ─────────────────────────────────────────────
# ENDPOINT 7: SENTIMENT
# ─────────────────────────────────────────────
@app.get("/api/v1/sentiment")
def get_sentiment() -> dict:
    """sentiment_rapor.json → sembol bazında sentiment skorları."""
    return json_oku(DOSYALAR["sentiment"])


# ─────────────────────────────────────────────
# ENDPOINT 8: AJAN DURUMLARI
# ─────────────────────────────────────────────
@app.get("/api/v1/agents")
def get_agents() -> dict:
    """
    Tüm ajanların son çalışma durumu: dosya var mı, ne zaman güncellendi?
    """
    ajanlar = {
        "mock_agent"     : {"dosya": "rapor.json",          **dosya_durumu("rapor.json")},
        "legends_agent"  : {"dosya": "legends_rapor.json",  **dosya_durumu("legends_rapor.json")},
        "swan_agent"     : {"dosya": "swan_rapor.json",      **dosya_durumu("swan_rapor.json")},
        "pairs_agent"    : {"dosya": "pairs_rapor.json",     **dosya_durumu("pairs_rapor.json")},
        "insider_agent"  : {"dosya": "insider_rapor.json",   **dosya_durumu("insider_rapor.json")},
        "gamma_agent"    : {"dosya": "gamma_rapor.json",     **dosya_durumu("gamma_rapor.json")},
        "state_manager"  : {"dosya": "final_karar.json",     **dosya_durumu("final_karar.json")},
        "sentiment_agent": {"dosya": "sentiment_rapor.json", **dosya_durumu("sentiment_rapor.json")},
    }

    aktif_sayisi = sum(1 for a in ajanlar.values() if a.get("mevcut"))

    return {
        "timestamp"    : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ajanlar"      : ajanlar,
        "aktif_sayisi" : aktif_sayisi,
        "toplam_ajan"  : len(ajanlar),
    }


# ─────────────────────────────────────────────
# ENDPOINT 9: EMERGENCY STOP
# ─────────────────────────────────────────────
class EmergencyStopRequest(BaseModel):
    confirm   : bool
    secret    : str
    reason    : str = "Manuel emergency stop"


@app.post("/api/v1/emergency-stop")
def emergency_stop(req: EmergencyStopRequest) -> dict:
    """
    Tüm açık pozisyonları kapatır.
    İki güvenlik katmanı:
        1. confirm=True olmalı
        2. secret == EMERGENCY_STOP_SECRET

    UYARI: Bu işlem geri alınamaz!
    """
    if not req.confirm:
        raise HTTPException(status_code=400, detail="confirm=true olmalı.")

    if req.secret != EMERGENCY_SECRET:
        raise HTTPException(status_code=403, detail="Geçersiz emergency secret.")

    try:
        import alpaca_trade_api as tradeapi

        api = tradeapi.REST(
            os.getenv("ALPACA_API_KEY"),
            os.getenv("ALPACA_SECRET_KEY"),
            os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
        )

        # Tüm açık pozisyonları listele
        pozlar = api.list_positions()
        if not pozlar:
            return {
                "status"    : "ok",
                "mesaj"     : "Kapatılacak açık pozisyon yok.",
                "timestamp" : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "kapatilan" : [],
            }

        # Hepsini kapat
        kapatilan = []
        hatalar   = []
        for poz in pozlar:
            try:
                api.close_position(poz.symbol)
                kapatilan.append(poz.symbol)
            except Exception as e:
                hatalar.append({"symbol": poz.symbol, "hata": str(e)})

        return {
            "status"    : "ok" if not hatalar else "partial",
            "mesaj"     : f"{len(kapatilan)} pozisyon kapatıldı. Sebep: {req.reason}",
            "timestamp" : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "kapatilan" : kapatilan,
            "hatalar"   : hatalar,
        }

    except ImportError:
        raise HTTPException(status_code=503, detail="alpaca_trade_api kurulu değil.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Emergency stop hatası: {e}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    print(f"\n{'='*60}")
    print(f"  Quant War Room API V1")
    print(f"  http://localhost:8000")
    print(f"  Docs: http://localhost:8000/docs")
    print(f"{'='*60}\n")

    uvicorn.run(
        "fastapi_bridge:app",
        host    = "0.0.0.0",
        port    = 8000,
        reload  = True,
        log_level = "info",
    )
