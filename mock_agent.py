"""
mock_agent.py  [V5 — ATR+SMA200 Pipeline Entegrasyonu]
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
    if puan >= 5:      trend, sinyal = "Bullish", "LONG"
    elif puan >= 2:    trend, sinyal = "Bullish", "HOLD"
    elif puan <= -5:   trend, sinyal = "Bearish", "HOLD"
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
        "mod"           : "MOCK-V5 — ATR+SMA200 entegre, API bağlantısı bekleniyor",
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
        rapor.append({"veri": veri, "karar": karar})
        basarili += 1
        atr_str = f"ATR:${veri['ATR_14']:.2f}" if veri.get("ATR_14") else ""
        print(f"✅  {karar['SİNYAL']} ({karar['TREND']}) {atr_str}")

    raporu_yazdir(rapor)
    raporu_kaydet(rapor, OUTPUT_FILE)

    print(f"\n📊 Özet: {basarili} başarılı / {basarisiz} başarısız / {len(WATCHLIST)} toplam")
    print(f"\n⚡ V5 ATR AKIŞ ZİNCİRİ:")
    print(f"   mock_agent → rapor.json[ATR_14] → state_manager → atr_sl_tp_hesapla()")
    print(f"   alpaca_trader → Pyramiding (1.5×ATR eşiği) ✅\n")