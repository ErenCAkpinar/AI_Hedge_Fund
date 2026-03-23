"""
watchdog_agent.py  [V1 — Kesintisiz Gözcü]
===========================================
Algoritmik Hedge Fon — Gerçek Zamanlı Fiyat Monitörü

Görevler:
    1. Pre-market + Regular + After-hours boyunca her 2 dakikada fiyat kontrol
    2. ATR bandı veya %3 ani değişim → alarm tetikle
    3. Alarm anında Claude Haiku ile hızlı değerlendirme (~$0.001 per alarm)
    4. KAPAT → Alpaca pozisyon kapat + Telegram bildirim
       PIPELINE → state_manager.py yeniden çalıştır
       BEKLE → sessiz devam

Market penceresi (EST):
    04:00 – 20:00  (pre-market + regular + after-hours)
    Hafta içi (Pzt–Cum)

Başlatma:
    python watchdog_agent.py
    # GCP'de arka planda:
    screen -S watchdog
    source venv/bin/activate && python watchdog_agent.py
    # Ctrl+A → D ile arka plana al

Maliyet tahmini:
    Alarm yok  → $0.00/gün
    3 alarm    → ~$0.003/gün  (Claude Haiku)
"""

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

KONTROL_ARALIK_SN  = 120     # Aktif kontrol periyodu (saniye)
UYKU_ARALIK_SN     = 300     # Market kapalıyken bekleme süresi (saniye)
ATR_ALARM_KATSAYI  = 2.0     # ATR'ın bu katından fazla hareket = alarm
FIYAT_DEGISIM_ESIK = 0.03    # %3 ani değişim = alarm (ATR yetersizse)
STOP_GECIKME_PCT   = 0.005   # Stop altına %0.5 geçme toleransı

BASE_DIR     = Path(__file__).parent
LOG_DOSYA    = BASE_DIR / "watchdog.log"
KARAR_DOSYA  = BASE_DIR / "final_karar.json"
RAPOR_DOSYA  = BASE_DIR / "rapor.json"

# Market penceresi — EST (UTC-5)
MARKET_BASLANGIC_H = 4    # 04:00 EST  (pre-market açılış)
MARKET_BITIS_H     = 20   # 20:00 EST  (after-hours kapanış)

# ─────────────────────────────────────────────
# LOGLAMA
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_DOSYA, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("watchdog")


# ─────────────────────────────────────────────
# BÖLÜM 1: ZAMAN YÖNETİMİ
# ─────────────────────────────────────────────

def est_saat() -> datetime:
    """Şu anki EST saatini döndürür (UTC-5, DST gözetilmez)."""
    return datetime.now(timezone.utc) - timedelta(hours=5)


def market_penceresi_acik_mi() -> bool:
    """
    Pre-market + Regular + After-hours tümü dahil.
    04:00 – 20:00 EST, Pazartesi – Cuma.
    """
    su_an = est_saat()
    if su_an.weekday() >= 5:   # Cumartesi=5, Pazar=6
        return False
    saat_dk = su_an.hour * 60 + su_an.minute
    return (MARKET_BASLANGIC_H * 60) <= saat_dk <= (MARKET_BITIS_H * 60)


# ─────────────────────────────────────────────
# BÖLÜM 2: VERİ OKUYUCULAR
# ─────────────────────────────────────────────

def aktif_pozisyonlari_cek() -> list[dict]:
    """Alpaca'dan açık pozisyonları çeker."""
    try:
        import alpaca_trade_api as tradeapi
        api = tradeapi.REST(
            os.getenv("ALPACA_API_KEY"),
            os.getenv("ALPACA_SECRET_KEY"),
            os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
        )
        return [
            {
                "sembol"      : p.symbol,
                "adet"        : float(p.qty),
                "giris_fiyat" : float(p.avg_entry_price),
                "guncel_fiyat": float(p.current_price),
                "pnl_pct"     : float(p.unrealized_plpc) * 100,
                "yon"         : "LONG" if float(p.qty) > 0 else "SHORT",
            }
            for p in api.list_positions()
        ]
    except Exception as e:
        log.warning(f"Alpaca pozisyon çekilemedi: {e}")
        return []


