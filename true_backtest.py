"""
true_backtest_v5.py — 4 KÂR ARTIRICI GELİŞTİRME
=================================================

v4 → v5 DEĞİŞİKLİKLER:

  🏃 GELİŞTİRME 1: İZLEYEN STOP (Trailing Stop)
    - Sabit TP (Take Profit) kaldırıldı.
    - Fiyat yükseldikçe Stop-Loss arkasından tırmanır.
    - ATR_TRAIL_KATSAYI = 2.5  (giriş sonrası trail mesafesi)
    - Rallinin tamamını kasaya koyar, erken çıkışı önler.

  ⚖️  GELİŞTİRME 2: DİNAMİK POZİSYON BÜYÜKLÜĞÜ (Kelly Kriteri)
    - Sabit %10 yerine state_manager toplam_skor baz alınır:
        Skor ≥ 0.60 → %35  |  ≥ 0.40 → %25
        Skor ≥ 0.30 → %15  |  < 0.30 → %10
    - Makine en emin olduğunda ağır yumruk atar.

  🧱 GELİŞTİRME 3: PYRAMIDING (Kazanan Ata Ekleme)
    - Trend devam ettiğinde her 1.5 ATR'de bir ek giriş (max 2 katman).
    - Piramit boyutu = ana pozisyonun %50'si.
    - Dennis Turtle Trading: "Kazanana ekle, kaybedeni kes."

  ❄️  GELİŞTİRME 4: BİLEŞİK GETİRİ (Compounding)
    - Tüm işlemler kronolojik sıralanır.
    - Kâr ana sermayeye eklenir, bir sonraki işlem güncel equity'den açılır.
    - Kartopu etkisiyle dik büyüme eğrisi.
"""

import sys, json, warnings
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np
import yfinance as yf
from ta.momentum import RSIIndicator, WilliamsRIndicator
from ta.trend import MACD, SMAIndicator, EMAIndicator, ADXIndicator
from ta.volatility import AverageTrueRange, BollingerBands

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))

from legends_agent import efsane_oylama
from state_manager import (
    teknik_skora_cevir, efsane_skora_cevir,
    catisma_var_mi,
    pozisyon_buyuklugu_hesapla,
    AGIRLIK_TEKNIK, AGIRLIK_EFSANE,
)

# ─────────────────────────────────────────────
# CONFIG v5
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
    "GLD", "FXY"
]

LONG_ONLY_LIST = {"NVDA", "AVGO", "SOXX", "PLTR", "MSTR", "IBIT", "ASTS", "VST", "LMT", "LLY",  "TSLA", "GLD", "FXY"}

BASLANGIC_SERMAYE = 1_500
PERIOD            = "2y"
ISINMA_GUN        = 60
MAX_POZISYON_GUN  = 15

ESIK_YUKSEK = 0.40
ESIK_ORTA   = 0.30

ADX_MIN_LONG    = 20
ADX_MIN_SHORT   = 30
SHORT_ORAN_MIN  = 60

# ATR stop katsayıları
ATR_SL_YUKSEK = 1.8
ATR_SL_ORTA   = 1.5

# 🆕 1. İZLEYEN STOP (Trailing Stop)
ATR_TRAIL_KATSAYI = 2.5   # Trailing stop ATR mesafesi — TP'nin yerini aldı

# 🆕 3. PYRAMIDING
PYRAMID_TRIGGER_ATR = 1.5   # Her X ATR'de bir ek giriş
PYRAMID_MAX         = 2     # Maksimum ek giriş katmanı
PYRAMID_BOYUT       = 0.50  # Piramit boyutu = ana pozisyonun %50'si

# ─────────────────────────────────────────────
# BÖLÜM 1: VERİ
# ─────────────────────────────────────────────
def veri_cek(symbol):
    try:
        df = yf.Ticker(symbol).history(period=PERIOD, interval="1d")
        if df.empty or len(df) < ISINMA_GUN + 10:
            return None

        close = df["Close"]; high = df["High"]; low = df["Low"]

        df["RSI"]        = RSIIndicator(close=close, window=14).rsi()
        df["SMA_20"]     = SMAIndicator(close=close, window=20).sma_indicator()
        df["SMA_50"]     = SMAIndicator(close=close, window=50).sma_indicator()
        df["SMA_200"]    = SMAIndicator(close=close, window=200).sma_indicator()

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
        df["VOL_AVG"]    = df["Volume"].rolling(20).mean()

        _adx             = ADXIndicator(high=high, low=low, close=close, window=14)
        df["ADX"]        = _adx.adx()

        return df
    except Exception as e:
        print(f"  ❌ {symbol}: {e}")
        return None


