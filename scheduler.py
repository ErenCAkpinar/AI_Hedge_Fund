"""
scheduler.py
============
Algoritmik Hedge Fon — Otomatik Zamanlayıcı
Görev: mock_agent.py + sheets_pusher.py ikilisini günde 2 kez çalıştırır.

Çalışma Saatleri (piyasa saatine göre):
    - 16:30 TR saati → NYSE açılışı (09:30 ET)
    - 23:00 TR saati → NYSE kapanış öncesi (16:00 ET)

Çalıştırma:
    python scheduler.py

Arka planda çalıştırma (terminal kapatılınca durmasın):
    nohup python scheduler.py &
    
Durdurmak için:
    Ctrl+C  veya  kill <PID>
"""

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import schedule


# ─────────────────────────────────────────────
# CONFIG — Sadece buradan değiştir
# ─────────────────────────────────────────────
SAAT_ACILIS  = "16:30"   # NYSE açılışı (TR saati)
SAAT_KAPANIS = "23:00"   # NYSE kapanış öncesi (TR saati)
LOG_DOSYA    = "scheduler.log"


# ─────────────────────────────────────────────
# YARDIMCI FONKSİYONLAR
# ─────────────────────────────────────────────
def log(mesaj: str) -> None:
    """Terminale ve log dosyasına yazar."""
    zaman = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    satir = f"[{zaman}] {mesaj}"
    print(satir)
    with open(LOG_DOSYA, "a", encoding="utf-8") as f:
        f.write(satir + "\n")


def script_calistir(dosya: str) -> bool:
    """Verilen Python scriptini çalıştırır, başarı/başarısızlık döndürür."""
    if not Path(dosya).exists():
        log(f"❌ HATA: '{dosya}' bulunamadı.")
        return False
    try:
        result = subprocess.run(
            [sys.executable, dosya],
            capture_output=True,
            text=True,
            timeout=120   # 2 dakika timeout — takılı kalmasın
        )
        if result.returncode == 0:
            log(f"✅ '{dosya}' başarıyla tamamlandı.")
            return True
        else:
            log(f"❌ '{dosya}' hata ile bitti:\n{result.stderr[:300]}")
            return False
    except subprocess.TimeoutExpired:
        log(f"⏰ '{dosya}' zaman aşımına uğradı (120s).")
        return False
    except Exception as e:
        log(f"❌ '{dosya}' çalıştırılırken beklenmeyen hata: {e}")
        return False


# ─────────────────────────────────────────────
# ANA GÖREV
# ─────────────────────────────────────────────
def analiz_calistir() -> None:
    """Her tetiklemede çalışan tam pipeline."""
    log("=" * 50)
    log("🚀 Pipeline tetiklendi")
    log("=" * 50)

    # Adım 1: Veri çek + karar üret
    log("[1/2] mock_agent.py çalıştırılıyor...")
    basari = script_calistir("mock_agent.py")

    if not basari:
        log("⚠️  mock_agent başarısız, sheets_pusher atlandı.")
        return

    # Adım 2: Google Sheets'e push et
    log("[2/2] sheets_pusher.py çalıştırılıyor...")
    script_calistir("sheets_pusher.py")

    log("✅ Pipeline tamamlandı.\n")


# ─────────────────────────────────────────────
# ZAMANLAMA
# ─────────────────────────────────────────────
if __name__ == "__main__":
    # schedule kütüphanesini kontrol et
    try:
        import schedule
    except ImportError:
        print("[HATA] 'schedule' kütüphanesi eksik.")
        print("       Kur: pip install schedule")
        sys.exit(1)

    print(f"\n{'='*50}")
    print(f"  Algoritmik Hedge Fon | Scheduler v0.1")
    print(f"  Çalışma saatleri: {SAAT_ACILIS} ve {SAAT_KAPANIS} (TR saati)")
    print(f"  Durdurmak için: Ctrl+C")
    print(f"{'='*50}\n")

    log("⏰ Scheduler başlatıldı.")
    log(f"   Açılış görevi: {SAAT_ACILIS}")
    log(f"   Kapanış görevi: {SAAT_KAPANIS}")

    # Görevleri zamanla
    schedule.every().day.at(SAAT_ACILIS).do(analiz_calistir)
    schedule.every().day.at(SAAT_KAPANIS).do(analiz_calistir)

    # Başlarken bir kez hemen çalıştır (test için)
    log("\n🔄 Başlangıç testi — pipeline şimdi bir kez çalıştırılıyor...")
    analiz_calistir()

    # Sonsuz döngü — her 30 saniyede saat kontrolü
    log(f"⏳ Bekleme moduna geçildi. Sonraki çalışma: {SAAT_ACILIS} veya {SAAT_KAPANIS}")
    while True:
        schedule.run_pending()
        time.sleep(30)