"""
mock_agent.py  [V6 — Quant Arsenal: HMM + Black-Litterman + Copula]
=======================================================
Algoritmik Hedge Fon — API'sız Tam Pipeline Testi

V5 DEĞİŞİKLİKLERİ:
    - fetch_and_enrich() → ATR_14 ve SMA_200 artık return dict'e yazılıyor
    - Bu sayede state_manager.py canlı pipeline'da ATR bazlı SL/TP hesaplayabilir
    - Backtest ile canlı sistem arasındaki ATR kopukluğu kapatıldı

API geldiğinde: _mock_karar_motoru() → run_analyst_agent() swap.
Dosya formatı aynı → geri kalan hiçbir şey kırılmaz.
"""

import json
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from ta.trend import MACD, SMAIndicator
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange  # V5: ATR için eklendi
from quant_math import (
    sembol_quant_metrikleri,       # V6: Kurtosis, Hurst, GARCH, Kalman
    hmm_rejim_tespit,              # V6: Piyasa rejim tespiti (SPY/QQQ)
    black_litterman_agirliklar,    # V6: AI görüşlü portföy optimizasyonu
)

warnings.filterwarnings("ignore")
load_dotenv()


# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
WATCHLIST = [
    # Yarı İletken & AI Liderleri
    "NVDA", "AVGO", "SOXX",
    # Veri, Yazılım & Kripto
    "PLTR", "MSTR", "IBIT",
    # Agresif Momentum Şampiyonları (Ana Kâr Motorları)
    "ASTS", "VST", 
    # Savunma, İlaç & Otomotiv
    "LMT", "LLY", "TSLA",
    # Makro Koruma & Değer
    "GLD", "FXY" , "META",
    "USO",  "WMT",  "QQQ"
   

]
PERIOD      = "1y"     # V5: SMA_200 için 1 yıl (eski 3mo yetersizdi)
INTERVAL    = "1d"
OUTPUT_FILE = "rapor.json"


# ─────────────────────────────────────────────
# BÖLÜM 1: Veri Çekimi ve Teknik Zenginleştirme
# V5: ATR_14 ve SMA_200 artık return dict'e dahil
# ─────────────────────────────────────────────
def fetch_and_enrich(symbol: str) -> dict | None:
    """
    yfinance'ten OHLCV çeker, indikatörleri hesaplar.

    V5 Eklentisi: ATR_14 ve SMA_200 artık döndürülen dict'te.
    Bu değerler state_manager.py'nin atr_sl_tp_hesapla()
    fonksiyonu tarafından dinamik stop seviyesi üretmek için kullanılır.
    """
    try:
        df = yf.Ticker(symbol).history(period=PERIOD, interval=INTERVAL)

        if df.empty or len(df) < 50:
            print(f"  ⚠️  {symbol}: Yeterli veri yok, atlanıyor.")
            return None

        close  = df["Close"]
        high   = df["High"]
        low    = df["Low"]

        # ── Mevcut İndikatörler ──────────────────────────────────────────
        df["RSI"]         = RSIIndicator(close=close, window=14).rsi()
        df["SMA_20"]      = SMAIndicator(close=close, window=20).sma_indicator()
        df["SMA_50"]      = SMAIndicator(close=close, window=50).sma_indicator()

        # V5: SMA_200 — Paul Tudor Jones trend filtresi için şart
        df["SMA_200"]     = SMAIndicator(close=close, window=200).sma_indicator()

        _macd             = MACD(close=close)
        df["MACD"]        = _macd.macd()
        df["MACD_Signal"] = _macd.macd_signal()
        df["MACD_Diff"]   = _macd.macd_diff()

        # V5: ATR — canlı pipeline'ın akıllı SL/TP ve Pyramiding için kullandığı değer
        df["ATR_14"]      = AverageTrueRange(
            high=high, low=low, close=close, window=14
        ).average_true_range()

        last = df.iloc[-1]
        prev = df.iloc[-2]

        # SMA pozisyonları
        price_vs_sma20 = "ÜSTÜNDE" if last["Close"] > last["SMA_20"] else "ALTINDA"
        price_vs_sma50 = "ÜSTÜNDE" if last["Close"] > last["SMA_50"] else "ALTINDA"
        sma20_vs_sma50 = "ÜSTÜNDE" if last["SMA_20"] > last["SMA_50"] else "ALTINDA"

        # SMA_200 trend tespiti (PTJ kuralı)
        try:
            sma200_val      = float(last["SMA_200"])
            fiyat_sma200    = "ÜSTÜNDE" if float(last["Close"]) > sma200_val else "ALTINDA"
            ana_trend       = "BULLISH" if float(last["Close"]) > sma200_val else "BEARISH"
        except Exception:
            sma200_val   = None
            fiyat_sma200 = "VERİ_YOK"
            ana_trend    = "NÖTR"

        # V5: ATR değeri
        try:
            atr_val = round(float(last["ATR_14"]), 4)
        except Exception:
            atr_val = None

        return {
            # ── Mevcut alanlar (format korundu) ─────────────────────────
            "symbol"           : symbol,
            "son_kapanış"      : round(float(last["Close"]), 2),
            "günlük_değişim_%" : round(
                ((float(last["Close"]) - float(prev["Close"])) / float(prev["Close"])) * 100, 2
            ),
            "RSI_14"           : round(float(last["RSI"]), 2),
            "SMA_20"           : round(float(last["SMA_20"]), 2),
            "SMA_50"           : round(float(last["SMA_50"]), 2),
            "MACD"             : round(float(last["MACD"]), 4),
            "MACD_Sinyal"      : round(float(last["MACD_Signal"]), 4),
            "MACD_Histogram"   : round(float(last["MACD_Diff"]), 4),
            "hacim"            : int(last["Volume"]),
            "fiyat_sma20_poz"  : price_vs_sma20,
            "fiyat_sma50_poz"  : price_vs_sma50,
            "sma20_sma50_poz"  : sma20_vs_sma50,
            # ── V5 Yeni Alanlar ─────────────────────────────────────────
            "ATR_14"           : atr_val,        # Dinamik SL/TP ve Pyramiding için
            "SMA_200"          : round(sma200_val, 2) if sma200_val else None,
            "fiyat_sma200_poz" : fiyat_sma200,   # Paul Tudor Jones trend filtresi
            "ana_trend"        : ana_trend,       # BULLISH/BEARISH/NÖTR
        }

    except Exception as e:
        print(f"  ❌ {symbol} veri hatası: {e}")
        return None


