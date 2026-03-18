"""
gamma_agent.py  [V1 — Gamma Sentinel]
==========================================
Algoritmik Hedge Fon — Opsiyon Radarı

Görevler:
    1. GEX hesaplama: Call/Put Gamma Exposure
    2. Zero Gamma seviyesi tespiti
    3. Put Wall / Call Wall belirleme
    4. UOA (Unusual Options Activity) tespiti
    5. IV Skew analizi

Çıktı:
    gamma_rapor.json → state_manager.py SL/TP'yi buna göre ayarlar

Veri Kaynakları:
    - yfinance options chain (ücretsiz)
    - Black-Scholes manuel implementasyon (scipy.stats.norm)

Çalışma zamanı:
    - Günde 1 kez, piyasa açılışından önce
    - ~3-5 dakika (17 sembol × opsiyon zinciri)
"""

import json
import math
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from scipy.stats import norm

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
WATCHLIST = [
    "NVDA", "AVGO", "SOXX", "PLTR", "MSTR", "IBIT",
    "ASTS", "VST",  "LMT",  "LLY",  "TSLA",
    "GLD",  "FXY",  "META", "USO",  "WMT",  "QQQ",
]

RISK_FREE_RATE = 0.053   # ABD 10Y tahvil faizi
OUTPUT_FILE    = "gamma_rapor.json"
UOA_HACIM_ESIK = 10      # Normal hacmin 10 katı = UOA
UOA_OI_MIN     = 500     # Minimum Open Interest


# ─────────────────────────────────────────────
# BÖLÜM 1: BLACK-SCHOLES HESAPLAMALAR
# ─────────────────────────────────────────────

def bs_gamma(S, K, T, r, sigma) -> float:
    """Black-Scholes Gamma. Spot $1 değiştiğinde Delta'nın değişim miktarı."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    try:
        d1    = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        gamma = norm.pdf(d1) / (S * sigma * math.sqrt(T))
        return float(gamma)
    except Exception:
        return 0.0


def bs_delta(S, K, T, r, sigma, option_type="call") -> float:
    """Black-Scholes Delta."""
    if T <= 0 or sigma <= 0:
        return 0.0
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        return float(norm.cdf(d1)) if option_type == "call" else float(norm.cdf(d1) - 1)
    except Exception:
        return 0.0


def bs_vanna(S, K, T, r, sigma) -> float:
    """Vanna: Delta'nın volatiliteye göre türevi."""
    if T <= 0 or sigma <= 0:
        return 0.0
    try:
        d1    = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2    = d1 - sigma * math.sqrt(T)
        return float(-norm.pdf(d1) * d2 / sigma)
    except Exception:
        return 0.0


# ─────────────────────────────────────────────
# BÖLÜM 2: OPSİYON ZİNCİRİ ÇEKME
# ─────────────────────────────────────────────

def opsiyon_zinciri_cek(sembol: str) -> dict:
    """
    yfinance üzerinden opsiyon zincirini çeker.
    En yakın 3 vadeyi analiz eder (haftalık + aylık).
    """
    try:
        ticker = yf_ticker(sembol)
        spot   = 0

        try:
            spot = ticker.fast_info.get("last_price", 0) or 0
        except Exception:
            pass

        if spot == 0:
            hist = ticker.history(period="2d")
            spot = float(hist["Close"].iloc[-1]) if not hist.empty else 0

        if spot == 0:
            return {}

        vadeler = ticker.options
        if not vadeler:
            return {}

        bugun         = datetime.now()
        secili_vadeler = []

        for vade in vadeler[:6]:
            vade_tarih = datetime.strptime(vade, "%Y-%m-%d")
            gun_kaldi  = (vade_tarih - bugun).days
            if gun_kaldi >= 3:
                secili_vadeler.append(vade)
            if len(secili_vadeler) >= 3:
                break

        if not secili_vadeler:
            return {}

        tum_calls = []
        tum_puts  = []

        for vade in secili_vadeler:
            try:
                zincir     = ticker.option_chain(vade)
                calls      = zincir.calls.copy()
                puts       = zincir.puts.copy()
                calls["vade"] = vade
                puts["vade"]  = vade
                vade_tarih = datetime.strptime(vade, "%Y-%m-%d")
                T          = max(0.001, (vade_tarih - bugun).days / 365)
                calls["T"] = T
                puts["T"]  = T
                tum_calls.append(calls)
                tum_puts.append(puts)
            except Exception:
                continue

        if not tum_calls:
            return {}

        return {
            "calls"  : pd.concat(tum_calls, ignore_index=True),
            "puts"   : pd.concat(tum_puts,  ignore_index=True),
            "spot"   : float(spot),
            "vadeler": secili_vadeler,
        }

    except Exception as e:
        print(f"    ⚠️  Opsiyon zinciri hatası {sembol}: {e}")
        return {}


