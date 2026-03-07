"""
analyst_agent.py
================
Algoritmik Hedge Fon — DAG Aşama 1 + 3 Köprüsü
Görev: yfinance ile veri çek → ta ile zenginleştir → CrewAI Analist Ajanına ilet.

Gereksinimler (.env dosyasında):
    OPENAI_API_KEY=sk-...

Kurulum (eğer henüz kurulmadıysa):
    pip install crewai yfinance pandas python-dotenv ta
"""

import os
import warnings
import yfinance as yf
from dotenv import load_dotenv
from crewai import Agent, Task, Crew, Process
from ta.trend import MACD, SMAIndicator
from ta.momentum import RSIIndicator

warnings.filterwarnings("ignore")
load_dotenv()  # .env dosyasını otomatik yükle


# ─────────────────────────────────────────────
# CONFIG — Sadece buradan değiştir
# ─────────────────────────────────────────────
SYMBOL   = "NVDA"   # Test için tek varlık, watchlist'ten herhangi biri
PERIOD   = "3mo"    # yfinance periyodu (swing trading için yeterli geçmiş)
INTERVAL = "1d"     # Günlük mum (1D kararlar için)
LLM      = "gpt-4o-mini"  # Ucuz ama yeterince akıllı — API faturasını düşük tutar


# ─────────────────────────────────────────────
# BÖLÜM 1: Veri Çekimi ve Teknik Zenginleştirme
# ─────────────────────────────────────────────
def fetch_and_enrich(symbol: str, period: str, interval: str) -> dict:
    """
    yfinance'ten OHLCV verisi çeker, ta kütüphanesiyle
    temel teknik indikatörleri hesaplar ve özet dict döndürür.
    """
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)

        if df.empty:
            raise ValueError(f"'{symbol}' için veri alınamadı. Sembolü kontrol et.")

        if len(df) < 50:
            raise ValueError(
                f"Yeterli veri yok (mevcut: {len(df)} mum, gerekli: 50+). "
                f"Period değerini artır."
            )

        close = df["Close"]

        # --- İndikatör Hesaplamaları ---
        df["RSI"]        = RSIIndicator(close=close, window=14).rsi()
        df["SMA_20"]     = SMAIndicator(close=close, window=20).sma_indicator()
        df["SMA_50"]     = SMAIndicator(close=close, window=50).sma_indicator()

        _macd            = MACD(close=close)
        df["MACD"]       = _macd.macd()
        df["MACD_Signal"]= _macd.macd_signal()
        df["MACD_Diff"]  = _macd.macd_diff()  # Histogram: pozitif = momentum artıyor

        last = df.iloc[-1]
        prev = df.iloc[-2]

        # Trend yardımcısı: SMA pozisyonları
        price_vs_sma20 = "ÜSTÜNDE" if last["Close"] > last["SMA_20"] else "ALTINDA"
        price_vs_sma50 = "ÜSTÜNDE" if last["Close"] > last["SMA_50"] else "ALTINDA"
        sma20_vs_sma50 = "ÜSTÜNDE" if last["SMA_20"] > last["SMA_50"] else "ALTINDA"

        summary = {
            "symbol"          : symbol,
            "son_kapanış"     : round(float(last["Close"]), 2),
            "önceki_kapanış"  : round(float(prev["Close"]), 2),
            "günlük_değişim_%": round(
                ((float(last["Close"]) - float(prev["Close"])) / float(prev["Close"])) * 100, 2
            ),
            "RSI_14"          : round(float(last["RSI"]), 2),
            "SMA_20"          : round(float(last["SMA_20"]), 2),
            "SMA_50"          : round(float(last["SMA_50"]), 2),
            "MACD"            : round(float(last["MACD"]), 4),
            "MACD_Sinyal"     : round(float(last["MACD_Signal"]), 4),
            "MACD_Histogram"  : round(float(last["MACD_Diff"]), 4),
            "hacim"           : int(last["Volume"]),
            "fiyat_sma20_poz" : price_vs_sma20,
            "fiyat_sma50_poz" : price_vs_sma50,
            "sma20_sma50_poz" : f"SMA20, SMA50'nin {sma20_vs_sma50}",
        }
        return summary

    except Exception as e:
        print(f"\n[HATA] fetch_and_enrich: {e}")
        return {}


