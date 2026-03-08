"""
sheets_pusher.py
================
Algoritmik Hedge Fon — Google Sheets Dashboard Entegrasyonu
Görev: mock_agent.py'nin ürettiği rapor.json'ı okur, Google Sheets'e yazar.

Gereksinimler:
    pip install gspread google-auth
    
.env dosyasına ekle:
    SHEET_ID=1H4IMIRpLLWOr1qQkQ9anYD6oFDRZLah366DKw37E5xY
    GCP_KEY_PATH=gcp_key.json
"""

import json
import os
from datetime import datetime
from pathlib import Path

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

load_dotenv()

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
SHEET_ID     = os.getenv("SHEET_ID", "1H4IMIRpLLWOr1qQkQ9anYD6oFDRZLah366DKw37E5xY")
GCP_KEY_PATH = os.getenv("GCP_KEY_PATH", "gcp_key.json")
RAPOR_DOSYA  = "rapor.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Sinyal renk kodları (Sheets arka plan rengi)
RENK = {
    "LONG" : {"red": 0.20, "green": 0.78, "blue": 0.35},   # Yeşil
    "SHORT": {"red": 0.91, "green": 0.26, "blue": 0.21},   # Kırmızı
    "HOLD" : {"red": 1.00, "green": 0.84, "blue": 0.00},   # Sarı
}


# ─────────────────────────────────────────────
# BÖLÜM 1: Google Sheets Bağlantısı
# ─────────────────────────────────────────────
def sheets_baglan():
    if not Path(GCP_KEY_PATH).exists():
        raise FileNotFoundError(
            f"'{GCP_KEY_PATH}' bulunamadı. "
            f"Dosyanın AI_Hedge_Fund/ klasöründe olduğundan emin ol."
        )
    creds  = Credentials.from_service_account_file(GCP_KEY_PATH, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID)


