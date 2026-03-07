"""
state_manager.py  [V5 — ATR Bazlı Dinamik SL/TP]
==================================================
Algoritmik Hedge Fon — Final Karar Motoru

V5 DEĞİŞİKLİĞİ:
    - sl_tp_hesapla() → atr_sl_tp_hesapla() ile değiştirildi
    - ATR verisi önce rapor.json[veri][ATR_14]'ten çekilir
    - Fallback: rapor.json'da yoksa legends_rapor.json[atr]'ye bakılır
    - İkisi de yoksa sabit % yöntemi kullanılır (geriye dönük uyum)

    Backtest V5 katsayıları:
        YÜKSEK güven: SL = 1.8 × ATR, TP = 4.5 × ATR  (~1:2.5)
        ORTA güven  : SL = 1.5 × ATR, TP = 3.5 × ATR  (~1:2.3)
        Fallback    : Eski sabit % (V4 uyumu)

AKIŞ:
    mock_agent.py → rapor.json (ATR_14 dahil)
    legends_agent → legends_rapor.json (atr dahil)
    sentiment_agent → sentiment_rapor.json
    state_manager  → final_karar.json (ATR bazlı SL/TP)
    alpaca_trader  → İşlem + Pyramiding

API GEREKTİRMEZ — JSON dosyalarını okuyarak çalışır.
"""

import os
import json
from datetime import datetime
from pathlib import Path

# ─────────────────────────────────────────────
# CONFIG — Ağırlıklar
# ─────────────────────────────────────────────
AGIRLIK_TEKNIK    = 0.40
AGIRLIK_EFSANE    = 0.35
AGIRLIK_SENTIMENT = 0.25

ESIK_YUKSEK = 0.30   # API gelince → 0.45
ESIK_ORTA   = 0.15   # API gelince → 0.28

DOSYA_TEKNIK    = "rapor.json"
DOSYA_SENTIMENT = "sentiment_rapor.json"
DOSYA_EFSANE    = "legends_rapor.json"
DOSYA_CIKTI     = "final_karar.json"

# V5: ATR katsayıları (true_backtest_v4.py ile senkron)
ATR_SL_YUKSEK = 1.8   # YÜKSEK güven: 1.8 × ATR stop
ATR_TP_YUKSEK = 4.5   # YÜKSEK güven: 4.5 × ATR hedef (~1:2.5)
ATR_SL_ORTA   = 1.5   # ORTA güven: 1.5 × ATR stop
ATR_TP_ORTA   = 3.5   # ORTA güven: 3.5 × ATR hedef (~1:2.3)


# ─────────────────────────────────────────────
# BÖLÜM 1: JSON OKUYUCULAR
# ─────────────────────────────────────────────
def teknik_veri_oku() -> dict:
    if not Path(DOSYA_TEKNIK).exists():
        print(f"  ⚠️  {DOSYA_TEKNIK} bulunamadı. Önce: python mock_agent.py")
        return {}
    with open(DOSYA_TEKNIK, "r", encoding="utf-8") as f:
        veri = json.load(f)
    return {r["veri"]["symbol"]: r for r in veri.get("varlıklar", [])}


def sentiment_veri_oku() -> dict:
    if not Path(DOSYA_SENTIMENT).exists():
        print(f"  ⚠️  {DOSYA_SENTIMENT} bulunamadı.")
        return {}
    with open(DOSYA_SENTIMENT, "r", encoding="utf-8") as f:
        veri = json.load(f)
    return {r["symbol"]: r for r in veri.get("sonuclar", [])}


def efsane_veri_oku() -> dict:
    if not Path(DOSYA_EFSANE).exists():
        print(f"  ⚠️  {DOSYA_EFSANE} bulunamadı. Önce: python legends_agent.py")
        return {}
    with open(DOSYA_EFSANE, "r", encoding="utf-8") as f:
        veri = json.load(f)
    return {r["symbol"]: r for r in veri.get("sonuclar", [])}


