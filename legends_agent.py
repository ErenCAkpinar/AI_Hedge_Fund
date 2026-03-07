"""
legends_agent.py
================
Algoritmik Hedge Fon — Efsane Trader & Yarışma Şampiyonu Stratejileri
DAG: Teknik Veri → 8 Efsane Strateji → Oylama → Konsensüs Karar

EFSANELER:
    1. Stanley Druckenmiller — Asimetrik Momentum (Makro Efsane)
    2. Paul Tudor Jones      — 200 SMA Trend Filtresi (Risk Yönetimi Ustası)
    3. Jesse Livermore       — Pivot Kırılım (Wall Street'in En Büyük Spekülcüsü)
    4. Richard Dennis        — Turtle Breakout + ATR Pozisyon (Trend Takip Babası)
    5. Michael Burry         — Contrarian Dip Avcısı (The Big Short)
    6. George Soros          — Refleksivite + Momentum Kırılımı (Piyasayı Kıran Adam)

YARI ŞAMPİYONLARI:
    7. Larry Williams        — Williams %R Momentum (1987 WCTC Şampiyonu, %11.376 getiri)
    8. Andrea Unger         — Sistematik Volatilite Breakout (4 kez WCTC Şampiyonu)

API GEREKTİRMEZ — Tüm hesaplamalar yfinance + ta ile yapılır.
Pazartesi: Her efsanein kararına AI gerekçesi eklenecek (Gemini Flash).
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
    "NVDA", "TSLA", "TSM", "ASTS",
    "VST", "LMT", "JPM", "LLY",
    "MSTR", "PLTR", "FXY"
]
PERIOD   = "2y"    # 2 yıl — SMA_200 için en az 200 gün şart
INTERVAL = "1d"

# Her efsanenin oy ağırlığı (toplam 100)
AGIRLIKLAR = {
    "druckenmiller" : 20,   # Makro momentum — swing için en uygun
    "tudor_jones"   : 18,   # Trend filtresi — hata önleyici
    "livermore"     : 15,   # Kırılım sinyali — giriş zamanlaması
    "dennis_turtle" : 12,   # ATR breakout — volatilite bazlı
    "burry"         : 10,   # Contrarian — dip yakalama
    "soros"         : 10,   # Refleksivite — momentum kırılımı
    "larry_williams": 8,    # Williams %R — kısa vadeli timing
    "andrea_unger"  : 7,    # Sistematik breakout — konfirmasyon
}

# ─────────────────────────────────────────────
# BÖLÜM 1: VERİ ÇEKİMİ VE ZENGİNLEŞTİRME
# ─────────────────────────────────────────────
def veri_cek(symbol: str) -> pd.DataFrame | None:
    """6 aylık günlük veriyi çeker ve tüm indikatörleri hesaplar."""
    try:
        df = yf.Ticker(symbol).history(period=PERIOD, interval=INTERVAL)
        if df.empty or len(df) < 30:
            return None

        close  = df["Close"]
        high   = df["High"]
        low    = df["Low"]
        volume = df["Volume"]

        # Temel indikatörler
        df["RSI"]      = RSIIndicator(close=close, window=14).rsi()
        df["SMA_20"]   = SMAIndicator(close=close, window=20).sma_indicator()
        df["SMA_50"]   = SMAIndicator(close=close, window=50).sma_indicator()
        df["SMA_200"]  = SMAIndicator(close=close, window=200).sma_indicator()  # PTJ için
        df["EMA_20"]   = EMAIndicator(close=close, window=20).ema_indicator()

        # MACD
        _macd          = MACD(close=close)
        df["MACD"]     = _macd.macd()
        df["MACD_SIG"] = _macd.macd_signal()
        df["MACD_HIST"]= _macd.macd_diff()

        # ATR (Dennis Turtle için)
        df["ATR"]      = AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range()

        # Bollinger Bands (Soros için)
        _bb            = BollingerBands(close=close, window=20, window_dev=2)
        df["BB_HIGH"]  = _bb.bollinger_hband()
        df["BB_LOW"]   = _bb.bollinger_lband()
        df["BB_MID"]   = _bb.bollinger_mavg()

        # Williams %R (Larry Williams için)
        df["WILLIAMS_R"] = WilliamsRIndicator(high=high, low=low, close=close, lbp=14).williams_r()

        # Pivotlar (Livermore için) — 20 günlük high/low
        df["HIGH_20"]  = high.rolling(20).max()
        df["LOW_20"]   = low.rolling(20).min()

        # Hacim ortalaması
        df["VOL_AVG"]  = volume.rolling(20).mean()

        return df.dropna(subset=["RSI", "MACD", "ATR", "WILLIAMS_R", "BB_HIGH", "HIGH_20"]).copy()
    except Exception as e:
        print(f"  ❌ {symbol} veri hatası: {e}")
        return None


# ─────────────────────────────────────────────
# BÖLÜM 2: EFSANE STRATEJİLER
# Her fonksiyon: veri alır → (sinyal, skor, gerekçe) döndürür
# sinyal: 1=LONG, -1=SHORT, 0=HOLD
# skor: 0.0 ile 1.0 (güven seviyesi)
# ─────────────────────────────────────────────

def strateji_druckenmiller(df: pd.DataFrame) -> tuple[int, float, str]:
    """
    Stanley Druckenmiller — Asimetrik Momentum
    Kural: Momentum yeni başlıyorsa büyük bahis.
    'Ben hiçbir zaman tahmin etmem. Ben tepki veririm.'
    """
    last = df.iloc[-1]
    prev = df.iloc[-2]
    prev3 = df.iloc[-5]  # 5 gün öncesi

    # RSI yükseliyor mu? (momentum ivmesi)
    rsi_yukseliyor = last["RSI"] > prev["RSI"] > prev3["RSI"]
    # MACD histogram pozitife döndü mü?
    macd_donus = last["MACD_HIST"] > 0 and prev["MACD_HIST"] < 0
    macd_guclu = last["MACD_HIST"] > 0 and last["MACD_HIST"] > prev["MACD_HIST"]
    # Hacim konfirmasyonu
    hacim_guclu = df.iloc[-1]["Volume"] > last["VOL_AVG"] * 1.3

    # LONG koşulu
    if rsi_yukseliyor and macd_donus and last["RSI"] < 65:
        return 1, 0.90, "MACD pozitife döndü, RSI ivme kazanıyor — momentum başlangıcı"
    if macd_guclu and hacim_guclu and last["RSI"] < 60:
        return 1, 0.75, "Güçlü hacim + MACD histogramı artıyor — asimetrik fırsat"

    # SHORT koşulu
    rsi_dusuyor = last["RSI"] < prev["RSI"] < prev3["RSI"]
    macd_negatif_donus = last["MACD_HIST"] < 0 and prev["MACD_HIST"] > 0
    if rsi_dusuyor and macd_negatif_donus:
        return -1, 0.85, "MACD negatife döndü, RSI zayıflıyor — momentum kaybı"

    return 0, 0.50, "Net asimetrik fırsat yok, bekleme"


def strateji_tudor_jones(df: pd.DataFrame) -> tuple[int, float, str]:
    """
    Paul Tudor Jones — 200 SMA Kuralı
    'Ben asla trendin tersine pozisyon almam.'
    '200 günlük ortalama altında long açmak kendini satmaktır.'
    """
    last = df.iloc[-1]

    # SMA200 varsa kullan, yoksa SMA50 ile yetин
    if pd.notna(last["SMA_200"]) and last["SMA_200"] > 0:
        ana_trend_yukari = last["Close"] > last["SMA_200"]
        sma_ref = last["SMA_200"]
        uzaklik = abs(last["Close"] - sma_ref) / sma_ref * 100
    else:
        # SMA200 için yeterli veri yoksa SMA50 kullan
        ana_trend_yukari = last["Close"] > last["SMA_50"]
        sma_ref = last["SMA_50"]
        uzaklik = abs(last["Close"] - sma_ref) / sma_ref * 100

    pullback_long  = ana_trend_yukari and last["Close"] < last["SMA_20"]   # Trend içi dip
    breakdown_short = not ana_trend_yukari and last["Close"] > last["SMA_20"]  # Dead cat bounce

    if ana_trend_yukari:
        if pullback_long:
            return 1, 0.85, f"Ana trend yukarı (SMA ref üstünde), pullback dip fırsatı"
        return 1, 0.65, f"Ana trend yukarı — yalnızca LONG tarafı geçerli"
    else:
        if breakdown_short:
            return -1, 0.85, f"Ana trend aşağı, dead cat bounce — SHORT fırsatı"
        return -1, 0.65, f"Ana trend aşağı — yalnızca SHORT tarafı geçerli"


def strateji_livermore(df: pd.DataFrame) -> tuple[int, float, str]:
    """
    Jesse Livermore — Pivot Kırılım
    'Gerçek para büyük hamlelerle kazanılır, küçük dalgalanmalarla değil.'
    Kural: 20 günlük high kırılınca gir, low kırılınca çık.
    """
    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Pivot kırılımı (dünkü high/low değil, 20 günlük)
    yukari_kirilim = (prev["Close"] < prev["HIGH_20"] and
                      last["Close"] > last["HIGH_20"] * 0.995)  # %0.5 tolerans
    asagi_kirilim  = (prev["Close"] > prev["LOW_20"] and
                      last["Close"] < last["LOW_20"] * 1.005)

    # Hacim konfirmasyonu şart (Livermore hacmi çok önemser)
    hacim_konfirm = last["Volume"] > last["VOL_AVG"] * 1.2

    if yukari_kirilim and hacim_konfirm:
        return 1, 0.88, f"20 günlük pivot high kırıldı, hacim {last['Volume']/last['VOL_AVG']:.1f}x — breakout long"
    if yukari_kirilim:
        return 1, 0.60, "Pivot kırılımı var ama hacim zayıf — dikkatli long"
    if asagi_kirilim and hacim_konfirm:
        return -1, 0.88, f"20 günlük pivot low kırıldı, hacim {last['Volume']/last['VOL_AVG']:.1f}x — breakout short"
    if asagi_kirilim:
        return -1, 0.60, "Pivot düşüş var ama hacim zayıf — dikkatli short"

    return 0, 0.50, "Pivot kırılımı yok, konsolidasyon"


def strateji_dennis_turtle(df: pd.DataFrame) -> tuple[int, float, str]:
    """
    Richard Dennis — Turtle Trading (Breakout + ATR Pozisyon)
    'Trend takip ederek tutarlı kazanç mümkün.'
    Kural: 20 günlük breakout + ATR ile pozisyon boyutu.
    """
    last = df.iloc[-1]
    prev = df.iloc[-2]

    atr = last["ATR"]
    atr_yuzde = (atr / last["Close"]) * 100  # ATR/Fiyat oranı

    # 20 günlük breakout
    long_entry  = last["Close"] > last["HIGH_20"] and prev["Close"] <= prev["HIGH_20"]
    short_entry = last["Close"] < last["LOW_20"]  and prev["Close"] >= prev["LOW_20"]

    # ATR bazlı stop önerisi (Dennis: 2 ATR stop kullanırdı)
    stop_uzaklik = round(atr * 2, 2)

    if long_entry:
        return 1, 0.82, f"Turtle LONG: 20g high kırıldı, ATR={atr:.2f}, stop önerisi: -{stop_uzaklik}"
    if short_entry:
        return -1, 0.82, f"Turtle SHORT: 20g low kırıldı, ATR={atr:.2f}, stop önerisi: +{stop_uzaklik}"

    # Trend içinde ek pozisyon (Dennis sistemi) — mevcut trend güçlüyse devam
    if last["Close"] > last["SMA_50"] and last["MACD_HIST"] > 0:
        return 1, 0.55, f"Trend içi — mevcut LONG pozisyonu güçlendirilebilir (ATR: {atr:.2f})"
    if last["Close"] < last["SMA_50"] and last["MACD_HIST"] < 0:
        return -1, 0.55, f"Trend içi — mevcut SHORT pozisyonu güçlendirilebilir (ATR: {atr:.2f})"

    return 0, 0.40, f"Breakout yok, ATR={atr:.2f} ile konsolidasyon"


def strateji_burry(df: pd.DataFrame) -> tuple[int, float, str]:
    """
    Michael Burry — Contrarian Dip Avcısı
    'Herkes satarken ben alırım. Paniği görünce araştırırım.'
    Kural: Aşırı satım + panik hacmi + temel kötü ama fiyat daha kötü.
    """
    last  = df.iloc[-1]
    prev  = df.iloc[-2]
    prev5 = df.iloc[-5]

    rsi_asiri_satim = last["RSI"] < 32
    rsi_dip_yapiyor = last["RSI"] > prev["RSI"]  # RSI toparlanıyor
    panik_hacmi     = last["Volume"] > last["VOL_AVG"] * 2.0  # 2x hacim spike
    fiyat_dip       = last["Close"] < last["BB_LOW"]  # Bollinger alt bandının altında
    momentum_donuyor= last["MACD_HIST"] > prev["MACD_HIST"]  # Histogram artıyor

    # Contrarian LONG
    if rsi_asiri_satim and rsi_dip_yapiyor and panik_hacmi:
        return 1, 0.88, f"BURRY: RSI {last['RSI']:.1f} aşırı satım + panik hacmi {last['Volume']/last['VOL_AVG']:.1f}x — contrarian dip"
    if rsi_asiri_satim and fiyat_dip and momentum_donuyor:
        return 1, 0.80, f"BURRY: BB altı + RSI {last['RSI']:.1f} + histogram toparlanıyor — dip fırsatı"

    # Aşırı alım SHORT (Burry'nin Big Short mantığı)
    rsi_asiri_alim = last["RSI"] > 78
    balon_isaretleri = (last["Close"] > last["BB_HIGH"] and
                        last["Volume"] < last["VOL_AVG"] * 0.8)  # Düşük hacimle yükseliş

    if rsi_asiri_alim and balon_isaretleri:
        return -1, 0.85, f"BURRY: RSI {last['RSI']:.1f} + düşük hacimli yükseliş — balon sinyali, SHORT"

    return 0, 0.45, "Burry: Net contrarian fırsat yok"


def strateji_soros(df: pd.DataFrame) -> tuple[int, float, str]:
    """
    George Soros — Refleksivite Teorisi
    'Piyasalar her zaman yanlıştır. Ben bu yanlışlığı istismar ederim.'
    Kural: Trendin kendi kendini besleyip beslemediğini kontrol et.
    Güçlenen trend daha da güçlenir (refleksif döngü).
    """
    last  = df.iloc[-1]
    prev  = df.iloc[-2]
    prev3 = df.iloc[-3]

    # Refleksif yükseliş döngüsü:
    # Fiyat yükseldikçe alım artıyor, alım arttıkça fiyat yükseliyor
    fiyat_serisi = [df.iloc[-5]["Close"], df.iloc[-4]["Close"],
                    df.iloc[-3]["Close"], prev["Close"], last["Close"]]
    sure_yukari  = all(fiyat_serisi[i] < fiyat_serisi[i+1] for i in range(len(fiyat_serisi)-1))
    sure_asagi   = all(fiyat_serisi[i] > fiyat_serisi[i+1] for i in range(len(fiyat_serisi)-1))

    hacim_artisi = (df.iloc[-1]["Volume"] > df.iloc[-3]["Volume"] * 1.2)
    momentum_guclu = last["RSI"] > 55 and last["MACD_HIST"] > 0

    # Refleksif kırılım (trend bozuluyor)
    trend_bozuluyor_long  = (df.iloc[-3]["Close"] > df.iloc[-4]["Close"] and
                             prev["Close"] < df.iloc[-3]["Close"] and
                             last["Close"] < prev["Close"])  # 3 gün yukarı, son 2 gün aşağı

    # Refleksif momentum devamı
    if sure_yukari and hacim_artisi:
        return 1, 0.85, "SOROS: 5 günlük sürekli yükseliş + artan hacim — refleksif LONG döngüsü"
    if sure_asagi and hacim_artisi:
        return -1, 0.85, "SOROS: 5 günlük sürekli düşüş + artan hacim — refleksif SHORT döngüsü"
    if momentum_guclu and last["Close"] > last["SMA_20"]:
        return 1, 0.65, "SOROS: Momentum güçlü, trend kendini besliyor — LONG devam"
    if trend_bozuluyor_long:
        return -1, 0.70, "SOROS: Yukarı trend kırılıyor — refleksif döngü bitti, SHORT"

    return 0, 0.45, "Soros: Refleksif döngü tespit edilemedi"


def strateji_larry_williams(df: pd.DataFrame) -> tuple[int, float, str]:
    """
    Larry Williams — Williams %R Momentum
    1987 WCTC Şampiyonu: $10.000'i $1.147.607'ye çıkardı (%11.376 getiri)
    Kural: Williams %R aşırı bölgeden çıkınca gir.
    """
    last  = df.iloc[-1]
    prev  = df.iloc[-2]
    prev3 = df.iloc[-3]

    wr = last["WILLIAMS_R"]
    wr_prev = prev["WILLIAMS_R"]

    # Williams %R: -100 ile 0 arasında
    # -80 altı = aşırı satım, -20 üstü = aşırı alım
    asiri_satimdan_cikis = (wr_prev < -80 and wr > -80)   # Aşırı satımdan çıkış
    asiri_alimdan_cikis  = (wr_prev > -20 and wr < -20)   # Aşırı alımdan çıkış

    # 3 günlük momentum konfirmasyonu
    momentum_yukari = (last["Close"] > prev["Close"] > prev3["Close"])
    momentum_asagi  = (last["Close"] < prev["Close"] < prev3["Close"])

    if asiri_satimdan_cikis and momentum_yukari:
        return 1, 0.90, f"WILLIAMS: %R {wr:.1f} aşırı satımdan çıkış + 3g momentum — güçlü LONG sinyali"
    if asiri_satimdan_cikis:
        return 1, 0.72, f"WILLIAMS: %R {wr:.1f} aşırı satımdan çıkış — LONG"
    if asiri_alimdan_cikis and momentum_asagi:
        return -1, 0.90, f"WILLIAMS: %R {wr:.1f} aşırı alımdan çıkış + 3g momentum — güçlü SHORT sinyali"
    if asiri_alimdan_cikis:
        return -1, 0.72, f"WILLIAMS: %R {wr:.1f} aşırı alımdan çıkış — SHORT"

    # Orta bölge: trend takip
    if -50 < wr < -20 and last["MACD_HIST"] > 0:
        return 1, 0.55, f"WILLIAMS: %R {wr:.1f} orta-güçlü bölge, momentum pozitif"
    if -80 < wr < -50 and last["MACD_HIST"] < 0:
        return -1, 0.55, f"WILLIAMS: %R {wr:.1f} orta-zayıf bölge, momentum negatif"

    return 0, 0.40, f"Williams: %R {wr:.1f} — net sinyal yok"


def strateji_andrea_unger(df: pd.DataFrame) -> tuple[int, float, str]:
    """
    Andrea Unger — Sistematik Volatilite Breakout
    4 kez WCTC Şampiyonu (2008, 2009, 2010, 2012)
    Kural: Volatilite (ATR) genişlediğinde breakout'a gir,
           Bollinger squeeze'den çıkışta pozisyon al.
    """
    last  = df.iloc[-1]
    prev5 = df.iloc[-5]

    atr_su_an    = last["ATR"]
    atr_gecmis   = df["ATR"].rolling(20).mean().iloc[-1]
    atr_genisledi = atr_su_an > atr_gecmis * 1.2  # ATR %20 üstünde

    # Bollinger Squeeze sonrası genişleme
    bb_genislik_su_an  = last["BB_HIGH"] - last["BB_LOW"]
    bb_genislik_gecmis = (df["BB_HIGH"] - df["BB_LOW"]).rolling(20).mean().iloc[-1]
    bb_genisledi = bb_genislik_su_an > bb_genislik_gecmis * 1.15

    # Yön belirleme
    yukari_yon = last["Close"] > last["BB_MID"] and last["MACD_HIST"] > 0
    asagi_yon  = last["Close"] < last["BB_MID"] and last["MACD_HIST"] < 0

    if atr_genisledi and bb_genisledi and yukari_yon:
        return 1, 0.87, f"UNGER: ATR genişliyor ({atr_su_an:.2f}>{atr_gecmis:.2f}) + BB açılıyor — sistematik LONG breakout"
    if atr_genisledi and bb_genisledi and asagi_yon:
        return -1, 0.87, f"UNGER: ATR genişliyor ({atr_su_an:.2f}>{atr_gecmis:.2f}) + BB açılıyor — sistematik SHORT breakout"
    if atr_genisledi and yukari_yon:
        return 1, 0.68, f"UNGER: Volatilite artıyor, yön yukarı — LONG"
    if atr_genisledi and asagi_yon:
        return -1, 0.68, f"UNGER: Volatilite artıyor, yön aşağı — SHORT"

    return 0, 0.40, f"Unger: Volatilite breakout yok (ATR: {atr_su_an:.2f})"


# ─────────────────────────────────────────────
# BÖLÜM 3: OYLAMA SİSTEMİ
# ─────────────────────────────────────────────
def efsane_oylama(symbol: str, df: pd.DataFrame) -> dict:
    """
    8 efsane stratejiyi çalıştırır, ağırlıklı oylama ile konsensüs üretir.
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
                "sinyal"  : {1: "LONG", -1: "SHORT", 0: "HOLD"}[sinyal],
                "guven"   : round(guven, 2),
                "agirlik" : agirlik,
                "etki"    : round(puan, 2),
                "gerekce" : gerekce,
            }

            if sinyal == 1:
                long_puan  += puan
            elif sinyal == -1:
                short_puan += puan
            else:
                hold_puan  += puan
        except Exception as e:
            sonuclar[isim] = {"sinyal": "HOLD", "guven": 0.0, "agirlik": agirlik, "etki": 0.0, "gerekce": f"Hata: {str(e)[:80]}"}
            hold_puan += agirlik * 0.1

    # Konsensüs karar
    toplam = long_puan + short_puan + hold_puan
    long_oran  = round(long_puan  / toplam * 100) if toplam > 0 else 0
    short_oran = round(short_puan / toplam * 100) if toplam > 0 else 0
    hold_oran  = round(hold_puan  / toplam * 100) if toplam > 0 else 0

    # Güven eşiği: %55 üstü kesin karar, %40-55 arası temkinli
    if long_puan > short_puan and long_oran >= 55:
        konsensus = "LONG"
        konsensus_guven = "YÜKSEK"
    elif short_puan > long_puan and short_oran >= 55:
        konsensus = "SHORT"
        konsensus_guven = "YÜKSEK"
    elif long_puan > short_puan and long_oran >= 40:
        konsensus = "LONG"
        konsensus_guven = "ORTA"
    elif short_puan > long_puan and short_oran >= 40:
        konsensus = "SHORT"
        konsensus_guven = "ORTA"
    else:
        konsensus = "HOLD"
        konsensus_guven = "DÜŞÜK"

    return {
        "symbol"           : symbol,
        "konsensus"        : konsensus,
        "konsensus_guven"  : konsensus_guven,
        "long_oran"        : long_oran,
        "short_oran"       : short_oran,
        "hold_oran"        : hold_oran,
        "efsane_sonuclari" : sonuclar,
    }


