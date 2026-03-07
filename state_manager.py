"""
state_manager.py
================
Algoritmik Hedge Fon — Final Karar Motoru (State Manager)
DAG'ın Son Halkası: 3 Ajanı Birleştir → Güven Skoru → Final Karar → Sheets

AKIŞ:
    mock_agent.py      → Teknik Sinyal  (RSI, MACD, SMA)
    sentiment_agent.py → Duyarlılık     (Haber, Reddit, F&G)
    legends_agent.py   → Efsane Oylama  (8 strateji)
         ↓
    state_manager.py   → Ağırlıklı Konsensüs → Final LONG/SHORT/HOLD
         ↓
    sheets_pusher.py   → Google Sheets Dashboard

AĞIRLIKLAR:
    Teknik Analiz  : %40  (en nesnel katman)
    Efsane Oylama  : %35  (kanıtlanmış stratejiler)
    Sentiment      : %25  (piyasa duyarlılığı)

API GEREKTİRMEZ — JSON dosyalarını okuyarak çalışır.
Pazartesi: Her karar için Claude API gerekçe ekler.
"""
import os
#import anthropic
import json
from datetime import datetime
from pathlib import Path
#load_dotenv()
# ─────────────────────────────────────────────
# CONFIG — Ağırlıklar
# ─────────────────────────────────────────────
AGIRLIK_TEKNIK    = 0.40
AGIRLIK_EFSANE    = 0.35
AGIRLIK_SENTIMENT = 0.25

# Karar eşikleri
ESIK_YUKSEK = 0.30   # Bu üstü → YÜKSEK güven (API gelince 0.45 yap)
ESIK_ORTA   = 0.15   # Bu üstü → ORTA güven  (API gelince 0.28 yap)
# Bu altı → HOLD (net sinyal yok)

DOSYA_TEKNIK    = "rapor.json"
DOSYA_SENTIMENT = "sentiment_rapor.json"
DOSYA_EFSANE    = "legends_rapor.json"
DOSYA_CIKTI     = "final_karar.json"


# ─────────────────────────────────────────────
# BÖLÜM 1: JSON OKUYUCULAR
# ─────────────────────────────────────────────
def teknik_veri_oku() -> dict:
    """mock_agent.py çıktısını sembol → karar dict'ine çevirir."""
    if not Path(DOSYA_TEKNIK).exists():
        print(f"  ⚠️  {DOSYA_TEKNIK} bulunamadı. Önce: python mock_agent.py")
        return {}
    with open(DOSYA_TEKNIK, "r", encoding="utf-8") as f:
        veri = json.load(f)
    return {r["veri"]["symbol"]: r for r in veri.get("varlıklar", [])}


def sentiment_veri_oku() -> dict:
    """sentiment_agent.py çıktısını sembol → skor dict'ine çevirir."""
    if not Path(DOSYA_SENTIMENT).exists():
        print(f"  ⚠️  {DOSYA_SENTIMENT} bulunamadı. Önce: python sentiment_agent.py")
        return {}
    with open(DOSYA_SENTIMENT, "r", encoding="utf-8") as f:
        veri = json.load(f)
    return {r["symbol"]: r for r in veri.get("sonuclar", [])}


def efsane_veri_oku() -> dict:
    """legends_agent.py çıktısını sembol → oylama dict'ine çevirir."""
    if not Path(DOSYA_EFSANE).exists():
        print(f"  ⚠️  {DOSYA_EFSANE} bulunamadı. Önce: python legends_agent.py")
        return {}
    with open(DOSYA_EFSANE, "r", encoding="utf-8") as f:
        veri = json.load(f)
    return {r["symbol"]: r for r in veri.get("sonuclar", [])}