# ─────────────────────────────────────────────
# BÖLÜM 2: Rapor Sayfasını Yaz
# ─────────────────────────────────────────────
def rapor_sayfasini_yaz(sheet, veri: dict) -> None:
    """Ana rapor sayfasını baştan yazar — her çalışmada taze veri."""
    try:
        ws = sheet.worksheet("Rapor")
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(title="Rapor", rows=50, cols=12)

    varlıklar = veri.get("varlıklar", [])
    simdi     = veri.get("tarih", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

# --- Başlık Bloğu ---
    ws.update(values=[[f"🤖 Algoritmik Hedge Fon | Dashboard"]], range_name="A1")
    ws.update(values=[[f"Son Güncelleme: {simdi}"]], range_name="A2")
    ws.update(values=[[f"Mod: {veri.get('mod', 'MOCK')}"]], range_name="A3")

    # --- Sütun Başlıkları ---
    basliklar = [
        "SEMBOL", "FİYAT ($)", "DEĞİŞİM %", "RSI",
        "SMA20 POZ", "SMA50 POZ", "MACD HIST",
        "TREND", "SİNYAL", "PUAN", "GEREKÇE"
    ]
    ws.update(values=[basliklar], range_name="A5")

    # --- Veri Satırları ---
    satirlar = []
    for r in varlıklar:
        v = r["veri"]
        k = r["karar"]
        satirlar.append([
            v["symbol"],
            v["son_kapanış"],
            f"{v['günlük_değişim_%']:+.2f}%",
            v["RSI_14"],
            v["fiyat_sma20_poz"],
            v["fiyat_sma50_poz"],
            v["MACD_Histogram"],
            k["TREND"],
            k["SİNYAL"],
            k.get("PUAN", "-"),
            k["GEREKÇE"],
        ])

    if satirlar:
        ws.update(values=satirlar, range_name="A6")
    # --- Renklendirme (SİNYAL sütunu = I kolonu) ---
    for i, r in enumerate(varlıklar):
        sinyal = r["karar"]["SİNYAL"]
        renk   = RENK.get(sinyal, {"red": 1, "green": 1, "blue": 1})
        satir  = i + 6  # 5. satırdan itibaren veri başlıyor

        ws.format(f"I{satir}", {
            "backgroundColor": renk,
            "textFormat": {"bold": True},
            "horizontalAlignment": "CENTER",
        })

    # Başlık satırını formatla
    ws.format("A5:K5", {
        "backgroundColor": {"red": 0.13, "green": 0.13, "blue": 0.13},
        "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
    })

    print(f"  ✅ 'Rapor' sayfası güncellendi — {len(varlıklar)} varlık yazıldı.")


# ─────────────────────────────────────────────
# BÖLÜM 3: Özet Sayfasını Yaz
# ─────────────────────────────────────────────
def ozet_sayfasini_yaz(sheet, veri: dict) -> None:
    """LONG / SHORT / HOLD sayılarını gösteren özet sayfa."""
    try:
        ws = sheet.worksheet("Özet")
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(title="Özet", rows=20, cols=4)

    varlıklar = veri.get("varlıklar", [])
    sayac     = {"LONG": 0, "SHORT": 0, "HOLD": 0}

    for r in varlıklar:
        s = r["karar"]["SİNYAL"]
        if s in sayac:
            sayac[s] += 1

    ws.update(values=[["📊 SİNYAL ÖZETİ"]], range_name="A1")
    ws.update(values=[
        ["SİNYAL", "SAYI", "ORAN"],
        ["LONG",  sayac["LONG"],  f"{sayac['LONG']/len(varlıklar)*100:.0f}%"],
        ["SHORT", sayac["SHORT"], f"{sayac['SHORT']/len(varlıklar)*100:.0f}%"],
        ["HOLD",  sayac["HOLD"],  f"{sayac['HOLD']/len(varlıklar)*100:.0f}%"],
        ["TOPLAM", len(varlıklar), "100%"],
    ], range_name="A3")

    # Renklendirme
    ws.format("A4", {"backgroundColor": RENK["LONG"],  "textFormat": {"bold": True}})
    ws.format("A5", {"backgroundColor": RENK["SHORT"], "textFormat": {"bold": True}})
    ws.format("A6", {"backgroundColor": RENK["HOLD"],  "textFormat": {"bold": True}})

    print(f"  ✅ 'Özet' sayfası güncellendi — "
          f"LONG:{sayac['LONG']} SHORT:{sayac['SHORT']} HOLD:{sayac['HOLD']}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"  Algoritmik Hedge Fon | Sheets Pusher v0.1")
    print(f"{'='*60}\n")

    # rapor.json'ı oku
    if not Path(RAPOR_DOSYA).exists():
        print(f"[HATA] '{RAPOR_DOSYA}' bulunamadı.")
        print(f"       Önce 'python mock_agent.py' çalıştır.")
        exit(1)

    with open(RAPOR_DOSYA, "r", encoding="utf-8") as f:
        veri = json.load(f)

    print(f"📂 {RAPOR_DOSYA} okundu — {veri['varlık_sayısı']} varlık bulundu.")

    # Sheets'e bağlan
    print(f"\n🔗 Google Sheets'e bağlanılıyor...")
    try:
        sheet = sheets_baglan()
        print(f"  ✅ Bağlantı başarılı → '{sheet.title}'")
    except FileNotFoundError as e:
        print(f"\n[HATA] {e}")
        exit(1)
    except Exception as e:
        print(f"\n[HATA] Sheets bağlantısı kurulamadı: {e}")
        exit(1)

    # Sayfaları yaz
    print(f"\n📝 Sayfalar yazılıyor...")
    rapor_sayfasini_yaz(sheet, veri)
    ozet_sayfasini_yaz(sheet, veri)

    print(f"\n🎉 Dashboard güncellendi!")
    print(f"🔗 https://docs.google.com/spreadsheets/d/{SHEET_ID}")
    print()