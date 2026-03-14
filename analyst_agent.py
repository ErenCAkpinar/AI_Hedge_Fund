"""
analyst_agent.py  [V5 — ATR+SMA200 Eklendi]
============================================
Algoritmik Hedge Fon — DAG Aşama 1 + 3 Köprüsü

V5 DEĞİŞİKLİKLERİ:
    - fetch_and_enrich() → ATR_14 ve SMA_200 artık hesaplanıp döndürülüyor
    - Bu sayede AI ajan kararları state_manager'a ATR ile birlikte gidiyor
    - mock_agent.py ile tam senkron: aynı veri alanları, aynı format

.env: OPENAI_API_KEY=sk-...
Kurulum: pip install crewai yfinance pandas python-dotenv ta
"""

import os
import warnings
import yfinance as yf
from dotenv import load_dotenv
from crewai import Agent, Task, Crew, Process
from ta.trend import MACD, SMAIndicator
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange   # V5: ATR için eklendi

warnings.filterwarnings("ignore")
load_dotenv()

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
SYMBOL   = "NVDA"
PERIOD   = "1y"     # V5: SMA_200 için 1 yıl (eski 3mo yetersizdi)
INTERVAL = "1d"
LLM      = "anthropic/claude-sonnet-4-20250514"


# ─────────────────────────────────────────────
# BÖLÜM 1: Veri Çekimi
# V5: ATR_14 ve SMA_200 eklendi — mock_agent ile tam senkron
# ─────────────────────────────────────────────
def fetch_and_enrich(symbol: str, period: str, interval: str) -> dict:
    """
    V5: ATR_14 ve SMA_200 artık döndürülen dict'e dahil.
    mock_agent.py ile tam senkron — pipeline boyunca ATR veri akışı sağlanıyor.
    """
    try:
        ticker = yf.Ticker(symbol)
        df     = ticker.history(period=period, interval=interval)

        if df.empty:
            raise ValueError(f"'{symbol}' için veri alınamadı.")

        if len(df) < 50:
            raise ValueError(f"Yetersiz veri (mevcut: {len(df)}, gerekli: 50+).")

        close = df["Close"]
        high  = df["High"]
        low   = df["Low"]

        # ── İndikatörler ─────────────────────────────────────────────────
        df["RSI"]        = RSIIndicator(close=close, window=14).rsi()
        df["SMA_20"]     = SMAIndicator(close=close, window=20).sma_indicator()
        df["SMA_50"]     = SMAIndicator(close=close, window=50).sma_indicator()

        # V5: SMA_200 — Paul Tudor Jones trend filtresi
        df["SMA_200"]    = SMAIndicator(close=close, window=200).sma_indicator()

        _macd            = MACD(close=close)
        df["MACD"]       = _macd.macd()
        df["MACD_Signal"]= _macd.macd_signal()
        df["MACD_Diff"]  = _macd.macd_diff()

        # V5: ATR — canlı pipeline'ın akıllı SL/TP ve Pyramiding için
        df["ATR_14"]     = AverageTrueRange(
            high=high, low=low, close=close, window=14
        ).average_true_range()

        last = df.iloc[-1]
        prev = df.iloc[-2]

        price_vs_sma20 = "ÜSTÜNDE" if last["Close"] > last["SMA_20"] else "ALTINDA"
        price_vs_sma50 = "ÜSTÜNDE" if last["Close"] > last["SMA_50"] else "ALTINDA"
        sma20_vs_sma50 = "ÜSTÜNDE" if last["SMA_20"] > last["SMA_50"] else "ALTINDA"

        # SMA_200 trend tespiti
        try:
            sma200_val = float(last["SMA_200"])
            ana_trend  = "BULLISH" if float(last["Close"]) > sma200_val else "BEARISH"
            fiyat_sma200 = "ÜSTÜNDE" if float(last["Close"]) > sma200_val else "ALTINDA"
        except Exception:
            sma200_val   = None
            ana_trend    = "NÖTR"
            fiyat_sma200 = "VERİ_YOK"

        # ATR değeri
        try:
            atr_val = round(float(last["ATR_14"]), 4)
        except Exception:
            atr_val = None

        summary = {
            # ── Mevcut alanlar ──────────────────────────────────────────
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
            # ── V5 Yeni Alanlar ─────────────────────────────────────────
            "ATR_14"          : atr_val,
            "SMA_200"         : round(sma200_val, 2) if sma200_val else None,
            "fiyat_sma200_poz": fiyat_sma200,
            "ana_trend"       : ana_trend,
        }
        return summary

    except Exception as e:
        print(f"\n[HATA] fetch_and_enrich: {e}")
        return {}


