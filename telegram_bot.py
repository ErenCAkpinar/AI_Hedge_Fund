"""
telegram_bot.py
===============
Algoritmik Hedge Fon — CEO Telegram Bildirim Motoru

Görev : .env'den token/chat okur, argüman olarak aldığı mesajı CEO'ya iletir.
Lib   : requests (harici bağımlılık minimal tutuldu)

.env dosyasına ekle:
    TELEGRAM_BOT_TOKEN=7xxxxxxxxx:AAF_xxxxxxxx
    TELEGRAM_CHAT_ID=-100xxxxxxxxx    ← kanal/grup negatif ID
                    veya
    TELEGRAM_CHAT_ID=123456789        ← kişisel pozitif ID

Kurulum:
    pip install requests python-dotenv

Kullanım:
    python telegram_bot.py "🟢 ASTS LONG emri Alpaca'ya iletildi."  # CLI
    from telegram_bot import telegram_gonder                          # Modül
"""

import os
import sys
import time
import logging
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID     = os.getenv("TELEGRAM_CHAT_ID", "")
TIMEOUT_SN  = 10    # API yanıt bekleme süresi (saniye)
MAX_DENEME  = 3     # Başarısız gönderimde maksimum tekrar sayısı
BEKLEME_SN  = 2     # Denemeler arası bekleme (saniye, her seferinde 2x artar)

# Loglama
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("telegram.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("telegram_bot")


# ─────────────────────────────────────────────
# ANA FONKSİYON
# ─────────────────────────────────────────────
def telegram_gonder(mesaj: str, parse_mode: str = "HTML") -> bool:
    """
    CEO'ya Telegram mesajı gönderir.

    Args:
        mesaj     : Gönderilecek metin. HTML tag'leri desteklenir (<b>, <code> vb.)
        parse_mode: "HTML" veya "MarkdownV2" (varsayılan HTML)

    Returns:
        True  = mesaj iletildi
        False = tüm denemeler başarısız

    Edge Cases:
        - Token/Chat ID eksik  → hata loglar, False döner, crash yapmaz
        - Boş mesaj            → uyarı loglar, False döner
        - Telegram rate limit  → retry_after kadar bekler
        - Ağ kesintisi         → exponential backoff ile tekrar dener
        - Düzeltilemez hata    → loglar, False döner, programı durdurmaz
    """
    # ── Ön Kontroller ───────────────────────────────────────────────────
    if not BOT_TOKEN:
        log.error("❌ TELEGRAM_BOT_TOKEN .env dosyasında tanımlı değil!")
        return False

    if not CHAT_ID:
        log.error("❌ TELEGRAM_CHAT_ID .env dosyasında tanımlı değil!")
        return False

    if not mesaj or not mesaj.strip():
        log.warning("⚠️  Boş mesaj isteği — gönderilmedi.")
        return False

    # ── Gönderim Döngüsü ────────────────────────────────────────────────
    url     = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id"   : CHAT_ID,
        "text"      : mesaj,
        "parse_mode": parse_mode,
    }

    for deneme in range(1, MAX_DENEME + 1):
        try:
            resp = requests.post(url, json=payload, timeout=TIMEOUT_SN)
            veri = resp.json()

            # Başarılı
            if resp.status_code == 200 and veri.get("ok"):
                log.info(f"✅ Mesaj iletildi (deneme {deneme}/{MAX_DENEME})")
                return True

            api_hata = veri.get("description", "Bilinmeyen Telegram hatası")

            # Rate Limit (429) → Telegram'ın bekleme süresine uy
            if resp.status_code == 429:
                bekleme = veri.get("parameters", {}).get("retry_after", 15)
                log.warning(f"⚠️  Rate limit — {bekleme}s bekleniyor... (deneme {deneme})")
                time.sleep(bekleme)
                continue

            # Düzeltilemez hatalar (geçersiz token, chat bulunamadı)
            if resp.status_code in (400, 401, 403):
                log.error(f"❌ Düzeltilemez Telegram hatası ({resp.status_code}): {api_hata}")
                return False

            log.warning(f"⚠️  Telegram API hatası (HTTP {resp.status_code}): {api_hata} "
                        f"(deneme {deneme}/{MAX_DENEME})")

        except requests.exceptions.Timeout:
            log.warning(f"⚠️  Bağlantı zaman aşımı (deneme {deneme}/{MAX_DENEME})")

        except requests.exceptions.ConnectionError:
            log.warning(f"⚠️  Ağ bağlantısı kurulamadı (deneme {deneme}/{MAX_DENEME})")

        except requests.exceptions.RequestException as e:
            log.error(f"❌ Beklenmeyen requests hatası: {e}")
            return False

        # Üstel geri çekilme (2s, 4s, 8s...)
        if deneme < MAX_DENEME:
            bekleme = BEKLEME_SN * (2 ** (deneme - 1))
            log.info(f"   {bekleme}s sonra tekrar deneniyor...")
            time.sleep(bekleme)

    log.error(f"❌ Mesaj {MAX_DENEME} denemede gönderilemedi. "
              f"İçerik: {mesaj[:60]}{'...' if len(mesaj) > 60 else ''}")
    return False


# ─────────────────────────────────────────────
# YARDIMCI: BAĞLANTI TESTİ
# ─────────────────────────────────────────────
def baglanti_test() -> bool:
    """
    Bot token'ının geçerliliğini ve chat_id'nin erişilebilir olduğunu test eder.
    Sistem başlatılırken bir kez çağrılması önerilir.
    """
    if not BOT_TOKEN:
        log.error("❌ Token eksik — test yapılamıyor.")
        return False

    try:
        r    = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getMe",
            timeout=TIMEOUT_SN
        )
        veri = r.json()
        if not veri.get("ok"):
            log.error(f"❌ Geçersiz bot token: {veri.get('description')}")
            return False
        bot_adi = veri["result"].get("username", "?")
        log.info(f"✅ Bot doğrulandı → @{bot_adi}")
    except Exception as e:
        log.error(f"❌ Token doğrulanamadı: {e}")
        return False

    zaman    = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    test_msg = (
        f"🤖 <b>Algoritmik Hedge Fon</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Telegram bağlantısı aktif!\n"
        f"⏰ <code>{zaman}</code>\n"
        f"🚀 Sistem canlıya hazır."
    )
    return telegram_gonder(test_msg)


# ─────────────────────────────────────────────
# CLI KULLANIMI
# ─────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Kullanım:")
        print("  python telegram_bot.py 'Mesajınız burada'")
        print("  python telegram_bot.py --test")
        sys.exit(1)

    if sys.argv[1] == "--test":
        basari = baglanti_test()
        sys.exit(0 if basari else 1)

    mesaj_metni = " ".join(sys.argv[1:])
    basari = telegram_gonder(mesaj_metni)
    sys.exit(0 if basari else 1)