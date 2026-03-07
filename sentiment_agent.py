"""
sentiment_agent.py
==================
Algoritmik Hedge Fon — Multi-Source Sentiment Modülü
DAG Aşama 2: Haber Duyarlılığı

KAYNAKLAR (tümü ücretsiz, API key yok):
    1. yfinance     → Kurumsal haber akışı
    2. Reddit       → r/stocks + r/wallstreetbets (trader duyarlılığı)
    3. StockTwits   → Gerçek zamanlı trader yorumları + boğa/ayı oranı
    4. Finviz       → Analist önerileri + fiyat hedefleri
    5. Fear & Greed → CNN makro piyasa duyarlılığı (tüm watchlist'e uygulanır)

MOD:
    Şu an: Keyword tabanlı kural motoru (API'sız)
    Pazartesi: GEMINI_API_KEY .env'e eklenir, keyword motoru → Gemini Flash ile değiştirilir

Kurulum:
    pip install requests beautifulsoup4 yfinance python-dotenv
"""
import os
import google.generativeai as genai
import json
import time
import warnings
from datetime import datetime
from pathlib import Path

import requests
import yfinance as yf
from bs4 import BeautifulSoup
from dotenv import load_dotenv

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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (AlgorithmicHedgeFund/1.0; research-bot)"
}

# Keyword ağırlık tabloları
POZITIF_KEYWORDS = {
    # Güçlü sinyaller (+2)
    "beat": 2, "record": 2, "surge": 2, "soar": 2, "rally": 2,
    "breakout": 2, "upgrade": 2, "outperform": 2, "strong buy": 2,
    "rekor": 2, "yükseliş": 2, "güçlü": 2, "aşıyor": 2,
    # Normal sinyaller (+1)
    "growth": 1, "profit": 1, "gain": 1, "positive": 1, "bullish": 1,
    "buy": 1, "opportunity": 1, "recovery": 1, "momentum": 1,
    "toparlanma": 1, "artış": 1, "fırsat": 1,
}

NEGATIF_KEYWORDS = {
    # Güçlü sinyaller (-2)
    "crash": -2, "plunge": -2, "collapse": -2, "downgrade": -2,
    "sell": -2, "miss": -2, "loss": -2, "layoff": -2, "bankruptcy": -2,
    "çöküş": -2, "düşüş": -2, "zarar": -2, "iflas": -2,
    # Normal sinyaller (-1)
    "decline": -1, "fall": -1, "drop": -1, "weak": -1, "bearish": -1,
    "concern": -1, "risk": -1, "uncertainty": -1, "warning": -1,
    "düşüyor": -1, "endişe": -1, "risk": -1, "uyarı": -1,
}


# ─────────────────────────────────────────────
# BÖLÜM 1: VERİ ÇEKİCİLER
# ─────────────────────────────────────────────

def yfinance_haberleri_cek(symbol: str) -> list[str]:
    """yfinance üzerinden son 5 haber başlığını çeker."""
    try:
        ticker = yf.Ticker(symbol)
        haberler = ticker.news or []
        basliklar = []
        for h in haberler[:5]:
            content = h.get("content", {})
            baslik = content.get("title", "") if isinstance(content, dict) else ""
            if baslik:
                basliklar.append(baslik)
        return basliklar
    except Exception:
        return []


def reddit_gonderileri_cek(symbol: str) -> list[str]:
    """
    Reddit JSON API — auth gerektirmez.
    r/stocks ve r/wallstreetbets'ten sembol ile arama yapar.
    """
    basliklar = []
    subredditler = ["stocks", "wallstreetbets", "investing"]

    # FXY (eski JPY spot paritesi) için özel arama terimi
    arama_terimi = "USDJPY" if symbol == "FXY" else symbol

    for sub in subredditler[:2]:  # 2 subreddit yeterli
        try:
            url = (
                f"https://www.reddit.com/r/{sub}/search.json"
                f"?q={arama_terimi}&limit=5&sort=new&restrict_sr=1"
            )
            r = requests.get(url, headers=HEADERS, timeout=8)
            if r.status_code == 200:
                data = r.json()
                posts = data.get("data", {}).get("children", [])
                for post in posts[:3]:
                    baslik = post.get("data", {}).get("title", "")
                    if baslik:
                        basliklar.append(f"[Reddit/{sub}] {baslik}")
            time.sleep(0.5)  # Rate limit önlemi
        except Exception:
            continue

    return basliklar