# ─────────────────────────────────────────────
# BÖLÜM 4: RAPORLAMA
# ─────────────────────────────────────────────
def raporu_yazdir(tum_sonuclar: list[dict]) -> None:
    sinyal_ikon = {"LONG": "🟢", "SHORT": "🔴", "HOLD": "🟡"}
    guven_ikon  = {"YÜKSEK": "💪", "ORTA": "👍", "DÜŞÜK": "🤔"}

    print(f"\n{'═'*75}")
    print(f"  {'SEMBOL':<10} {'KARAR':<8} {'GÜVEN':<10} {'LONG%':>6} {'SHORT%':>7} {'HOLD%':>6}  OYBIRLIĞI")
    print(f"{'─'*75}")

    for s in tum_sonuclar:
        ki = sinyal_ikon.get(s["konsensus"], "⚪")
        gi = guven_ikon.get(s["konsensus_guven"], "")
        # Kaç efsane aynı yönde oy verdi?
        oylar = [v["sinyal"] for v in s["efsane_sonuclari"].values() if v.get("sinyal") in ["LONG","SHORT","HOLD"]]
        ayni_yon = oylar.count(s["konsensus"])

        print(
            f"  {s['symbol']:<10} "
            f"{ki} {s['konsensus']:<6} "
            f"{gi} {s['konsensus_guven']:<8} "
            f"{s['long_oran']:>5}%  "
            f"{s['short_oran']:>6}%  "
            f"{s['hold_oran']:>5}%  "
            f"{ayni_yon}/8 efsane"
        )
    print(f"{'═'*75}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{'═'*75}")
    print(f"  Algoritmik Hedge Fon | Legends Agent v0.1")
    print(f"  8 Efsane Strateji: Druckenmiller · Jones · Livermore · Dennis")
    print(f"                     Burry · Soros · L.Williams · A.Unger")
    print(f"{'═'*75}\n")

    tum_sonuclar = []

    for i, sembol in enumerate(WATCHLIST, 1):
        print(f"[{i:>2}/{len(WATCHLIST)}] {sembol} analiz ediliyor...", end=" ", flush=True)
        df = veri_cek(sembol)

        if df is None:
            print("⚠️ Yetersiz veri")
            continue

        sonuc = efsane_oylama(sembol, df)
        tum_sonuclar.append(sonuc)

        ki = {"LONG": "🟢", "SHORT": "🔴", "HOLD": "🟡"}.get(sonuc["konsensus"], "⚪")
        print(f"{ki} {sonuc['konsensus']} ({sonuc['konsensus_guven']}) "
              f"— L:{sonuc['long_oran']}% S:{sonuc['short_oran']}% H:{sonuc['hold_oran']}%")

    # Özet tablo
    raporu_yazdir(tum_sonuclar)

    # Detaylı gerekçeler (sadece YÜKSEK güvenli kararlar)
    print(f"\n📋 YÜKSEK GÜVENLİ KARARLAR — DETAY:")
    for s in tum_sonuclar:
        if s["konsensus_guven"] == "YÜKSEK":
            print(f"\n  ══ {s['symbol']} → {s['konsensus']} ══")
            for efsane, veri in s["efsane_sonuclari"].items():
                if veri.get("sinyal") == s["konsensus"]:
                    print(f"  ✅ {efsane.upper():<18} → {veri['gerekce']}")

    # JSON kaydet
    cikti = {
        "tarih"    : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "strateji" : "8 Efsane + Yarışma Şampiyonu",
        "sonuclar" : tum_sonuclar,
    }
    Path("legends_rapor.json").write_text(
        json.dumps(cikti, ensure_ascii=False, indent=2)
    )
    print(f"\n💾 legends_rapor.json kaydedildi.")
    print(f"\n⚡ SONRAKI ADIM: mock_agent.py + legends_agent.py birleştirilecek")
    print(f"   Karar = Teknik Puan + Sentiment + Efsane Oylaması → Final Konsensüs\n")