# ─────────────────────────────────────────────
# BÖLÜM 3: GEX HESAPLAMA
# ─────────────────────────────────────────────

def gex_hesapla(zincir: dict) -> dict:
    """
    Gamma Exposure hesaplaması.

    Call GEX = Call OI × Gamma × 100 × Spot
    Put GEX  = Put OI  × Gamma × 100 × Spot × (-1)
    Net GEX  = Σ (Call GEX + Put GEX)
    """
    if not zincir:
        return {}

    spot  = zincir["spot"]
    calls = zincir["calls"]
    puts  = zincir["puts"]
    gex_by_strike = {}

    for _, row in calls.iterrows():
        try:
            K  = float(row["strike"])
            OI = float(row["openInterest"] or 0)
            T  = float(row["T"])
            iv = float(row["impliedVolatility"] or 0.30)
            if OI == 0 or T <= 0 or iv <= 0:
                continue
            gex = OI * bs_gamma(spot, K, T, RISK_FREE_RATE, iv) * 100 * spot
            k   = round(K, 0)
            gex_by_strike[k] = gex_by_strike.get(k, 0.0) + gex
        except Exception:
            continue

    for _, row in puts.iterrows():
        try:
            K  = float(row["strike"])
            OI = float(row["openInterest"] or 0)
            T  = float(row["T"])
            iv = float(row["impliedVolatility"] or 0.30)
            if OI == 0 or T <= 0 or iv <= 0:
                continue
            gex = -1 * OI * bs_gamma(spot, K, T, RISK_FREE_RATE, iv) * 100 * spot
            k   = round(K, 0)
            gex_by_strike[k] = gex_by_strike.get(k, 0.0) + gex
        except Exception:
            continue

    if not gex_by_strike:
        return {}

    net_gex  = sum(gex_by_strike.values())
    call_gex = sum(v for v in gex_by_strike.values() if v > 0)
    put_gex  = sum(v for v in gex_by_strike.values() if v < 0)

    # Zero Gamma seviyesi (işaret değişimi)
    sirali     = sorted(gex_by_strike.items())
    zero_gamma = spot
    for i in range(len(sirali) - 1):
        k1, g1 = sirali[i]
        k2, g2 = sirali[i + 1]
        if g1 * g2 < 0:
            zero_gamma = k1 + (k2 - k1) * abs(g1) / (abs(g1) + abs(g2))
            break

    # Put Wall / Call Wall (±%20 aralık)
    yakin = {k: v for k, v in gex_by_strike.items() if spot * 0.80 <= k <= spot * 1.20}
    if yakin:
        put_wall  = min(yakin, key=lambda k: yakin[k])
        call_wall = max(yakin, key=lambda k: yakin[k])
    else:
        put_wall, call_wall = spot * 0.95, spot * 1.05

    piyasa_modu = "POZITIF" if net_gex > 0 else "NEGATIF"

    return {
        "net_gex"      : round(net_gex, 0),
        "call_gex"     : round(call_gex, 0),
        "put_gex"      : round(put_gex, 0),
        "zero_gamma"   : round(zero_gamma, 2),
        "put_wall"     : round(put_wall, 2),
        "call_wall"    : round(call_wall, 2),
        "gex_by_strike": {str(k): round(v, 2) for k, v in gex_by_strike.items()},
        "piyasa_modu"  : piyasa_modu,
        "aciklama"     : (
            f"Pozitif GEX: Market maker stabilize ediyor — {zero_gamma:.1f}$ etrafında tutma"
            if piyasa_modu == "POZITIF" else
            f"Negatif GEX: Market maker amplify ediyor — trend hızlanabilir"
        ),
    }


