"""
pairs_agent.py [V6 — Ornstein-Uhlenbeck Pairs Trading]
=========================================================
Algoritmik Hedge Fon — İkinci Motor: Pairs Trading

Mevcut sistem tek hisse üzerine LONG/SHORT alır.
Bu modül ÇIFT hisse üzerine spread ticareti yapar.

Fikir: İki ilgili hisse arasındaki fiyat "bağı" koptuğunda,
       hangisinin normale döneceğini matematiksel olarak hesapla.

ÇIFTLER:
    NVDA / SOXX   — Semiconductor leader vs ETF
    GLD  / USO    — Altın vs Ham Petrol (makro hedge çifti)
    AVGO / NVDA   — İki yarı iletken devi
    PLTR / META   — Yazılım/veri şirketleri

WORKFLOW:
    1. pairs_agent.py çalışır → ou_spread_analizi() ile sinyaller hesaplanır
    2. pairs_rapor.json'a yazar
    3. state_manager.py bunu okuyarak LONG/SHORT kararını güçlendirebilir
    4. alpaca_trader.py çift pozisyon (hedge) açabilir

ÇALIŞTIRMA:
    python pairs_agent.py

    Veya import:
        from pairs_agent import ciftleri_tara
        sinyaller = ciftleri_tara()
"""

import json
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from quant_math import ou_spread_analizi, copula_korelasyon_kalkan

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
CIFTLER = [
    ("NVDA", "SOXX"),   # Semiconductor leader vs ETF
    ("GLD",  "USO"),    # Altın vs Ham Petrol
    ("AVGO", "NVDA"),   # İki yarı iletken devi
    ("PLTR", "META"),   # Yazılım/veri çifti
    ("LMT",  "LLY"),    # Savunma vs İlaç (makro hedge)
]

PERIOD   = "1y"
INTERVAL = "1d"

# Z-skoru eşikleri
Z_GIRIS  = 2.0   # |Z| > bu değer → pozisyon aç
Z_CIKIS  = 0.5   # |Z| < bu değer → pozisyonu kapat

# Yarı-ömür filtreleri (gün)
MIN_YARI_OMUR = 2    # Çok hızlı dönüş → maliyetli
MAX_YARI_OMUR = 30   # Çok yavaş → sermaye bağlar

OUTPUT_FILE = "pairs_rapor.json"


# ─────────────────────────────────────────────
# BÖLÜM 1: VERİ ÇEKİMİ
# ─────────────────────────────────────────────
def cift_veri_cek(sembol1: str, sembol2: str) -> tuple[pd.Series | None, pd.Series | None]:
    """İki sembol için kapanış fiyatlarını çeker, hizalar."""
    try:
        t1 = yf.Ticker(sembol1).history(period=PERIOD, interval=INTERVAL)["Close"]
        t2 = yf.Ticker(sembol2).history(period=PERIOD, interval=INTERVAL)["Close"]

        if t1.empty or t2.empty or len(t1) < 60 or len(t2) < 60:
            return None, None

        # Tarihleri hizala (inner join)
        df = pd.DataFrame({sembol1: t1, sembol2: t2}).dropna()
        return df[sembol1], df[sembol2]

    except Exception as e:
        print(f"  ❌ {sembol1}/{sembol2} veri hatası: {e}")
        return None, None