def stocktwits_cek(symbol: str) -> dict:
    """
    StockTwits public API — auth gerektirmez.
    Boğa/ayı oranı + son mesaj başlıkları döndürür.
    """
    # StockTwits FXY'i tanımaz (JPY spot paritesini de tanımaz)
    if symbol == "FXY":
        return {"basliklar": [], "boga_orani": None, "ayi_orani": None}

    try:
        url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
        r = requests.get(url, headers=HEADERS, timeout=8)

        if r.status_code != 200:
            return {"basliklar": [], "boga_orani": None, "ayi_orani": None}

        data = r.json()
        mesajlar = data.get("messages", [])

        basliklar = []
        boga = 0
        ayi = 0

        for m in mesajlar[:8]:
            body = m.get("body", "")
            if body:
                basliklar.append(f"[StockTwits] {body[:100]}")

            # Boğa/ayı sentiment verisi (StockTwits kullanıcıların işaretlediği)
            entities = m.get("entities", {})
            sentiment = entities.get("sentiment", {})
            if sentiment:
                if sentiment.get("basic") == "Bullish":
                    bogа = bogа + 1
                elif sentiment.get("basic") == "Bearish":
                    ayi += 1

        toplam = bogа + ayi
        return {
            "basliklar"  : basliklar[:5],
            "bogа_orani" : round(bogа / toplam * 100) if toplam > 0 else None,
            "ayi_orani"  : round(ayi / toplam * 100)  if toplam > 0 else None,
            "toplam_oy"  : toplam,
        }
    except Exception:
        return {"basliklar": [], "bogа_orani": None, "ayi_orani": None}


def finviz_analist_cek(symbol: str) -> dict:
    """
    Finviz'den analist öneri dağılımı ve fiyat hedefini çeker.
    """
    if symbol == "FXY":
        return {"oneri": None, "fiyat_hedefi": None, "analist_ozet": None}

    try:
        url = f"https://finviz.com/quote.ashx?t={symbol}"
        r = requests.get(url, headers=HEADERS, timeout=10)

        if r.status_code != 200:
            return {"oneri": None, "fiyat_hedefi": None, "analist_ozet": None}

        soup = BeautifulSoup(r.text, "html.parser")

        # Finviz tablo verisi
        tablo = soup.find_all("td", class_="snapshot-td2")
        veri = {}
        etiketler = soup.find_all("td", class_="snapshot-td2-cp")

        for i, etiket in enumerate(etiketler):
            if i < len(tablo):
                veri[etiket.text.strip()] = tablo[i].text.strip()

        oneri       = veri.get("Recom", None)       # 1=Güçlü Al, 5=Güçlü Sat
        fiyat_hedef = veri.get("Target Price", None)

        # Öneriyi yorumla
        analist_ozet = None
        if oneri:
            try:
                oneri_float = float(oneri)
                if oneri_float <= 1.5:
                    analist_ozet = "Güçlü Al"
                elif oneri_float <= 2.5:
                    analist_ozet = "Al"
                elif oneri_float <= 3.5:
                    analist_ozet = "Tut"
                elif oneri_float <= 4.5:
                    analist_ozet = "Sat"
                else:
                    analist_ozet = "Güçlü Sat"
            except ValueError:
                analist_ozet = oneri

        return {
            "oneri"        : oneri,
            "analist_ozet" : analist_ozet,
            "fiyat_hedefi" : fiyat_hedef,
        }
    except Exception:
        return {"oneri": None, "fiyat_hedefi": None, "analist_ozet": None}


# ─────────────────────────────────────────────
# BÖLÜM 2: SENTIMENT MOTORU
# (Pazartesi burası Gemini Flash ile değişecek)
# ─────────────────────────────────────────────