# ─────────────────────────────────────────────
# BÖLÜM 2: SİNYAL DÖNÜŞTÜRÜCÜLER
# Her katmanı -1/+1 skalasına normalize eder
# ─────────────────────────────────────────────
def teknik_skora_cevir(karar: dict) -> float:
    """
    mock_agent kararını skora çevirir.
    Puan sistemi: -8 ile +8 arası → -1/+1'e normalize edilir.
    """
    if not karar:
        return 0.0
    puan = karar.get("karar", {}).get("PUAN", 0)
    # mock_agent puanı genellikle -8 ile +8 arasında
    return max(-1.0, min(1.0, puan / 8.0))


def sentiment_skora_cevir(sentiment: dict) -> float:
    """sentiment_agent zaten -1/+1 skalasında döndürüyor."""
    if not sentiment:
        return 0.0
    return sentiment.get("sentiment_skoru", 0.0)


def efsane_skora_cevir(efsane: dict) -> float:
    """
    legends_agent LONG/SHORT/HOLD + oranları → -1/+1 skora çevirir.
    """
    if not efsane:
        return 0.0
    long_oran  = efsane.get("long_oran", 0) / 100
    short_oran = efsane.get("short_oran", 0) / 100
    return round(long_oran - short_oran, 3)


# ─────────────────────────────────────────────
# BÖLÜM 3: ÇATIŞMA DEDEKTÖRÜ
# ─────────────────────────────────────────────
def catisma_var_mi(teknik_skor: float, efsane_skor: float, sentiment_skor: float) -> bool:
    """
    Çatışma kontrolü — sadece GÜÇLÜ sinyaller arasında bakar.
    Sentiment zayıfsa (<0.15 abs) çatışmaya dahil etme.
    Teknik ve efsane ters yöndeyse çatışma var.
    """
    # Sentiment çok zayıfsa (API yok veya nötr piyasa) görmezden gel
    sentiment_guclu = abs(sentiment_skor) >= 0.15

    aktif_skorlar = [teknik_skor, efsane_skor]
    if sentiment_guclu:
        aktif_skorlar.append(sentiment_skor)

    # Sadece net sinyaller (±0.15 üstü) say
    pozitif = sum(1 for s in aktif_skorlar if s > 0.15)
    negatif = sum(1 for s in aktif_skorlar if s < -0.15)

    # Tüm aktif sinyaller aynı yöndeyse çatışma yok
    return pozitif >= 1 and negatif >= 1


# ─────────────────────────────────────────────
# BÖLÜM 4: DİNAMİK POZİSYON BÜYÜKLÜĞÜ (Kelly Kriteri) 🆕
# ─────────────────────────────────────────────
def pozisyon_buyuklugu_hesapla(toplam_skor: float) -> float:
    """
    Kelly Kriteri ilhamıyla dinamik pozisyon büyüklüğü.
    Sistem ne kadar eminse o kadar çok para basar.

    Eşikler (abs(toplam_skor)):
        ≥ 0.60  → Mükemmel sinyal  → Kasanın %35'i
        ≥ 0.40  → Güçlü sinyal     → Kasanın %25'i
        ≥ 0.30  → Orta sinyal      → Kasanın %15'i
        < 0.30  → Zayıf sinyal     → Kasanın %10'u (minimum)
    """
    abs_skor = abs(toplam_skor)
    if abs_skor >= 0.60:
        return 0.35   # 💪 Mükemmel → %35 — ağır yumruk
    elif abs_skor >= 0.40:
        return 0.25   # 👍 Güçlü   → %25
    elif abs_skor >= 0.30:
        return 0.15   # 🤔 Orta    → %15
    else:
        return 0.10   # ⚠️  Zayıf   → %10 (minimum)


