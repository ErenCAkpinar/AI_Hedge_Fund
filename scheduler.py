"""
scheduler.py  [V5 — 4 Adımlı Tam Pipeline]
============================================
Algoritmik Hedge Fon — Otomatik Zamanlayıcı

V5 DEĞİŞİKLİĞİ:
    ÖNCEKİ (V4.5 — EKSİK):
        mock_agent → sheets_pusher
        (state_manager ve alpaca_trader ÇAĞRILMIYORDU!)

    V5 (TAM PİPELİNE):
        1. mock_agent.py       → rapor.json (ATR+SMA200 dahil)
        2. legends_agent.py    → legends_rapor.json (ATR ihraç)
        3. state_manager.py    → final_karar.json (ATR bazlı SL/TP)
        4. alpaca_trader.py    → İşlem aç/kapat + Pyramiding
        5. sheets_pusher.py    → Dashboard güncelle (her zaman çalışır)

        Eğer market kapalıysa: 1-2-3 çalışır, 4 atlanır, 5 çalışır.
        Bu sayede dashboard sabah açılışından önce güncellenir.

    NOT: sentiment_agent.py bağımsız tarama yapıyor.
         Hafta içi her gün 12:00 TR'de çalıştırılıyor (ayrı görev).

Çalışma Saatleri (NYSE'ye göre):
    16:30 TR → NYSE açılış (09:30 ET)
    23:00 TR → NYSE kapanış öncesi (16:00 ET)

Çalıştırma:
    python scheduler.py
    nohup python scheduler.py &    ← arka planda

Durdurmak: Ctrl+C veya kill <PID>
"""

import subprocess
import sys
import time
import logging
from datetime import datetime
from pathlib import Path

import schedule


# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
SAAT_ACILIS    = "16:30"   # NYSE açılış (TR)
SAAT_KAPANIS   = "23:00"   # NYSE kapanış öncesi (TR)
SAAT_SENTIMENT = "12:00"   # Gündüz sentiment taraması (TR)
LOG_DOSYA      = "scheduler.log"
TIMEOUT_KISA   = 180       # mock/legends/sheets: 3 dakika
TIMEOUT_UZUN   = 300       # state_manager/alpaca: 5 dakika

# Pipeline adımları: (dosya_adi, timeout, kritik_mi)
# kritik=True → başarısız olursa sonraki adımlar çalışmaz
PIPELINE_ADIMLARI = [
    ("mock_agent.py",    TIMEOUT_KISA, True,  "Teknik Analiz (ATR+SMA200)"),
    ("legends_agent.py", TIMEOUT_KISA, True,  "Efsane Oylama (ATR ihraç)"),
    ("state_manager.py", TIMEOUT_UZUN, True,  "Final Karar (ATR bazlı SL/TP)"),
    ("alpaca_trader.py", TIMEOUT_UZUN, False, "İşlem Motoru (Pyramiding)"),
    ("sheets_pusher.py", TIMEOUT_KISA, False, "Dashboard Güncelleme"),
]

# Loglama
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_DOSYA, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("scheduler")


