"""
alpaca_trader.py  [V5 — Pyramiding + ATR Trailing Stop]
========================================================
Algoritmik Hedge Fon — Kusursuz Tetikçi Motoru

V5 DEĞİŞİKLİKLERİ:
    - SENARYO 3: DUPLICATE_SKIP  →  PYRAMİDİNG MOTORU
      Eski: Aynı yönde pozisyon varsa → atla
      Yeni: Fiyat giriş fiyatı + 1.5×ATR üstündeyse + kârdaysa → EKLEMEİ YAP
      Koşul: unrealized_pl > 0 AND fiyat > (avg_entry + 1.5×ATR)
      
    - ATR Trailing Stop: TRAILING_PCT sabit kalmaya devam eder ama
      ATR değeri final_karar.json'dan okunarak Alpaca'ya iletilir
      (Alpaca trail_percent parametresi ATR/fiyat × 100 ile hesaplanır)

HAYATİ KURALLAR (Korundu):
    1. State Awareness: SHORT + LONG var → LONG kapat, SHORT AÇMA
    2. İzleyen Stop: Trailing Stop, artık ATR bazlı yüzde ile hesaplanır
    3. Market Saati: NYSE kapalıysa emir yok
    4. PDT: Day trade limiti koruması
    5. Bakiye: Yetersiz bakiyede pozisyon küçültülür

.env:
    ALPACA_API_KEY=...
    ALPACA_SECRET_KEY=...
    ALPACA_BASE_URL=https://paper-api.alpaca.markets
    TRAILING_STOP_PCT=3.0     ← ATR yoksa fallback
    MAX_POZISYON_PCT=0.15
    PYRAMIDING_AKTIF=true     ← false yaparak kapatılabilir
    PYRAMIDING_ATR_KATSAYI=1.5  ← Pyramiding giriş eşiği (backtest ile senkron)
"""

import os
import sys
import json
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import alpaca_trade_api as tradeapi
from alpaca_trade_api.rest import APIError
from dotenv import load_dotenv

try:
    from telegram_bot import telegram_gonder
    TELEGRAM_AKTIF = True
except ImportError:
    TELEGRAM_AKTIF = False

load_dotenv()

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
API_KEY       = os.getenv("ALPACA_API_KEY", "")
SECRET_KEY    = os.getenv("ALPACA_SECRET_KEY", "")
BASE_URL      = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
TRAILING_PCT  = float(os.getenv("TRAILING_STOP_PCT", "3.0"))
MAX_POZ_PCT   = float(os.getenv("MAX_POZISYON_PCT", "0.15"))
FINAL_KARAR   = "final_karar.json"
ORDER_LOG     = "order_log.json"

# V5: Pyramiding ayarları
PYRAMIDING_AKTIF      = os.getenv("PYRAMIDING_AKTIF", "true").lower() == "true"
PYRAMIDING_ATR_KAT    = float(os.getenv("PYRAMIDING_ATR_KATSAYI", "1.5"))  # backtest ile senkron
PYRAMIDING_MAX_EKLE   = 2      # En fazla kaç kez ekleme yapılabilir (backtest: 2)
PYRAMIDING_POZ_PCT    = 0.50   # Ekleme emrinde pozisyon büyüklüğünün kaçı kadar

CANLI_PARA = "paper" not in BASE_URL.lower()

