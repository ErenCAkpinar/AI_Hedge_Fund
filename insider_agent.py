"""
insider_agent.py  [V2 — Project Gözcü]
=========================================
Algoritmik Hedge Fon — Alternatif Veri İstihbarat Ajansı

Veri Katmanları:
    1. SEC EDGAR Form 4    → CEO/CFO insider işlemleri
    2. Quiver Quant        → Kongre/senatör işlemleri
    3. FINRA Dark Pool     → Kurumsal hacim anomalisi
    4. SEC 13F             → Başarılı fon yöneticisi pozisyonları  (YENİ)
    5. Whale Tracker       → Büyük blok işlem tespiti              (YENİ)
    6. Short Interest      → Açığa satış baskısı radarı            (YENİ)

Çıktı:
    insider_rapor.json → state_manager.py tarafından okunur

Ağırlıklar (V2):
    Form 4         : %30
    Kongre         : %15
    Dark Pool      : %10
    13F Fon        : %20
    Whale          : %15
    Short Interest : %10
"""

import json
import os
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

warnings.filterwarnings("ignore")
load_dotenv()

# curl_cffi opsiyonel — yfinance fallback
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

SON_30_GUN       = 30
BUYUK_ISLEM_USD  = 500_000
EDGAR_BEKLEME    = 1.5
OUTPUT_FILE      = "insider_rapor.json"

AGIRLIKLAR_V2 = {
    "form4"         : 0.30,
    "kongre"        : 0.15,
    "dark_pool"     : 0.10,
    "form13f"       : 0.20,
    "whale"         : 0.15,
    "short_interest": 0.10,
}

SHORT_INTEREST_YUKSEK = 0.20
SHORT_INTEREST_ASIRI  = 0.30
HACIM_CARPAN_MIN      = 3.0

IZLENEN_FONLAR = {
    "Druckenmiller (Duquesne)" : "0001536411",
    "Michael Burry (Scion)"    : "0001649339",
    "Bill Ackman (Pershing Sq)": "0001336528",
    "Ray Dalio (Bridgewater)"  : "0001350694",
    "David Tepper (Appaloosa)" : "0001656456",
    "Carl Icahn"               : "0000813672",
    "Dan Loeb (Third Point)"   : "0001040273",
    "Chase Coleman (Tiger Gl)" : "0001167483",
}

EDGAR_HEADERS = {
    "User-Agent": "AI-HedgeFund contact@ai-hedge.com",
    "Accept"    : "application/json",
}