# ─────────────────────────────────────────────
# BÖLÜM 2: SİNYAL DÖNÜŞTÜRÜCÜLER
# ─────────────────────────────────────────────
def teknik_skora_cevir(karar: dict) -> float:
    if not karar: return 0.0
    puan = karar.get("karar", {}).get("PUAN", 0)
    return max(-1.0, min(1.0, puan / 10.0))  # V5: max puan 10 oldu


def sentiment_skora_cevir(sentiment: dict) -> float:
    if not sentiment: return 0.0
    return sentiment.get("sentiment_skoru", 0.0)


def efsane_skora_cevir(efsane: dict) -> float:
    if not efsane: return 0.0
    long_oran  = efsane.get("long_oran", 0) / 100
    short_oran = efsane.get("short_oran", 0) / 100
    return round(long_oran - short_oran, 3)


# ─────────────────────────────────────────────
# BÖLÜM 3: ÇATIŞMA DEDEKTÖRÜ
# ─────────────────────────────────────────────
def catisma_var_mi(teknik_skor: float, efsane_skor: float, sentiment_skor: float) -> bool:
    """
    Güçlü sinyaller ZITTI yönde gidiyorsa çatışma var.
    Threshold: 0.15 (zayıf sinyaller çatışmaya dahil edilmez).
    """
    esik = 0.15
    if abs(teknik_skor) > esik and abs(efsane_skor) > esik:
        if (teknik_skor > 0) != (efsane_skor > 0):
            return True
    return False


# ─────────────────────────────────────────────
# BÖLÜM 4: KELLY KRİTERİ POZİSYON BÜYÜKLÜĞÜ
# ─────────────────────────────────────────────
def pozisyon_buyuklugu_hesapla(toplam_skor: float) -> float:
    abs_skor = abs(toplam_skor)
    if abs_skor >= 0.60: return 0.35
    elif abs_skor >= 0.40: return 0.25
    elif abs_skor >= 0.30: return 0.15
    else: return 0.10


# ─────────────────────────────────────────────
# BÖLÜM 4b: V5 — ATR BAZLI DİNAMİK SL/TP
# ─────────────────────────────────────────────
def atr_sl_tp_hesapla(
    fiyat       : float,
    sinyal      : str,
    guven_skoru : float,
    atr         : float | None,
) -> dict:
    """
    V5 ATR bazlı dinamik Stop-Loss / Take-Profit hesabı.

    Öncelik sırası:
        1. ATR verisi geldi → ATR × katsayı (V5 backtest ile senkron)
        2. ATR yok → eski sabit % (V4 fallback, geriye dönük uyum)

    Katsayılar (true_backtest_v4.py ile birebir senkron):
        YÜKSEK: SL = 1.8 × ATR, TP = 4.5 × ATR  → R/R ≈ 1:2.5
        ORTA  : SL = 1.5 × ATR, TP = 3.5 × ATR  → R/R ≈ 1:2.3

    Volatil hisse (NVDA ATR ~$8):
        YÜKSEK güven: SL = $14.4, TP = $36 — gürültüye takılmaz
    Düşük volatil (LMT ATR ~$12):
        YÜKSEK güven: SL = $21.6, TP = $54 — proporsiyonel
    """
    if sinyal == "HOLD" or fiyat == 0:
        return {"stop_loss": None, "take_profit": None, "risk_odül": None,
                "sl_tipi": "HOLD", "atr_kullanildi": False}

    # ── ATR bazlı hesap (V5) ────────────────────────────────────────────
    if atr and atr > 0:
        if guven_skoru >= 0.40:   # YÜKSEK güven eşiği
            sl_katsayi = ATR_SL_YUKSEK
            tp_katsayi = ATR_TP_YUKSEK
        else:                     # ORTA güven
            sl_katsayi = ATR_SL_ORTA
            tp_katsayi = ATR_TP_ORTA

        sl_miktar = atr * sl_katsayi
        tp_miktar = atr * tp_katsayi

        if sinyal == "LONG":
            sl = round(fiyat - sl_miktar, 2)
            tp = round(fiyat + tp_miktar, 2)
        else:
            sl = round(fiyat + sl_miktar, 2)
            tp = round(fiyat - tp_miktar, 2)

        rr = round(tp_katsayi / sl_katsayi, 1)

        return {
            "stop_loss"       : sl,
            "take_profit"     : tp,
            "risk_odül"       : f"1:{rr}",
            "sl_tipi"         : f"ATR×{sl_katsayi}",
            "tp_tipi"         : f"ATR×{tp_katsayi}",
            "atr_kullanildi"  : True,
            "atr_degeri"      : round(atr, 4),
        }

    # ── Fallback: Sabit % (ATR yoksa, V4 geriye dönük uyum) ────────────
    if guven_skoru >= 0.7:
        sl_pct, tp_pct = 0.03, 0.09
    elif guven_skoru >= 0.5:
        sl_pct, tp_pct = 0.025, 0.06
    else:
        sl_pct, tp_pct = 0.02, 0.04

    if sinyal == "LONG":
        sl = round(fiyat * (1 - sl_pct), 2)
        tp = round(fiyat * (1 + tp_pct), 2)
    else:
        sl = round(fiyat * (1 + sl_pct), 2)
        tp = round(fiyat * (1 - tp_pct), 2)

    return {
        "stop_loss"     : sl,
        "take_profit"   : tp,
        "risk_odül"     : f"1:{round(tp_pct/sl_pct, 1)}",
        "sl_tipi"       : f"SABİT %{sl_pct*100:.1f} (ATR yok — fallback)",
        "atr_kullanildi": False,
    }


