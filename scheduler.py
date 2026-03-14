"""
scheduler.py  [V5.3 — 6 Adımlı Tam Pipeline]
==============================================
Algoritmik Hedge Fon — Otomatik Zamanlayıcı

V5 (TAM PİPELİNE):
    1. mock_agent.py       → rapor.json (ATR+SMA200 dahil)
    2. legends_agent.py    → legends_rapor.json (ATR ihraç)
    3. pairs_agent.py      → Pairs Tarama ve Copula Kalkanı
    4. state_manager.py    → final_karar.json (ATR bazlı SL/TP)
    5. alpaca_trader.py    → İşlem aç/kapat + Pyramiding
    6. sheets_pusher.py    → Dashboard güncelle (her zaman çalışır)

    Eğer market kapalıysa: 1-2-3-4 çalışır, 5 atlanır, 6 çalışır.
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

Düzeltme Geçmişi:
    V5.1 — Fix 1-5  : alpaca çakışan if, docstring, pairs_agent atlama, özet sayacı
    V5.2 — Fix 6-9  : KeyboardInterrupt, bulunamadı sayacı, sentiment timeout, hafta sonu
    V5.3 — Fix 10-12: Başlangıç testi hafta sonu koruması, stdout loglama,
                       TimeoutExpired sonrası zombie process temizleme
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
SAAT_ACILIS       = "16:30"   # NYSE açılış (TR)
SAAT_KAPANIS      = "23:00"   # NYSE kapanış öncesi (TR)
SAAT_SENTIMENT    = "12:00"   # Gündüz sentiment taraması (TR)
LOG_DOSYA         = "scheduler.log"
TIMEOUT_KISA      = 180       # mock/legends/pairs/sheets: 3 dakika
TIMEOUT_UZUN      = 300       # state_manager/alpaca: 5 dakika
TIMEOUT_SENTIMENT = 600       # 17 sembol × ~15sn ≈ 255sn min, gecikmelerle 400-500sn

# Pipeline adımları: (dosya_adi, timeout, kritik_mi, aciklama)
# kritik=True  → başarısız olursa sonraki tüm adımlar atlanır
#                (HER_ZAMAN_CALIS setindekiler hariç)
# kritik=False → başarısız olsa bile pipeline devam eder,
#                ancak pipeline_dur=True ise bu adım da atlanır
PIPELINE_ADIMLARI = [
    ("mock_agent.py",    TIMEOUT_KISA, True,  "Teknik Analiz (ATR+SMA200)"),
    ("legends_agent.py", TIMEOUT_KISA, True,  "Efsane Oylama (ATR ihraç)"),
    ("pairs_agent.py",   TIMEOUT_KISA, False, "Pairs Tarama ve Copula Kalkanı"),
    ("state_manager.py", TIMEOUT_UZUN, True,  "Final Karar (ATR bazlı SL/TP)"),
    ("alpaca_trader.py", TIMEOUT_UZUN, False, "İşlem Motoru (Pyramiding)"),
    ("sheets_pusher.py", TIMEOUT_KISA, False, "Dashboard Güncelleme"),
]

# Kritik hata sonrası yine de çalışması gereken adımlar.
# pipeline_dur=True olsa bile bu dosyalar atlanmaz.
HER_ZAMAN_CALIS = {"sheets_pusher.py"}

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
def hafta_ici_mi() -> bool:
    """
    Cumartesi (5) ve Pazar (6) günleri False döner.
    Pipeline ve sentiment yalnızca hafta içi (Pzt–Cum) çalışır.
    """
    return datetime.now().weekday() < 5


def market_acik_mi_basit() -> bool:
    """
    Alpaca API'ye bağlanmadan basit saat + gün kontrolü.
    NYSE: Hafta içi 09:30–16:00 ET = TR 16:30–23:00.
    Yalnızca alpaca_trader adımının çalışıp çalışmayacağını belirler.
    Not: alpaca_trader kendi içinde de kontrol yapar → ikinci güvenlik katmanı.
    """
    if not hafta_ici_mi():
        return False
    simdi   = datetime.now()
    saat    = simdi.hour * 60 + simdi.minute
    acilis  = 16 * 60 + 25    # 16:25 TR
    kapanis = 23 * 60 + 5     # 23:05 TR
    return acilis <= saat <= kapanis


def script_calistir(dosya: str, timeout: int, aciklama: str) -> tuple[bool, bool]:
    """
    Verilen Python scriptini çalıştırır.

    Returns:
        (basari, bulunamadi) tuple'ı:
            (True,  False) → başarıyla tamamlandı
            (False, False) → hata veya timeout ile sonlandı
            (False, True)  → dosya bulunamadı, hiç çalışmadı

    ✅ Fix 10: Başarılı çalışmada script stdout'u da loglanır (debug için).
    ✅ Fix 11: TimeoutExpired sonrası process kill() ile temizlenir —
               zombie/orphan process bırakmaz.
    """
    if not Path(dosya).exists():
        log.error(f"  ❌ '{dosya}' bulunamadı — atlanıyor.")
        return False, True

    log.info(f"  ▶  {aciklama} ({dosya}) başlatılıyor...")
    baslangic = time.time()
    proc = None

    try:
        proc = subprocess.Popen(
            [sys.executable, dosya],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = proc.communicate(timeout=timeout)
        sure = round(time.time() - baslangic, 1)

        # ✅ Fix 10: stdout'u logla (boş değilse)
        if stdout.strip():
            for satir in stdout.strip().splitlines()[-20:]:   # son 20 satır
                log.info(f"    │ {satir}")

        if proc.returncode == 0:
            log.info(f"  ✅ {dosya} tamamlandı ({sure}s)")
            return True, False

        hata_ozet = stderr.strip()[-300:] if stderr else "Bilinmiyor"
        log.error(f"  ❌ {dosya} hata kodu {proc.returncode} ({sure}s):")
        log.error(f"     {hata_ozet}")
        return False, False

    except subprocess.TimeoutExpired:
        # ✅ Fix 11: Zombie process'i temizle
        if proc is not None:
            proc.kill()
            proc.communicate()   # buffer'ları boşalt, process'i tamamen kapat
        sure = round(time.time() - baslangic, 1)
        log.error(f"  ⏰ {dosya} zaman aşımı ({timeout}s, {sure}s geçti) — süreç sonlandırıldı.")
        return False, False

    except Exception as e:
        if proc is not None:
            proc.kill()
        log.error(f"  ❌ {dosya} çalıştırılamadı: {e}")
        return False, False


# ─────────────────────────────────────────────
# V5 TAM PİPELİNE
# ─────────────────────────────────────────────
def tam_pipeline_calistir() -> None:
    """
    V5.3 — 6 adımlı tam pipeline.

    Çalışma mantığı:
        Hafta sonu schedule tetiklese bile fonksiyon erken çıkar.
        Adım 1-4 (mock→legends→pairs→state): Her zaman çalışır.
            Piyasa kapalı olsa bile sabah dashboard'u güncel tutar.
            KRİTİK adım başarısız olursa sonraki tüm adımlar atlanır;
            yalnızca HER_ZAMAN_CALIS setindeki adımlar (sheets_pusher) çalışır.
        Adım 5 (alpaca_trader): Sadece market saatinde VE kritik hata yoksa çalışır.
        Adım 6 (sheets_pusher): Kritik hata olsa bile HER ZAMAN çalışır.
    """
    if not hafta_ici_mi():
        log.info("📅 Hafta sonu — pipeline çalışmıyor.")
        return

    log.info("=" * 60)
    log.info(f"🚀 V5.3 Pipeline başlatıldı — {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    log.info("=" * 60)

    market_var     = market_acik_mi_basit()
    basari_sayisi  = 0
    atlanan_sayisi = 0
    hata_sayisi    = 0
    pipeline_dur   = False

    log.info(f"📡 Market: {'AÇIK ✅' if market_var else 'KAPALI ⏰ (Alpaca adımı atlanacak)'}")

    for dosya, timeout, kritik, aciklama in PIPELINE_ADIMLARI:

        # ── Atlama kararları ──────────────────────────────────────────

        # alpaca_trader özel kuralı: market kapalıysa VEYA kritik hata varsa atla
        if dosya == "alpaca_trader.py":
            if not market_var:
                log.info(f"  ⏭️  {dosya} atlandı — market kapalı (emir bekliyor)")
                atlanan_sayisi += 1
                continue
            if pipeline_dur:
                log.info(f"  ⏭️  {dosya} atlandı — kritik adım başarısız, emir açılmıyor")
                atlanan_sayisi += 1
                continue

        # HER_ZAMAN_CALIS dışındaki tüm adımlar pipeline_dur'da atlanır
        if dosya not in HER_ZAMAN_CALIS and pipeline_dur:
            log.warning(f"  ⏭️  {dosya} atlandı — önceki kritik adım başarısız")
            atlanan_sayisi += 1
            continue

        # ── Adımı çalıştır ───────────────────────────────────────────
        basari, bulunamadi = script_calistir(dosya, timeout, aciklama)

        if bulunamadi:
            atlanan_sayisi += 1
            if kritik:
                log.error(f"  🛑 KRİTİK DOSYA YOK: {dosya} — pipeline durduruluyor!")
                pipeline_dur = True
        elif basari:
            basari_sayisi += 1
        else:
            hata_sayisi += 1
            if kritik:
                log.error(f"  🛑 KRİTİK ADIM BAŞARISIZ: {dosya} — pipeline durduruluyor!")
                pipeline_dur = True

        time.sleep(2)   # Adımlar arası küçük bekleme

    # ── Özet ─────────────────────────────────────────────────────────
    toplam = len(PIPELINE_ADIMLARI)
    log.info(f"\n{'─'*60}")
    log.info(
        f"📊 Pipeline özeti: "
        f"{basari_sayisi} başarılı / "
        f"{hata_sayisi} hatalı / "
        f"{atlanan_sayisi} atlandı "
        f"(toplam {toplam} adım)"
    )
    if pipeline_dur:
        log.error("⚠️  Pipeline kritik hata nedeniyle kısmen durduruldu!")
    else:
        log.info("✅ Pipeline başarıyla tamamlandı.")
    log.info("=" * 60)


def sentiment_tarama_calistir() -> None:
    """
    Gündüz sentiment taraması (12:00 TR).
    Pipeline'dan bağımsız çalışır — sadece sentiment_rapor.json günceller.
    Hafta sonu çalışmaz. TIMEOUT_SENTIMENT (600s) kullanır.
    """
    if not hafta_ici_mi():
        log.info("📅 Hafta sonu — sentiment taraması çalışmıyor.")
        return

    log.info("─" * 40)
    log.info("📰 Gündüz Sentiment Taraması başlatıldı")

    basari, bulunamadi = script_calistir(
        "sentiment_agent.py", TIMEOUT_SENTIMENT, "Sentiment Taraması"
    )

    if bulunamadi:
        log.error("⚠️  sentiment_agent.py bulunamadı — tarama atlandı.")
    elif basari:
        log.info("✅ Sentiment tamamlandı — sentiment_rapor.json güncellendi")
    else:
        log.warning("⚠️  Sentiment başarısız — eski veri kullanılacak")

    log.info("─" * 40)


# ─────────────────────────────────────────────
# ZAMANLAMA
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"  Algoritmik Hedge Fon | Scheduler V5.3")
    print(f"  Pipeline: mock→legends→pairs→state_mgr→alpaca→sheets")
    print(f"  Çalışma: {SAAT_ACILIS} | {SAAT_KAPANIS} | {SAAT_SENTIMENT} (TR saati)")
    print(f"  Yalnızca hafta içi (Pzt–Cum) çalışır.")
    print(f"  Durdurmak: Ctrl+C")
    print(f"{'='*60}\n")

    log.info("⏰ Scheduler V5.3 başlatıldı.")
    log.info(f"   NYSE Açılış  : {SAAT_ACILIS} TR — tam pipeline")
    log.info(f"   NYSE Kapanış : {SAAT_KAPANIS} TR — tam pipeline")
    log.info(f"   Sentiment    : {SAAT_SENTIMENT} TR — bağımsız tarama")
    log.info(f"   Timeout      : kısa={TIMEOUT_KISA}s | uzun={TIMEOUT_UZUN}s | sentiment={TIMEOUT_SENTIMENT}s")

    schedule.every().day.at(SAAT_ACILIS).do(tam_pipeline_calistir)
    schedule.every().day.at(SAAT_KAPANIS).do(tam_pipeline_calistir)
    schedule.every().day.at(SAAT_SENTIMENT).do(sentiment_tarama_calistir)

    # ✅ Fix 12: Başlangıç testi hafta sonu çalışmaz — tutarlı davranış
    if hafta_ici_mi():
        log.info("\n🔄 Başlangıç testi — pipeline şimdi bir kez çalıştırılıyor...")
        tam_pipeline_calistir()
    else:
        log.info("\n📅 Hafta sonu başlatıldı — başlangıç testi atlandı.")
        log.info("   İlk çalışma Pazartesi saat 12:00'de (sentiment) olacak.")

    log.info(f"\n⏳ Bekleme moduna geçildi.")
    log.info(f"   Sonraki çalışmalar: {SAAT_ACILIS} | {SAAT_KAPANIS} | {SAAT_SENTIMENT}")

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        log.info("⏹️  Scheduler durduruldu (Ctrl+C). İyi günler!")