# ─────────────────────────────────────────────
# BÖLÜM 4b: STOP-LOSS & TAKE-PROFIT HESAPLAMA
# ─────────────────────────────────────────────
def sl_tp_hesapla(fiyat: float, sinyal: str, guven_skoru: float) -> dict:
    """
    Güven skoruna göre dinamik SL/TP seviyeleri hesaplar.
    Yüksek güven = daha geniş TP, daha sıkı SL.
    Risk/Ödül oranı her zaman minimum 1:2.
    """
    if sinyal == "HOLD" or fiyat == 0:
        return {"stop_loss": None, "take_profit": None, "risk_odül": None}

    # Güven skoru yükseldikçe daha agresif hedef
    if guven_skoru >= 0.7:
        sl_pct = 0.03    # %3 stop
        tp_pct = 0.09    # %9 hedef (1:3)
    elif guven_skoru >= 0.5:
        sl_pct = 0.025   # %2.5 stop
        tp_pct = 0.06    # %6 hedef (1:2.4)
    else:
        sl_pct = 0.02    # %2 stop
        tp_pct = 0.04    # %4 hedef (1:2)

    if sinyal == "LONG":
        sl = round(fiyat * (1 - sl_pct), 2)
        tp = round(fiyat * (1 + tp_pct), 2)
    else:  # SHORT
        sl = round(fiyat * (1 + sl_pct), 2)
        tp = round(fiyat * (1 - tp_pct), 2)

    risk_odul = round(tp_pct / sl_pct, 1)

    return {
        "stop_loss"  : sl,
        "take_profit": tp,
        "risk_odül"  : f"1:{risk_odul}",
        "sl_yuzde"   : f"%{sl_pct*100:.1f}",
        "tp_yuzde"   : f"%{tp_pct*100:.1f}",
    }


# ─────────────────────────────────────────────
# BÖLÜM 5: ANA KARAR MOTORU
# ─────────────────────────────────────────────
def final_karar_uret(sembol: str,
                     teknik: dict,
                     sentiment: dict,
                     efsane: dict) -> dict:
    """
    3 katmanı birleştirir, ağırlıklı skor üretir, final karar verir.
    """
    # Skorları dönüştür
    t_skor = teknik_skora_cevir(teknik)
    s_skor = sentiment_skora_cevir(sentiment)
    e_skor = efsane_skora_cevir(efsane)

    # Sentiment zayıfsa ağırlığını teknik ve efsaneye dağıt
    if abs(s_skor) < 0.10:
        # Sentiment neredeyse nötr — ağırlığını %60/%40 oranında dağıt
        agirlik_t = AGIRLIK_TEKNIK    + AGIRLIK_SENTIMENT * 0.60
        agirlik_e = AGIRLIK_EFSANE    + AGIRLIK_SENTIMENT * 0.40
        agirlik_s = 0.0
    else:
        agirlik_t = AGIRLIK_TEKNIK
        agirlik_e = AGIRLIK_EFSANE
        agirlik_s = AGIRLIK_SENTIMENT

    # Ağırlıklı toplam skor (-1 ile +1 arası)
    toplam_skor = (
        t_skor * agirlik_t +
        s_skor * agirlik_s +
        e_skor * agirlik_e
    )
    toplam_skor = round(toplam_skor, 3)

    # Çatışma kontrolü
    catisma = catisma_var_mi(t_skor, e_skor, s_skor)

    # Final karar
    if catisma:
        sinyal = "HOLD"
        guven  = "DÜŞÜK"
        guven_skoru = abs(toplam_skor)
        aciklama = "⚠️ Ajanlar çelişiyor — güvenli bekleme"
    elif toplam_skor >= ESIK_YUKSEK:
        sinyal = "LONG"
        guven  = "YÜKSEK"
        guven_skoru = toplam_skor
        aciklama = "💪 Güçlü yükseliş konsensüsü"
    elif toplam_skor >= ESIK_ORTA:
        sinyal = "LONG"
        guven  = "ORTA"
        guven_skoru = toplam_skor
        aciklama = "👍 Zayıf yükseliş eğilimi"
    elif toplam_skor <= -ESIK_YUKSEK:
        sinyal = "SHORT"
        guven  = "YÜKSEK"
        guven_skoru = abs(toplam_skor)
        aciklama = "💪 Güçlü düşüş konsensüsü"
    elif toplam_skor <= -ESIK_ORTA:
        sinyal = "SHORT"
        guven  = "ORTA"
        guven_skoru = abs(toplam_skor)
        aciklama = "👍 Zayıf düşüş eğilimi"
    else:
        sinyal = "HOLD"
        guven  = "DÜŞÜK"
        guven_skoru = abs(toplam_skor)
        aciklama = "🟡 Net sinyal yok — bekleme"

    # Fiyat bilgisi
    fiyat = teknik.get("veri", {}).get("son_kapanış", 0) if teknik else 0

    # SL/TP hesapla
    sl_tp = sl_tp_hesapla(fiyat, sinyal, guven_skoru)

    # Katman özeti
    katman_ozet = {
        "teknik": {
            "sinyal" : teknik.get("karar", {}).get("SİNYAL", "N/A") if teknik else "N/A",
            "puan"   : teknik.get("karar", {}).get("PUAN", 0) if teknik else 0,
            "skor"   : t_skor,
        },
        "sentiment": {
            "skor"   : s_skor,
            "yorum"  : sentiment.get("sentiment_yorum", "N/A") if sentiment else "N/A",
        },
        "efsane": {
            "konsensus": efsane.get("konsensus", "N/A") if efsane else "N/A",
            "long_oran": efsane.get("long_oran", 0) if efsane else 0,
            "short_oran":efsane.get("short_oran", 0) if efsane else 0,
            "skor"     : e_skor,
        },
    }

    return {
        "symbol"       : sembol,
        "fiyat"        : fiyat,
        "final_sinyal" : sinyal,
        "guven"        : guven,
        "guven_skoru"  : round(guven_skoru, 3),
        "toplam_skor"  : toplam_skor,
        "catisma"      : catisma,
        "aciklama"     : aciklama,
        "stop_loss"    : sl_tp["stop_loss"],
        "take_profit"  : sl_tp["take_profit"],
        "risk_odül"    : sl_tp.get("risk_odül"),
        "sl_yuzde"     : sl_tp.get("sl_yuzde"),
        "tp_yuzde"     : sl_tp.get("tp_yuzde"),
        "katmanlar"    : katman_ozet,
    }


