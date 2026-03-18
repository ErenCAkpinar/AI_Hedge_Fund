"""
swan_agent.py  [V1 — Black Swan Engine]
=========================================
Algoritmik Hedge Fon — Kriz Simülatörü

Görevler:
    1. VIX bazlı günlük risk skoru
    2. Tarihi kriz stres testi: 2008, 2020, 2022, 2001, 2018
    3. Monte Carlo: 10.000 senaryo, 252 günlük ufuk
    4. Risk-Off sinyali üretme → state_manager okur

Çalışma zamanı:
    - Her gün 1 kez (scheduler tarafından çağrılır)
    - ~30-60 saniye (10.000 simülasyon)

Çıktı:
    swan_rapor.json → state_manager.py tarafından okunur
"""

import json
import os
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from quant_math import (
    monte_carlo_sim,
    kriz_stres_testi,
    vix_stres_hesapla,
)

warnings.filterwarnings("ignore")
load_dotenv()

# curl_cffi opsiyonel
try:
    import yfinance as yf
    from curl_cffi import requests as cf_requests
    cf_session = cf_requests.Session(impersonate="chrome110")
    def yf_ticker(sembol):
        return yf.Ticker(sembol, session=cf_session)
except ImportError:
    import yfinance as yf
    cf_session = None
    def yf_ticker(sembol):
        return yf.Ticker(sembol)

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
PORTFOY_VARSAYILAN = 1500.0
RISK_OFF_ESIK      = 40.0     # VIX bu seviyeyi geçerse Risk-Off
MONTE_CARLO_SIM    = 10000
UFUK_GUN           = 252      # 1 yıllık ileriye bakış
OUTPUT_FILE        = "swan_rapor.json"


# ─────────────────────────────────────────────
# BÖLÜM 1: VIX ÇEK
# ─────────────────────────────────────────────
def vix_cek() -> float:
    """VIX endeksini yfinance'ten çeker."""
    try:
        ticker = yf_ticker("^VIX")
        df     = ticker.history(period="5d", interval="1d")
        if df.empty:
            return 20.0
        return round(float(df["Close"].iloc[-1]), 2)
    except Exception as e:
        print(f"  ⚠️  VIX çekilemedi: {e} — fallback: 20.0")
        return 20.0


# ─────────────────────────────────────────────
# BÖLÜM 2: PORTFÖY GETİRİSİ (SPY proxy)
# ─────────────────────────────────────────────
def portfoy_getirisi_hesapla() -> pd.Series:
    """
    SPY getirilerini portföy proxy olarak kullan.
    İleride: kelly_gecmis.json'dan gerçek işlem getirileri.
    """
    try:
        ticker  = yf_ticker("SPY")
        df      = ticker.history(period="2y", interval="1d")
        log_ret = np.log(df["Close"] / df["Close"].shift(1)).dropna()
        return log_ret
    except Exception as e:
        print(f"  ⚠️  SPY getirisi çekilemedi: {e}")
        return pd.Series(dtype=float)


# ─────────────────────────────────────────────
# BÖLÜM 3: ALPACA'DAN GERÇEK BAKİYE ÇEK
# ─────────────────────────────────────────────
def portfoy_degeri_cek() -> float:
    """
    Alpaca paper hesabından gerçek equity çeker.
    Bağlantı yoksa .env varsayılanını kullan.
    """
    try:
        import alpaca_trade_api as tradeapi
        api = tradeapi.REST(
            os.getenv("ALPACA_API_KEY"),
            os.getenv("ALPACA_SECRET_KEY"),
            os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
        )
        hesap = api.get_account()
        return float(hesap.equity)
    except Exception:
        return PORTFOY_VARSAYILAN