# ─────────────────────────────────────────────
# BÖLÜM 2: CrewAI Analist Ajan
# ─────────────────────────────────────────────
def run_analyst_agent(market_data: dict) -> str:
    """
    Teknik veriyi alır, CrewAI üzerinden AI Analist Ajanını çalıştırır,
    yapılandırılmış TREND / SİNYAL / GEREKÇE formatında rapor döndürür.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "OPENAI_API_KEY bulunamadı. "
            "Proje kökündeki .env dosyasına 'OPENAI_API_KEY=sk-...' satırını ekle."
        )
    os.environ["OPENAI_API_KEY"] = api_key  # CrewAI bu env variable'ı doğrudan okur

    # Veriyi ajana okunabilir metin olarak hazırla
    data_lines = "\n".join(f"  • {k}: {v}" for k, v in market_data.items())

    # --- AJAN ---
# --- AJAN ---
    analyst = Agent(
        role="Otonom Sistemler Baş Kantitatif Analisti (Lead Quant)",
        goal=(
            "Verilen teknik indikatörlerdeki gürültüyü (noise) filtrelemek, "
            "insan onayı olmaksızın otomatik işlem açacak bir algoritmaya "
            "sadece asimetrik kâr potansiyeli olan kusursuz sinyaller üretmek."
        ),
        backstory=(
            "Sen %100 otonom (insan müdahalesi olmayan) bir Hedge Fonun teknik beynisin. "
            "Sıradan analistler gibi her MACD kesişiminde veya RSI aşırı satımında işlem onayı vermezsin. "
            "Senin işin 'Boğa Tuzaklarını' (Bull Trap) ve 'Düşen Bıçakları' (Falling Knives) tespit etmektir. "
            "Eğer verilerde mükemmel bir uyum (Confluence) yoksa, robotik sistemin parayı çöpe atmasını "
            "engellemek için gözünü kırpmadan 'HOLD' (Bekle) sinyali verirsin. Yasal uyarı yapmazsın."
        ),
        verbose=True,
        allow_delegation=False,
        llm=LLM,
    )

    # --- GÖREV ---
    task = Task(
        description=(
            f"Aşağıdaki kurumsal seviye teknik verileri analiz et:\n{data_lines}\n\n"
            f"GÖREVİN:\n"
            f"1. Fiyat SMA50'nin altındayken RSI 30'un altına indiyse, bu bir alım fırsatı mıdır yoksa trendin çöktüğünün kanıtı mıdır? Robotun düşen bıçağı tutmasını engelle.\n"
            f"2. MACD Histogram momentumu fiyatı destekliyor mu?\n"
            f"3. BU KARAR DOĞRUDAN BORSAYA İLETİLECEKTİR. Sadece kazanma ihtimali kusursuza yakınsa LONG veya SHORT ver, aksi halde kesinlikle HOLD ver.\n\n"
            f"YANIT FORMATI (YASAL UYARI KULLANMA. SADECE AŞAĞIDAKİ 3 SATIRI YAZ):\n"
            f"TREND: [Bullish / Bearish / Nötr]\n"
            f"SİNYAL: [LONG / SHORT / HOLD]\n"
            f"GEREKÇE: [Makro trendi açıklayan maksimum 2 cümlelik net bir finansal gerekçe.]"
        ),
        expected_output="TREND, SİNYAL ve GEREKÇE etiketlerini içeren 3 satırlık yapılandırılmış metin.",
        agent=analyst,
    )
    # --- CREW ---
    crew = Crew(
        agents=[analyst],
        tasks=[task],
        process=Process.sequential,
        verbose=True,
    )

    result = crew.kickoff()
    return str(result)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    SEPARATOR = "=" * 60

    print(f"\n{SEPARATOR}")
    print(f"  Algoritmik Hedge Fon | Analist Ajan v0.1")
    print(f"  Hedef: {SYMBOL} | Periyot: {PERIOD} | Aralık: {INTERVAL}")
    print(SEPARATOR)

    # Adım 1: Veri çek ve zenginleştir
    print(f"\n[1/2] Piyasa verisi hazırlanıyor → {SYMBOL}")
    market_data = fetch_and_enrich(SYMBOL, PERIOD, INTERVAL)

    if not market_data:
        print("\n[KRITIK HATA] Veri alınamadı. Sistem durduruluyor.")
        exit(1)

    print("\n📊 Teknik Özet (AI'a gönderilecek veri):")
    for k, v in market_data.items():
        print(f"  {k}: {v}")

    # Adım 2: AI ajanını çalıştır
    print(f"\n[2/2] AI Analist Ajan başlatılıyor... (API çağrısı yapılacak)")
    try:
        report = run_analyst_agent(market_data)
    except EnvironmentError as e:
        print(f"\n[KRITIK HATA] {e}")
        exit(1)
    except Exception as e:
        print(f"\n[HATA] Ajan çalıştırılırken beklenmeyen hata: {e}")
        exit(1)

    # Sonuç
    print(f"\n{SEPARATOR}")
    print("  ANALİST AJAN RAPORU")
    print(SEPARATOR)
    print(report)
    print(SEPARATOR)
    print("\n✅ Test tamamlandı.")