# ─────────────────────────────────────────────
# BÖLÜM 2: SPREAD SİNYAL FİLTRELEME
# ─────────────────────────────────────────────
def sinyal_filtrele(ou_sonuc: dict) -> dict:
    """
    OU sonucuna ek filtreler uygular.

    Filtreler:
    1. Yarı-ömür: [MIN, MAX] aralığında mı?
    2. Z-skoru eşiği: |Z| > Z_GIRIS ise LONG/SHORT, < Z_CIKIS ise KAPAT
    3. Mean-reversion hız skoru
    """
    if "hata" in ou_sonuc or ou_sonuc.get("sinyal") in ("VERİ_YOK", "YETERSİZ_VERİ", "TREND_YAPMIYOR"):
        return {**ou_sonuc, "filtre_gecti": False, "filtre_neden": ou_sonuc.get("sinyal", "bilinmiyor")}

    yarim_omur = ou_sonuc.get("yarim_omur_gun", 999)
    z          = abs(ou_sonuc.get("z_skoru", 0))

    if yarim_omur < MIN_YARI_OMUR:
        return {**ou_sonuc, "filtre_gecti": False, "filtre_neden": f"Yarı-ömür {yarim_omur:.1f}g < {MIN_YARI_OMUR}g (çok hızlı)"}
    if yarim_omur > MAX_YARI_OMUR:
        return {**ou_sonuc, "filtre_gecti": False, "filtre_neden": f"Yarı-ömür {yarim_omur:.1f}g > {MAX_YARI_OMUR}g (çok yavaş)"}

    if ou_sonuc["sinyal"] in ("LONG_SPREAD", "SHORT_SPREAD") and z < Z_GIRIS:
        return {**ou_sonuc, "filtre_gecti": False, "filtre_neden": f"|Z|={z:.2f} < {Z_GIRIS} (eşik geçilmedi)"}

    return {**ou_sonuc, "filtre_gecti": True, "filtre_neden": "Tüm filtreler geçildi ✅"}


# ─────────────────────────────────────────────
# BÖLÜM 3: TÜM ÇİFTLERİ TARA
# ─────────────────────────────────────────────
def ciftleri_tara() -> list[dict]:
    """
    Tüm pair listesini tarar, OU sinyalleri hesaplar.

    Returns:
        list[dict]: Her çift için sonuç dict'i
    """
    sonuclar = []

    for (s1, s2) in CIFTLER:
        p1, p2 = cift_veri_cek(s1, s2)
        if p1 is None or p2 is None:
            sonuclar.append({
                "cift": f"{s1}/{s2}", "sinyal": "VERİ_YOK", "filtre_gecti": False
            })
            continue

        ou = ou_spread_analizi(p1, p2, s1, s2)
        ou_filtrelendi = sinyal_filtrele(ou)
        sonuclar.append(ou_filtrelendi)

    return sonuclar