# ─────────────────────────────────────────────
# BÖLÜM 5: ANA KARAR MOTORU
# ─────────────────────────────────────────────
def final_karar_uret(
    sembol    : str,
    teknik    : dict,
    sentiment : dict,
    efsane    : dict,
) -> dict:
    """
    3 katmanı birleştirir → ağırlıklı skor → final karar.
    V5: SL/TP için ATR önce teknik dict'ten, sonra efsane dict'ten alınır.
    """
    t_skor = teknik_skora_cevir(teknik)
    s_skor = sentiment_skora_cevir(sentiment)
    e_skor = efsane_skora_cevir(efsane)

    # Sentiment zayıfsa ağırlığını dağıt
    if abs(s_skor) < 0.10:
        agirlik_t = AGIRLIK_TEKNIK    + AGIRLIK_SENTIMENT * 0.60
        agirlik_e = AGIRLIK_EFSANE    + AGIRLIK_SENTIMENT * 0.40
        agirlik_s = 0.0
    else:
        agirlik_t, agirlik_e, agirlik_s = AGIRLIK_TEKNIK, AGIRLIK_EFSANE, AGIRLIK_SENTIMENT

    toplam_skor = round(t_skor * agirlik_t + s_skor * agirlik_s + e_skor * agirlik_e, 3)
    catisma     = catisma_var_mi(t_skor, e_skor, s_skor)

    # Final karar
    if catisma:
        sinyal, guven = "HOLD", "DÜŞÜK"
        guven_skoru   = abs(toplam_skor)
        aciklama      = "⚠️ Ajanlar çelişiyor — güvenli bekleme"
    elif toplam_skor >= ESIK_YUKSEK:
        sinyal, guven = "LONG", "YÜKSEK"
        guven_skoru   = toplam_skor
        aciklama      = "💪 Güçlü yükseliş konsensüsü"
    elif toplam_skor >= ESIK_ORTA:
        sinyal, guven = "LONG", "ORTA"
        guven_skoru   = toplam_skor
        aciklama      = "👍 Zayıf yükseliş eğilimi"
    elif toplam_skor <= -ESIK_YUKSEK:
        sinyal, guven = "SHORT", "YÜKSEK"
        guven_skoru   = abs(toplam_skor)
        aciklama      = "💪 Güçlü düşüş konsensüsü"
    elif toplam_skor <= -ESIK_ORTA:
        sinyal, guven = "SHORT", "ORTA"
        guven_skoru   = abs(toplam_skor)
        aciklama      = "👍 Zayıf düşüş eğilimi"
    else:
        sinyal, guven = "HOLD", "DÜŞÜK"
        guven_skoru   = abs(toplam_skor)
        aciklama      = "🤔 Net sinyal yok"

    # Güncel fiyat
    try:
        fiyat = float(teknik["veri"]["son_kapanış"]) if teknik else 0.0
    except Exception:
        fiyat = 0.0

    # V5: ATR — Önce teknik (mock_agent), sonra efsane (legends_agent)
    atr = None
    try:
        atr = teknik["veri"].get("ATR_14") if teknik else None
    except Exception:
        pass
    if not atr:
        try:
            atr = efsane.get("atr") if efsane else None
        except Exception:
            pass

    # V5: ATR bazlı SL/TP
    sl_tp = atr_sl_tp_hesapla(fiyat, sinyal, guven_skoru, atr)

    # Pozisyon büyüklüğü (Kelly)
    poz_buyukluk = pozisyon_buyuklugu_hesapla(toplam_skor)

    # Katman özeti
    katman_ozet = {
        "teknik": {
            "sinyal" : teknik.get("karar", {}).get("SİNYAL", "N/A") if teknik else "N/A",
            "puan"   : teknik.get("karar", {}).get("PUAN", 0) if teknik else 0,
            "skor"   : t_skor,
        },
        "sentiment": {
            "skor"  : s_skor,
            "yorum" : sentiment.get("sentiment_yorum", "N/A") if sentiment else "N/A",
        },
        "efsane": {
            "konsensus" : efsane.get("konsensus", "N/A") if efsane else "N/A",
            "long_oran" : efsane.get("long_oran", 0) if efsane else 0,
            "short_oran": efsane.get("short_oran", 0) if efsane else 0,
            "skor"      : e_skor,
        },
    }

    return {
        "symbol"        : sembol,
        "fiyat"         : fiyat,
        "final_sinyal"  : sinyal,
        "guven"         : guven,
        "guven_skoru"   : round(guven_skoru, 3),
        "toplam_skor"   : toplam_skor,
        "catisma"       : catisma,
        "aciklama"      : aciklama,
        "stop_loss"     : sl_tp["stop_loss"],
        "take_profit"   : sl_tp["take_profit"],
        "risk_odül"     : sl_tp.get("risk_odül"),
        "sl_tipi"       : sl_tp.get("sl_tipi"),   # V5: ATR × katsayı mı, sabit % mi
        "atr_kullanildi": sl_tp.get("atr_kullanildi", False),
        "atr_degeri"    : sl_tp.get("atr_degeri"),
        "poz_buyukluk"  : poz_buyukluk,            # Hesabın kaçta kaçı → alpaca_trader okur
        "katmanlar"     : katman_ozet,
    }