# ─────────────────────────────────────────────
# BÖLÜM 6: RAPORLAMA
# ─────────────────────────────────────────────
def raporu_yazdir(kararlar: list[dict]) -> None:
    sinyal_ikon = {"LONG": "🟢", "SHORT": "🔴", "HOLD": "🟡"}
    guven_ikon  = {"YÜKSEK": "💪", "ORTA": "👍", "DÜŞÜK": "🤔"}

    print(f"\n{'═'*80}")
    print(f"  {'SEMBOL':<10} {'KARAR':<8} {'GÜVEN':<10} {'SKOR':>6}  "
          f"{'FİYAT':>8}  {'STOP':>9}  {'HEDEF':>9}  {'R/Ö':>5}")
    print(f"{'─'*80}")

    for k in kararlar:
        ki  = sinyal_ikon.get(k["final_sinyal"], "⚪")
        gi  = guven_ikon.get(k["guven"], "")
        cat = " ⚠️" if k["catisma"] else ""

        fiyat = f"${k['fiyat']:.2f}" if k["fiyat"] else "N/A"
        sl    = f"${k['stop_loss']:.2f}"   if k["stop_loss"]    else "  -"
        tp    = f"${k['take_profit']:.2f}" if k["take_profit"]  else "  -"
        ro    = k["risk_odül"] or "-"

        print(
            f"  {k['symbol']:<10} "
            f"{ki} {k['final_sinyal']:<6} "
            f"{gi} {k['guven']:<8} "
            f"{k['toplam_skor']:>+6.3f}  "
            f"{fiyat:>8}  "
            f"{sl:>9}  "
            f"{tp:>9}  "
            f"{ro:>5}"
            f"{cat}"
        )
    print(f"{'═'*80}")