# ─────────────────────────────────────────────
# BÖLÜM 4: UOA (UNUSUAL OPTIONS ACTIVITY)
# ─────────────────────────────────────────────

def uoa_tespit(zincir: dict) -> dict:
    """
    Hacim / OI > 10 VE Hacim > 500 → "Biri bir şey biliyor" sinyali.
    """
    if not zincir:
        return {"uoa_var": False, "uoa_listesi": [], "sinyal": 0.0,
                "uoa_yonu": "YOK", "aciklama": "Veri yok"}

    spot = zincir["spot"]
    uoa_listesi = []

    for df, yon in [(zincir["calls"], "CALL"), (zincir["puts"], "PUT")]:
        for _, row in df.iterrows():
            try:
                hacim = float(row["volume"]       or 0)
                oi    = float(row["openInterest"] or 0)
                K     = float(row["strike"])
                if oi == 0 or hacim < UOA_OI_MIN:
                    continue
                oran = hacim / oi
                if oran >= UOA_HACIM_ESIK:
                    uoa_listesi.append({
                        "strike": K, "vade": str(row.get("vade", "N/A")),
                        "yon": yon, "hacim": int(hacim), "oi": int(oi),
                        "oran": round(oran, 1),
                        "atm": abs(K - spot) / spot < 0.05,
                    })
            except Exception:
                continue

    call_uoa = sum(1 for u in uoa_listesi if u["yon"] == "CALL")
    put_uoa  = sum(1 for u in uoa_listesi if u["yon"] == "PUT")

    if call_uoa > put_uoa * 2:
        uoa_yonu, sinyal = "CALL",    min(1.0,  0.3 + len(uoa_listesi) * 0.1)
    elif put_uoa > call_uoa * 2:
        uoa_yonu, sinyal = "PUT",     max(-1.0, -0.3 - len(uoa_listesi) * 0.1)
    elif uoa_listesi:
        uoa_yonu, sinyal = "KARISIK", 0.0
    else:
        uoa_yonu, sinyal = "YOK",     0.0

    uoa_listesi.sort(key=lambda x: x["oran"], reverse=True)

    return {
        "uoa_var"    : len(uoa_listesi) > 0,
        "uoa_listesi": uoa_listesi[:5],
        "uoa_yonu"   : uoa_yonu,
        "call_uoa"   : call_uoa,
        "put_uoa"    : put_uoa,
        "sinyal"     : round(sinyal, 3),
        "aciklama"   : (
            f"🔥 UOA TESPİT: {len(uoa_listesi)} anormal işlem — {uoa_yonu} yönünde"
            if uoa_listesi else "Normal opsiyon aktivitesi"
        ),
    }


# ─────────────────────────────────────────────
# BÖLÜM 5: IV SKEW ANALİZİ
# ─────────────────────────────────────────────