# ─────────────────────────────────────────────
# BÖLÜM 6: RAPORLAMA
# ─────────────────────────────────────────────
def raporu_yazdir(kararlar: list[dict]) -> None:
    guven_ikon = {"YÜKSEK": "💪", "ORTA": "👍", "DÜŞÜK": "🤔"}

    print(f"\n{'─'*85}")
    print(f"  {'SEMBOL':<8} {'SİNYAL':<8} {'GÜVEN':<10} {'SKOR':>7}  "
          f"{'FİYAT':>8}  {'SL':>8}  {'TP':>8}  {'SL_TİPİ':<20}")
    print(f"{'─'*85}")

    for k in kararlar:
        si  = {"LONG": "🟢", "SHORT": "🔴", "HOLD": "🟡"}.get(k["final_sinyal"], "⚪")
        gi  = guven_ikon.get(k["guven"], "")
        fiy = f"${k['fiyat']:.2f}" if k["fiyat"] else " N/A"
        sl  = f"${k['stop_loss']:.2f}"   if k["stop_loss"]   else "  N/A"
        tp  = f"${k['take_profit']:.2f}" if k["take_profit"] else "  N/A"
        st  = k.get("sl_tipi", "")[:20]
        cat = " ⚠️" if k["catisma"] else ""
        print(
            f"  {k['symbol']:<8} {si} {k['final_sinyal']:<6} {gi} {k['guven']:<8} "
            f"{k['toplam_skor']:>+7.3f}  {fiy:>8}  {sl:>8}  {tp:>8}  {st}{cat}"
        )
    print(f"{'─'*85}")