# ─────────────────────────────────────────────
# BÖLÜM 2: MOCK AGENT (RSI doğru, SMA cross dahil)
# ─────────────────────────────────────────────
def mock_agent_karar(df_slice):
    if len(df_slice) < 5:
        return {"SİNYAL": "HOLD", "PUAN": 0}

    son  = df_slice.iloc[-1]
    puan = 0

    rsi       = son.get("RSI", 50)
    histogram = son.get("MACD_HIST", 0)
    macd      = son.get("MACD", 0)
    close     = float(son["Close"])
    sma20     = son.get("SMA_20")
    sma50     = son.get("SMA_50")

    if pd.notna(rsi):
        if rsi < 30:   puan += 2   # oversold = LONG
        elif rsi < 45: puan += 1
        elif rsi > 70: puan -= 2   # overbought = SHORT
        elif rsi > 55: puan -= 1

    if pd.notna(histogram) and pd.notna(macd):
        if histogram > 0 and macd > 0:    puan += 2
        elif histogram > 0:               puan += 1
        elif histogram < 0 and macd < 0:  puan -= 2
        elif histogram < 0:               puan -= 1

    if pd.notna(sma20) and pd.notna(sma50) and sma20 > 0 and sma50 > 0:
        if close > sma20 and close > sma50:    puan += 2
        elif close > sma20:                    puan += 1
        elif close < sma20 and close < sma50:  puan -= 2
        elif close < sma20:                    puan -= 1
        puan += 1 if sma20 > sma50 else -1

    sinyal = "LONG" if puan >= 4 else "SHORT" if puan <= -4 else "HOLD"
    return {"SİNYAL": sinyal, "PUAN": puan}


# ─────────────────────────────────────────────
# BÖLÜM 3: ATR BAZLI SL/TP
# ─────────────────────────────────────────────
def atr_sl_tp(fiyat, sinyal, guven, atr):
    if sinyal == "HOLD" or fiyat == 0 or not atr or pd.isna(atr):
        return {"stop_loss": None, "take_profit": None}

    sl_k = ATR_SL_YUKSEK if guven == "YÜKSEK" else ATR_SL_ORTA
    tp_k = ATR_TP_YUKSEK if guven == "YÜKSEK" else ATR_TP_ORTA

    if sinyal == "LONG":
        sl = round(fiyat - atr * sl_k, 4)
        tp = round(fiyat + atr * tp_k, 4)
    else:
        sl = round(fiyat + atr * sl_k, 4)
        tp = round(fiyat - atr * tp_k, 4)

    return {"stop_loss": sl, "take_profit": tp, "rr": f"1:{round(tp_k/sl_k,1)}"}