def iv_skew_hesapla(zincir: dict) -> dict:
    """
    Skew = OTM Put IV - OTM Call IV
    Pozitif → downside koruması pahalı → Bearish
    Negatif → upside talebi var       → Bullish
    """
    if not zincir:
        return {"skew": 0.0, "sinyal": 0.0, "yorum": "Veri yok"}

    spot  = zincir["spot"]
    calls = zincir["calls"]
    puts  = zincir["puts"]

    try:
        atm_calls = calls[(calls["strike"] >= spot*0.95) & (calls["strike"] <= spot*1.05)]["impliedVolatility"].dropna()
        atm_puts  = puts [(puts ["strike"] >= spot*0.95) & (puts ["strike"] <= spot*1.05)]["impliedVolatility"].dropna()
        otm_puts  = puts [(puts ["strike"] >= spot*0.85) & (puts ["strike"] <  spot*0.95)]["impliedVolatility"].dropna()
        otm_calls = calls[(calls["strike"] >  spot*1.05) & (calls["strike"] <= spot*1.15)]["impliedVolatility"].dropna()

        otm_put_iv  = float(otm_puts.mean())  if not otm_puts.empty  else float(atm_puts.mean()  if not atm_puts.empty  else 0.30)
        otm_call_iv = float(otm_calls.mean()) if not otm_calls.empty else float(atm_calls.mean() if not atm_calls.empty else 0.30)

        skew = round(otm_put_iv - otm_call_iv, 4)

        if skew > 0.10:
            yorum, sinyal = f"⚠️  Güçlü downside skew (+{skew:.1%}): Düşüş riskini fiyatlıyor", -0.4
        elif skew > 0.05:
            yorum, sinyal = f"Hafif bearish skew (+{skew:.1%})", -0.2
        elif skew < -0.05:
            yorum, sinyal = f"✅ Bullish skew ({skew:.1%}): Upside talebi var", 0.3
        else:
            yorum, sinyal = f"Nötr skew ({skew:.1%})", 0.0

        return {
            "skew"       : skew,
            "otm_put_iv" : round(otm_put_iv, 4),
            "otm_call_iv": round(otm_call_iv, 4),
            "sinyal"     : sinyal,
            "yorum"      : yorum,
        }

    except Exception as e:
        return {"skew": 0.0, "sinyal": 0.0, "yorum": f"Hata: {e}"}


# ─────────────────────────────────────────────
# BÖLÜM 6: KOMPOZİT GAMMA SKORU
# ─────────────────────────────────────────────

def gamma_skor_hesapla(gex: dict, uoa: dict, iv_skew: dict, spot: float) -> dict:
    """GEX (%40) + UOA (%35) + IV Skew (%25) → final skor."""
    if not gex:
        return {
            "final_skor"   : 0.0,
            "put_wall"     : round(spot * 0.95, 2),
            "call_wall"    : round(spot * 1.05, 2),
            "zero_gamma"   : round(spot, 2),
            "piyasa_modu"  : "BILINMIYOR",
            "net_gex"      : 0,
            "uoa_var"      : False,
            "uoa_yonu"     : "YOK",
            "iv_skew"      : 0.0,
            "tp_ayar"      : "değişiklik yok",
            "pozisyon_ayar": 1.0,
            "uyarilar"     : [],
        }

    gex_sinyal  = 0.2 if gex.get("piyasa_modu") == "POZITIF" else -0.2
    uoa_sinyal  = uoa.get("sinyal", 0.0)
    skew_sinyal = iv_skew.get("sinyal", 0.0)

    ham_skor   = gex_sinyal * 0.40 + uoa_sinyal * 0.35 + skew_sinyal * 0.25
    final_skor = max(-1.0, min(1.0, round(ham_skor, 3)))

    put_wall   = gex.get("put_wall",   spot * 0.95)
    call_wall  = gex.get("call_wall",  spot * 1.05)
    zero_gamma = gex.get("zero_gamma", spot)

    # TP ayar önerisi
    if abs(spot - put_wall) / spot < 0.03:
        tp_ayar = f"TP'yi put_wall'a çek: ${put_wall:.2f}"
    elif abs(call_wall - spot) / spot < 0.03:
        tp_ayar = f"TP call_wall yakınında: ${call_wall:.2f}"
    else:
        tp_ayar = "TP değişiklik yok"

    # Pozisyon büyüklüğü ayarı
    pozisyon_ayar = 1.0
    if gex.get("piyasa_modu") == "NEGATIF":
        pozisyon_ayar *= 0.75
    if uoa.get("uoa_var") and uoa.get("uoa_yonu") == "PUT":
        pozisyon_ayar *= 0.80

    return {
        "final_skor"   : final_skor,
        "put_wall"     : round(put_wall, 2),
        "call_wall"    : round(call_wall, 2),
        "zero_gamma"   : round(zero_gamma, 2),
        "piyasa_modu"  : gex.get("piyasa_modu", "BILINMIYOR"),
        "net_gex"      : gex.get("net_gex", 0),
        "uoa_var"      : uoa.get("uoa_var", False),
        "uoa_yonu"     : uoa.get("uoa_yonu", "YOK"),
        "iv_skew"      : iv_skew.get("skew", 0.0),
        "tp_ayar"      : tp_ayar,
        "pozisyon_ayar": round(pozisyon_ayar, 2),
        "uyarilar"     : [
            f"GEX: {gex.get('aciklama','N/A')}",
            f"UOA: {uoa.get('aciklama','N/A')}",
            f"Skew: {iv_skew.get('yorum','N/A')}",
        ],
    }