def detay_yazdir(kararlar: list[dict]) -> None:
    islem_kararlar = [k for k in kararlar if k["final_sinyal"] != "HOLD"]
    if not islem_kararlar:
        print("\n  ℹ️  Bugün işlem sinyali yok — Tüm varlıklar HOLD")
        return

    print(f"\n  📋 İŞLEM SİNYALLERİ:")
    for k in islem_kararlar:
        si = "🟢" if k["final_sinyal"] == "LONG" else "🔴"
        print(f"\n  {si} {k['symbol']} → {k['final_sinyal']} ({k['guven']})")
        print(f"     Skor    : {k['toplam_skor']:+.3f} | {k['aciklama']}")
        print(f"     Fiyat   : ${k['fiyat']:.2f}")
        print(f"     SL      : ${k['stop_loss']} ({k.get('sl_tipi', '')})")
        print(f"     TP      : ${k['take_profit']} | R/R: {k.get('risk_odül', '?')}")
        print(f"     Poz.    : Hesabın %{k['poz_buyukluk']*100:.0f}'i")
        if k.get("atr_kullanildi"):
            print(f"     ATR     : ${k.get('atr_degeri', '?')} ✅ V5 ATR bazlı stop")
        else:
            print(f"     ATR     : ⚠️  Sabit % fallback kullanıldı")


def ozet_istatistik(kararlar: list[dict]) -> None:
    long_  = [k for k in kararlar if k["final_sinyal"] == "LONG"]
    short_ = [k for k in kararlar if k["final_sinyal"] == "SHORT"]
    hold_  = [k for k in kararlar if k["final_sinyal"] == "HOLD"]
    catisma_ = [k for k in kararlar if k["catisma"]]
    atr_kul  = [k for k in kararlar if k.get("atr_kullanildi")]

    print(f"\n  📊 SEANS ÖZETİ:")
    print(f"     🟢 LONG  : {len(long_)} varlık → {', '.join(k['symbol'] for k in long_) or 'yok'}")
    print(f"     🔴 SHORT : {len(short_)} varlık → {', '.join(k['symbol'] for k in short_) or 'yok'}")
    print(f"     🟡 HOLD  : {len(hold_)} varlık")
    print(f"     🎯 ATR SL: {len(atr_kul)}/{len(kararlar)} varlık V5 ATR bazlı stop kullandı")
    if catisma_:
        print(f"     ⚠️  ÇATIŞMA: {', '.join(k['symbol'] for k in catisma_)}")