def keyword_sentiment_hesapla(metinler: list[str]) -> float:
    """
    Keyword tabanlı sentiment skoru (-1.0 ile +1.0 arası).
    Pazartesi bu fonksiyon → gemini_sentiment_hesapla() ile değiştirilecek.
    """
    if not metinler:
        return 0.0

    toplam_puan = 0
    for metin in metinler:
        metin_lower = metin.lower()
        for kelime, agirlik in POZITIF_KEYWORDS.items():
            if kelime in metin_lower:
                toplam_puan += agirlik
        for kelime, agirlik in NEGATIF_KEYWORDS.items():
            if kelime in metin_lower:
                toplam_puan += agirlik

    # Normalize: metin sayısına böl, -1/+1 arasına sıkıştır
    normalize = toplam_puan / (len(metinler) * 2)
    return round(max(-1.0, min(1.0, normalize)), 3)

#  Gemini API kullanarak sentiment hesapla

def gemini_sentiment_hesapla(symbol: str, metinler: list[str]) -> float:
    """Gemini 2.0 Flash kullanarak Otonom Sentiment analizi yapar."""
    if not metinler:
        return 0.0
        
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print(f"  ⚠️ GEMINI API yok! Şimdilik 0.0 (Nötr) dönülüyor.")
        return 0.0
        
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash")
        
        prompt = f"""
        Sen Wall Street'in en zeki 'Otonom Piyasa Duyarlılık (Sentiment) Algoritması'sın.
        Görevin, {symbol} varlığı hakkında internetten çekilen aşağıdaki ham metinleri okuyup, insan müdahalesi olmadan işlem yapan ticaret botumuza matematiksel bir yön (skor) vermektir.
        
        METİNLER:
        {json.dumps(metinler, ensure_ascii=False)}

        KURALLAR:
        1. Reddit/StockTwits argosunu ("To the moon", "Diamond hands" = Pozitif | "Bagholder", "Rug pull" = Negatif) ve en önemlisi İRONİYİ anla.
        2. Kurumsal clickbait tuzaklarını filtrele.
        3. EĞER METİNLERDE CİDDİ BİR İFLAS, SAVAŞ VEYA FED FAİZ ŞOKU GÖRÜRSEN, robotu korumak için skoru acımasızca -1.0'a çek.
        
        ÇIKTI FORMATI:
        Bana HİÇBİR açıklama veya uyarı yapma. Makinenin okuyabilmesi için SADECE -1.000 ile +1.000 arasında ondalıklı bir sayı ver. (Örnek: -0.850)
        """
        
        response = model.generate_content(prompt)
        return float(response.text.strip())
        
    except Exception as e:
        print(f"  ❌ Gemini API Hatası: {e}")
        return 0.0

def stocktwits_orani_skora_cevir(bogа_orani) -> float:
    """StockTwits boğa/ayı oranını -1/+1 skalasına çevirir."""
    if bogа_orani is None:
        return 0.0
    # 50% = nötr (0.0), 100% = tam pozitif (1.0), 0% = tam negatif (-1.0)
    return round((bogа_orani - 50) / 50, 3)


def fear_greed_cek() -> dict:
    """
    CNN Fear & Greed Index — public JSON endpoint, API key gerektirmez.
    0-100 arası skor: 0=Aşırı Korku, 50=Nötr, 100=Aşırı Açgözlülük
    Tüm watchlist için tek bir makro sinyal olarak kullanılır.
    """
    try:
        url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
        r = requests.get(url, headers=HEADERS, timeout=8)

        if r.status_code != 200:
            return {"skor": None, "yorum": None, "sinyal_skoru": 0.0}

        data = r.json()
        fear_greed = data.get("fear_and_greed", {})
        skor = fear_greed.get("score", None)

        if skor is None:
            return {"skor": None, "yorum": None, "sinyal_skoru": 0.0}

        skor = round(float(skor), 1)

        # CNN'in kendi kategorileri
        if skor >= 75:
            yorum = "Aşırı Açgözlülük 😱"
            sinyal_skoru = -0.5   # Aşırı açgözlülük → düzeltme riski
        elif skor >= 55:
            yorum = "Açgözlülük 📈"
            sinyal_skoru = 0.3
        elif skor >= 45:
            yorum = "Nötr ➡️"
            sinyal_skoru = 0.0
        elif skor >= 25:
            yorum = "Korku 📉"
            sinyal_skoru = -0.3
        else:
            yorum = "Aşırı Korku 🩸"
            sinyal_skoru = 0.5    # Aşırı korku → alım fırsatı (contrarian)

        return {
            "skor"         : skor,
            "yorum"        : yorum,
            "sinyal_skoru" : sinyal_skoru,   # -1/+1 skalasında
        }
    except Exception:
        return {"skor": None, "yorum": "Veri alınamadı", "sinyal_skoru": 0.0}