# ─────────────────────────────────────────────
# YARDIMCI FONKSİYONLAR
# ─────────────────────────────────────────────
def script_calistir(dosya: str, timeout: int, aciklama: str) -> bool:
    """
    Verilen Python scriptini çalıştırır.

    Returns:
        True  = başarılı (returncode == 0)
        False = hata veya timeout
    """
    if not Path(dosya).exists():
        log.error(f"  ❌ '{dosya}' bulunamadı — atlanıyor.")
        return False

    log.info(f"  ▶  {aciklama} ({dosya}) başlatılıyor...")
    baslangic = time.time()

    try:
        result = subprocess.run(
            [sys.executable, dosya],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        sure = round(time.time() - baslangic, 1)

        if result.returncode == 0:
            log.info(f"  ✅ {dosya} tamamlandı ({sure}s)")
            return True
        else:
            hata_ozet = result.stderr.strip()[-300:] if result.stderr else "Bilinmiyor"
            log.error(f"  ❌ {dosya} hata kodu {result.returncode} ({sure}s):")
            log.error(f"     {hata_ozet}")
            return False

    except subprocess.TimeoutExpired:
        log.error(f"  ⏰ {dosya} zaman aşımı ({timeout}s) — süreç sonlandırıldı.")
        return False
    except Exception as e:
        log.error(f"  ❌ {dosya} çalıştırılamadı: {e}")
        return False


def market_acik_mi_basit() -> bool:
    """
    Alpaca API'ye bağlanmadan basit saat kontrolü.
    NYSE: Hafta içi 09:30–16:00 ET = TR 16:30–23:00.
    Sadece scheduler'ın alpaca_trader adımını çalıştırıp çalıştırmayacağını belirler.
    Not: alpaca_trader kendi içinde de kontrol yapar, bu ikinci güvenlik katmanı.
    """
    simdi = datetime.now()
    # Hafta sonu mu?
    if simdi.weekday() >= 5:  # 5=Cumartesi, 6=Pazar
        return False
    # Saat aralığı: 16:25 - 23:05 (biraz tolerans)
    saat = simdi.hour * 60 + simdi.minute
    acilis  = 16 * 60 + 25   # 16:25 TR
    kapanis = 23 * 60 + 5    # 23:05 TR
    return acilis <= saat <= kapanis


# ─────────────────────────────────────────────
# V5 TAM PİPELİNE
# ─────────────────────────────────────────────
def tam_pipeline_calistir() -> None:
    """
    V5 4-adımlı tam pipeline.

    Mantık:
        Adım 1-3 (mock→legends→state): Her zaman çalışır.
            Piyasa kapalı olsa bile sabah dashboard'u güncel tutar.
        Adım 4 (alpaca_trader): Sadece market saatinde çalışır.
            Market kapalıysa sinyal üretilir ama emir GÖNDERİLMEZ.
        Adım 5 (sheets_pusher): Her zaman çalışır.
    """
    log.info("=" * 60)
    log.info(f"🚀 V5 Pipeline başlatıldı — {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    log.info("=" * 60)

    market_var = market_acik_mi_basit()
    log.info(f"📡 Market durumu: {'AÇIK ✅' if market_var else 'KAPALI ⏰ (Alpaca adımı atlanacak)'}")

    basari_sayisi = 0
    toplam_adim   = 0
    pipeline_dur  = False

    for dosya, timeout, kritik, aciklama in PIPELINE_ADIMLARI:

        # alpaca_trader: sadece market saatinde
        if dosya == "alpaca_trader.py" and not market_var:
            log.info(f"  ⏭️  {dosya} atlandı — market kapalı (sinyal üretildi, emir bekliyor)")
            continue

        if pipeline_dur:
            log.warning(f"  ⏭️  {dosya} atlandı — önceki kritik adım başarısız")
            continue

        toplam_adim += 1
        basari = script_calistir(dosya, timeout, aciklama)

        if basari:
            basari_sayisi += 1
        elif kritik:
            log.error(f"  🛑 KRİTİK ADIM BAŞARISIZ: {dosya} — pipeline durduruluyor!")
            pipeline_dur = True

        time.sleep(2)  # Adımlar arası küçük bekleme

    log.info(f"\n{'─'*60}")
    log.info(f"📊 Pipeline özeti: {basari_sayisi}/{toplam_adim} adım başarılı")
    if pipeline_dur:
        log.error("⚠️  Pipeline kritik hata nedeniyle durduruldu!")
    else:
        log.info("✅ Pipeline başarıyla tamamlandı.")
    log.info("=" * 60)


def sentiment_tarama_calistir() -> None:
    """
    Gündüz sentiment taraması (12:00 TR).
    Pipeline'dan bağımsız çalışır — sadece sentiment_rapor.json günceller.
    """
    log.info("─" * 40)
    log.info(f"📰 Gündüz Sentiment Taraması başlatıldı")
    basari = script_calistir("sentiment_agent.py", TIMEOUT_UZUN, "Sentiment Taraması")
    if basari:
        log.info("✅ Sentiment taraması tamamlandı — sentiment_rapor.json güncellendi")
    else:
        log.warning("⚠️  Sentiment taraması başarısız — eski veri kullanılacak")
    log.info("─" * 40)


# ─────────────────────────────────────────────
# ZAMANLAMA
# ─────────────────────────────────────────────
if __name__ == "__main__":
    try:
        import schedule
    except ImportError:
        print("[HATA] 'schedule' kütüphanesi eksik: pip install schedule")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  Algoritmik Hedge Fon | Scheduler V5")
    print(f"  Pipeline: mock→legends→state_mgr→alpaca→sheets")
    print(f"  Çalışma: {SAAT_ACILIS} | {SAAT_KAPANIS} | {SAAT_SENTIMENT} (TR saati)")
    print(f"  Durdurmak: Ctrl+C")
    print(f"{'='*60}\n")

    log.info("⏰ Scheduler V5 başlatıldı.")
    log.info(f"   NYSE Açılış  : {SAAT_ACILIS} TR — tam pipeline")
    log.info(f"   NYSE Kapanış : {SAAT_KAPANIS} TR — tam pipeline")
    log.info(f"   Sentiment    : {SAAT_SENTIMENT} TR — bağımsız tarama")

    # Görevleri zamanla
    schedule.every().day.at(SAAT_ACILIS).do(tam_pipeline_calistir)
    schedule.every().day.at(SAAT_KAPANIS).do(tam_pipeline_calistir)
    schedule.every().day.at(SAAT_SENTIMENT).do(sentiment_tarama_calistir)

    # Başlarken hemen bir kez çalıştır
    log.info("\n🔄 Başlangıç testi — pipeline şimdi bir kez çalıştırılıyor...")
    tam_pipeline_calistir()

    log.info(f"\n⏳ Bekleme moduna geçildi.")
    log.info(f"   Sonraki çalışmalar: {SAAT_ACILIS} | {SAAT_KAPANIS} | {SAAT_SENTIMENT}")

    while True:
        schedule.run_pending()
        time.sleep(30)