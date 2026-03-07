"""
mock_agent.py
=============
Algoritmik Hedge Fon — API'sız Tam Pipeline Testi
DAG: Veri Çekimi → Teknik Zenginleştirme → Kural Bazlı Karar Motoru → Rapor

AMAÇ:
    Gerçek API key olmadan sistemin tüm iskeletini test etmek.
    Kural bazlı karar motoru, AI'ın döndüreceği formatın BİREBİR aynısını üretir.
    API geldiğinde: sadece `_mock_karar_motoru()` fonksiyonunu CrewAI çağrısıyla değiştir.

Gereken kurulum (zaten kurulu olmalı):
    pip install yfinance pandas python-dotenv ta
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

warnings.filterwarnings("ignore")
load_dotenv()


# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
WATCHLIST = [
    "NVDA", "TSLA", "TSM", "ASTS",
    "VST", "LMT", "JPM", "LLY",
    "MSTR", "PLTR", "FXY"
]
PERIOD   = "3mo"
INTERVAL = "1d"
OUTPUT_FILE = "rapor.json"   # Çıktı dosyası (aynı klasöre kaydeder)


# ─────────────────────────────────────────────
# BÖLÜM 1: Veri Çekimi ve Teknik Zenginleştirme
# (analyst_agent.py ile aynı — değişmedi)
# ─────────────────────────────────────────────
def fetch_and_enrich(symbol: str) -> dict | None:
    try:
        df = yf.Ticker(symbol).history(period=PERIOD, interval=INTERVAL)

        if df.empty or len(df) < 50:
            print(f"  ⚠️  {symbol}: Yeterli veri yok, atlanıyor.")
            return None

        close = df["Close"]
        df["RSI"]         = RSIIndicator(close=close, window=14).rsi()
        df["SMA_20"]      = SMAIndicator(close=close, window=20).sma_indicator()
        df["SMA_50"]      = SMAIndicator(close=close, window=50).sma_indicator()
        _macd             = MACD(close=close)
        df["MACD"]        = _macd.macd()
        df["MACD_Signal"] = _macd.macd_signal()
        df["MACD_Diff"]   = _macd.macd_diff()

        last = df.iloc[-1]
        prev = df.iloc[-2]

        return {
            "symbol"           : symbol,
            "son_kapanış"      : round(float(last["Close"]), 2),
            "günlük_değişim_%" : round(((float(last["Close"]) - float(prev["Close"])) / float(prev["Close"])) * 100, 2),
            "RSI_14"           : round(float(last["RSI"]), 2),
            "SMA_20"           : round(float(last["SMA_20"]), 2),
            "SMA_50"           : round(float(last["SMA_50"]), 2),
            "MACD"             : round(float(last["MACD"]), 4),
            "MACD_Sinyal"      : round(float(last["MACD_Signal"]), 4),
            "MACD_Histogram"   : round(float(last["MACD_Diff"]), 4),
            "hacim"            : int(last["Volume"]),
            "fiyat_sma20_poz"  : "ÜSTÜNDE" if last["Close"] > last["SMA_20"] else "ALTINDA",
            "fiyat_sma50_poz"  : "ÜSTÜNDE" if last["Close"] > last["SMA_50"] else "ALTINDA",
            "sma20_sma50_poz"  : "ÜSTÜNDE" if last["SMA_20"] > last["SMA_50"] else "ALTINDA",
        }
    except Exception as e:
        print(f"  ❌ {symbol} veri hatası: {e}")
        return None


# ─────────────────────────────────────────────
# BÖLÜM 2: Kural Bazlı Karar Motoru (Mock AI)
#
# ⚡ API GELDİĞİNDE: Bu fonksiyonu sil, yerine
#    analyst_agent.py'deki run_analyst_agent() koy.
#    Format aynı → geri kalan hiçbir şey değişmez.
# ─────────────────────────────────────────────
def _mock_karar_motoru(veri: dict) -> dict:
    """
    RSI + MACD + SMA pozisyonlarından deterministik karar üretir.
    CrewAI'ın döndüreceği formatın birebir aynısı: TREND / SİNYAL / GEREKÇE.
    """
    rsi       = veri["RSI_14"]
    histogram = veri["MACD_Histogram"]
    macd      = veri["MACD"]
    sma20_poz = veri["fiyat_sma20_poz"]
    sma50_poz = veri["fiyat_sma50_poz"]
    sma_sirasi= veri["sma20_sma50_poz"]   # SMA20'nin SMA50'ye göre konumu

    # --- Puan Sistemi (her sinyale ağırlık ver) ---
    puan = 0

    # RSI sinyali
    if rsi < 30:
        puan += 2      # Aşırı satım → güçlü alım fırsatı
    elif rsi < 45:
        puan += 1      # Zayıf ama nötr değil
    elif rsi > 70:
        puan -= 2      # Aşırı alım → güçlü satış sinyali
    elif rsi > 55:
        puan -= 1

    # MACD Histogram (momentum yönü — en önemli sinyal)
    if histogram > 0 and macd > 0:
        puan += 2      # Hem histogram pozitif hem MACD pozitif
    elif histogram > 0:
        puan += 1      # Sadece histogram pozitif (toparlanıyor)
    elif histogram < 0 and macd < 0:
        puan -= 2      # Her ikisi de negatif → güçlü satış baskısı
    elif histogram < 0:
        puan -= 1

    # SMA pozisyonları (trend yönü)
    if sma20_poz == "ÜSTÜNDE" and sma50_poz == "ÜSTÜNDE":
        puan += 2      # Fiyat her iki ortalamanın üstünde
    elif sma20_poz == "ÜSTÜNDE":
        puan += 1
    elif sma50_poz == "ALTINDA" and sma20_poz == "ALTINDA":
        puan -= 2      # Güçlü düşüş trendi
    elif sma20_poz == "ALTINDA":
        puan -= 1

    # SMA sıralaması (Golden/Death Cross benzeri)
    if sma_sirasi == "ÜSTÜNDE":
        puan += 1      # SMA20 > SMA50 → bullish yapı
    else:
        puan -= 1      # SMA20 < SMA50 → bearish yapı

    # --- Karar Eşiği ---
    if puan >= 4:
        trend, sinyal = "Bullish", "LONG"
    elif puan >= 2:
        trend, sinyal = "Bullish", "HOLD"   # Pozitif ama yeterince güçlü değil
    elif puan <= -4:
        trend, sinyal = "Bearish", "SHORT"
    elif puan <= -2:
        trend, sinyal = "Bearish", "HOLD"   # Negatif ama kesin değil
    else:
        trend, sinyal = "Nötr", "HOLD"

    # Gerekçe oluştur
    rsi_yorum = (
        f"RSI {rsi:.1f} ile aşırı satım bölgesinde"  if rsi < 30 else
        f"RSI {rsi:.1f} ile aşırı alım bölgesinde"   if rsi > 70 else
        f"RSI {rsi:.1f} ile nötr bölgede"
    )
    macd_yorum = (
        "MACD histogramı pozitif ve momentum artıyor"   if histogram > 0 and macd > 0 else
        "MACD histogramı pozitife dönüyor, toparlanma var" if histogram > 0 else
        "MACD histogramı negatif, satış baskısı sürüyor"   if histogram < 0 and macd < 0 else
        "MACD histogramı negatife döndü, momentum zayıflıyor"
    )
    sma_yorum = (
        f"Fiyat her iki SMA'nın üstünde, trend yapısı güçlü"
        if sma20_poz == "ÜSTÜNDE" and sma50_poz == "ÜSTÜNDE"
        else f"Fiyat her iki SMA'nın altında, trend baskısı devam ediyor"
        if sma20_poz == "ALTINDA" and sma50_poz == "ALTINDA"
        else f"Fiyat SMA20 {sma20_poz}, SMA50 {sma50_poz}, karışık sinyal"
    )

    return {
        "TREND"    : trend,
        "SİNYAL"   : sinyal,
        "PUAN"     : puan,         # Debug için — AI versiyonunda olmayacak
        "GEREKÇE"  : f"{rsi_yorum}. {macd_yorum}. {sma_yorum}.",
        "MOD"      : "MOCK"        # API gelince bu satır "AI" olacak
    }


# ─────────────────────────────────────────────
# BÖLÜM 3: Raporlama
# ─────────────────────────────────────────────
def raporu_yazdir(rapor: list[dict]) -> None:
    """Terminale renkli özet tablo basar."""
    sinyal_ikonu = {"LONG": "🟢", "SHORT": "🔴", "HOLD": "🟡"}

    print(f"\n{'─'*70}")
    print(f"  {'SEMBOL':<10} {'FİYAT':>8}  {'DEĞ%':>6}  {'RSI':>6}  "
          f"{'TREND':<10} {'SİNYAL':<8} {'PUAN':>5}")
    print(f"{'─'*70}")

    for r in rapor:
        v = r["veri"]
        k = r["karar"]
        ikon = sinyal_ikonu.get(k["SİNYAL"], "⚪")
        print(
            f"  {v['symbol']:<10} "
            f"{v['son_kapanış']:>8.2f}  "
            f"{v['günlük_değişim_%']:>+6.2f}%  "
            f"{v['RSI_14']:>6.1f}  "
            f"{k['TREND']:<10} "
            f"{ikon} {k['SİNYAL']:<6} "
            f"{k['PUAN']:>+5}"
        )
    print(f"{'─'*70}")


def raporu_kaydet(rapor: list[dict], dosya: str) -> None:
    """Tüm veri ve kararları JSON olarak kaydeder. AI gelince bu dosyayı okuyacak."""
    cikti = {
        "tarih"      : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mod"        : "MOCK — API bağlantısı bekleniyor",
        "varlık_sayısı": len(rapor),
        "varlıklar"  : rapor
    }
    Path(dosya).write_text(json.dumps(cikti, ensure_ascii=False, indent=2))
    print(f"\n💾 Rapor kaydedildi → {dosya}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{'='*70}")
    print(f"  Algoritmik Hedge Fon | Mock Pipeline v0.2")
    print(f"  Watchlist: {len(WATCHLIST)} varlık | Mod: API'sız Kural Motoru")
    print(f"{'='*70}\n")

    rapor = []
    basarili = 0
    basarisiz = 0

    for i, sembol in enumerate(WATCHLIST, 1):
        print(f"[{i:>2}/{len(WATCHLIST)}] {sembol} işleniyor...", end=" ")
        veri = fetch_and_enrich(sembol)

        if veri is None:
            basarisiz += 1
            continue

        karar = _mock_karar_motoru(veri)
        rapor.append({"veri": veri, "karar": karar})
        basarili += 1
        print(f"✅  {karar['SİNYAL']} ({karar['TREND']})")

    # Özet tablo
    raporu_yazdir(rapor)

    # Gerekçeleri göster
    print("\n📋 GEREKÇELER:")
    for r in rapor:
        print(f"\n  [{r['veri']['symbol']}] {r['karar']['SİNYAL']}")
        print(f"  → {r['karar']['GEREKÇE']}")

    # JSON'a kaydet
    raporu_kaydet(rapor, OUTPUT_FILE)

    # Özet
    print(f"\n📊 Özet: {basarili} başarılı / {basarisiz} başarısız / "
          f"{len(WATCHLIST)} toplam varlık")
    print(f"\n⚡ API KEY GELDİĞİNDE:")
    print(f"   _mock_karar_motoru() → run_analyst_agent() ile değiştir.")
    print(f"   rapor.json formatı aynı kalır, hiçbir şey kırılmaz.\n")