def analist_onerisi_skora_cevir(oneri_str: str) -> float:
    """Finviz analist önerisini -1/+1 skalasına çevirir."""
    mapping = {
        "Güçlü Al": 1.0, "Al": 0.5, "Tut": 0.0,
        "Sat": -0.5, "Güçlü Sat": -1.0
    }
    return mapping.get(oneri_str, 0.0)


# ─────────────────────────────────────────────
# BÖLÜM 3: ANA SENTIMENT HESAPLAYICI
# ─────────────────────────────────────────────

def sentiment_hesapla(symbol: str) -> dict:
    """
    5 kaynaktan veri toplar, ağırlıklı ortalama sentiment skoru üretir.
    Döndürülen skor mock_agent.py'deki karar motoruna eklenecek.
    """
    print(f"    📰 Haberler çekiliyor...", end=" ", flush=True)
    haberler = yfinance_haberleri_cek(symbol)
    haber_skoru = gemini_sentiment_hesapla(symbol, haberler)
    print(f"✓ ({len(haberler)} haber, skor: {haber_skoru:+.2f})")

    print(f"    🤖 Reddit taranıyor...", end=" ", flush=True)
    reddit_gonderiler = reddit_gonderileri_cek(symbol)
    reddit_skoru = gemini_sentiment_hesapla(symbol, reddit_gonderiler)
    print(f"✓ ({len(reddit_gonderiler)} gönderi, skor: {reddit_skoru:+.2f})")

    print(f"    📊 StockTwits çekiliyor...", end=" ", flush=True)
    st_data = stocktwits_cek(symbol)
    st_skoru = stocktwits_orani_skora_cevir(st_data.get("bogа_orani"))
    bogа_str = f"{st_data.get('bogа_orani', '?')}% 🐂" if st_data.get("bogа_orani") else "veri yok"
    print(f"✓ ({bogа_str}, skor: {st_skoru:+.2f})")

    print(f"    🎯 Finviz analisti çekiliyor...", end=" ", flush=True)
    finviz_data = finviz_analist_cek(symbol)
    finviz_skoru = analist_onerisi_skora_cevir(finviz_data.get("analist_ozet", ""))
    print(f"✓ ({finviz_data.get('analist_ozet', 'veri yok')}, "
          f"hedef: {finviz_data.get('fiyat_hedefi', '?')}, skor: {finviz_skoru:+.2f})")

    print(f"    😱 Fear & Greed çekiliyor...", end=" ", flush=True)
    fg_data = fear_greed_cek()
    print(f"✓ (skor: {fg_data.get('skor', '?')} | {fg_data.get('yorum', '?')}, "
          f"sinyal: {fg_data['sinyal_skoru']:+.2f})")

    # Analist görüşü en güvenilir → en yüksek ağırlık
    # Fear & Greed makro filtre → düşük ağırlık ama tüm kararları etkiler
    agirliklar = {
        "haber"      : 0.15,
        "reddit"     : 0.15,
        "stocktwits" : 0.20,
        "analist"    : 0.35,
        "fear_greed" : 0.15,
    }
    toplam_skor = (
        haber_skoru          * agirliklar["haber"]      +
        reddit_skoru         * agirliklar["reddit"]     +
        st_skoru             * agirliklar["stocktwits"] +
        finviz_skoru         * agirliklar["analist"]    +
        fg_data["sinyal_skoru"] * agirliklar["fear_greed"]
    )
    toplam_skor = round(toplam_skor, 3)

    # Skoru yorumla
    if toplam_skor >= 0.3:
        yorum = "POZİTİF 🟢"
    elif toplam_skor <= -0.3:
        yorum = "NEGATİF 🔴"
    else:
        yorum = "NÖTR 🟡"

    return {
        "symbol"           : symbol,
        "sentiment_skoru"  : toplam_skor,
        "sentiment_yorum"  : yorum,
        "mod"              : "MOCK-KEYWORD",   # Pazartesi "GEMINI" olacak
        "kaynaklar": {
            "haber_skoru"      : haber_skoru,
            "haber_sayisi"     : len(haberler),
            "reddit_skoru"     : reddit_skoru,
            "reddit_sayisi"    : len(reddit_gonderiler),
            "stocktwits_skoru" : st_skoru,
            "bogа_orani"       : st_data.get("bogа_orani"),
            "ayi_orani"        : st_data.get("ayi_orani"),
            "analist_skoru"    : finviz_skoru,
            "analist_oneri"    : finviz_data.get("analist_ozet"),
            "fiyat_hedefi"     : finviz_data.get("fiyat_hedefi"),
            "fear_greed_skor"  : fg_data.get("skor"),
            "fear_greed_yorum" : fg_data.get("yorum"),
        },
        "ham_metinler": {
            "haberler"  : haberler,
            "reddit"    : reddit_gonderiler[:3],
            "stocktwits": st_data.get("basliklar", [])[:3],
        }
    }


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{'='*65}")
    print(f"  Algoritmik Hedge Fon | Multi-Source Sentiment v0.1")
    print(f"  Kaynaklar: yfinance + Reddit + StockTwits + Finviz + Fear&Greed")
    print(f"  Mod: Keyword Motoru (Pazartesi → Gemini Flash)")
    print(f"{'='*65}\n")

    # Fear & Greed tüm watchlist için tek → başta bir kez çek ve göster
    print(f"🌍 MAKRO GÖSTERGE — Fear & Greed Index:")
    fg_global = fear_greed_cek()
    if fg_global["skor"]:
        print(f"   Skor: {fg_global['skor']} | {fg_global['yorum']}")
        print(f"   Piyasa Yorumu: {'Contrarian AL fırsatı' if fg_global['sinyal_skoru'] > 0 else 'Düzeltme riski yüksek' if fg_global['sinyal_skoru'] < 0 else 'Nötr ortam'}\n")
    else:
        print(f"   Veri alınamadı\n")

    sonuclar = []

    for i, sembol in enumerate(WATCHLIST, 1):
        print(f"\n[{i:>2}/{len(WATCHLIST)}] {sembol} sentiment analizi:")
        try:
            sonuc = sentiment_hesapla(sembol)
            sonuclar.append(sonuc)
            print(f"  {'─'*50}")
            print(f"  TOPLAM SKOR: {sonuc['sentiment_skoru']:+.3f} | {sonuc['sentiment_yorum']}")
            print(f"  Analist: {sonuc['kaynaklar']['analist_oneri'] or 'N/A'} | "
                  f"Hedef: ${sonuc['kaynaklar']['fiyat_hedefi'] or 'N/A'}")
        except Exception as e:
            print(f"  ❌ Hata: {e}")
        time.sleep(1)  # Tüm API'lara karşı礼儀

    # Özet tablo
    print(f"\n\n{'='*65}")
    print(f"  SENTIMENT ÖZET TABLOSU")
    print(f"{'─'*65}")
    print(f"  {'SEMBOL':<10} {'SKOR':>7}  {'YORUM':<15} {'ANALİST':<12} {'HEDEF':>8}")
    print(f"{'─'*65}")

    for s in sonuclar:
        k = s["kaynaklar"]
        print(
            f"  {s['symbol']:<10} "
            f"{s['sentiment_skoru']:>+7.3f}  "
            f"{s['sentiment_yorum']:<15} "
            f"{(k['analist_oneri'] or 'N/A'):<12} "
            f"{('$'+str(k['fiyat_hedefi'])) if k['fiyat_hedefi'] else 'N/A':>8}"
        )
    print(f"{'='*65}")

    # JSON'a kaydet — mock_agent.py bu dosyayı okuyacak
    cikti = {
        "tarih"  : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mod"    : "MOCK-KEYWORD — Gemini bekleniyor",
        "sonuclar": sonuclar
    }
    Path("sentiment_rapor.json").write_text(
        json.dumps(cikti, ensure_ascii=False, indent=2)
    )
    print(f"\n💾 sentiment_rapor.json kaydedildi.")
    print(f"\n⚡ PAZARTESİ YAPILACAKLAR:")
    print(f"   1. .env'e GEMINI_API_KEY ekle")
    print(f"   2. gemini_sentiment_hesapla() fonksiyonunu aktif et")
    print(f"   3. keyword_sentiment_hesapla() çağrılarını değiştir")
    print(f"   4. mock_agent.py'e sentiment skoru entegre et\n")