def atr_oku(sembol: str) -> float:
    """rapor.json'dan ATR_14 değerini okur. Yoksa 5.0 döner."""
    try:
        if RAPOR_DOSYA.exists():
            veri = json.loads(RAPOR_DOSYA.read_text(encoding="utf-8"))
            for item in veri.get("varlıklar", []):
                if item.get("veri", {}).get("symbol") == sembol:
                    return float(item["veri"].get("ATR_14", 5.0) or 5.0)
    except Exception:
        pass
    return 5.0


def son_karar_oku(sembol: str) -> dict:
    """final_karar.json'dan sembol için son kararı okur."""
    try:
        if KARAR_DOSYA.exists():
            veri = json.loads(KARAR_DOSYA.read_text(encoding="utf-8"))
            for s in veri.get("sinyaller", []):
                if s.get("sembol") == sembol:
                    return s
    except Exception:
        pass
    return {}


# ─────────────────────────────────────────────
# BÖLÜM 3: ANOMALI TESPİTİ
# ─────────────────────────────────────────────

def anormal_hareket_mi(sembol: str, guncel: float,
                        onceki: float, atr: float) -> dict | None:
    """
    İki katmanlı anomali tespiti:
      1. ATR_ALARM_KATSAYI × ATR'dan fazla mutlak hareket
      2. FIYAT_DEGISIM_ESIK'den fazla yüzdesel değişim

    Alarm varsa dict döner, yoksa None.
    """
    if onceki <= 0 or guncel <= 0:
        return None

    degisim_abs = abs(guncel - onceki)
    degisim_pct = degisim_abs / onceki

    atr_alarm = degisim_abs > (atr * ATR_ALARM_KATSAYI)
    pct_alarm = degisim_pct > FIYAT_DEGISIM_ESIK

    if not (atr_alarm or pct_alarm):
        return None

    yon    = "YUKARI" if guncel > onceki else "AŞAĞI"
    siddet = degisim_pct * 100
    return {
        "sebep" : (
            f"{yon} %{siddet:.2f} hareket "
            f"({degisim_abs:.3f}$ | ATR: {atr:.2f}$)"
        ),
        "siddet": siddet,
        "yon"   : yon,
    }


def stop_loss_asild_mi(pozisyon: dict, karar: dict) -> bool:
    """Stop-Loss seviyesi aşıldıysa True döner."""
    sl = karar.get("stop_loss") or karar.get("sl")
    if not sl:
        return False
    try:
        sl = float(sl)
        fiyat = pozisyon["guncel_fiyat"]
        if pozisyon["yon"] == "LONG" and fiyat < sl * (1 - STOP_GECIKME_PCT):
            return True
        if pozisyon["yon"] == "SHORT" and fiyat > sl * (1 + STOP_GECIKME_PCT):
            return True
    except (TypeError, ValueError):
        pass
    return False


# ─────────────────────────────────────────────
# BÖLÜM 4: CLAUDE HAIKU DEĞERLENDİRMESİ
# ─────────────────────────────────────────────