# ─────────────────────────────────────────────
# KATMAN 1: SEC EDGAR FORM 4
# ─────────────────────────────────────────────
def form4_cek(sembol: str, gun: int = SON_30_GUN) -> dict:
    """
    SEC EDGAR'dan Form 4 insider işlemlerini çeker.
    Döndürür: alım/satım tutarları, sinyal -1.0 → +1.0
    """
    bugun     = datetime.now()
    baslangic = (bugun - timedelta(days=gun)).strftime("%Y-%m-%d")
    bitis     = bugun.strftime("%Y-%m-%d")

    url = (
        f"https://efts.sec.gov/LATEST/search-index"
        f"?q=%22{sembol}%22"
        f"&dateRange=custom"
        f"&startdt={baslangic}"
        f"&enddt={bitis}"
        f"&forms=4"
        f"&hits.hits.total.value=true"
    )

    bos = {
        "toplam_alis_usd" : 0.0,
        "toplam_satis_usd": 0.0,
        "net_islem_usd"   : 0.0,
        "buyuk_satis"     : False,
        "buyuk_alis"      : False,
        "islem_sayisi"    : 0,
        "son_islem"       : "N/A",
        "sinyal"          : 0.0,
        "aciklama"        : "Veri yok",
    }

    try:
        time.sleep(EDGAR_BEKLEME)
        resp = requests.get(url, headers=EDGAR_HEADERS, timeout=15)
        if resp.status_code != 200:
            return bos

        hits = resp.json().get("hits", {}).get("hits", [])
        if not hits:
            return {**bos, "aciklama": "Son 30 günde Form 4 yok"}

        toplam_alis  = 0.0
        toplam_satis = 0.0
        tarihler     = []

        for hit in hits[:20]:
            kaynak = hit.get("_source", {})
            adet   = float(kaynak.get("transaction_amounts", 0) or 0)
            fiyat  = float(kaynak.get("transaction_price_per_share", 0) or 0)

            if fiyat > 0:
                if adet < 0:
                    toplam_satis += abs(adet) * fiyat
                elif adet > 0:
                    toplam_alis  += adet * fiyat

            tarih = kaynak.get("period_of_report", "N/A")
            tarihler.append(tarih)

        toplam_hacim = toplam_alis + toplam_satis
        sinyal       = round((toplam_alis - toplam_satis) / toplam_hacim, 3) if toplam_hacim else 0.0
        buyuk_satis  = toplam_satis > BUYUK_ISLEM_USD
        buyuk_alis   = toplam_alis  > BUYUK_ISLEM_USD

        if buyuk_satis and sinyal < -0.3:
            aciklama = f"⚠️  BÜYÜK İÇERİDEN SATIŞ: ${toplam_satis:,.0f}"
        elif buyuk_alis and sinyal > 0.3:
            aciklama = f"✅ BÜYÜK İÇERİDEN ALIM: ${toplam_alis:,.0f}"
        elif toplam_satis > toplam_alis:
            aciklama = f"Insider net satış: ${toplam_satis:,.0f}"
        else:
            aciklama = f"Insider net alım: ${toplam_alis:,.0f}"

        return {
            "toplam_alis_usd" : round(toplam_alis, 2),
            "toplam_satis_usd": round(toplam_satis, 2),
            "net_islem_usd"   : round(toplam_alis - toplam_satis, 2),
            "buyuk_satis"     : buyuk_satis,
            "buyuk_alis"      : buyuk_alis,
            "islem_sayisi"    : len(hits),
            "son_islem"       : max(tarihler) if tarihler else "N/A",
            "sinyal"          : sinyal,
            "aciklama"        : aciklama,
        }

    except Exception as e:
        return {**bos, "aciklama": f"Hata: {e}"}