def detay_yazdir(kararlar: list[dict]) -> None:
    """Sadece LONG ve SHORT kararların detaylı gerekçesini yazar."""
    islem_kararlar = [k for k in kararlar if k["final_sinyal"] != "HOLD"]

    if not islem_kararlar:
        print("\n  📭 Bu seansta işlem sinyali yok — tüm pozisyonlar beklemede.")
        return

    print(f"\n{'═'*80}")
    print(f"  📋 İŞLEM SİNYALLERİ — DETAY")
    print(f"{'═'*80}")

    for k in islem_kararlar:
        ki = {"LONG": "🟢", "SHORT": "🔴"}.get(k["final_sinyal"])
        print(f"\n  {ki} {k['symbol']} → {k['final_sinyal']} | Güven: {k['guven']} | Skor: {k['toplam_skor']:+.3f}")
        print(f"  {'─'*60}")
        print(f"  💰 Fiyat    : ${k['fiyat']}")
        print(f"  🛑 Stop-Loss: ${k['stop_loss']} ({k['sl_yuzde']})")
        print(f"  🎯 Hedef    : ${k['take_profit']} ({k['tp_yuzde']})")
        print(f"  ⚖️  Risk/Ödül: {k['risk_odül']}")
        print(f"  📝 Açıklama : {k['aciklama']}")

        kat = k["katmanlar"]
        print(f"\n  Katman Katkıları:")
        print(f"    Teknik   ({AGIRLIK_TEKNIK*100:.0f}%): {kat['teknik']['sinyal']:<6} "
              f"puan={kat['teknik']['puan']:+d}, skor={kat['teknik']['skor']:+.3f}")
        print(f"    Efsane   ({AGIRLIK_EFSANE*100:.0f}%): {kat['efsane']['konsensus']:<6} "
              f"L:{kat['efsane']['long_oran']}% S:{kat['efsane']['short_oran']}%, "
              f"skor={kat['efsane']['skor']:+.3f}")
        print(f"    Sentiment({AGIRLIK_SENTIMENT*100:.0f}%): {kat['sentiment']['yorum']:<20} "
              f"skor={kat['sentiment']['skor']:+.3f}")


def ozet_istatistik(kararlar: list[dict]) -> None:
    """Seansın özet istatistiklerini gösterir."""
    long_  = [k for k in kararlar if k["final_sinyal"] == "LONG"]
    short_ = [k for k in kararlar if k["final_sinyal"] == "SHORT"]
    hold_  = [k for k in kararlar if k["final_sinyal"] == "HOLD"]
    catisma_ = [k for k in kararlar if k["catisma"]]

    print(f"\n  📊 SEANS ÖZETİ:")
    print(f"     🟢 LONG : {len(long_)} varlık  → {', '.join(k['symbol'] for k in long_) or 'yok'}")
    print(f"     🔴 SHORT: {len(short_)} varlık  → {', '.join(k['symbol'] for k in short_) or 'yok'}")
    print(f"     🟡 HOLD : {len(hold_)} varlık")
    if catisma_:
        print(f"     ⚠️  ÇATIŞMA: {', '.join(k['symbol'] for k in catisma_)}")


# Claude API kullanarak nihai kararı onaylar ve Alpaca için JSON üretir.