def claude_karar_ver(pozisyon: dict, alarm: dict,
                      karar: dict) -> str:
    """
    Alarm anında Claude Haiku ile hızlı karar al.
    Dönüş: "KAPAT" | "PIPELINE" | "BEKLE"
    Maliyet: ~$0.001 per çağrı (Haiku)
    """
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

        prompt = (
            f"Hedge fon risk yöneticisisin. Hızlı karar ver.\n\n"
            f"Pozisyon:\n"
            f"  Sembol     : {pozisyon['sembol']}\n"
            f"  Yön        : {pozisyon['yon']}\n"
            f"  Giriş      : ${pozisyon['giris_fiyat']:.2f}\n"
            f"  Şu an      : ${pozisyon['guncel_fiyat']:.2f}\n"
            f"  P&L        : %{pozisyon['pnl_pct']:.2f}\n"
            f"  Stop-Loss  : {karar.get('stop_loss') or karar.get('sl', 'N/A')}\n"
            f"  Take-Profit: {karar.get('take_profit') or karar.get('tp', 'N/A')}\n\n"
            f"Anomali: {alarm['sebep']}\n"
            f"Son sinyal: {karar.get('final_sinyal', 'N/A')} "
            f"(skor: {karar.get('toplam_skor', karar.get('skor', 'N/A'))})\n\n"
            f"Sadece tek kelime: KAPAT veya PIPELINE veya BEKLE"
        )

        resp = client.messages.create(
            model      ="claude-haiku-4-5-20251001",
            max_tokens = 5,
            messages   = [{"role": "user", "content": prompt}],
        )
        metin = resp.content[0].text.strip().upper()

        if "KAPAT" in metin:
            return "KAPAT"
        if "PIPELINE" in metin:
            return "PIPELINE"
        return "BEKLE"

    except Exception as e:
        log.warning(f"Claude hatası: {e} → BEKLE varsayıldı")
        return "BEKLE"


# ─────────────────────────────────────────────
# BÖLÜM 5: AKSİYON METODLARI
# ─────────────────────────────────────────────

def pozisyon_kapat(sembol: str, sebep: str):
    """Alpaca üzerinden pozisyonu piyasa fiyatıyla kapatır."""
    try:
        import alpaca_trade_api as tradeapi
        api = tradeapi.REST(
            os.getenv("ALPACA_API_KEY"),
            os.getenv("ALPACA_SECRET_KEY"),
            os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
        )
        api.close_position(sembol)
        log.info(f"🔴 POZİSYON KAPATILDI: {sembol} | {sebep}")
        telegram_bildir(f"🚨 WATCHDOG: {sembol} kapatıldı\n{sebep}")
    except Exception as e:
        log.error(f"Pozisyon kapatma hatası {sembol}: {e}")


def pipeline_tetikle(sembol: str, sebep: str):
    """
    state_manager.py'ı yeniden çalıştırır.
    Tam pipeline değil — sadece karar motoru yenilenir.
    """
    log.info(f"🔄 PİPELİNE TETİKLENDİ: {sembol} | {sebep}")
    try:
        subprocess.run(
            [sys.executable, str(BASE_DIR / "state_manager.py")],
            cwd    =str(BASE_DIR),
            timeout=120,
            capture_output=True,
        )
        log.info("✅ state_manager tamamlandı")
    except subprocess.TimeoutExpired:
        log.error("state_manager timeout (120s)")
    except Exception as e:
        log.error(f"Pipeline tetiklenemedi: {e}")


def telegram_bildir(mesaj: str):
    """telegram_bot.py modülünü kullanarak bildirim gönderir."""
    try:
        from telegram_bot import telegram_gonder
        telegram_gonder(mesaj)
    except Exception:
        pass


# ─────────────────────────────────────────────
# BÖLÜM 6: FİYAT ÇEKİCİ
# ─────────────────────────────────────────────

# curl_cffi opsiyonel — yfinance session hızlandırır
try:
    from curl_cffi import requests as cf_requests
    _cf_session = cf_requests.Session(impersonate="chrome110")
except ImportError:
    _cf_session = None

import yfinance as yf

def fiyat_cek(sembol: str) -> float:
    """Güncel piyasa fiyatını çeker."""
    try:
        ticker = yf.Ticker(sembol, session=_cf_session) if _cf_session else yf.Ticker(sembol)
        fiyat  = ticker.fast_info.get("last_price", 0)
        return float(fiyat) if fiyat else 0.0
    except Exception:
        return 0.0