# ─────────────────────────────────────────────
# BÖLÜM 4: PORTFÖY KORELASYON TARAMASI
# ─────────────────────────────────────────────
def portfoy_korelasyon_tara(watchlist: list[str]) -> dict:
    """
    Tüm watchlist için Copula portföy korelasyon kalkanını hesaplar.
    Sonucu copula_durum.json'a yazar (state_manager okur).
    """
    close_dict = {}
    for s in watchlist:
        try:
            seri = yf.Ticker(s).history(period="1mo", interval="1d")["Close"]
            if not seri.empty and len(seri) >= 15:
                close_dict[s] = seri
        except Exception:
            pass

    if len(close_dict) < 3:
        return {"sinyal": "YETERSİZ_VERİ", "ort_korelasyon": 0.0}

    copula = copula_korelasyon_kalkan(list(close_dict.keys()), close_dict)

    # copula_durum.json'a yaz → state_manager okur
    Path("copula_durum.json").write_text(
        json.dumps({**copula, "tarih": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                   ensure_ascii=False, indent=2)
    )
    return copula


# ─────────────────────────────────────────────
# BÖLÜM 5: RAPORLAMA
# ─────────────────────────────────────────────
def raporu_yazdir(sonuclar: list[dict], copula: dict) -> None:
    sinyal_ikon = {
        "LONG_SPREAD": "🟢", "SHORT_SPREAD": "🔴",
        "KAPAT": "🔵", "BEKLE": "🟡", "VERİ_YOK": "⚫",
        "TREND_YAPMIYOR": "⚪", "YETERSİZ_VERİ": "⚪",
    }

    print(f"\n{'═'*85}")
    print(f"  {'ÇİFT':<14} {'SİNYAL':<16} {'Z-SKOR':>8} {'T½ GÜN':>8} {'FILTRE':<6}  AÇIKLAMA")
    print(f"{'─'*85}")

    aktif = 0
    for s in sonuclar:
        ikon    = sinyal_ikon.get(s.get("sinyal", ""), "⚪")
        z       = f"{s.get('z_skoru', 0):+.2f}" if s.get("z_skoru") is not None else "  N/A"
        yo      = f"{s.get('yarim_omur_gun', 0):.1f}g" if s.get("yarim_omur_gun") else "  N/A"
        filtre  = "✅" if s.get("filtre_gecti") else "❌"
        aciklama= s.get("aciklama", s.get("filtre_neden", ""))[:40]

        print(f"  {s.get('cift','?'):<14} {ikon} {s.get('sinyal','?'):<14} "
              f"{z:>8}  {yo:>7}  {filtre}    {aciklama}")

        if s.get("filtre_gecti") and s.get("sinyal") in ("LONG_SPREAD", "SHORT_SPREAD"):
            aktif += 1

    print(f"{'═'*85}")
    print(f"  Aktif sinyal: {aktif}/{len(sonuclar)} çift")

    print(f"\n  📊 PORTFÖY KORELASYON (Copula Kalkan):")
    print(f"  Ortalama ρ : {copula.get('ort_korelasyon', 'N/A')}")
    print(f"  Durum      : {copula.get('sinyal', 'N/A')} — {copula.get('yorum', '')}")
    print(f"  GL Ağırlık : ×{copula.get('guvenli_liman_agirlik', 1.0)} (GLD/USO ESIK çarpanı)")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    WATCHLIST = [
        "NVDA", "AVGO", "SOXX", "PLTR", "MSTR", "IBIT",
        "ASTS", "VST", "LMT", "LLY", "TSLA",
        "GLD", "FXY", "META", "USO", "WMT", "QQQ"
    ]

    print(f"\n{'═'*85}")
    print(f"  Algoritmik Hedge Fon | Pairs Agent V6")
    print(f"  Ornstein-Uhlenbeck Spread Analizi | Copula Portföy Kalkanı")
    print(f"  {len(CIFTLER)} çift taranıyor...")
    print(f"{'═'*85}\n")

    # 1. Pairs tarama
    for i, (s1, s2) in enumerate(CIFTLER, 1):
        print(f"[{i}/{len(CIFTLER)}] {s1}/{s2} analiz ediliyor...", end=" ", flush=True)

    sonuclar = ciftleri_tara()

    for i, (sonuc, cift) in enumerate(zip(sonuclar, CIFTLER)):
        s1, s2 = cift
        sinyal = sonuc.get("sinyal", "?")
        filtre = "✅" if sonuc.get("filtre_gecti") else "❌"
        ikon = {"LONG_SPREAD": "🟢", "SHORT_SPREAD": "🔴", "KAPAT": "🔵"}.get(sinyal, "🟡")
        print(f"\r[{i+1}/{len(CIFTLER)}] {s1}/{s2}: {ikon} {sinyal} {filtre}" + " " * 20)

    # 2. Portföy korelasyon
    print(f"\n  📊 Copula korelasyon taraması ({len(WATCHLIST)} sembol)...", end=" ", flush=True)
    copula = portfoy_korelasyon_tara(WATCHLIST)
    print(f"✅ ρ̄={copula.get('ort_korelasyon', 'N/A')} — {copula.get('sinyal', '?')}")

    # 3. Rapor
    raporu_yazdir(sonuclar, copula)

    cikti = {
        "tarih"    : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "strateji" : "Ornstein-Uhlenbeck Pairs + Copula V6",
        "ciftler"  : sonuclar,
        "copula"   : copula,
    }
    Path(OUTPUT_FILE).write_text(
        json.dumps(cikti, ensure_ascii=False, indent=2, default=str)
    )

    aktif_n = sum(1 for s in sonuclar if s.get("filtre_gecti") and s.get("sinyal") in ("LONG_SPREAD", "SHORT_SPREAD"))
    print(f"\n💾 {OUTPUT_FILE} kaydedildi.")
    print(f"   Aktif pairs sinyali: {aktif_n}")
    print(f"   Copula durumu: {copula.get('sinyal')} → copula_durum.json ✅\n")