# ─────────────────────────────────────────────
# BÖLÜM 7: ANA FONKSİYON
# ─────────────────────────────────────────────

def gamma_analizi_yap() -> dict:
    """17 varlık için tam gamma analizi."""

    print(f"\n{'='*65}")
    print(f"  Algoritmik Hedge Fon | Gamma Sentinel V1")
    print(f"  Kaynak: yfinance Options + Black-Scholes")
    print(f"{'='*65}\n")

    rapor = {}

    for i, sembol in enumerate(WATCHLIST, 1):
        print(f"[{i:2d}/17] {sembol}:")

        print(f"    📈 Opsiyon zinciri...", end=" ", flush=True)
        zincir = opsiyon_zinciri_cek(sembol)

        if not zincir:
            print("⚠️  Veri yok")
            rapor[sembol] = {"hata": "opsiyon verisi yok"}
            continue

        spot = zincir["spot"]
        print(f"✓ Spot: ${spot:.2f} | {len(zincir['vadeler'])} vade")

        print(f"    🌊 GEX...", end=" ", flush=True)
        gex = gex_hesapla(zincir)
        if gex:
            print(f"✓ Net GEX: {gex['net_gex']:,.0f} | Mod: {gex['piyasa_modu']}")
            print(f"       Put Wall: ${gex['put_wall']:.2f} | "
                  f"Call Wall: ${gex['call_wall']:.2f} | "
                  f"Zero-γ: ${gex['zero_gamma']:.2f}")
        else:
            print("⚠️  GEX hesaplanamadı")

        print(f"    🔥 UOA...", end=" ", flush=True)
        uoa = uoa_tespit(zincir)
        print(f"✓ {uoa['aciklama']}")

        print(f"    📊 IV Skew...", end=" ", flush=True)
        iv_skew = iv_skew_hesapla(zincir)
        print(f"✓ {iv_skew['yorum']}")

        skor = gamma_skor_hesapla(gex, uoa, iv_skew, spot)

        emoji = "🟢" if skor["final_skor"] > 0.2 else \
                "🔴" if skor["final_skor"] < -0.2 else "🟡"

        print(f"  ► {emoji} GAMMA SKOR: {skor['final_skor']:+.3f} | "
              f"Pozisyon: ×{skor['pozisyon_ayar']} | {skor['tp_ayar']}\n")

        rapor[sembol] = {
            "sembol"  : sembol,
            "spot"    : spot,
            "gex"     : gex,
            "uoa"     : uoa,
            "iv_skew" : iv_skew,
            "skor"    : skor,
        }

    return rapor


if __name__ == "__main__":
    rapor = gamma_analizi_yap()

    cikti = {
        "tarih"    : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "versiyon" : "V1",
        "varlıklar": rapor,
    }

    Path(OUTPUT_FILE).write_text(
        json.dumps(cikti, ensure_ascii=False, indent=2, default=str)
    )

    print(f"\n{'='*65}")
    print(f"  {'SEM':<6} {'SKOR':>6}  {'PUT_WALL':>10}  {'CALL_WALL':>10}  {'MOD':<10}  UOA")
    print(f"{'─'*65}")

    for sem, veri in rapor.items():
        if "hata" in veri:
            print(f"  {sem:<6}  N/A")
            continue
        s = veri["skor"]
        e = "🟢" if s["final_skor"] > 0.2 else "🔴" if s["final_skor"] < -0.2 else "🟡"
        print(f"  {sem:<6} {s['final_skor']:>+6.3f}  "
              f"${s['put_wall']:>9.2f}  "
              f"${s['call_wall']:>9.2f}  "
              f"{s['piyasa_modu']:<10}  "
              f"{'✅' if s['uoa_var'] else '—'} {e}")

    print(f"{'='*65}")
    print(f"\n💾 {OUTPUT_FILE} kaydedildi. ✅\n")