# ─────────────────────────────────────────────
# CLAUDE API — Final Onay (API gelince aktif olur)
# ─────────────────────────────────────────────
def claude_otonom_onay(symbol, fiyat, sinyal, guven, toplam_skor, sl, tp, atr=None):
    """Claude 3.5 Sonnet ile nihai kararı onaylar ve Alpaca için JSON üretir."""
    try:
        import anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            print("  ⚠️ CLAUDE API yok! İşlem Alpaca'ya GÖNDERİLMEYECEK.")
            return {"action": "HOLD", "telegram_log": "API bekleniyor."}

        client = anthropic.Anthropic(api_key=api_key)
        atr_bilgi = f"ATR: ${atr:.2f} (V5 dinamik stop kullanıldı)" if atr else "ATR: Sabit % fallback"

        prompt = f"""
        Sen 1500$ sermayeli %100 Otonom Kantitatif Hedge Fonumuzun Baş Stratejisti ve 'Son Karar Merciisin'.
        Sistem kararı:
        - Varlık: {symbol} | Fiyat: {fiyat}
        - Karar: {sinyal} (Güven: {guven}, Skor: {toplam_skor})
        - Stop-Loss: {sl} | Take-Profit: {tp}
        - {atr_bilgi}

        GÖREVIN:
        1. HOLD ise işlemi geç.
        2. LONG/SHORT ise son kontrol. Anomali görürsen VETO yap.
        3. Onaylarsan aşağıdaki JSON formatını SADECE döndür:

        {{
            "action": "EXECUTE",
            "symbol": "{symbol}",
            "order_type": "{sinyal}",
            "stop_loss": {sl},
            "fiyat": {fiyat},
            "guven_skoru": {toplam_skor},
            "atr": {atr if atr else 0},
            "telegram_log": "{'🟢' if sinyal == 'LONG' else '🔴'} {symbol} {sinyal} emri Alpaca'ya iletildi. Fiyat: ${fiyat}."
        }}
        """

        message = client.messages.create(
            model="claude-opus-4-6",
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
    print(f"\n{'═'*85}")
    print(f"  Algoritmik Hedge Fon | State Manager V5")
    print(f"  Final Karar: Teknik(%40) + Efsane(%35) + Sentiment(%25)")
    print(f"  V5: ATR bazlı dinamik SL/TP (backtest ile senkron)")
    print(f"{'═'*85}\n")

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

    semboller = list(teknik_map.keys())
    kararlar  = []

    print(f"⚙️  Final kararlar üretiliyor (V5 ATR SL/TP)...")
    for sembol in semboller:
        karar = final_karar_uret(
            sembol    = sembol,
            teknik    = teknik_map.get(sembol),
            sentiment = sentiment_map.get(sembol),
            efsane    = efsane_map.get(sembol),
        )
        kararlar.append(karar)

        ki  = {"LONG": "🟢", "SHORT": "🔴", "HOLD": "🟡"}.get(karar["final_sinyal"], "⚪")
        cat = " ⚠️ ÇATIŞMA" if karar["catisma"] else ""
        atr_str = f" [ATR:${karar['atr_degeri']:.2f}✅]" if karar.get("atr_kullanildi") else " [fallback%]"
        print(f"  {sembol:<8} → {ki} {karar['final_sinyal']:<6} "
              f"({karar['guven']:<8}) skor:{karar['toplam_skor']:+.3f}{cat}{atr_str}")

    raporu_yazdir(kararlar)
    detay_yazdir(kararlar)
    ozet_istatistik(kararlar)

    cikti = {
        "tarih"      : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "versiyon"   : "V5 — ATR bazlı dinamik SL/TP",
        "agirliklar" : {"teknik": AGIRLIK_TEKNIK, "efsane": AGIRLIK_EFSANE, "sentiment": AGIRLIK_SENTIMENT},
        "atr_config" : {"sl_yuksek": ATR_SL_YUKSEK, "tp_yuksek": ATR_TP_YUKSEK,
                        "sl_orta": ATR_SL_ORTA, "tp_orta": ATR_TP_ORTA},
        "kararlar"   : kararlar,
    }
    Path(DOSYA_CIKTI).write_text(json.dumps(cikti, ensure_ascii=False, indent=2))
    print(f"\n💾 {DOSYA_CIKTI} kaydedildi.")
    print(f"\n⚡ V5 DURUM:")
    print(f"   ATR bazlı SL/TP: AKTIF ✅")
    print(f"   Backtest-canlı senkron: ✅")
    print(f"   Sonraki adım: python alpaca_trader.py (Pyramiding aktif)\n")