def _quant_metrikleri_ekle(veri: dict, df: object) -> dict:
    """
    V6: Kurtosis, Hurst, GARCH ve Kalman metriklerini veri dict'ine ekler.
    fetch_and_enrich() sonucuna uygulanır.
    state_manager.py bu değerleri ATR çarpanı ve pozisyon ölçekleme için kullanır.
    """
    try:
        quant = sembol_quant_metrikleri(df["Close"])
        veri["kurtosis"]        = quant["kurtosis"]       # Fat tail radarı
        veri["hurst"]           = quant["hurst"]          # Trend/testere kararı
        veri["garch"]           = quant["garch"]          # Yarınki volatilite
        veri["kalman_son"]      = quant["kalman_son_fiyat"]  # Filtrelenmiş fiyat
    except Exception as e:
        veri["kurtosis"]   = {"kurtosis": 0.0, "atr_carpan": 2.5, "risk_seviyesi": "NORMAL"}
        veri["hurst"]      = {"hurst": 0.5, "yorum": "HESAPLANAMADI", "long_izni": True}
        veri["garch"]      = {"sigma_yarin": None, "pozisyon_olcegi": 1.0}
        veri["kalman_son"] = None
    return veri


# ─────────────────────────────────────────────
# BÖLÜM 2: Kural Bazlı Karar Motoru (Mock AI)
# API gelince → run_analyst_agent() ile değiştir, format aynı kalır
# ─────────────────────────────────────────────
def _mock_karar_motoru(veri: dict) -> dict:
    """
    RSI + MACD + SMA pozisyonlarından deterministik karar üretir.
    V5: ATR değerini de puan sistemine dahil etmez (o state_manager'ın işi),
        ama PUAN'a SMA_200 trend filtresi eklendi.
    """
    rsi       = veri["RSI_14"]
    histogram = veri["MACD_Histogram"]
    macd      = veri["MACD"]
    sma20_poz = veri["fiyat_sma20_poz"]
    sma50_poz = veri["fiyat_sma50_poz"]
    sma_sirasi= veri["sma20_sma50_poz"]
    ana_trend = veri.get("ana_trend", "NÖTR")   # V5: SMA_200 trend

    puan = 0

    # RSI sinyali
    if rsi < 30:     puan += 2
    elif rsi < 45:   puan += 1
    elif rsi > 70:   puan -= 2
    elif rsi > 55:   puan -= 1

    # MACD Histogram (momentum yönü)
    if histogram > 0 and macd > 0:        puan += 2
    elif histogram > 0:                   puan += 1
    elif histogram < 0 and macd < 0:      puan -= 2
    elif histogram < 0:                   puan -= 1

    # SMA pozisyonları
    if sma20_poz == "ÜSTÜNDE" and sma50_poz == "ÜSTÜNDE":   puan += 2
    elif sma20_poz == "ÜSTÜNDE":                              puan += 1
    elif sma50_poz == "ALTINDA" and sma20_poz == "ALTINDA":  puan -= 2
    elif sma20_poz == "ALTINDA":                              puan -= 1

    # SMA sıralaması (Golden/Death Cross)
    puan += 1 if sma_sirasi == "ÜSTÜNDE" else -1

    # V5 EKLENTİ: SMA_200 ana trend ağırlığı (Paul Tudor Jones kuralı)
    # Bull trend → LONG puanı +1, Bear trend → SHORT puanı +1
    if ana_trend == "BULLISH":
        puan += 1
    elif ana_trend == "BEARISH":
        puan -= 1

    # Karar eşiği (V5: puan aralığı -10/+10'a genişledi, eşikler güncellendi)