def claude_otonom_onay(symbol, fiyat, sinyal, guven, toplam_skor, sl, tp):
    """Claude 3.5 Sonnet ile nihai kararı onaylar ve Alpaca için JSON üretir."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("  ⚠️ CLAUDE API yok! İşlem Alpaca'ya GÖNDERİLMEYECEK.")
        return {"action": "HOLD", "telegram_log": "API bekleniyor."}

    try:
        client = anthropic.Anthropic(api_key=api_key)
        
        prompt = f"""
        Sen, 1500$ sermayeli %100 Otonom Kantitatif Hedge Fonumuzun Baş Stratejisti ve 'Son Karar Merciisin'. 
        Sistemin ürettiği matematiksel veriler şunlar:
        - Varlık: {symbol} | Güncel Fiyat: {fiyat}
        - Sistem Kararı: {sinyal} (Güven: {guven}, Skor: {toplam_skor})
        - Risk Skoru: Stop-Loss: {sl}, Take-Profit: {tp}
        
        GÖREVİN:
        1. Eğer {sinyal} kararı "HOLD" ise, işlemi geç.
        2. Eğer "LONG" veya "SHORT" ise; son bir kez kontrol et. Saçma bir anomali seziyorsan "VETO" yap.
        3. Kararın kesinleştiğinde; sistemin (Alpaca API) doğrudan okuyup işlem açacağı bir JSON formatı üret.

        ÇIKTI FORMATI:
        SADECE AŞAĞIDAKİ JSON FORMATINI DÖNDÜR. DIŞINDA HİÇBİR KELİME YAZMA:
        {{
            "action": "EXECUTE",
            "symbol": "{symbol}",
            "order_type": "{sinyal}",
            "stop_loss": {sl},
            "telegram_log": "🟢 {symbol} LONG emri Alpaca'ya iletildi. Fiyat: ${fiyat}."
        }}
        """

        message = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=300,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}]
        )
        
        return json.loads(message.content[0].text)
        
    except Exception as e:
        print(f"  ❌ Claude API Hatası: {e}")
        return {"action": "HOLD", "telegram_log": "Hata oluştu."}


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{'═'*80}")
    print(f"  Algoritmik Hedge Fon | State Manager v0.1")
    print(f"  Final Karar Motoru: Teknik(%40) + Efsane(%35) + Sentiment(%25)")
    print(f"{'═'*80}\n")

    # JSON dosyalarını oku
    print(f"📂 Ajan raporları yükleniyor...")
    teknik_map    = teknik_veri_oku()
    sentiment_map = sentiment_veri_oku()
    efsane_map    = efsane_veri_oku()

    if not teknik_map:
        print("\n[KRİTİK HATA] Teknik veri yok. Önce: python mock_agent.py")
        exit(1)

    yuklenen = []
    if teknik_map:    yuklenen.append(f"Teknik ({len(teknik_map)} varlık)")
    if sentiment_map: yuklenen.append(f"Sentiment ({len(sentiment_map)} varlık)")
    if efsane_map:    yuklenen.append(f"Efsane ({len(efsane_map)} varlık)")
    print(f"  ✅ {' | '.join(yuklenen)}\n")

    # Tüm semboller için karar üret
    semboller = list(teknik_map.keys())
    kararlar  = []

    print(f"⚙️  Final kararlar üretiliyor...")
    for sembol in semboller:
        karar = final_karar_uret(
            sembol    = sembol,
            teknik    = teknik_map.get(sembol),
            sentiment = sentiment_map.get(sembol),
            efsane    = efsane_map.get(sembol),
        )
        kararlar.append(karar)

        ki = {"LONG": "🟢", "SHORT": "🔴", "HOLD": "🟡"}.get(karar["final_sinyal"], "⚪")
        cat = " ⚠️ ÇATIŞMA" if karar["catisma"] else ""
        print(f"  {sembol:<8} → {ki} {karar['final_sinyal']:<6} "
              f"({karar['guven']:<8}) skor: {karar['toplam_skor']:+.3f}{cat}")

    # Özet tablo
    raporu_yazdir(kararlar)

    # İşlem sinyali detayları
    detay_yazdir(kararlar)

    # İstatistik
    ozet_istatistik(kararlar)

    # JSON kaydet
    cikti = {
        "tarih"     : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "agirliklar": {
            "teknik"   : AGIRLIK_TEKNIK,
            "efsane"   : AGIRLIK_EFSANE,
            "sentiment": AGIRLIK_SENTIMENT,
        },
        "kararlar"  : kararlar,
    }
    Path(DOSYA_CIKTI).write_text(
        json.dumps(cikti, ensure_ascii=False, indent=2)
    )
    print(f"\n💾 {DOSYA_CIKTI} kaydedildi.")
    print(f"\n⚡ SONRAKI ADIM: Pazartesi API gelince Claude final kararı")
    print(f"   onaylayacak ve Alpaca'ya emir gönderilecek.\n")