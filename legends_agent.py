"""
legends_agent.py  [V5 — ATR Dışa Aktarımı]
============================================
Algoritmik Hedge Fon — Efsane Trader Stratejileri

V5 DEĞİŞİKLİĞİ:
    - efsane_oylama() return dict'ine "atr" alanı eklendi
    - Bu sayede state_manager.py legends_rapor.json'dan da ATR okuyabilir
    - Backlog: legends_agent kendi içinde ATR hesaplıyor ama dışa AKTARMİYORDU
    - Şimdi: ATR → legends_rapor.json → state_manager → atr_sl_tp_hesapla()

Tüm efsane stratejiler korundu, sadece return dict güncellendi.
"""

import json
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf
from ta.momentum import RSIIndicator, WilliamsRIndicator
from ta.trend import MACD, SMAIndicator, EMAIndicator
from ta.volatility import AverageTrueRange, BollingerBands

warnings.filterwarnings("ignore")

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
PERIOD   = "2y"
INTERVAL = "1d"

AGIRLIKLAR = {
    "druckenmiller" : 20,
    "tudor_jones"   : 18,
    "livermore"     : 15,
    "dennis_turtle" : 12,
    "burry"         : 10,
    "soros"         : 10,
    "larry_williams": 8,
    "andrea_unger"  : 7,
}


# ─────────────────────────────────────────────
# BÖLÜM 1: VERİ ÇEKİMİ
# ─────────────────────────────────────────────
def veri_cek(symbol: str) -> pd.DataFrame | None:
    try:
        df = yf.Ticker(symbol).history(period=PERIOD, interval=INTERVAL)
        if df.empty or len(df) < 30:
            return None

        close  = df["Close"]
        high   = df["High"]
        low    = df["Low"]
        volume = df["Volume"]

        df["RSI"]        = RSIIndicator(close=close, window=14).rsi()
        df["SMA_20"]     = SMAIndicator(close=close, window=20).sma_indicator()
        df["SMA_50"]     = SMAIndicator(close=close, window=50).sma_indicator()
        df["SMA_200"]    = SMAIndicator(close=close, window=200).sma_indicator()
        df["EMA_20"]     = EMAIndicator(close=close, window=20).ema_indicator()

        _macd            = MACD(close=close)
        df["MACD"]       = _macd.macd()
        df["MACD_SIG"]   = _macd.macd_signal()
        df["MACD_HIST"]  = _macd.macd_diff()

        df["ATR"]        = AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range()

        _bb              = BollingerBands(close=close, window=20, window_dev=2)
        df["BB_HIGH"]    = _bb.bollinger_hband()
        df["BB_LOW"]     = _bb.bollinger_lband()
        df["BB_MID"]     = _bb.bollinger_mavg()

        df["WILLIAMS_R"] = WilliamsRIndicator(high=high, low=low, close=close, lbp=14).williams_r()
        df["HIGH_20"]    = high.rolling(20).max()
        df["LOW_20"]     = low.rolling(20).min()
        df["VOL_AVG"]    = volume.rolling(20).mean()

        return df.dropna(subset=["RSI", "MACD", "ATR", "WILLIAMS_R", "BB_HIGH", "HIGH_20"]).copy()
    except Exception as e:
        print(f"  ❌ {symbol} veri hatası: {e}")
        return None


# ─────────────────────────────────────────────
# BÖLÜM 2: EFSANE STRATEJİLER (Değişmedi)
# ─────────────────────────────────────────────

def strateji_druckenmiller(df: pd.DataFrame) -> tuple[int, float, str]:
    last  = df.iloc[-1]; prev = df.iloc[-2]; prev3 = df.iloc[-5]
    rsi_yukseliyor  = last["RSI"] > prev["RSI"] > prev3["RSI"]
    macd_donus      = last["MACD_HIST"] > 0 and prev["MACD_HIST"] < 0
    macd_guclu      = last["MACD_HIST"] > 0 and last["MACD_HIST"] > prev["MACD_HIST"]
    hacim_guclu     = df.iloc[-1]["Volume"] > last["VOL_AVG"] * 1.3
    rsi_dusuyor     = last["RSI"] < prev["RSI"] < prev3["RSI"]
    macd_neg_donus  = last["MACD_HIST"] < 0 and prev["MACD_HIST"] > 0

    if rsi_yukseliyor and macd_donus and last["RSI"] < 65:
        return 1, 0.90, "MACD pozitife döndü, RSI ivme kazanıyor"
    if macd_guclu and hacim_guclu and last["RSI"] < 60:
        return 1, 0.75, "Güçlü hacim + MACD histogramı artıyor"
    if rsi_dusuyor and macd_neg_donus:
        return -1, 0.85, "MACD negatife döndü, RSI zayıflıyor"
    return 0, 0.50, "Net asimetrik fırsat yok"