# Karar eşiği
    if puan >= 5:      trend, sinyal = "Bullish", "LONG"
    elif puan >= 2:    trend, sinyal = "Bullish", "HOLD"
    elif puan <= -5:   trend, sinyal = "Bearish", "SHORT"   # <-- DÜZELTİLDİ
    elif puan <= -2:   trend, sinyal = "Bearish", "HOLD"
    else:              trend, sinyal = "Nötr",    "HOLD"
    # Gerekçe
    rsi_yorum = (
        f"RSI {rsi:.1f} ile aşırı satım bölgesinde"  if rsi < 30 else
        f"RSI {rsi:.1f} ile aşırı alım bölgesinde"   if rsi > 70 else
        f"RSI {rsi:.1f} ile nötr bölgede"
    )
    macd_yorum = (
        "MACD histogramı pozitif ve momentum artıyor"           if histogram > 0 and macd > 0 else
        "MACD histogramı pozitife dönüyor, toparlanma var"      if histogram > 0 else
        "MACD histogramı negatif, satış baskısı sürüyor"        if histogram < 0 and macd < 0 else
        "MACD histogramı negatife döndü, momentum zayıflıyor"
    )
    trend_yorum = f"Ana trend {ana_trend} (SMA200 filtresi)"

    return {
        "TREND"   : trend,
        "SİNYAL"  : sinyal,
        "PUAN"    : puan,
        "GEREKÇE" : f"{rsi_yorum}. {macd_yorum}. {trend_yorum}.",
        "MOD"     : "MOCK-V5"
    }


# ─────────────────────────────────────────────
# BÖLÜM 3: Raporlama
# ─────────────────────────────────────────────
def raporu_yazdir(rapor: list[dict]) -> None:
    sinyal_ikonu = {"LONG": "🟢", "SHORT": "🔴", "HOLD": "🟡"}

    print(f"\n{'─'*75}")
    print(f"  {'SEMBOL':<10} {'FİYAT':>8}  {'DEĞ%':>6}  {'RSI':>6}  "
          f"{'ATR':>7}  {'TREND':<10} {'SİNYAL':<8} {'PUAN':>5}")
    print(f"{'─'*75}")

    for r in rapor:
        v    = r["veri"]
        k    = r["karar"]
        ikon = sinyal_ikonu.get(k["SİNYAL"], "⚪")
        atr  = f"${v['ATR_14']:.2f}" if v.get("ATR_14") else "  N/A "
        print(
            f"  {v['symbol']:<10} "
            f"{v['son_kapanış']:>8.2f}  "
            f"{v['günlük_değişim_%']:>+6.2f}%  "
            f"{v['RSI_14']:>6.1f}  "
            f"{atr:>7}  "
            f"{k['TREND']:<10} "
            f"{ikon} {k['SİNYAL']:<6} "
            f"{k['PUAN']:>+5}"
        )
    print(f"{'─'*75}")


def raporu_kaydet(rapor: list[dict], dosya: str) -> None:
    cikti = {
        "tarih"         : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mod"           : "QUANT-V6 — Gemini AI & Alpaca API CANLI BAĞLANTI AKTİF 🟢",
        "varlık_sayısı" : len(rapor),
        "v5_degisiklik" : "ATR_14 ve SMA_200 tüm varlıklar için rapor.json'a eklendi",
        "varlıklar"     : rapor
    }
    Path(dosya).write_text(json.dumps(cikti, ensure_ascii=False, indent=2))
    print(f"\n💾 Rapor kaydedildi → {dosya}")
    print(f"   V5: ATR_14 ve SMA_200 verisi artık state_manager'a akıyor ✅")
# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{'='*70}")
    print(f"  Algoritmik Hedge Fon | Mock Pipeline V5")
    print(f"  Watchlist: {len(WATCHLIST)} varlık | V5: ATR+SMA200 Pipeline Entegrasyonu")
    print(f"{'='*70}\n")

    rapor      = []
    basarili   = 0
    basarisiz  = 0

    for i, sembol in enumerate(WATCHLIST, 1):
        print(f"[{i:>2}/{len(WATCHLIST)}] {sembol} işleniyor...", end=" ")
        veri = fetch_and_enrich(sembol)

        if veri is None:
            basarisiz += 1
            continue

        karar = _mock_karar_motoru(veri)

        # V6: Quant metrikleri ekle (Kurtosis, Hurst, GARCH, Kalman)
        df_tmp = yf.Ticker(sembol).history(period=PERIOD, interval=INTERVAL)
        if not df_tmp.empty:
            veri = _quant_metrikleri_ekle(veri, df_tmp)

        rapor.append({"veri": veri, "karar": karar})
        basarili += 1
        atr_str = f"ATR:${veri['ATR_14']:.2f}" if veri.get("ATR_14") else ""
        print(f"✅  {karar['SİNYAL']} ({karar['TREND']}) {atr_str}")

    raporu_yazdir(rapor)
    raporu_kaydet(rapor, OUTPUT_FILE)

    print(f"\n📊 Özet: {basarili} başarılı / {basarisiz} başarısız / {len(WATCHLIST)} toplam")

    # ─── V6: HMM Rejim Günlük Güncelleme ─────────────────────────────
    print(f"\n  🔍 HMM Rejim tespiti (SPY)...", end=" ", flush=True)
    try:
        import json as json_hmm
        from pathlib import Path as Path_hmm
        spy_close = yf.Ticker("SPY").history(period="1y", interval="1d")["Close"]
        rejim = hmm_rejim_tespit(spy_close)
        Path_hmm("hmm_rejim.json").write_text(
            json_hmm.dumps({**rejim, "tarih": __import__("datetime").datetime.now().strftime("%Y-%m-%d")},
                           ensure_ascii=False, indent=2)
        )
        print(f"✅ {rejim['rejim_adi']} (çarpan: ×{rejim['esik_carpani']}) — {rejim['yorum']}")
    except Exception as e:
        print(f"⚠️ HMM hata: {e}")

    # ─── V6: Black-Litterman Portföy Ağırlıkları ───────────────────────
    print(f"  📐 Black-Litterman portföy optimizasyonu...", end=" ", flush=True)
    try:
        import json as json_bl
        from pathlib import Path as Path_bl
        import numpy as np_bl

        # Sentiment skorlarını görüş olarak kullan
        goruc_dict = {}
        for item in rapor:
            sem = item["veri"]["symbol"]
            puan = item["karar"].get("PUAN", 0)
            goruc_dict[sem] = max(-1.0, min(1.0, puan / 10.0))  # [-10, +10] → [-1, +1]

        # Getiri serilerini topla
        getiri_dict = {}
        for item in rapor:
            sem = item["veri"]["symbol"]
            try:
                ser = yf.Ticker(sem).history(period="1y", interval="1d")["Close"]
                if len(ser) > 50:
                    log_ret = np_bl.diff(np_bl.log(ser.values))
                    getiri_dict[sem] = log_ret
            except Exception:
                pass

        sembol_l = [item["veri"]["symbol"] for item in rapor]
        bl_agirliklar = black_litterman_agirliklar(sembol_l, getiri_dict, goruc_dict)

        Path_bl("bl_agirliklar.json").write_text(
            json_bl.dumps(
                {"tarih": __import__("datetime").datetime.now().strftime("%Y-%m-%d"), "agirliklar": bl_agirliklar},
                ensure_ascii=False, indent=2, default=str
            )
        )
        top3 = sorted(bl_agirliklar.items(), key=lambda x: x[1] if x[1] else 0, reverse=True)[:3]
        top3_str = ", ".join(f"{s}:{w:.0%}" for s,w in top3 if w)
        print(f"✅ Top-3: {top3_str}")
    except Exception as e:
        print(f"⚠️ BL hata: {e}")

    print(f"\n⚡ V6 QUANT AKIŞ ZİNCİRİ:")
    print(f"   mock_agent → rapor.json[ATR+Kurtosis+Hurst+GARCH+Kalman]")
    print(f"   HMM rejim → hmm_rejim.json → state_manager ESIK×")
    print(f"   Black-Litterman → bl_agirliklar.json → portföy ağırlıkları")
    print(f"   state_manager → Kelly×GARCH×Copula → final_karar.json ✅\n")