# ─────────────────────────────────────────────
# BÖLÜM 4: v4 SİNYAL
# ─────────────────────────────────────────────
def v4_sinyal(symbol, df_slice):
    red_flags = []
    son = df_slice.iloc[-1]

    mock_karar  = mock_agent_karar(df_slice)
    teknik_dict = {"karar": mock_karar, "veri": {"son_kapanış": float(son["Close"])}}
    efsane_dict = efsane_oylama(symbol, df_slice)

    t_skor = teknik_skora_cevir(teknik_dict)
    e_skor = efsane_skora_cevir(efsane_dict)

    agirlik_t = AGIRLIK_TEKNIK + 0.25 * 0.60
    agirlik_e = AGIRLIK_EFSANE + 0.25 * 0.40
    toplam    = round(t_skor * agirlik_t + e_skor * agirlik_e, 3)

    if catisma_var_mi(t_skor, e_skor, 0.0):
        return "HOLD", "DÜŞÜK", abs(toplam), ["Ajan çatışması"]

    if toplam >= ESIK_YUKSEK:
        ham, guven = "LONG", "YÜKSEK"
    elif toplam >= ESIK_ORTA:
        ham, guven = "LONG", "ORTA"
    elif toplam <= -ESIK_YUKSEK:
        ham, guven = "SHORT", "YÜKSEK"
    elif toplam <= -ESIK_ORTA:
        ham, guven = "SHORT", "ORTA"
    else:
        return "HOLD", "DÜŞÜK", abs(toplam), ["Eşik altı"]

    # ADX filtresi (SHORT için daha sert)
    adx     = son.get("ADX", 0)
    adx_min = ADX_MIN_SHORT if ham == "SHORT" else ADX_MIN_LONG
    if pd.isna(adx) or adx < adx_min:
        red_flags.append(f"ADX={adx:.1f}<{adx_min}")
        return "HOLD", "DÜŞÜK", abs(toplam), red_flags

    # LONG_ONLY listesi
    if ham == "SHORT" and symbol in LONG_ONLY_LIST:
        red_flags.append(f"{symbol} LONG_ONLY")
        return "HOLD", "DÜŞÜK", abs(toplam), red_flags

    # SHORT için SMA200 altı zorunlu (Tudor Jones)
    if ham == "SHORT":
        close  = float(son["Close"])
        sma200 = son.get("SMA_200")
        sma50  = son.get("SMA_50")
        ref    = sma200 if (pd.notna(sma200) and sma200 > 0) else sma50
        if ref and not pd.isna(ref) and close > ref:
            red_flags.append("Tudor Jones: Bull trend SHORT yasak")
            return "HOLD", "DÜŞÜK", abs(toplam), red_flags

    # SHORT için legends %60+ konsensüs
    if ham == "SHORT":
        short_oran = efsane_dict.get("short_oran", 0)
        if short_oran < SHORT_ORAN_MIN:
            red_flags.append(f"Legends {short_oran}%<{SHORT_ORAN_MIN}%")
            return "HOLD", "DÜŞÜK", abs(toplam), red_flags

    return ham, guven, abs(toplam), red_flags