# ─────────────────────────────────────────────
# BÖLÜM 4: ANA FONKSİYON
# ─────────────────────────────────────────────
def swan_analizi_yap() -> dict:
    """Tam Black Swan analizi — 3 katman."""

    print("\n🦢 Black Swan Engine başlatılıyor...")

    # Gerçek portföy değeri
    pv = portfoy_degeri_cek()
    print(f"  💰 Portföy değeri: ${pv:,.2f}")

    # VIX
    print(f"  📊 VIX çekiliyor...", end=" ", flush=True)
    vix = vix_cek()
    print(f"✅ VIX = {vix}")

    # VIX stres analizi
    vix_stres = vix_stres_hesapla(vix, pv)
    print(f"  🎯 Risk seviyesi: {vix_stres['risk_adi']} — {vix_stres['tavsiye']}")

    # Tarihi kriz stres testi
    print(f"  💥 Tarihi kriz stres testi...", end=" ", flush=True)
    kriz = kriz_stres_testi(pv)
    print(f"✅ En kötü: {kriz['en_kotu']} (kalan: ${kriz['min_kalan']:,.0f})")

    # Monte Carlo
    print(f"  🎲 Monte Carlo ({MONTE_CARLO_SIM:,} sim, {UFUK_GUN} gün)...",
          end=" ", flush=True)
    getiriler = portfoy_getirisi_hesapla()

    if getiriler.empty:
        mc = {
            "median_sonuc"   : pv,
            "iflas_olasiligi": 0.05,
            "hedef_olasiligi": 0.50,
            "var_95"         : pv * 0.85,
            "cvar_95"        : pv * 0.75,
            "en_kotu_10"     : pv * 0.80,
            "en_iyi_10"      : pv * 1.20,
            "n_sim"          : MONTE_CARLO_SIM,
            "ufuk_gun"       : UFUK_GUN,
        }
        print("⚠️  Veri yok, fallback kullanıldı")
    else:
        mc = monte_carlo_sim(getiriler, MONTE_CARLO_SIM, UFUK_GUN, pv)
        print(f"✅ Medyan: ${mc['median_sonuc']:,.0f} | "
              f"İflas olasılığı: %{mc['iflas_olasiligi']*100:.1f}")

    # ─── Risk-Off kararı ────────────────────────────────────────
    risk_off = (
        vix_stres["risk_off_aktif"]
        or kriz["risk_seviyesi"] == "YUKSEK"
        or mc.get("iflas_olasiligi", 0) > 0.25
    )

    # ─── Genel risk skoru (0–100) ───────────────────────────────
    risk_skoru = min(100, int(
        (vix / 80 * 40)                              +  # VIX katkısı: max 40 puan
        (mc.get("iflas_olasiligi", 0) * 100 * 0.35) +  # MC katkısı: max 35 puan
        (abs(kriz["min_kalan"] / pv - 1) * 25)         # Kriz katkısı: max 25 puan
    ))

    return {
        "tarih"          : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "portfoy_degeri" : pv,
        "risk_skoru"     : risk_skoru,
        "risk_off"       : risk_off,
        "risk_adi"       : vix_stres["risk_adi"],
        "tavsiye"        : vix_stres["tavsiye"],
        "vix"            : {
            "deger"         : vix,
            "risk_seviyesi" : vix_stres["risk_adi"],
            "beklenen_kayip": vix_stres["beklenen_kayip"],
        },
        "kriz_stres"     : kriz["krizler"],
        "en_kotu_kriz"   : kriz["en_kotu"],
        "monte_carlo"    : mc,
        "aksiyon"        : (
            "RISK_OFF: Tüm pozisyonları kapat, sadece GLD/USO/FXY tut"
            if risk_off else
            "NORMAL: Standart işlem devam"
        ),
    }


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"  Black Swan Engine V1 | Kriz Simülatörü")
    print(f"  Monte Carlo: {MONTE_CARLO_SIM:,} sim | Ufuk: {UFUK_GUN} gün")
    print(f"{'='*60}")

    rapor = swan_analizi_yap()

    # NaN → None dönüşümü (JSON'da NaN geçersiz; FastAPI null olarak döndürür)
    import math
    def nan_to_none(obj):
        if isinstance(obj, float) and math.isnan(obj):
            return None
        if isinstance(obj, dict):
            return {k: nan_to_none(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [nan_to_none(v) for v in obj]
        return obj

    Path(OUTPUT_FILE).write_text(
        json.dumps(nan_to_none(rapor), ensure_ascii=False, indent=2, default=str)
    )

    risk_ikon = {
        "DUSUK" : "🟢",
        "ORTA"  : "🟡",
        "YUKSEK": "🔴",
        "PANIK" : "🔴",
    }.get(rapor["risk_adi"], "⚪")

    print(f"\n{'─'*60}")
    print(f"  📊 RİSK SKORU  : {rapor['risk_skoru']}/100")
    print(f"  {risk_ikon} DURUM       : {rapor['risk_adi']}")
    print(f"  💬 TAVSİYE     : {rapor['tavsiye']}")
    print(f"  🦢 RISK-OFF    : {'🔴 AKTİF' if rapor['risk_off'] else '🟢 PASİF'}")
    print(f"{'─'*60}")
    print(f"\n💾 {OUTPUT_FILE} kaydedildi.")
    print(f"   state_manager.py bu dosyayı okuyarak eşikleri ayarlar ✅\n")