def strateji_tudor_jones(df: pd.DataFrame) -> tuple[int, float, str]:
    last = df.iloc[-1]
    if pd.notna(last["SMA_200"]) and last["SMA_200"] > 0:
        ana_trend_yukari = last["Close"] > last["SMA_200"]
    else:
        ana_trend_yukari = last["Close"] > last["SMA_50"]

    pullback_long    = ana_trend_yukari and last["Close"] < last["SMA_20"]
    breakdown_short  = not ana_trend_yukari and last["Close"] > last["SMA_20"]

    if ana_trend_yukari:
        if pullback_long: return 1, 0.85, "Ana trend yukarı, pullback dip fırsatı"
        return 1, 0.65, "Ana trend yukarı — yalnızca LONG geçerli"
    else:
        if breakdown_short: return -1, 0.85, "Ana trend aşağı, dead cat bounce — SHORT"
        return -1, 0.65, "Ana trend aşağı — yalnızca SHORT geçerli"


def strateji_livermore(df: pd.DataFrame) -> tuple[int, float, str]:
    last = df.iloc[-1]; prev = df.iloc[-2]
    yukari_kirilim  = prev["Close"] < prev["HIGH_20"] and last["Close"] > last["HIGH_20"] * 0.995
    asagi_kirilim   = prev["Close"] > prev["LOW_20"]  and last["Close"] < last["LOW_20"]  * 1.005
    hacim_konfirm   = last["Volume"] > last["VOL_AVG"] * 1.2

    if yukari_kirilim and hacim_konfirm:
        return 1, 0.88, f"20g pivot high kırıldı, hacim {last['Volume']/last['VOL_AVG']:.1f}x"
    if yukari_kirilim:
        return 1, 0.60, "Pivot kırılımı var ama hacim zayıf"
    if asagi_kirilim and hacim_konfirm:
        return -1, 0.88, f"20g pivot low kırıldı, hacim {last['Volume']/last['VOL_AVG']:.1f}x"
    if asagi_kirilim:
        return -1, 0.60, "Pivot düşüş var ama hacim zayıf"
    return 0, 0.50, "Pivot kırılımı yok"


def strateji_dennis_turtle(df: pd.DataFrame) -> tuple[int, float, str]:
    last = df.iloc[-1]; prev = df.iloc[-2]
    atr         = last["ATR"]
    long_entry  = last["Close"] > last["HIGH_20"] and prev["Close"] <= prev["HIGH_20"]
    short_entry = last["Close"] < last["LOW_20"]  and prev["Close"] >= prev["LOW_20"]
    stop_uzaklik= round(atr * 2, 2)

    if long_entry:  return 1, 0.82, f"Turtle LONG: 20g high kırıldı, ATR={atr:.2f}, stop:{stop_uzaklik}"
    if short_entry: return -1, 0.82, f"Turtle SHORT: 20g low kırıldı, ATR={atr:.2f}, stop:{stop_uzaklik}"
    if last["Close"] > last["SMA_50"] and last["MACD_HIST"] > 0:
        return 1, 0.55, f"Trend içi LONG güçlendirilebilir (ATR:{atr:.2f})"
    if last["Close"] < last["SMA_50"] and last["MACD_HIST"] < 0:
        return -1, 0.55, f"Trend içi SHORT güçlendirilebilir (ATR:{atr:.2f})"
    return 0, 0.40, f"Breakout yok, ATR={atr:.2f}"