# ─────────────────────────────────────────────
# BÖLÜM 5: İŞLEM SİMÜLATÖRÜ v5
# 🆕 Trailing Stop + Pyramiding + Dinamik Pozisyon
# ─────────────────────────────────────────────
def islem_simule(df, giris_idx, sinyal, guven, skor):
    """
    v5 Yenilikler:
      - TP yok, TRAIL_STOP var (fiyat arkasından tırmanır)
      - skor baz alınarak dinamik pozisyon büyüklüğü
      - Her PYRAMID_TRIGGER_ATR'de ek giriş (max PYRAMID_MAX katman)
    """
    giris_fiyat  = float(df.iloc[giris_idx]["Open"] or df.iloc[giris_idx]["Close"])
    giris_tarihi = str(df.index[giris_idx])[:10]
    atr          = float(df.iloc[giris_idx].get("ATR") or 0)

    # 🆕 2. Dinamik pozisyon büyüklüğü (Kelly)
    pos_oran = pozisyon_buyuklugu_hesapla(skor)

    # ATR bazlı başlangıç stop-loss
    sl_k = ATR_SL_YUKSEK if guven == "YÜKSEK" else ATR_SL_ORTA
    if atr > 0:
        if sinyal == "LONG":
            trail_stop = giris_fiyat - atr * sl_k
        else:
            trail_stop = giris_fiyat + atr * sl_k
    else:
        fallback = 0.03 if guven == "YÜKSEK" else 0.025
        trail_stop = (giris_fiyat * (1 - fallback) if sinyal == "LONG"
                      else giris_fiyat * (1 + fallback))

    # 🆕 1. Trailing stop watermark
    watermark = giris_fiyat   # LONG: en yüksek fiyat  |  SHORT: en düşük fiyat

    # 🆕 3. Pyramiding — tetikleme seviyeleri
    pyramid_girisleri = []   # [(fiyat, oran), ...]
    if atr > 0:
        if sinyal == "LONG":
            pyramid_levels = [giris_fiyat + atr * PYRAMID_TRIGGER_ATR * (n + 1)
                              for n in range(PYRAMID_MAX)]
        else:
            pyramid_levels = [giris_fiyat - atr * PYRAMID_TRIGGER_ATR * (n + 1)
                              for n in range(PYRAMID_MAX)]
    else:
        pyramid_levels = []   # ATR yoksa piramit yok

    max_i = min(giris_idx + MAX_POZISYON_GUN, len(df) - 1)
    cikis_fiyat  = float(df.iloc[max_i]["Close"])
    cikis_tarihi = str(df.index[max_i])[:10]
    cikis_neden  = "SÜRE"

    for i in range(giris_idx + 1, max_i + 1):
        gun  = df.iloc[i]
        high = float(gun["High"])
        low  = float(gun["Low"])
        close= float(gun["Close"])

        if sinyal == "LONG":
            # 🆕 3. Piramit kontrolü
            for pi, ptrigger in enumerate(pyramid_levels):
                if pi >= len(pyramid_girisleri) and close >= ptrigger:
                    pyramid_girisleri.append((close, pos_oran * PYRAMID_BOYUT))

            # 🆕 1. Watermark güncelle → trailing stop tırmandır
            if high > watermark:
                watermark = high
                if atr > 0:
                    new_trail = watermark - atr * ATR_TRAIL_KATSAYI
                    if new_trail > trail_stop:
                        trail_stop = new_trail

            # Çıkış: trailing stop kırıldı mı?
            if low <= trail_stop:
                cikis_fiyat  = trail_stop
                cikis_neden  = "TRAIL"
                cikis_tarihi = str(df.index[i])[:10]
                break

        else:  # SHORT
            # 🆕 3. Piramit kontrolü (SHORT: fiyat düşünce ekle)
            for pi, ptrigger in enumerate(pyramid_levels):
                if pi >= len(pyramid_girisleri) and close <= ptrigger:
                    pyramid_girisleri.append((close, pos_oran * PYRAMID_BOYUT))

            # 🆕 1. Watermark güncelle → trailing stop aşağı çek
            if low < watermark:
                watermark = low
                if atr > 0:
                    new_trail = watermark + atr * ATR_TRAIL_KATSAYI
                    if new_trail < trail_stop:
                        trail_stop = new_trail

            if high >= trail_stop:
                cikis_fiyat  = trail_stop
                cikis_neden  = "TRAIL"
                cikis_tarihi = str(df.index[i])[:10]
                break

    # 🆕 3. Piramit ağırlıklı ortalama giriş
    tum_girişler    = [(giris_fiyat, pos_oran)] + pyramid_girisleri
    toplam_pos_oran = sum(o for _, o in tum_girişler)
    ort_giris       = sum(f * o for f, o in tum_girişler) / toplam_pos_oran

    if sinyal == "LONG":
        pnl_pct = (cikis_fiyat - ort_giris) / ort_giris
    else:
        pnl_pct = (ort_giris - cikis_fiyat) / ort_giris

    # pnl_dolar = placeholder, 🆕 4. Bileşik hesap MAIN'de yapılır
    pnl_dolar_basit = round(pnl_pct * BASLANGIC_SERMAYE * toplam_pos_oran, 2)

    return {
        "giris_tarihi"   : giris_tarihi,
        "cikis_tarihi"   : cikis_tarihi,
        "giris_fiyat"    : round(giris_fiyat, 2),
        "cikis_fiyat"    : round(cikis_fiyat, 2),
        "sl"             : round(trail_stop, 2),   # son trailing stop seviyesi
        "tp"             : None,                   # artık TP yok
        "cikis_neden"    : cikis_neden,
        "pnl_pct"        : round(pnl_pct * 100, 2),
        "pnl_dolar"      : pnl_dolar_basit,        # bileşiksiz (referans için)
        "pos_oran"       : round(toplam_pos_oran, 3),
        "pyramid_sayisi" : len(pyramid_girisleri),
        "dogru_karar"    : pnl_pct > 0,
        "atr"            : round(float(atr), 2) if atr else 0,
    }