# ─────────────────────────────────────────────
# BÖLÜM 2: CrewAI Analist Ajan
# V5: ATR ve ana trend bilgisi prompt'a eklendi
# ─────────────────────────────────────────────
def run_analyst_agent(market_data: dict) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY bulunamadı. .env'e ekle.")
    os.environ["ANTHROPIC_API_KEY"] = api_key

    data_lines = "\n".join(f"  • {k}: {v}" for k, v in market_data.items())

    analyst = Agent(
        role="Otonom Sistemler Baş Kantitatif Analisti (Lead Quant)",
        goal=(
            "ATR bazlı stop ve Pyramiding içeren otonom sistemin sinyallerini üretmek. "
            "SMA200 altında LONG, SMA200 üstünde SHORT vermemek."
        ),
        backstory=(
            "Sen %100 otonom Hedge Fonun teknik beynisin. ATR değeri büyükse "
            "volatiliteyi göz önünde bulundurursun. SMA200 trend filtresine kesinlikle uyarsın. "
            "Yasal uyarı yapmazsın."
        ),
        verbose=True,
        allow_delegation=False,
        llm=LLM,
    )

    task = Task(
        description=(
            f"Kurumsal seviye teknik verileri analiz et:\n{data_lines}\n\n"
            f"V5 KURALLAR:\n"
            f"1. Ana trend BEARISH (fiyat SMA200 altında) ise LONG verme.\n"
            f"2. ATR yüksekse (fiyatın >%3'ü) bu volatil hisse, stop daha geniş tutulacak.\n"
            f"3. Sadece güçlü konfirmasyon varsa LONG veya SHORT ver.\n\n"
            f"YANIT FORMATI (sadece 3 satır):\n"
            f"TREND: [Bullish / Bearish / Nötr]\n"
            f"SİNYAL: [LONG / SHORT / HOLD]\n"
            f"GEREKÇE: [Maksimum 2 cümle, ATR ve SMA200 trendini belirt.]"
        ),
        expected_output="TREND, SİNYAL ve GEREKÇE etiketlerini içeren 3 satırlık metin.",
        agent=analyst,
    )

    crew   = Crew(agents=[analyst], tasks=[task], process=Process.sequential, verbose=True)
    result = crew.kickoff()
    return str(result)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    SEPARATOR = "=" * 60
    print(f"\n{SEPARATOR}")
    print(f"  Algoritmik Hedge Fon | Analist Ajan V5")
    print(f"  Hedef: {SYMBOL} | V5: ATR+SMA200 Entegrasyonu")
    print(SEPARATOR)

    print(f"\n[1/2] Piyasa verisi hazırlanıyor → {SYMBOL}")
    market_data = fetch_and_enrich(SYMBOL, PERIOD, INTERVAL)

    if not market_data:
        print("\n[KRİTİK HATA] Veri alınamadı.")
        exit(1)

    print("\n📊 Teknik Özet (AI'a gönderilecek veri):")
    for k, v in market_data.items():
        print(f"  {k}: {v}")

    print(f"\n[2/2] AI Analist Ajan başlatılıyor...")
    try:
        report = run_analyst_agent(market_data)
    except EnvironmentError as e:
        print(f"\n[KRİTİK HATA] {e}"); exit(1)
    except Exception as e:
        print(f"\n[HATA] {e}"); exit(1)

    print(f"\n{SEPARATOR}")
    print("  ANALİST AJAN RAPORU V5")
    print(SEPARATOR)
    print(report)
    print(SEPARATOR)
    print("\n✅ Test tamamlandı.")