# ─────────────────────────────────────────────
# BÖLÜM 7: ANA DÖNGÜ
# ─────────────────────────────────────────────

def watchdog_calistir():
    """Ana izleme döngüsü — kesintisiz çalışır."""
    log.info("=" * 60)
    log.info("🐕 Watchdog Agent V1 başlatıldı")
    log.info(f"   Kontrol: her {KONTROL_ARALIK_SN}s | ATR katsayı: ×{ATR_ALARM_KATSAYI} | Eşik: %{FIYAT_DEGISIM_ESIK*100}")
    log.info(f"   Market penceresi: 04:00–20:00 EST (Pzt–Cum)")
    log.info("=" * 60)

    # Sembol başına önceki fiyat cache'i
    fiyat_cache: dict[str, float] = {}

    while True:
        try:
            # ── Market penceresi kapalı ──────────────────────────
            if not market_penceresi_acik_mi():
                est = est_saat()
                log.info(
                    f"💤 Market kapalı "
                    f"({['Pzt','Sal','Çar','Per','Cum','Cmt','Paz'][est.weekday()]} "
                    f"{est.strftime('%H:%M')} EST) — "
                    f"{UYKU_ARALIK_SN}s uyku"
                )
                time.sleep(UYKU_ARALIK_SN)
                continue

            # ── Açık pozisyonları al ─────────────────────────────
            pozisyonlar = aktif_pozisyonlari_cek()

            if not pozisyonlar:
                log.info("📭 Açık pozisyon yok")
                time.sleep(KONTROL_ARALIK_SN)
                continue

            semboller = [p["sembol"] for p in pozisyonlar]
            log.info(f"👁️  İzleniyor: {', '.join(semboller)}")

            # ── Her pozisyonu değerlendir ─────────────────────────
            for poz in pozisyonlar:
                sembol = poz["sembol"]
                guncel = fiyat_cek(sembol)
                if guncel <= 0:
                    continue

                onceki = fiyat_cache.get(sembol, guncel)
                fiyat_cache[sembol] = guncel

                atr   = atr_oku(sembol)
                karar = son_karar_oku(sembol)

                # ── Stop-Loss aşıldı mı? (Kural bazlı — ücretsiz) ─
                if stop_loss_asild_mi(poz, karar):
                    log.info(f"🛑 STOP-LOSS AŞILDI: {sembol} @ ${guncel:.2f}")
                    pozisyon_kapat(
                        sembol,
                        f"Stop-Loss aşıldı @ ${guncel:.2f} "
                        f"(SL: {karar.get('stop_loss') or karar.get('sl')})"
                    )
                    fiyat_cache.pop(sembol, None)
                    continue

                # ── Fiyat anomalisi var mı? ────────────────────────
                alarm = anormal_hareket_mi(sembol, guncel, onceki, atr)
                if alarm is None:
                    continue

                log.info(f"🚨 ALARM: {sembol} | {alarm['sebep']}")

                # ── Claude Haiku değerlendirmesi ───────────────────
                claude_karar = claude_karar_ver(poz, alarm, karar)
                log.info(f"   └─ Claude: {claude_karar}")

                if claude_karar == "KAPAT":
                    pozisyon_kapat(sembol, f"Claude kararı: {alarm['sebep']}")
                    fiyat_cache.pop(sembol, None)

                elif claude_karar == "PIPELINE":
                    pipeline_tetikle(sembol, alarm["sebep"])
                    telegram_bildir(
                        f"⚠️ WATCHDOG: {sembol} anomalisi\n"
                        f"{alarm['sebep']}\n"
                        f"Pipeline yeniden çalıştırıldı."
                    )

                else:
                    log.info(f"   {sembol}: izlemeye devam")

        except Exception as e:
            log.error(f"Ana döngü hatası: {e}")

        time.sleep(KONTROL_ARALIK_SN)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    watchdog_calistir()