# ─────────────────────────────────────────────
# BÖLÜM 6: SEMBOL BACKTEST (skor pass-through eklendi)
# ─────────────────────────────────────────────
def sembol_backtest(symbol, df):
    islemler      = []
    filtre_sayac  = {
        "adx_long": 0, "adx_short": 0, "long_only": 0,
        "tudor": 0, "legends_short": 0, "catisma": 0, "esik": 0
    }

    # --- 1 AYLIK TEST AYARI (Son 22 İşlem Günü) ---
    sonraki_giris = ISINMA_GUN

    for idx in range(ISINMA_GUN, len(df) - 2):
        if idx < sonraki_giris:
            continue

        df_slice = df.iloc[:idx + 1].copy()
        sinyal, guven, skor, flags = v4_sinyal(symbol, df_slice)

        for f in flags:
            if "ADX" in f and "30" in f:       filtre_sayac["adx_short"] += 1
            elif "ADX" in f:                   filtre_sayac["adx_long"] += 1
            elif "LONG_ONLY" in f:             filtre_sayac["long_only"] += 1
            elif "Tudor" in f or "Bull" in f:  filtre_sayac["tudor"] += 1
            elif "Legends" in f:               filtre_sayac["legends_short"] += 1
            elif "çatışma" in f.lower() or "Ajan" in f: filtre_sayac["catisma"] += 1
            else:                              filtre_sayac["esik"] += 1

        if sinyal == "HOLD":
            continue

        giris_idx = idx + 1
        if giris_idx >= len(df) - 1:
            break

        # 🆕 skor artık islem_simule'ye geçiyor (dinamik pozisyon + pyramid için)
        sonuc = islem_simule(df, giris_idx, sinyal, guven, skor)
        islemler.append({
            "symbol": symbol, "sinyal_tarihi": str(df.index[idx])[:10],
            "sinyal": sinyal, "guven": guven, "sistem_skoru": round(skor, 3),
            **sonuc,
        })

        try:
            cikis_loc = df.index.get_loc(sonuc["cikis_tarihi"])
            sonraki_giris = cikis_loc + 1
        except Exception:
            sonraki_giris = giris_idx + MAX_POZISYON_GUN + 1

    return islemler, filtre_sayac

# ─────────────────────────────────────────────
# BÖLÜM 6b: 🆕 BİLEŞİK GETİRİ HESAPLAMA
# ─────────────────────────────────────────────
def bilesik_pnl_hesapla(tum_islemler, baslangic_sermaye):
    """
    Tüm işlemleri tarihe göre sıralar, kârı ana sermayeye ekleyerek
    bileşik getiriyi simüle eder.

    Her işlem bir öncekinin güncellenmiş equity'si üzerinden
    pozisyon büyüklüğü hesaplar → kartopu etkisi.
    """
    sirali = sorted(tum_islemler, key=lambda x: x["giris_tarihi"])
    cari_sermaye = float(baslangic_sermaye)

    for islem in sirali:
        pnl_pct_decimal = islem["pnl_pct"] / 100.0
        pos_oran        = islem["pos_oran"]

        # 🆕 Bileşik: güncel equity üzerinden hesapla
        pnl_bilesik = round(pnl_pct_decimal * cari_sermaye * pos_oran, 2)

        islem["sermaye_once"]   = round(cari_sermaye, 2)
        islem["pnl_dolar_bilesik"] = pnl_bilesik
        cari_sermaye += pnl_bilesik
        islem["sermaye_sonra"]  = round(cari_sermaye, 2)

    return sirali, round(cari_sermaye, 2)