# ─────────────────────────────────────────────
# KATMAN 2: KONGRE/SENATÖR İŞLEMLERİ
# ─────────────────────────────────────────────
def kongre_islemleri_cek(sembol: str) -> dict:
    """
    Quiver Quant API üzerinden ABD kongre üyelerinin
    son 90 günlük işlemlerini çeker (STOCK Act).
    """
    bos = {
        "alis_adedi" : 0,
        "satis_adedi": 0,
        "net_yon"    : "NÖTR",
        "son_islem"  : "N/A",
        "sinyal"     : 0.0,
        "aciklama"   : "Kongre verisi yok",
    }

    try:
        api_key = os.getenv("QUIVER_API_KEY", "")
        headers = {"User-Agent": "AI-HedgeFund", "Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Token {api_key}"

        url  = f"https://api.quiverquant.com/beta/live/congresstrading/{sembol}"
        resp = requests.get(url, headers=headers, timeout=10)

        if resp.status_code == 401:
            return {**bos, "aciklama": "Quiver API key gerekli"}
        if resp.status_code != 200:
            return bos

        islemler  = resp.json()
        if not islemler:
            return {**bos, "aciklama": "Kongre işlemi yok"}

        esik_tarih  = datetime.now() - timedelta(days=90)
        alis_adedi  = 0
        satis_adedi = 0
        son_tarih   = None
        son_islem   = "N/A"

        for islem in islemler:
            tarih_str = islem.get("TransactionDate", "")
            yon       = islem.get("Transaction", "").upper()
            try:
                tarih = datetime.strptime(tarih_str, "%Y-%m-%d")
            except Exception:
                continue
            if tarih < esik_tarih:
                continue

            if "PURCHASE" in yon or "BUY" in yon:
                alis_adedi += 1
            elif "SALE" in yon or "SELL" in yon:
                satis_adedi += 1

            if son_tarih is None or tarih > son_tarih:
                son_tarih = tarih
                son_islem = f"{tarih_str} — {islem.get('Representative','?')}"

        toplam = alis_adedi + satis_adedi
        if toplam == 0:
            return {**bos, "aciklama": "Son 90 günde işlem yok"}

        sinyal = round((alis_adedi - satis_adedi) / toplam, 3)

        if sinyal > 0.3:
            net_yon  = "ALIM"
            aciklama = f"Kongre net alım: {alis_adedi} alım / {satis_adedi} satım"
        elif sinyal < -0.3:
            net_yon  = "SATIM"
            aciklama = f"⚠️  Kongre net satım: {satis_adedi} satım / {alis_adedi} alım"
        else:
            net_yon  = "NÖTR"
            aciklama = f"Karışık: {alis_adedi} alım / {satis_adedi} satım"

        return {
            "alis_adedi" : alis_adedi,
            "satis_adedi": satis_adedi,
            "net_yon"    : net_yon,
            "son_islem"  : son_islem,
            "sinyal"     : sinyal,
            "aciklama"   : aciklama,
        }

    except Exception as e:
        return {**bos, "aciklama": f"Hata: {e}"}


# ─────────────────────────────────────────────
# KATMAN 3: FINRA DARK POOL HACİM RADAR
# ─────────────────────────────────────────────
def dark_pool_cek(sembol: str) -> dict:
    """
    FINRA OTC weeklySummary'den dark pool hacim verisini çeker.
    Normal hacmin 3x+ üstü = kurumsal para hareketi.
    """
    bos = {
        "dark_pool_hacim": 0,
        "normal_hacim"   : 0,
        "oran"           : 0.0,
        "anormal"        : False,
        "sinyal"         : 0.0,
        "aciklama"       : "Dark pool verisi yok",
    }

    try:
        url = (
            "https://api.finra.org/data/group/OTCMarket"
            "/name/weeklySummary"
            f"?compareFilters=eq:issueSymbolIdentifier:{sembol}"
            "&fields=issueSymbolIdentifier,totalWeeklyShareQuantity"
            ",totalWeeklyTradeCount"
            "&limit=4"
        )
        resp = requests.get(url, headers={"Accept": "application/json",
                                          "User-Agent": "AI-HedgeFund"}, timeout=15)
        if resp.status_code != 200:
            return bos

        veriler = resp.json()
        if not veriler:
            return {**bos, "aciklama": f"{sembol} için dark pool kaydı yok"}

        haftalar = [int(v.get("totalWeeklyShareQuantity", 0) or 0) for v in veriler]
        haftalar = [h for h in haftalar if h]

        if len(haftalar) < 2:
            return bos

        son_hafta = haftalar[0]
        ortalama  = sum(haftalar[1:]) / len(haftalar[1:])

        if ortalama == 0:
            return bos

        oran    = round(son_hafta / ortalama, 2)
        anormal = oran > 2.5

        if oran > 3.0:
            sinyal   = 0.3
            aciklama = f"🔥 ANORMAL dark pool: normalin {oran:.1f}x — büyük hareket bekleniyor"
        elif oran > 2.0:
            sinyal   = 0.15
            aciklama = f"⚠️  Yüksek dark pool hacmi: normalin {oran:.1f}x"
        else:
            sinyal   = 0.0
            aciklama = f"Normal dark pool hacmi ({oran:.1f}x)"

        return {
            "dark_pool_hacim": son_hafta,
            "normal_hacim"   : int(ortalama),
            "oran"           : oran,
            "anormal"        : anormal,
            "sinyal"         : sinyal,
            "aciklama"       : aciklama,
        }

    except Exception as e:
        return {**bos, "aciklama": f"Hata: {e}"}


# ─────────────────────────────────────────────
# KATMAN 4: SEC 13F — KURUMSAL POZİSYON TAKİBİ
# ─────────────────────────────────────────────
def form13f_cek(sembol: str) -> dict:
    """
    SEC EDGAR 13F dosyalarından izlenen fonların pozisyon takibi.
    Her çeyrek güncellenir; yalnızca LONG pozisyonlar görünür.
    """
    bos = {
        "tutan_fon_sayisi": 0,
        "tutan_fonlar"    : [],
        "toplam_kayit"    : 0,
        "sinyal"          : 0.0,
        "aciklama"        : "13F verisi yok",
    }

    try:
        baslangic = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
        url = (
            f"https://efts.sec.gov/LATEST/search-index"
            f"?q=%22{sembol}%22"
            f"&forms=13F-HR"
            f"&dateRange=custom"
            f"&startdt={baslangic}"
            f"&enddt={datetime.now().strftime('%Y-%m-%d')}"
        )

        time.sleep(EDGAR_BEKLEME)
        resp = requests.get(url, headers=EDGAR_HEADERS, timeout=15)
        if resp.status_code != 200:
            return bos

        hits = resp.json().get("hits", {}).get("hits", [])
        if not hits:
            return {**bos, "aciklama": "Son çeyrekte 13F yok"}

        tutan_fonlar = []
        for hit in hits[:50]:
            entity = hit.get("_source", {}).get("entity_name", "").upper()
            for fon_adi in IZLENEN_FONLAR:
                fon_kisalt = fon_adi.split("(")[0].strip().upper()
                kelimeleri = fon_kisalt.split()
                if any(k in entity for k in kelimeleri):
                    tutan_fonlar.append(fon_adi)

        tutan_fonlar = list(set(tutan_fonlar))
        sinyal       = min(1.0, len(tutan_fonlar) * 0.2)

        aciklama = (
            f"✅ {len(tutan_fonlar)} takip fonu tutuyor: {', '.join(tutan_fonlar[:3])}"
            if tutan_fonlar else
            "Takip edilen fonlarda tespit edilmedi"
        )

        return {
            "tutan_fon_sayisi": len(tutan_fonlar),
            "tutan_fonlar"    : tutan_fonlar,
            "toplam_kayit"    : len(hits),
            "sinyal"          : round(sinyal, 3),
            "aciklama"        : aciklama,
        }

    except Exception as e:
        return {**bos, "aciklama": f"Hata: {e}"}


# ─────────────────────────────────────────────
# KATMAN 5: WHALE TRACKER
# ─────────────────────────────────────────────
def whale_tracker_cek(sembol: str) -> dict:
    """
    İki kaynak birleşimi:
    1. SEC 13D/13G: %5+ hisse alımı bildirimleri (gerçek whale girişi)
    2. yfinance hacim spike: Normalin 3x+ hacim
    """
    bos = {
        "buyuk_alim_var": False,
        "hacim_carpani" : 1.0,
        "d13_var"       : False,
        "d13_detay"     : "N/A",
        "sinyal"        : 0.0,
        "aciklama"      : "Whale aktivitesi yok",
    }

    try:
        # Hacim Spike Tespiti
        hacim_carpani = 1.0
        try:
            ticker = yf_ticker(sembol)
            df     = ticker.history(period="30d", interval="1d")
            if not df.empty and len(df) >= 5:
                son_hacim = df["Volume"].iloc[-1]
                ort_hacim = df["Volume"].iloc[:-1].mean()
                if ort_hacim > 0:
                    hacim_carpani = round(son_hacim / ort_hacim, 2)
        except Exception:
            pass

        # SEC 13D/13G Taraması
        baslangic = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        url = (
            f"https://efts.sec.gov/LATEST/search-index"
            f"?q=%22{sembol}%22"
            f"&forms=SC+13D,SC+13G"
            f"&dateRange=custom"
            f"&startdt={baslangic}"
        )

        time.sleep(EDGAR_BEKLEME)
        resp = requests.get(url, headers=EDGAR_HEADERS, timeout=15)

        d13_var   = False
        d13_detay = "N/A"

        if resp.status_code == 200:
            hits = resp.json().get("hits", {}).get("hits", [])
            if hits:
                d13_var   = True
                kaynak    = hits[0].get("_source", {})
                kim       = kaynak.get("entity_name", "Bilinmeyen")
                tarih     = kaynak.get("period_of_report", "N/A")
                d13_detay = f"{kim} — {tarih} tarihinde %5+ alım bildirdi"

        uyarilar = []
        sinyal   = 0.0

        if hacim_carpani >= 5.0:
            sinyal   += 0.4
            uyarilar.append(f"🐋 DEV hacim spike: normalin {hacim_carpani:.1f}x")
        elif hacim_carpani >= HACIM_CARPAN_MIN:
            sinyal   += 0.2
            uyarilar.append(f"⚠️  Yüksek hacim: normalin {hacim_carpani:.1f}x")

        if d13_var:
            sinyal   += 0.4
            uyarilar.append(f"🐋 13D/13G: {d13_detay}")

        sinyal   = min(1.0, round(sinyal, 3))
        aciklama = " | ".join(uyarilar) if uyarilar else f"Normal aktivite ({hacim_carpani:.1f}x hacim)"

        return {
            "buyuk_alim_var": d13_var or hacim_carpani >= 5.0,
            "hacim_carpani" : hacim_carpani,
            "d13_var"       : d13_var,
            "d13_detay"     : d13_detay,
            "sinyal"        : sinyal,
            "aciklama"      : aciklama,
        }

    except Exception as e:
        return {**bos, "aciklama": f"Hata: {e}"}


# ─────────────────────────────────────────────
# KATMAN 6: SHORT INTEREST RADAR
# ─────────────────────────────────────────────
def short_interest_cek(sembol: str) -> dict:
    """
    yfinance üzerinden short interest verisini çeker.
    Yüksek short → down bet veya squeeze potansiyeli.
    """
    bos = {
        "short_pct_float"   : 0.0,
        "short_ratio"       : 0.0,
        "shares_short"      : 0,
        "short_baskisi"     : "DÜŞÜK",
        "squeeze_potansiyel": False,
        "sinyal"            : 0.0,
        "aciklama"          : "Short interest verisi yok",
    }

    try:
        ticker      = yf_ticker(sembol)
        info        = ticker.info
        short_pct   = float(info.get("shortPercentOfFloat", 0) or 0)
        short_ratio = float(info.get("shortRatio",          0) or 0)
        shares_short= int(info.get("sharesShort",           0) or 0)

        if short_pct >= SHORT_INTEREST_ASIRI:
            seviye   = "AŞIRI"
            sinyal   = -0.2
            squeeze  = True
            aciklama = f"⚠️  AŞIRI SHORT: Float'ın %{short_pct*100:.1f}'i — SQUEEZE RİSKİ"
        elif short_pct >= SHORT_INTEREST_YUKSEK:
            seviye   = "YÜKSEK"
            sinyal   = -0.3
            squeeze  = False
            aciklama = f"Short baskısı yüksek: %{short_pct*100:.1f} float"
        elif short_ratio > 7:
            seviye   = "YÜKSEK"
            sinyal   = -0.25
            squeeze  = True
            aciklama = f"Days-to-cover yüksek: {short_ratio:.1f} gün — squeeze riski"
        else:
            seviye   = "DÜŞÜK"
            sinyal   = 0.0
            squeeze  = False
            aciklama = f"Normal short interest: %{short_pct*100:.1f} ({short_ratio:.1f} gün)"

        return {
            "short_pct_float"   : round(short_pct, 4),
            "short_ratio"       : round(short_ratio, 2),
            "shares_short"      : shares_short,
            "short_baskisi"     : seviye,
            "squeeze_potansiyel": squeeze,
            "sinyal"            : round(sinyal, 3),
            "aciklama"          : aciklama,
        }

    except Exception as e:
        return {**bos, "aciklama": f"Hata: {e}"}


# ─────────────────────────────────────────────
# KOMPOZİT SKOR V2 (6 Katman)
# ─────────────────────────────────────────────
def insider_skor_hesapla_v2(
    form4     : dict,
    kongre    : dict,
    dark_pool : dict,
    form13f   : dict,
    whale     : dict,
    short_int : dict,
) -> dict:
    """
    6 katmanlı kompozit insider skor hesaplama.

    Özel kurallar:
    - Büyük insider SATIŞ + yüksek short interest → güçlü SHORT
    - 13F fon alımı + whale girişi → güçlü LONG
    - Squeeze potansiyeli varsa SHORT sinyali bastırılır
    - Kongre + insider + whale üçlü uyumu → MAX güven
    """
    f4  = form4.get("sinyal", 0.0)
    kg  = kongre.get("sinyal", 0.0)
    dp  = dark_pool.get("sinyal", 0.0)
    f13 = form13f.get("sinyal", 0.0)
    wh  = whale.get("sinyal", 0.0)
    si  = short_int.get("sinyal", 0.0)

    ham_skor = (
        f4  * AGIRLIKLAR_V2["form4"]          +
        kg  * AGIRLIKLAR_V2["kongre"]         +
        dp  * AGIRLIKLAR_V2["dark_pool"]      +
        f13 * AGIRLIKLAR_V2["form13f"]        +
        wh  * AGIRLIKLAR_V2["whale"]          +
        si  * AGIRLIKLAR_V2["short_interest"]
    )

    uyarilar = []
    carpan   = 1.0

    # Özel Kural 1: Büyük insider satış
    if form4.get("buyuk_satis", False):
        ham_skor = min(ham_skor, -0.35)
        carpan   = 1.5
        uyarilar.append("⚠️  BÜYÜK İNSİDER SATIŞ — sinyal override")

    # Özel Kural 2: Whale + 13F uyumu
    if f13 > 0.2 and wh > 0.2:
        ham_skor *= 1.4
        uyarilar.append("🐋 Whale + Kurumsal alım uyumu — güçlü LONG")

    # Özel Kural 3: Squeeze riski varsa SHORT sinyali bastır
    if short_int.get("squeeze_potansiyel", False) and ham_skor < -0.2:
        ham_skor *= 0.5
        uyarilar.append("⚡ SHORT SQUEEZE riski — sinyal zayıflatıldı")

    # Özel Kural 4: Kongre + insider + whale üçlü uyumu
    if kg > 0.2 and f4 > 0.2 and wh > 0.2:
        ham_skor = min(1.0, ham_skor * 1.5)
        uyarilar.append("🎯 ÜÇLÜ UYUM: Kongre + Insider + Whale — MAX GÜVEN")

    final_skor = max(-1.0, min(1.0, round(ham_skor, 3)))

    aktif_katman = sum([
        abs(f4)  > 0.1,
        abs(kg)  > 0.1,
        abs(dp)  > 0.1,
        abs(f13) > 0.1,
        abs(wh)  > 0.1,
        abs(si)  > 0.1,
    ])
    guven = min(100, aktif_katman * 17)

    if final_skor > 0.5:
        yorum = "🟢 ULTRA GÜÇLÜ ALIM — tüm katmanlar aynı yönde"
    elif final_skor > 0.3:
        yorum = "✅ Güçlü insider ALIM sinyali"
    elif final_skor > 0.1:
        yorum = "Zayıf alım eğilimi"
    elif final_skor < -0.5:
        yorum = "🔴 ULTRA GÜÇLÜ SATIŞ — tüm katmanlar aynı yönde"
    elif final_skor < -0.3:
        yorum = "⚠️  Güçlü insider SATIŞ sinyali"
    elif final_skor < -0.1:
        yorum = "Zayıf satış eğilimi"
    else:
        yorum = "Nötr — karışık insider aktivitesi"

    return {
        "final_skor": final_skor,
        "guven"     : guven,
        "carpan"    : carpan,
        "yorum"     : yorum,
        "uyarilar"  : uyarilar,
        "detay"     : {
            "form4_sinyal"     : f4,
            "kongre_sinyal"    : kg,
            "dark_pool_sinyal" : dp,
            "form13f_sinyal"   : f13,
            "whale_sinyal"     : wh,
            "short_int_sinyal" : si,
        },
    }


# ─────────────────────────────────────────────
# ANA DÖNGÜ
# ─────────────────────────────────────────────
def insider_analizi_yap_v2() -> dict:
    """17 varlık için 6 katmanlı tam insider analizi."""

    print(f"\n{'='*70}")
    print(f"  Algoritmik Hedge Fon | Project Gözcü V2")
    print(f"  Katmanlar: Form4 + Kongre + DarkPool + 13F + Whale + ShortInt")
    print(f"{'='*70}\n")

    rapor = {}

    for i, sembol in enumerate(WATCHLIST, 1):
        print(f"[{i:2d}/17] {sembol}:")

        print(f"    📋 Form 4...",    end=" ", flush=True)
        form4 = form4_cek(sembol)
        print(f"✓ sinyal:{form4['sinyal']:+.2f}")

        print(f"    🏛️  Kongre...",  end=" ", flush=True)
        kongre = kongre_islemleri_cek(sembol)
        print(f"✓ {kongre['net_yon']}")

        print(f"    🌊 Dark Pool...", end=" ", flush=True)
        dark_pool = dark_pool_cek(sembol)
        print(f"✓ {dark_pool['oran']:.1f}x")

        print(f"    📊 13F Fon...",  end=" ", flush=True)
        form13f = form13f_cek(sembol)
        print(f"✓ {form13f['tutan_fon_sayisi']} fon tutuyor")

        print(f"    🐋 Whale...",    end=" ", flush=True)
        whale = whale_tracker_cek(sembol)
        print(f"✓ hacim:{whale['hacim_carpani']:.1f}x {'🐋 WHALE!' if whale['d13_var'] else ''}")

        print(f"    📉 Short Int...", end=" ", flush=True)
        short_int = short_interest_cek(sembol)
        print(f"✓ %{short_int['short_pct_float']*100:.1f} float "
              f"{'⚡SQUEEZE' if short_int['squeeze_potansiyel'] else ''}")

        skor = insider_skor_hesapla_v2(form4, kongre, dark_pool, form13f, whale, short_int)

        emoji = (
            "🟢" if skor["final_skor"] >  0.5 else
            "✅" if skor["final_skor"] >  0.2 else
            "🔴" if skor["final_skor"] < -0.5 else
            "⚠️" if skor["final_skor"] < -0.2 else "🟡"
        )

        print(f"  ► {emoji} İNSİDER SKOR: {skor['final_skor']:+.3f} | "
              f"Güven: %{skor['guven']} | {skor['yorum']}")
        if skor["uyarilar"]:
            for u in skor["uyarilar"]:
                print(f"    {u}")
        print()

        rapor[sembol] = {
            "sembol"    : sembol,
            "form4"     : form4,
            "kongre"    : kongre,
            "dark_pool" : dark_pool,
            "form13f"   : form13f,
            "whale"     : whale,
            "short_int" : short_int,
            "skor"      : skor,
        }

    return rapor


if __name__ == "__main__":
    rapor = insider_analizi_yap_v2()

    cikti = {
        "tarih"    : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "versiyon" : "V2",
        "kaynak"   : "SEC EDGAR + Quiver Quant + FINRA + yfinance",
        "varlıklar": rapor,
    }

    Path(OUTPUT_FILE).write_text(
        json.dumps(cikti, ensure_ascii=False, indent=2, default=str)
    )

    print(f"\n{'='*70}")
    print(f"  ÖZET TABLO")
    print(f"{'─'*70}")
    print(f"  {'SEMBOL':<8} {'SKOR':>7}  {'GÜVEN':>6}  YORUM")
    print(f"{'─'*70}")

    for sem, veri in rapor.items():
        skor  = veri["skor"]["final_skor"]
        guven = veri["skor"]["guven"]
        yorum = veri["skor"]["yorum"][:38]
        emoji = "🔴" if skor < -0.15 else "🟢" if skor > 0.15 else "🟡"
        print(f"  {sem:<8} {skor:>+7.3f}  %{guven:>3}   {emoji} {yorum}")

    print(f"{'='*70}")
    print(f"\n💾 {OUTPUT_FILE} kaydedildi. ✅\n")