def strateji_burry(df: pd.DataFrame) -> tuple[int, float, str]:
    last = df.iloc[-1]; prev = df.iloc[-2]
    rsi_asiri_satim = last["RSI"] < 32
    rsi_dip_yapiyor = last["RSI"] > prev["RSI"]
    panik_hacmi     = last["Volume"] > last["VOL_AVG"] * 2.0
    fiyat_dip       = last["Close"] < last["BB_LOW"]
    momentum_donuyor= last["MACD_HIST"] > prev["MACD_HIST"]
    rsi_asiri_alim  = last["RSI"] > 78
    balon_isaretleri= last["Close"] > last["BB_HIGH"] and last["Volume"] < last["VOL_AVG"] * 0.8

    if rsi_asiri_satim and rsi_dip_yapiyor and panik_hacmi:
        return 1, 0.88, f"RSI {last['RSI']:.1f} + panik hacmi {last['Volume']/last['VOL_AVG']:.1f}x — contrarian dip"
    if rsi_asiri_satim and fiyat_dip and momentum_donuyor:
        return 1, 0.80, f"BB altı + RSI {last['RSI']:.1f} + histogram toparlanıyor"
    if rsi_asiri_alim and balon_isaretleri:
        return -1, 0.85, f"RSI {last['RSI']:.1f} + düşük hacimli yükseliş — balon, SHORT"
    return 0, 0.45, "Net contrarian fırsat yok"


def strateji_soros(df: pd.DataFrame) -> tuple[int, float, str]:
    last = df.iloc[-1]; prev = df.iloc[-2]
    fiyat_serisi = [df.iloc[-5]["Close"], df.iloc[-4]["Close"], df.iloc[-3]["Close"], prev["Close"], last["Close"]]
    sure_yukari  = all(fiyat_serisi[i] < fiyat_serisi[i+1] for i in range(len(fiyat_serisi)-1))
    sure_asagi   = all(fiyat_serisi[i] > fiyat_serisi[i+1] for i in range(len(fiyat_serisi)-1))
    hacim_artisi = df.iloc[-1]["Volume"] > df.iloc[-3]["Volume"] * 1.2
    momentum_guclu = last["RSI"] > 55 and last["MACD_HIST"] > 0
    trend_bozuluyor= (df.iloc[-3]["Close"] > df.iloc[-4]["Close"] and prev["Close"] < df.iloc[-3]["Close"] and last["Close"] < prev["Close"])

    if sure_yukari and hacim_artisi: return 1, 0.85, "5g sürekli yükseliş + artan hacim — refleksif LONG"
    if sure_asagi and hacim_artisi:  return -1, 0.85, "5g sürekli düşüş + artan hacim — refleksif SHORT"
    if momentum_guclu and last["Close"] > last["SMA_20"]: return 1, 0.65, "Momentum güçlü — LONG devam"
    if trend_bozuluyor: return -1, 0.70, "Yukarı trend kırılıyor — SHORT"
    return 0, 0.45, "Refleksif döngü tespit edilemedi"


def strateji_larry_williams(df: pd.DataFrame) -> tuple[int, float, str]:
    last = df.iloc[-1]; prev = df.iloc[-2]; prev3 = df.iloc[-3]
    wr   = last["WILLIAMS_R"]; wr_prev = prev["WILLIAMS_R"]
    asiri_satimdan_cikis = wr_prev < -80 and wr > -80
    asiri_alimdan_cikis  = wr_prev > -20 and wr < -20
    momentum_yukari = last["Close"] > prev["Close"] > prev3["Close"]
    momentum_asagi  = last["Close"] < prev["Close"] < prev3["Close"]

    if asiri_satimdan_cikis and momentum_yukari: return 1, 0.90, f"WR {wr:.1f} aşırı satımdan çıkış + 3g momentum"
    if asiri_satimdan_cikis:                     return 1, 0.72, f"WR {wr:.1f} aşırı satımdan çıkış"
    if asiri_alimdan_cikis and momentum_asagi:   return -1, 0.90, f"WR {wr:.1f} aşırı alımdan çıkış + 3g momentum"
    if asiri_alimdan_cikis:                      return -1, 0.72, f"WR {wr:.1f} aşırı alımdan çıkış"
    if -50 < wr < -20 and last["MACD_HIST"] > 0: return 1, 0.55, f"WR {wr:.1f} orta-güçlü, momentum pozitif"
    if -80 < wr < -50 and last["MACD_HIST"] < 0: return -1, 0.55, f"WR {wr:.1f} orta-zayıf, momentum negatif"
    return 0, 0.40, f"WR {wr:.1f} — net sinyal yok"