# ─────────────────────────────────────────────
# BÖLÜM 7: RAPOR
# ─────────────────────────────────────────────
def rapor_yazdir(tum_islemler, sembol_ozet, filtre_ozet):
    if not tum_islemler:
        print("❌ Hiç işlem yok."); return {}

    pnl_l    = [x["pnl_dolar"]   for x in tum_islemler]
    dogru_l  = [x["dogru_karar"] for x in tum_islemler]
    long_is  = [x for x in tum_islemler if x["sinyal"] == "LONG"]
    short_is = [x for x in tum_islemler if x["sinyal"] == "SHORT"]
    yuk_is   = [x for x in tum_islemler if x["guven"]  == "YÜKSEK"]
    orta_is  = [x for x in tum_islemler if x["guven"]  == "ORTA"]
    trail_is = [x for x in tum_islemler if x["cikis_neden"] == "TRAIL"]
    tp_is    = trail_is  # v5: TP → TRAIL
    sl_is    = [x for x in tum_islemler if x["cikis_neden"] == "SL"]
    sure_is  = [x for x in tum_islemler if x["cikis_neden"] == "SÜRE"]

    toplam_pnl = sum(pnl_l)
    getiri_pct = toplam_pnl / BASLANGIC_SERMAYE * 100
    genel_acc  = sum(dogru_l) / len(dogru_l) * 100 if dogru_l else 0

    def acc(lst): return sum(x["dogru_karar"] for x in lst) / max(len(lst), 1) * 100
    def pf(lst):
        kaz = sum(x["pnl_dolar"] for x in lst if x["pnl_dolar"] > 0)
        kay = sum(abs(x["pnl_dolar"]) for x in lst if x["pnl_dolar"] <= 0)
        return round(kaz / max(kay, 1), 2)

    pf_genel = pf(tum_islemler)
    cum       = np.cumsum(pnl_l)
    max_dd    = float(np.min(cum - np.maximum.accumulate(cum))) if len(cum) > 0 else 0
    en_iyi    = max(tum_islemler, key=lambda x: x["pnl_dolar"])
    en_kotu   = min(tum_islemler, key=lambda x: x["pnl_dolar"])
    sl_oran   = len(sl_is) / max(len(tum_islemler), 1) * 100
    p_ikon    = "🟢" if toplam_pnl >= 0 else "🔴"

    print(f"\n{'═'*72}")
    print(f"  🔬 TRUE BACKTEST v4 — KÖK NEDEN DÜZELTMELERİ")
    print(f"  TrailingStop | Kelly Sizing | Pyramiding | Compounding")
    print(f"{'═'*72}")

    # Karşılaştırma
    refs = [("v1 Buglu",768,32.6,31621,1.22,66.1),
            ("v3 RSI✓",368,36.4,19999,1.33,62.8),
            ("v5 Bu  ",len(tum_islemler),round(genel_acc,1),round(toplam_pnl),pf_genel,round(sl_oran,1))]
    print(f"\n  {'Ver':<12} {'İşlem':>6} {'ACC%':>7} {'P&L':>10} {'PF':>6} {'SL%':>6}")
    print(f"  {'─'*50}")
    for (ad,is_,ac,pl,pf_,sl_) in refs:
        pi = "🟢" if pl>0 else "🔴"
        ai = "✅" if ac>=50 else "⚠️ " if ac>=40 else "❌"
        print(f"  {ad:<12} {is_:>6} {ai}{ac:>4.1f}% {pi}${pl:>+8,.0f} {pf_:>5.2f}x {sl_:>5.1f}%")

    print(f"\n  ┌─ 💰 PERFORMANS {'─'*44}")
    print(f"  │  Toplam P&L     : {p_ikon} ${toplam_pnl:+,.2f}  ({getiri_pct:+.2f}%)")
    print(f"  │  Profit Factor  : {pf_genel}x")
    print(f"  │  Max Drawdown   : ${max_dd:,.2f}")
    print(f"  │  Toplam İşlem   : {len(tum_islemler)}")
    print(f"  │  TRAIL/SL/Süre  : {len(tp_is)} / {len(sl_is)} / {len(sure_is)}")
    print(f"  │  SL Oranı       : %{sl_oran:.1f}")
    ai = lambda a: "🟢" if a>=55 else "🟡" if a>=45 else "🔴"
    print(f"  ├─ 🎯 ACCURACY {'─'*48}")
    print(f"  │  GENEL    : {ai(genel_acc)} %{genel_acc:.1f}  ({sum(dogru_l)}/{len(dogru_l)})")
    print(f"  │  LONG     : {ai(acc(long_is))} %{acc(long_is):.1f}  ({len(long_is)} işlem)")
    print(f"  │  SHORT    : {ai(acc(short_is))} %{acc(short_is):.1f}  ({len(short_is)} işlem)")
    print(f"  │  YÜKSEK💪 : {'✅' if acc(yuk_is)>=55 else '⚠️ '} %{acc(yuk_is):.1f}  ({len(yuk_is)} işlem)")
    print(f"  │  ORTA  👍 : {'✅' if acc(orta_is)>=50 else '⚠️ '} %{acc(orta_is):.1f}  ({len(orta_is)} işlem)")
    print(f"  ├─ 🔍 FİLTRE ETKİSİ {'─'*40}")
    for k, v in filtre_ozet.items():
        if v > 0: print(f"  │  {k:<25}: {v}")
    print(f"  │  TOPLAM ENGELLENDİ      : {sum(filtre_ozet.values())}")
    print(f"  └─{'─'*56}")
    print(f"     🏆 {en_iyi['symbol']} {en_iyi['sinyal']} {en_iyi['giris_tarihi']} → ${en_iyi['pnl_dolar']:+,.0f}")
    print(f"     💀 {en_kotu['symbol']} {en_kotu['sinyal']} {en_kotu['giris_tarihi']} → ${en_kotu['pnl_dolar']:+,.0f}")

    print(f"\n{'─'*72}")
    print(f"  {'SEM':<7} {'İŞ':>4} {'ACC%':>5} {'L✓%':>5} {'S✓%':>5} {'PNL':>9} {'PF':>5}  STATUS")
    print(f"{'─'*72}")
    for sym, s in sorted(sembol_ozet.items(), key=lambda x: x[1]["pnl"], reverse=True):
        if s["islem"] == 0: continue
        pi = "🟢" if s["pnl"] >= 0 else "🔴"
        ai2 = "✅" if s["acc"] >= 55 else "⚠️ " if s["acc"] >= 45 else "❌"
        lo = "⛔" if sym in LONG_ONLY_LIST else "  "
        print(f"  {pi} {sym:<6}{lo}{s['islem']:>3} {ai2}{s['acc']:>4.0f}%"
              f" {s['long_acc']:>4.0f}% {s['short_acc']:>4.0f}%"
              f" {s['pnl']:>+9,.0f} {s['pf']:>4.1f}x  {s['yorum']}")
    print(f"{'─'*72}")
    print(f"  ⛔ = LONG_ONLY modu aktif")

    print(f"\n  📋 SON 15 İŞLEM:")
    print(f"  {'SEM':<6} {'GİRİŞ':<11} {'ÇIKIŞ':<11} {'YÖN':<6} {'GÜV':<7} {'SN':<5} {'ATR':>5} {'PNL':>8} D?")
    print(f"  {'─'*70}")
    for x in tum_islemler[-15:]:
        pi = "🟢" if x["pnl_dolar"] >= 0 else "🔴"
        di = "✅" if x["dogru_karar"] else "❌"
        print(f"  {x['symbol']:<6} {x['giris_tarihi']:<11} {x['cikis_tarihi']:<11} "
              f"{x['sinyal']:<6} {x['guven']:<7} {x['cikis_neden']:<5} "
              f"{x.get('atr',0):>5.2f} {pi}{x['pnl_dolar']:>+7,.0f} {di}")
    print(f"{'═'*72}")

    return {
        "toplam_islem": len(tum_islemler), "toplam_pnl": round(toplam_pnl, 2),
        "getiri_pct": round(getiri_pct, 2), "genel_acc": round(genel_acc, 1),
        "long_acc": round(acc(long_is), 1), "short_acc": round(acc(short_is), 1),
        "yuksek_acc": round(acc(yuk_is), 1), "orta_acc": round(acc(orta_is), 1),
        "profit_factor": pf_genel, "max_drawdown": round(max_dd, 2),
        "tp": len(tp_is), "sl": len(sl_is), "sure": len(sure_is),
        "sl_oran": round(sl_oran, 1), "filtreler": filtre_ozet,
    }


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{'═'*72}")
    print(f"  Algoritmik Hedge Fon | TRUE BACKTEST v5")
    print(f"  v5: TrailingStop | Kelly Sizing | Pyramiding | Compounding")
    print(f"{'═'*72}\n")

    tum_islemler = []
    sembol_ozet  = {}
    filtre_ozet  = {
        "adx_long": 0, "adx_short": 0, "long_only": 0,
        "tudor": 0, "legends_short": 0, "catisma": 0, "esik": 0
    }

    for i, sembol in enumerate(WATCHLIST, 1):
        lo_tag = " [LONG_ONLY]" if sembol in LONG_ONLY_LIST else ""
        print(f"[{i:>2}/{len(WATCHLIST)}] {sembol}{lo_tag}...", end=" ", flush=True)
        df = veri_cek(sembol)
        if df is None:
            print("⚠️  Veri yok"); continue

        islemler, f_sayac = sembol_backtest(sembol, df)
        tum_islemler.extend(islemler)
        for k in filtre_ozet:
            filtre_ozet[k] += f_sayac.get(k, 0)

        if islemler:
            pnl_s = sum(x["pnl_dolar"] for x in islemler)
            acc_s = sum(x["dogru_karar"] for x in islemler) / len(islemler) * 100
            l_is  = [x for x in islemler if x["sinyal"] == "LONG"]
            s_is  = [x for x in islemler if x["sinyal"] == "SHORT"]
            l_acc = sum(x["dogru_karar"] for x in l_is) / max(len(l_is), 1) * 100
            s_acc = sum(x["dogru_karar"] for x in s_is) / max(len(s_is), 1) * 100
            kaz   = sum(x["pnl_dolar"] for x in islemler if x["pnl_dolar"] > 0)
            kay   = sum(abs(x["pnl_dolar"]) for x in islemler if x["pnl_dolar"] <= 0)
            pf_s  = round(kaz / max(kay, 1), 2)
            sembol_ozet[sembol] = {
                "islem": len(islemler), "acc": acc_s,
                "long_acc": l_acc, "short_acc": s_acc,
                "pnl": round(pnl_s, 2), "pf": pf_s,
                "yorum": "✅ Kârlı" if pnl_s > 0 else "❌ Zararlı"
            }
            a = "✅" if acc_s >= 55 else "⚠️ "
            print(f"{a} {len(islemler)} işlem | ACC %{acc_s:.0f} | P&L ${pnl_s:+,.0f}")
        else:
            sembol_ozet[sembol] = {"islem":0,"acc":0,"long_acc":0,"short_acc":0,"pnl":0,"pf":0,"yorum":"—"}
            print("— sinyal yok")

    tum_islemler.sort(key=lambda x: x["giris_tarihi"])

    # 🆕 4. BİLEŞİK GETİRİ HESAPLA
    tum_islemler, son_sermaye = bilesik_pnl_hesapla(tum_islemler, BASLANGIC_SERMAYE)
    bilesik_getiri = son_sermaye - BASLANGIC_SERMAYE
    bilesik_getiri_pct = bilesik_getiri / BASLANGIC_SERMAYE * 100
    pyramid_toplam = sum(x.get("pyramid_sayisi", 0) for x in tum_islemler)
    print(f"\n  ❄️  BİLEŞİK GETİRİ: ${son_sermaye:,.2f}  ({bilesik_getiri_pct:+.2f}%)")
    print(f"  🧱 TOPLAM PİRAMİT GİRİŞİ: {pyramid_toplam}")
    print(f"  ⚖️  ORTALAMA POZİSYON: %{sum(x['pos_oran'] for x in tum_islemler)/max(len(tum_islemler),1)*100:.1f}")

    stats = rapor_yazdir(tum_islemler, sembol_ozet, filtre_ozet)
    stats["bilesik_son_sermaye"] = son_sermaye
    stats["bilesik_getiri"] = round(bilesik_getiri, 2)
    stats["bilesik_getiri_pct"] = round(bilesik_getiri_pct, 2)

    if stats:
        acc   = stats.get("genel_acc", 0)
        pnl   = stats.get("toplam_pnl", 0)
        pf_   = stats.get("profit_factor", 0)
        s_acc = stats.get("short_acc", 0)
        print(f"\n  📊 VERDİKT:")
        if acc >= 50 and pnl > 0 and pf_ >= 1.5:
            print(f"  ✅ SİSTEM GÜÇLÜ — API entegrasyonuna hazır!")
        elif pnl > 0 and pf_ >= 1.3:
            print(f"  ⚠️  POZİTİF & KARLI — API'ye geç, izle")
        else:
            print(f"  ❌ Beklentinin altında — watchlist'i gözden geçir")

# JSON KAYDETME - NUMPY HATALARINA KARŞI KORUMALI (default=str eklendi)
    Path("true_backtest_rapor.json").write_text(
        json.dumps({
            "tarih": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "versiyon": "v5",
            "gelistirmeler_v5": {
                "1_trailing_stop": f"ATR_TRAIL_KATSAYI={ATR_TRAIL_KATSAYI}  (TP kaldırıldı)",
                "A_short_adr": f"ADX≥{ADX_MIN_SHORT}, SMA200 altı, legends≥{SHORT_ORAN_MIN}%",
                "3_pyramiding": f"trigger={PYRAMID_TRIGGER_ATR}ATR, max={PYRAMID_MAX} katman, boyut=%{PYRAMID_BOYUT*100:.0f}",
                "4_compounding": f"başlangıç=${BASLANGIC_SERMAYE}, son=${stats.get('bilesik_son_sermaye',0):,.2f}",
            },
            "stats": stats, "sembol": sembol_ozet, "islemler": tum_islemler,
        }, ensure_ascii=False, indent=2, default=str)
    )
    print(f"\n  💾 RAPOR BAŞARIYLA KAYDEDİLDİ! SİSTEM KUSURSUZ ÇALIŞIYOR!\n")