# Loglama
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("alpaca_trader.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("alpaca_trader")

if CANLI_PARA:
    log.warning("=" * 60)
    log.warning("  ⚠️  DİKKAT: CANLI PARA MODU AKTİF!")
    log.warning("=" * 60)

if PYRAMIDING_AKTIF:
    log.info(f"🔺 Pyramiding AKTIF — ATR eşiği: {PYRAMIDING_ATR_KAT}×ATR, maks {PYRAMIDING_MAX_EKLE} ekleme")


# ─────────────────────────────────────────────
# BÖLÜM 1: API BAĞLANTISI
# ─────────────────────────────────────────────
def api_baglan() -> Optional[tradeapi.REST]:
    if not API_KEY or not SECRET_KEY:
        log.error("❌ API key eksik!")
        return None
    try:
        api   = tradeapi.REST(key_id=API_KEY, secret_key=SECRET_KEY,
                              base_url=BASE_URL, api_version="v2")
        hesap = api.get_account()
        log.info(f"✅ Alpaca {'CANLI 💵' if CANLI_PARA else 'PAPER 📄'} | "
                 f"Durum: {hesap.status} | Equity: ${float(hesap.equity):,.2f}")
        return api
    except Exception as e:
        log.error(f"❌ Bağlantı hatası: {e}")
        return None


# ─────────────────────────────────────────────
# BÖLÜM 2: KONTROL KATMANLARI (Edge Cases)
# ─────────────────────────────────────────────
def market_acik_mi(api: tradeapi.REST) -> bool:
    try:
        clock = api.get_clock()
        if clock.is_open:
            log.info(f"✅ Market AÇIK | Kapanış: {clock.next_close}")
            return True
        log.warning(f"⚠️  Market KAPALI | Açılış: {clock.next_open}")
        return False
    except Exception as e:
        log.error(f"❌ Market saati alınamadı: {e}")
        return False


def pdt_kontrol(api: tradeapi.REST) -> bool:
    try:
        hesap  = api.get_account()
        equity = float(hesap.equity)
        if equity >= 25_000:
            return True
        pdt = int(getattr(hesap, "daytrade_count", 0))
        if pdt >= 3:
            log.warning(f"🚨 PDT LİMİTİ: {pdt}/3 kullanıldı, Equity=${equity:,.0f} < $25k")
            return False
        log.info(f"  ✅ PDT: {pdt}/3")
        return True
    except Exception as e:
        log.error(f"❌ PDT kontrol hatası: {e}")
        return True


def pozisyon_al(api: tradeapi.REST, symbol: str) -> Optional[object]:
    try:
        return api.get_position(symbol)
    except APIError as e:
        if "position does not exist" in str(e).lower() or "404" in str(e):
            return None
        log.error(f"❌ Pozisyon sorgu hatası ({symbol}): {e}")
        return None
    except Exception as e:
        log.error(f"❌ Beklenmeyen hata ({symbol}): {e}")
        return None


def acik_emirleri_iptal_et(api: tradeapi.REST, symbol: str) -> bool:
    try:
        emirler = api.list_orders(status="open", symbols=[symbol])
        for e in emirler:
            api.cancel_order(e.id)
            log.info(f"  🗑️  Emir iptal: {e.id} ({symbol} {e.side})")
        return True
    except Exception as e:
        log.error(f"❌ Emir iptal hatası ({symbol}): {e}")
        return False


def emir_fill_bekle(api: tradeapi.REST, emir_id: str, max_bekleme: int = 30) -> bool:
    bitis = time.time() + max_bekleme
    while time.time() < bitis:
        try:
            emir = api.get_order(emir_id)
            if emir.status == "filled":
                log.info(f"  ✅ Fill: {emir_id} @ ${float(emir.filled_avg_price or 0):.2f}")
                return True
            elif emir.status in ("canceled", "expired", "rejected"):
                log.error(f"  ❌ Emir başarısız: {emir.status}")
                return False
            time.sleep(2)
        except Exception as e:
            log.error(f"  ❌ Emir sorgu hatası: {e}")
            return False
    log.warning(f"  ⚠️  Fill timeout ({max_bekleme}s): {emir_id}")
    return False


# ─────────────────────────────────────────────
# BÖLÜM 3: POZİSYON BOYUTU (Kelly — V5 Uyumlu)
# ─────────────────────────────────────────────
def pozisyon_boyutu_hesapla(
    api        : tradeapi.REST,
    fiyat      : float,
    guven_skoru: float = 0.30,
    katsayi    : float = 1.0,     # Pyramiding için 0.5 geçilebilir
) -> int:
    """
    Kelly Kriteri ile hisse adedi hesaplar. state_manager ile %100 senkron.
    katsayi: Pyramiding eklemelerinde 0.5 (yarım pozisyon).
    """
    try:
        hesap        = api.get_account()
        equity       = float(hesap.equity)
        buying_power = float(hesap.buying_power)

        abs_skor = abs(guven_skoru)
        if abs_skor >= 0.60:   hedef_pct = 0.35
        elif abs_skor >= 0.40: hedef_pct = 0.25
        elif abs_skor >= 0.30: hedef_pct = 0.15
        else:                  hedef_pct = 0.10

        hedef_pct   = min(hedef_pct * katsayi, MAX_POZ_PCT)
        hedef_tutar = equity * hedef_pct

        if hedef_tutar > buying_power:
            log.warning(f"⚠️  Bakiye yetersiz → küçültülüyor: ${hedef_tutar:,.0f} → ${buying_power*0.95:,.0f}")
            hedef_tutar = buying_power * 0.95

        adet = max(int(hedef_tutar / fiyat), 1)
        log.info(f"  📊 Kelly: ${equity:,.0f} × {hedef_pct:.0%} = ${hedef_tutar:,.0f} / ${fiyat:.2f} = {adet} hisse")
        return adet
    except Exception as e:
        log.error(f"❌ Pozisyon hesap hatası: {e}")
        return 1


# ─────────────────────────────────────────────
# BÖLÜM 4: ATR BAZLI TRAİLİNG STOP HESABI (V5)
# ─────────────────────────────────────────────
def atr_trailing_pct_hesapla(atr: Optional[float], fiyat: float) -> float:
    """
    V5: ATR değeri varsa Alpaca trailing stop yüzdesi ATR'ye göre ayarlanır.
    backtest ile senkron: ATR/fiyat × 100 × 2.5 katsayısı.

    Örnek:
        NVDA fiyat=$130, ATR=$4.5 → trail_pct = (4.5/130)*100*2.5 = 8.65% → çok geniş
        Sınır: min %2.0, max %8.0 (Alpaca gerçekçi aralık)
    """
    if not atr or not fiyat or fiyat == 0:
        return TRAILING_PCT   # Fallback: .env'deki sabit değer

    # ATR bazlı trailing yüzde
    atr_pct = (atr / fiyat) * 100 * 2.5
    # Sınırlar: minimum %2, maksimum %8
    atr_pct = max(2.0, min(8.0, round(atr_pct, 2)))

    log.info(f"  🎯 ATR Trailing: ATR=${atr:.2f} / Fiyat=${fiyat:.2f} × 2.5 = %{atr_pct}")
    return atr_pct


# ─────────────────────────────────────────────
# BÖLÜM 5: LONG EMİR MOTORU (V5 ATR Trailing)
# ─────────────────────────────────────────────
def long_emri_gonder(
    api      : tradeapi.REST,
    symbol   : str,
    adet     : int,
    atr      : Optional[float] = None,
    fiyat    : float = 0.0,
    etiket   : str = "YENİ_LONG",
) -> dict:
    """
    HAYATİ KURAL 2 — İzleyen Stop:
    V5: Trailing stop yüzdesi artık ATR bazlı hesaplanıyor.
    """
    sonuc = {
        "sembol": symbol, "yon": "LONG", "adet": adet,
        "market_emir": None, "trailing_emir": None,
        "durum": "BAŞARISIZ", "hata": None,
        "etiket": etiket, "zaman": datetime.now().isoformat(),
    }
    try:
        # Adım 1: Piyasa alım emri
        log.info(f"  📤 Piyasa al: {adet}× {symbol} [{etiket}]")
        market = api.submit_order(symbol=symbol, qty=adet, side="buy",
                                  type="market", time_in_force="day")
        sonuc["market_emir"] = market.id

        # Adım 2: Fill bekle
        if not emir_fill_bekle(api, market.id):
            sonuc["hata"] = "Fill olmadı"; sonuc["durum"] = "KISMEN"; return sonuc

        # Adım 3: V5 ATR Trailing Stop
        trail_pct = atr_trailing_pct_hesapla(atr, fiyat)
        log.info(f"  🔒 Trailing Stop: %{trail_pct} "
                 f"({'ATR bazlı V5' if atr else 'sabit fallback'})")

        trail = api.submit_order(
            symbol=symbol, qty=adet, side="sell",
            type="trailing_stop", time_in_force="gtc",
            trail_percent=str(trail_pct),
        )
        sonuc["trailing_emir"]  = trail.id
        sonuc["trail_pct_used"] = trail_pct
        sonuc["atr_kullanildi"] = bool(atr)
        sonuc["durum"]          = "BAŞARILI"
        log.info(f"  ✅ Trailing bağlandı: {trail.id} (%{trail_pct})")

    except APIError as e:
        hata = str(e)
        log.error(f"  ❌ Alpaca API Hatası ({symbol} LONG): {hata}")
        if "insufficient" in hata.lower():
            log.error("     → Yetersiz bakiye")
        elif "not tradable" in hata.lower():
            log.error("     → Sembol Alpaca'da işlem görmüyor")
        sonuc["hata"] = hata
    except Exception as e:
        log.error(f"  ❌ Beklenmeyen hata ({symbol} LONG): {e}")
        sonuc["hata"] = str(e)

    return sonuc


# ─────────────────────────────────────────────
# BÖLÜM 6: SHORT EMİR MOTORU
# ─────────────────────────────────────────────
def short_emri_gonder(
    api      : tradeapi.REST,
    symbol   : str,
    adet     : int,
    stop_loss: Optional[float] = None,
) -> dict:
    sonuc = {
        "sembol": symbol, "yon": "SHORT", "adet": adet,
        "short_emir": None, "sl_emir": None,
        "durum": "BAŞARISIZ", "hata": None,
        "zaman": datetime.now().isoformat(),
    }
    try:
        log.info(f"  📤 Açığa sat: {adet}× {symbol}")
        short = api.submit_order(symbol=symbol, qty=adet, side="sell",
                                 type="market", time_in_force="day")
        sonuc["short_emir"] = short.id

        if not emir_fill_bekle(api, short.id):
            sonuc["hata"] = "Fill olmadı"; sonuc["durum"] = "KISMEN"; return sonuc

        if stop_loss and stop_loss > 0:
            sl = api.submit_order(symbol=symbol, qty=adet, side="buy",
                                  type="stop", time_in_force="gtc",
                                  stop_price=str(round(stop_loss, 2)))
            sonuc["sl_emir"] = sl.id
            log.info(f"  🔒 Short SL: {sl.id} @ ${stop_loss:.2f}")
        else:
            log.warning(f"  ⚠️  Short KORUMASIZ (stop_loss yok)")

        sonuc["durum"] = "BAŞARILI"

    except APIError as e:
        hata = str(e)
        log.error(f"  ❌ Alpaca API Hatası ({symbol} SHORT): {hata}")
        if "not tradable" in hata.lower():
            log.error("     → Açığa satış kapalı olabilir")
        sonuc["hata"] = hata
    except Exception as e:
        log.error(f"  ❌ Beklenmeyen hata ({symbol} SHORT): {e}")
        sonuc["hata"] = str(e)

    return sonuc


# ─────────────────────────────────────────────
# BÖLÜM 7: V5 PYRAMİDİNG MOTORU ← YENİ
# ─────────────────────────────────────────────
def pyramiding_kontrol(
    mevcut   : object,       # Alpaca pozisyon nesnesi
    atr      : Optional[float],
    guncel_fiyat: float,
    guven_skoru : float,
) -> dict:
    """
    Backtest V5 ile %100 senkron Pyramiding mantığı.

    Koşul (true_backtest_v4.py'deki PYRAMİDİNG_ATR_KATSAYI ile birebir):
        1. Pozisyon LONG yönünde
        2. Pozisyon kârda (unrealized_pl > 0)
        3. Güncel fiyat > giriş fiyatı + (ATR × PYRAMIDING_ATR_KAT)
        4. Daha önce max ekleme sayısına ulaşılmamış

    Returns:
        {"ekle": True/False, "sebep": açıklama, "adet": eklenecek adet}
    """
    if not PYRAMIDING_AKTIF:
        return {"ekle": False, "sebep": "Pyramiding devre dışı (.env: PYRAMIDING_AKTIF=false)"}

    if not atr or atr <= 0:
        return {"ekle": False, "sebep": "ATR verisi yok — pyramiding hesaplanamaz"}

    try:
        pnl        = float(mevcut.unrealized_pl or 0)
        giris_fiy  = float(mevcut.avg_entry_price or 0)
        mevcut_adet= abs(int(float(mevcut.qty)))

        # Koşul 1: Kârda mı?
        if pnl <= 0:
            return {"ekle": False, "sebep": f"Pozisyon zararda (PnL: ${pnl:+.2f}) — eklemE yok"}

        # Koşul 2: Fiyat yeterince yüksek mi?
        esik_fiyat = giris_fiy + (atr * PYRAMIDING_ATR_KAT)
        if guncel_fiyat < esik_fiyat:
            return {
                "ekle"  : False,
                "sebep" : (f"Fiyat eşiğin altında — "
                           f"Güncel: ${guncel_fiyat:.2f} < "
                           f"Eşik: ${esik_fiyat:.2f} "
                           f"(giriş ${giris_fiy:.2f} + {PYRAMIDING_ATR_KAT}×ATR ${atr:.2f})")
            }

        # Koşul 3: Max ekleme kontrolü
        # Not: Alpaca'da ekleme geçmişini takip etmek için order_log.json'a bakılır
        eklemeler = _pyramiding_gecmis_say(mevcut.symbol)
        if eklemeler >= PYRAMIDING_MAX_EKLE:
            return {
                "ekle"  : False,
                "sebep" : f"Maksimum ekleme sayısına ulaşıldı ({eklemeler}/{PYRAMIDING_MAX_EKLE})"
            }

        # Ekleme miktarı: Mevcut pozisyonun %50'si
        eklenecek = max(int(mevcut_adet * PYRAMIDING_POZ_PCT), 1)

        log.info(f"  🔺 PYRAMİDİNG KOŞULLARI SAĞLANDI:")
        log.info(f"     PnL: ${pnl:+.2f} | Giriş: ${giris_fiy:.2f} | "
                 f"Güncel: ${guncel_fiyat:.2f} | Eşik: ${esik_fiyat:.2f}")
        log.info(f"     Eklenecek: {eklenecek} hisse (mevcut {mevcut_adet} × %{PYRAMIDING_POZ_PCT:.0%})")

        return {
            "ekle"          : True,
            "sebep"         : (f"Pyramiding: ${guncel_fiyat:.2f} > ${esik_fiyat:.2f} eşiği "
                               f"({PYRAMIDING_ATR_KAT}×ATR), PnL: ${pnl:+.2f}"),
            "adet"          : eklenecek,
            "onceki_eklemeler": eklemeler,
        }

    except Exception as e:
        log.error(f"  ❌ Pyramiding kontrol hatası: {e}")
        return {"ekle": False, "sebep": f"Hata: {e}"}


def _pyramiding_gecmis_say(symbol: str) -> int:
    """order_log.json'dan bu sembol için kaç pyramiding yapıldığını sayar."""
    try:
        if not Path(ORDER_LOG).exists():
            return 0
        with open(ORDER_LOG, "r", encoding="utf-8") as f:
            log_data = json.load(f)
        bugun = datetime.now().strftime("%Y-%m-%d")
        say = sum(
            1 for kayit in log_data
            if (kayit.get("symbol") == symbol and
                kayit.get("eylem") == "PYRAMİDİNG" and
                kayit.get("zaman", "").startswith(bugun))
        )
        return say
    except Exception:
        return 0


# ─────────────────────────────────────────────
# BÖLÜM 8: ANA KARAR MOTORU (HAYATİ KURALLAR 1&2)
# V5: DUPLICATE_SKIP → PYRAMİDİNG
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# BÖLÜM 8: ANA KARAR MOTORU (HAYATİ KURALLAR 1&2)
# V5: DUPLICATE_SKIP → PYRAMİDİNG
# ─────────────────────────────────────────────
def emri_isle(
    api         : tradeapi.REST,
    symbol      : str,
    order_type  : str,
    stop_loss   : Optional[float],
    guven_skoru : float = 0.30,
    fiyat       : float = 0.0,
    atr         : Optional[float] = None,
) -> dict:
    """
    HAYATİ KURAL 1 — State Awareness:
        SHORT + LONG var   → LONG kapat, SHORT açma
        LONG  + SHORT var  → SHORT kapat, LONG aç
        Aynı yön + kâr + ATR eşiği aşıldı → PYRAMİDİNG (V5 YENİ)
        Aynı yön + koşul yok → ATLA

    HAYATİ KURAL 2 — V5 ATR Trailing:
        LONG emirlerinde trail_percent = ATR bazlı hesap.
    """
    log.info(f"\n{'─'*60}")
    log.info(f"  ⚡ {symbol} {order_type} | ATR: {'$'+str(round(atr,2)) if atr else 'yok'}")
    log.info(f"{'─'*60}")

    sonuc = {
        "symbol"    : symbol,
        "order_type": order_type,
        "stop_loss" : stop_loss,
        "eylem"     : None,
        "sonuc"     : None,
        "zaman"     : datetime.now().isoformat(),
    }

    mevcut = pozisyon_al(api, symbol)
    if mevcut:
        mevcut_yan   = mevcut.side
        mevcut_adet  = abs(int(float(mevcut.qty)))
        log.info(f"  📋 Mevcut: {symbol} {mevcut_yan.upper()} {mevcut_adet} hisse")
    else:
        mevcut_yan  = None
        mevcut_adet = 0
        log.info(f"  📋 Pozisyon yok: {symbol}")

    # ── 🛡️ GÜVENLİK DUVARI (FIREWALL): LONG ONLY KORUMASI ──────────────
    if order_type == "SHORT" and mevcut_yan != "long":
        log.warning(f"  🚫 FIREWALL: {symbol} için yeni SHORT emri imha edildi (LONG_ONLY Zırhı Aktif)!")
        sonuc["eylem"] = "SHORT_BLOCKED"
        sonuc["sonuc"] = {"durum": "REDDEDİLDİ", "hata": "Sistem %100 LONG_ONLY modundadır."}
        return sonuc

    # ── SENARYO 1: LONG sinyal + mevcut SHORT → kapat, LONG aç ──────────
    if order_type == "LONG" and mevcut_yan == "short":
        log.info(f"  ↩️  SHORT kapatılıyor → LONG açılacak")
        sonuc["eylem"] = "CLOSE_SHORT_THEN_LONG"
        try:
            acik_emirleri_iptal_et(api, symbol)
            api.close_position(symbol)
            log.info(f"  ✅ SHORT kapatıldı: {symbol}")
            _telegram_gonder(f"🟡 <b>{symbol} SHORT kapatıldı</b>\nSebep: LONG sinyali.")
            time.sleep(2)
            adet   = pozisyon_boyutu_hesapla(api, fiyat, guven_skoru)
            emir_s = long_emri_gonder(api, symbol, adet, atr, fiyat, "CLOSE_SHORT_THEN_LONG")
            sonuc["sonuc"] = emir_s
        except Exception as e:
            log.error(f"  ❌ SHORT kapat/LONG aç hatası: {e}")
            sonuc["sonuc"] = {"hata": str(e)}
        return sonuc

    # ── SENARYO 2: SHORT sinyal + mevcut LONG → HAYATİ KURAL 1 ──────────
    elif order_type == "SHORT" and mevcut_yan == "long":
        log.info(f"  ↩️  HAYATİ KURAL 1: LONG kapatılıyor, SHORT AÇILMIYOR")
        sonuc["eylem"] = "CLOSE_LONG_NO_SHORT"
        try:
            acik_emirleri_iptal_et(api, symbol)
            api.close_position(symbol)
            giris = float(mevcut.avg_entry_price or 0)
            pnl   = (fiyat - giris) * mevcut_adet if fiyat and giris else None
            log.info(f"  ✅ LONG kapatıldı: {symbol} | PnL≈${pnl:+.2f}" if pnl else f"  ✅ LONG kapatıldı")
            _telegram_gonder(
                f"🟡 <b>{symbol} LONG kapatıldı (HAYATİ KURAL 1)</b>\n"
                f"Sebep: SHORT sinyali (SATIŞ).\n"
                f"{'💰 PnL≈$'+f'{pnl:+.2f}' if pnl else ''}\n"
                f"⛔ Yeni SHORT açılmadı."
            )
            sonuc["sonuc"] = {"durum": "BAŞARILI", "pnl_tahmini": pnl}
        except Exception as e:
            log.error(f"  ❌ LONG kapat hatası: {e}")
            sonuc["sonuc"] = {"hata": str(e)}
        return sonuc

    # ── SENARYO 3: V5 PYRAMİDİNG — Aynı yönde pozisyon var ──────────────
    elif (order_type == "LONG"  and mevcut_yan == "long") or \
         (order_type == "SHORT" and mevcut_yan == "short"):

        # V5: Eski DUPLICATE_SKIP → Pyramiding kontrol
        pyra = pyramiding_kontrol(mevcut, atr, fiyat or 0, guven_skoru)

        if pyra.get("ekle"):
            log.info(f"  🔺 PYRAMİDİNG: {pyra['sebep']}")
            sonuc["eylem"] = "PYRAMİDİNG"
            eklenecek_adet = pyra.get("adet", 1)

            if order_type == "LONG":
                emir_s = long_emri_gonder(api, symbol, eklenecek_adet, atr, fiyat, "PYRAMİDİNG")
            else:
                emir_s = short_emri_gonder(api, symbol, eklenecek_adet, stop_loss)

            sonuc["sonuc"] = emir_s
            sonuc["sonuc"]["pyramiding_bilgi"] = pyra
        else:
            # Koşul sağlanmadı → sessizce atla (eski DUPLICATE_SKIP gibi)
            log.info(f"  ⏭️  Pyramiding koşulu yok: {pyra.get('sebep', '?')}")
            sonuc["eylem"] = "PYRAMIDING_SKIP"
            sonuc["sonuc"] = {"durum": "ATLANDI", "sebep": pyra.get("sebep")}

        return sonuc

    # ── SENARYO 4: Pozisyon yok + LONG → yeni LONG + ATR Trailing ───────
    elif order_type == "LONG" and mevcut_yan is None:
        log.info(f"  🟢 Yeni LONG açılıyor")
        sonuc["eylem"] = "YENİ_LONG"
        acik_emirleri_iptal_et(api, symbol)
        time.sleep(0.5)
        adet   = pozisyon_boyutu_hesapla(api, fiyat, guven_skoru)
        emir_s = long_emri_gonder(api, symbol, adet, atr, fiyat, "YENİ_LONG")
        sonuc["sonuc"] = emir_s
        return sonuc

    # ── SENARYO 5: Pozisyon yok + SHORT → yeni SHORT + Stop ─────────────
    elif order_type == "SHORT" and mevcut_yan is None:
        log.info(f"  🔴 Yeni SHORT açılıyor")
        sonuc["eylem"] = "YENİ_SHORT"
        acik_emirleri_iptal_et(api, symbol)
        time.sleep(0.5)
        adet   = pozisyon_boyutu_hesapla(api, fiyat, guven_skoru)
        emir_s = short_emri_gonder(api, symbol, adet, stop_loss)
        sonuc["sonuc"] = emir_s
        return sonuc

    log.error(f"  ❌ Bilinmeyen senaryo: order_type={order_type}, mevcut={mevcut_yan}")
    sonuc["eylem"] = "HATA"
    sonuc["sonuc"] = {"hata": "Bilinmeyen senaryo"}
    return sonuc

# ─────────────────────────────────────────────
# BÖLÜM 9: TELEGRAM YARDIMCISI
# ─────────────────────────────────────────────
def _telegram_gonder(mesaj: str) -> None:
    if TELEGRAM_AKTIF:
        telegram_gonder(mesaj)
    else:
        log.info(f"[TELEGRAM] {mesaj}")


def islem_telegram_bildir(sonuc: dict) -> None:
    symbol      = sonuc.get("symbol", "?")
    order_type  = sonuc.get("order_type", "?")
    eylem       = sonuc.get("eylem", "?")
    emir_sonuc  = sonuc.get("sonuc") or {}
    durum       = emir_sonuc.get("durum", "?")
    zaman       = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    yon_ikon    = "🟢" if order_type == "LONG" else "🔴" if order_type == "SHORT" else "🟡"

    if durum == "BAŞARILI":
        trail  = emir_sonuc.get("trail_pct_used")
        atr_ok = emir_sonuc.get("atr_kullanildi", False)
        sl     = sonuc.get("stop_loss")
        trail_str = (f"\n🎯 <b>Trailing:</b> %{trail} {'(ATR-V5)' if atr_ok else '(sabit)'}"
                     if trail else "")
        sl_str = f"\n🛑 <b>Stop:</b> ${sl:.2f}" if sl else ""
        mesaj = (
            f"{yon_ikon} <b>{symbol} {order_type} — AÇILDI</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"⏰ <code>{zaman}</code>\n"
            f"📌 <b>Eylem:</b> {eylem}"
            f"{trail_str}{sl_str}\n"
            f"✅ Alpaca'ya iletildi."
        )
    elif eylem == "PYRAMİDİNG":
        pyra_bilgi = emir_sonuc.get("pyramiding_bilgi", {})
        mesaj = (
            f"🔺 <b>{symbol} PYRAMİDİNG</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"⏰ <code>{zaman}</code>\n"
            f"📈 {pyra_bilgi.get('sebep', '')}\n"
            f"{'✅ Ekleme yapıldı' if durum == 'BAŞARILI' else '❌ Ekleme başarısız'}"
        )
    elif eylem == "PYRAMIDING_SKIP":
        return  # Sessizce atla — her skip için bildirim gereksiz
    elif eylem in ("CLOSE_LONG_NO_SHORT", "CLOSE_SHORT_THEN_LONG"):
        return  # emri_isle içinde zaten bildirildi
    else:
        hata = emir_sonuc.get("hata", "?")
        mesaj = (
            f"🚨 <b>{symbol} {order_type} — HATA</b>\n"
            f"⏰ <code>{zaman}</code>\n"
            f"❌ <code>{hata[:150]}</code>"
        )

    _telegram_gonder(mesaj)


# ─────────────────────────────────────────────
# BÖLÜM 10: AUDIT LOG
# ─────────────────────────────────────────────
def audit_log_yaz(sonuclar: list) -> None:
    try:
        mevcut = []
        if Path(ORDER_LOG).exists():
            try:
                with open(ORDER_LOG, "r", encoding="utf-8") as f:
                    mevcut = json.load(f)
            except Exception:
                mevcut = []
        mevcut.extend(sonuclar)
        with open(ORDER_LOG, "w", encoding="utf-8") as f:
            json.dump(mevcut, f, ensure_ascii=False, indent=2)
        log.info(f"✅ {len(sonuclar)} kayıt {ORDER_LOG}'a yazıldı.")
    except Exception as e:
        log.error(f"❌ Audit log hatası: {e}")


# ─────────────────────────────────────────────
# BÖLÜM 11: JSON FORMAT DÖNÜŞTÜRÜCÜ (V5)
# ─────────────────────────────────────────────
def json_normalize(ham: dict) -> list[dict]:
    """
    Format A: claude_otonom_onay() tek emir JSON'u
    Format B: final_karar.json (state_manager, tüm varlıklar)

    V5: ATR ve guven_skoru her iki formattan da okunuyor.
    """
    emirler = []

    # Format A: Tek emir
    if "action" in ham:
        if ham.get("action") == "EXECUTE":
            emirler.append({
                "symbol"     : ham.get("symbol"),
                "order_type" : ham.get("order_type"),
                "stop_loss"  : ham.get("stop_loss"),
                "guven_skoru": ham.get("guven_skoru", 0.30),
                "fiyat"      : ham.get("fiyat", 0.0),
                "atr"        : ham.get("atr"),          # V5: ATR
            })
        else:
            log.info(f"  ⏭️  action={ham.get('action')} — işlem yok")
        return emirler

    # Format B: final_karar.json
    if "kararlar" in ham:
        for k in ham["kararlar"]:
            sinyal = k.get("final_sinyal", "HOLD")
            if sinyal in ("LONG", "SHORT") and not k.get("catisma", False):
                emirler.append({
                    "symbol"     : k.get("symbol"),
                    "order_type" : sinyal,
                    "stop_loss"  : k.get("stop_loss"),
                    "guven_skoru": k.get("guven_skoru", 0.30),
                    "fiyat"      : k.get("fiyat", 0.0),
                    "atr"        : k.get("atr_degeri"),  # V5: state_manager'ın yazdığı ATR
                })
            else:
                log.info(f"  ⏭️  {k.get('symbol')}: {sinyal} "
                         f"{'(çatışma)' if k.get('catisma') else ''} — atlandı")
        return emirler

    log.error("❌ Tanınmayan JSON formatı")
    return []


# ─────────────────────────────────────────────
# BÖLÜM 12: ANA PİPELİNE
# ─────────────────────────────────────────────
def pipeline_calistir(ham_json: dict) -> None:
    """
    1. API bağlantı
    2. Market saati
    3. PDT koruma
    4. JSON normalleştir
    5. Her emir: State Awareness → İşlem (HAYATİ KURAL 1) → ATR Trailing (HAYATİ KURAL 2)
       V5 YENİ: Aynı yön + koşul → Pyramiding
    6. Audit log
    """
    api = api_baglan()
    if not api:
        _telegram_gonder("🚨 <b>KRİTİK HATA</b>\nAlpaca bağlantısı kurulamadı!")
        return

    if not market_acik_mi(api):
        _telegram_gonder("⏰ <b>Market Kapalı</b>\nEmir gönderilemedi.")
        return

    if not pdt_kontrol(api):
        _telegram_gonder("🚨 <b>PDT Limiti</b>\nDay trade limiti doldu!")
        return

    emirler = json_normalize(ham_json)
    if not emirler:
        log.info("İşlenecek EXECUTE sinyali yok.")
        return

    log.info(f"\n🚀 {len(emirler)} sinyal işlenecek.")
    tum_sonuclar = []

    for e in emirler:
        symbol = e.get("symbol")
        otype  = e.get("order_type")
        if not symbol or not otype:
            log.warning(f"  ⚠️  Eksik alan — atlandı: {e}"); continue

        log.info(f"\n{'═'*60}")
        log.info(f"  → {symbol} {otype} | ATR: {'$'+str(round(e['atr'],2)) if e.get('atr') else 'yok'}")

        sonuc = emri_isle(
            api         = api,
            symbol      = symbol,
            order_type  = otype,
            stop_loss   = e.get("stop_loss"),
            guven_skoru = e.get("guven_skoru", 0.30),
            fiyat       = e.get("fiyat", 0.0),
            atr         = e.get("atr"),       # V5: ATR pipeline'dan geliyor
        )
        tum_sonuclar.append(sonuc)
        islem_telegram_bildir(sonuc)
        time.sleep(1)

    audit_log_yaz(tum_sonuclar)
    log.info(f"\n✅ Pipeline tamamlandı — {len(tum_sonuclar)} işlem.")


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{'═'*60}")
    print(f"  Algoritmik Hedge Fon | Alpaca Trader V5")
    print(f"  Mod: {'CANLI 💵' if CANLI_PARA else 'PAPER 📄'}")
    print(f"  Pyramiding: {'AKTIF 🔺' if PYRAMIDING_AKTIF else 'KAPALI ⏹'}")
    print(f"{'═'*60}\n")

    if "--test" in sys.argv:
        api = api_baglan()
        if api:
            market_acik_mi(api)
            pdt_kontrol(api)
            log.info("✅ Bağlantı testleri tamam.")
        sys.exit(0)

    if "--json" in sys.argv:
        try:
            idx  = sys.argv.index("--json")
            jstr = sys.argv[idx + 1]
            pipeline_calistir(json.loads(jstr))
        except (IndexError, json.JSONDecodeError) as e:
            log.error(f"❌ JSON argüman hatası: {e}"); sys.exit(1)
    else:
        if not Path(FINAL_KARAR).exists():
            log.error(f"❌ {FINAL_KARAR} bulunamadı — önce: python state_manager.py")
            sys.exit(1)
        with open(FINAL_KARAR, "r", encoding="utf-8") as f:
            ham = json.load(f)
        log.info(f"📂 {FINAL_KARAR} okundu.")
        pipeline_calistir(ham)