def strateji_andrea_unger(df: pd.DataFrame) -> tuple[int, float, str]:
    last = df.iloc[-1]
    atr_su_an    = last["ATR"]
    atr_gecmis   = df["ATR"].rolling(20).mean().iloc[-1]
    atr_genisledi= atr_su_an > atr_gecmis * 1.2
    bb_genislik  = last["BB_HIGH"] - last["BB_LOW"]
    bb_gec_genislik = (df["BB_HIGH"] - df["BB_LOW"]).rolling(20).mean().iloc[-1]
    bb_genisledi = bb_genislik > bb_gec_genislik * 1.15
    yukari_yon   = last["Close"] > last["BB_MID"] and last["MACD_HIST"] > 0
    asagi_yon    = last["Close"] < last["BB_MID"] and last["MACD_HIST"] < 0

    if atr_genisledi and bb_genisledi and yukari_yon:  return 1, 0.87, f"ATR genişliyor + BB açılıyor — LONG"
    if atr_genisledi and bb_genisledi and asagi_yon:   return -1, 0.87, f"ATR genişliyor + BB açılıyor — SHORT"
    if atr_genisledi and yukari_yon:                   return 1, 0.68, "Volatilite artıyor, yön yukarı"
    if atr_genisledi and asagi_yon:                    return -1, 0.68, "Volatilite artıyor, yön aşağı"
    return 0, 0.40, f"Volatilite breakout yok (ATR: {atr_su_an:.2f})"


# ─────────────────────────────────────────────
# BÖLÜM 3: OYLAMA SİSTEMİ
# V5: Return dict'e "atr" ve "short_oran" eklendi
# ─────────────────────────────────────────────
def efsane_oylama(symbol: str, df: pd.DataFrame) -> dict:
    """
    8 efsane strateji → ağırlıklı oylama → konsensüs.

    V5 EKLENTİ: Return dict'e "atr" alanı eklendi.
    Bu sayede state_manager.py, legends_rapor.json'dan ATR okuyarak
    atr_sl_tp_hesapla() ile akıllı stop seviyeleri üretebiliyor.
    """
    stratejiler = {
        "druckenmiller" : strateji_druckenmiller,
        "tudor_jones"   : strateji_tudor_jones,
        "livermore"     : strateji_livermore,
        "dennis_turtle" : strateji_dennis_turtle,
        "burry"         : strateji_burry,
        "soros"         : strateji_soros,
        "larry_williams": strateji_larry_williams,
        "andrea_unger"  : strateji_andrea_unger,
    }

    sonuclar   = {}
    long_puan  = 0.0
    short_puan = 0.0
    hold_puan  = 0.0

    for isim, fonk in stratejiler.items():
        try:
            sinyal, guven, gerekce = fonk(df)
            agirlik = AGIRLIKLAR[isim]
            puan    = agirlik * guven
            sonuclar[isim] = {
                "sinyal" : {1: "LONG", -1: "SHORT", 0: "HOLD"}[sinyal],
                "guven"  : round(guven, 2),
                "agirlik": agirlik,
                "etki"   : round(puan, 2),
                "gerekce": gerekce,
            }
            if sinyal == 1:    long_puan  += puan
            elif sinyal == -1: short_puan += puan
            else:              hold_puan  += puan
        except Exception as e:
            agirlik = AGIRLIKLAR.get(isim, 0)
            sonuclar[isim] = {"sinyal": "HOLD", "guven": 0.0, "agirlik": agirlik, "etki": 0.0, "gerekce": f"Hata: {str(e)[:80]}"}
            hold_puan += agirlik * 0.1

    toplam     = long_puan + short_puan + hold_puan
    long_oran  = round(long_puan  / toplam * 100) if toplam > 0 else 0
    short_oran = round(short_puan / toplam * 100) if toplam > 0 else 0
    hold_oran  = round(hold_puan  / toplam * 100) if toplam > 0 else 0

    if long_puan > short_puan and long_oran >= 55:
        konsensus, konsensus_guven = "LONG", "YÜKSEK"
    elif short_puan > long_puan and short_oran >= 55:
        konsensus, konsensus_guven = "HOLD", "DÜŞÜK"
    elif long_puan > short_puan and long_oran >= 40:
        konsensus, konsensus_guven = "LONG", "ORTA"
    elif short_puan > long_puan and short_oran >= 40:
        konsensus, konsensus_guven = "HOLD", "DÜŞÜK"
    else:
        konsensus, konsensus_guven = "HOLD", "DÜŞÜK"

    # V5: ATR son değerini dışa aktar (state_manager için)
    try:
        atr_son = round(float(df.iloc[-1]["ATR"]), 4)
    except Exception:
        atr_son = None

    return {
        "symbol"          : symbol,
        "konsensus"       : konsensus,
        "konsensus_guven" : konsensus_guven,
        "long_oran"       : long_oran,
        "short_oran"      : short_oran,    # V5: alpaca_trader pyramiding için
        "hold_oran"       : hold_oran,
        "atr"             : atr_son,       # V5: state_manager atr_sl_tp_hesapla() için
        "efsane_sonuclari": sonuclar,
    }


# ─────────────────────────────────────────────
# BÖLÜM 4: RAPORLAMA
# ─────────────────────────────────────────────
def raporu_yazdir(tum_sonuclar: list[dict]) -> None:
    sinyal_ikon = {"LONG": "🟢", "SHORT": "🔴", "HOLD": "🟡"}
    guven_ikon  = {"YÜKSEK": "💪", "ORTA": "👍", "DÜŞÜK": "🤔"}

    print(f"\n{'═'*80}")
    print(f"  {'SEMBOL':<10} {'KARAR':<8} {'GÜVEN':<10} {'LONG%':>6} {'SHORT%':>7} {'ATR':>8}  OYBIRLIĞI")
    print(f"{'─'*80}")

    for s in tum_sonuclar:
        ki   = sinyal_ikon.get(s["konsensus"], "⚪")
        gi   = guven_ikon.get(s["konsensus_guven"], "")
        oylar= [v["sinyal"] for v in s["efsane_sonuclari"].values()]
        ayni = oylar.count(s["konsensus"])
        atr  = f"${s['atr']:.2f}" if s.get("atr") else "  N/A"
        print(f"  {s['symbol']:<10} {ki} {s['konsensus']:<6} {gi} {s['konsensus_guven']:<8} "
              f"{s['long_oran']:>5}%  {s['short_oran']:>6}%  {atr:>7}  {ayni}/8 efsane")
    print(f"{'═'*80}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{'═'*80}")
    print(f"  Algoritmik Hedge Fon | Legends Agent V5")
    print(f"  V5: ATR artık legends_rapor.json'a yazılıyor → state_manager'a akıyor")
    print(f"{'═'*80}\n")

    tum_sonuclar = []

    for i, sembol in enumerate(WATCHLIST, 1):
        print(f"[{i:>2}/{len(WATCHLIST)}] {sembol} analiz ediliyor...", end=" ", flush=True)
        df = veri_cek(sembol)
        if df is None:
            print("⚠️ Yetersiz veri"); continue

        sonuc = efsane_oylama(sembol, df)
        tum_sonuclar.append(sonuc)

        ki  = {"LONG": "🟢", "SHORT": "🔴", "HOLD": "🟡"}.get(sonuc["konsensus"], "⚪")
        atr = f"ATR:${sonuc['atr']:.2f}" if sonuc.get("atr") else ""
        print(f"{ki} {sonuc['konsensus']} ({sonuc['konsensus_guven']}) "
              f"L:{sonuc['long_oran']}% S:{sonuc['short_oran']}% {atr}")

    raporu_yazdir(tum_sonuclar)

    cikti = {
        "tarih"    : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "strateji" : "8 Efsane V5 — ATR Dışa Aktarımı Aktif",
        "sonuclar" : tum_sonuclar,
    }
    Path("legends_rapor.json").write_text(json.dumps(cikti, ensure_ascii=False, indent=2))
    print(f"\n💾 legends_rapor.json kaydedildi.")
    print(f"   V5: Her sembol için ATR değeri artık legends_rapor.json'da ✅\n")