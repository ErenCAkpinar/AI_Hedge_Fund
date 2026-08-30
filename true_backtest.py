"""
true_backtest_v6.py — STATE MANAGER V6 İLE TAM SENKRON
=======================================================

v5 → v6 DEĞİŞİKLİKLER:

  🧠 GELİŞTİRME 5: HMM REJİM TESPİTİ
    - BULL/SIDEWAYS/BEAR piyasa rejimi → dinamik eşik çarpanı
    - BEAR piyasada giriş eşiği ×1.5 → daha az işlem, daha güvenli

  📊 GELİŞTİRME 6: KURTOSİS DİNAMİK ATR TRAİL
    - Sabit ATR_TRAIL_KATSAYI=2.5 → Kurtosis bazlı 2.5-5.0
    - Kalın kuyruk (fat tail) algılanırsa trail mesafesi genişler

  📈 GELİŞTİRME 7: HURST LONG FİLTRESİ
    - Hurst < 0.45 (mean-reversion) → LONG engellenir
    - Trend var mı yok mu istatistiksel test

  🌊 GELİŞTİRME 8: GARCH POZİSYON ÖLÇEĞİ
    - GARCH(1,1) volatilite tahmini → Kelly pozisyon küçültme
    - Yüksek vol → yarım pozisyon

  🦢 GELİŞTİRME 9: VIX/BLACK SWAN SİMÜLASYONU
    - Tarihsel ^VIX verisi ile risk-off dönemleri simüle edilir
    - VIX > 30 → güvenli liman hariç HOLD

  📉 GELİŞTİRME 10: GELİŞMİŞ METRİKLER
    - Sortino Ratio, VaR/CVaR, Walk-Forward analiz

  v5'ten devam:
    1. İzleyen Stop (Trailing Stop)
    2. Dinamik Pozisyon Büyüklüğü (Kelly)
    3. Pyramiding
    4. Bileşik Getiri (Compounding)
"""

import math
import sys, json, warnings
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np
import yfinance as yf
from ta.momentum import RSIIndicator, WilliamsRIndicator
from ta.trend import MACD, SMAIndicator, EMAIndicator, ADXIndicator
from ta.volatility import AverageTrueRange, BollingerBands

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))

from legends_agent import efsane_oylama
from state_manager import (
    teknik_skora_cevir, efsane_skora_cevir,
    catisma_var_mi,
    pozisyon_buyuklugu_hesapla,
    AGIRLIK_TEKNIK, AGIRLIK_EFSANE, AGIRLIK_SENTIMENT,
    AGIRLIK_INSIDER, AGIRLIK_GAMMA,
)
from portfolio_simulator import (
    SignalDecision,
    SimulatorConfig,
    equity_metrics,
    simulate_portfolio,
)
from quant_math import (
    kurtosis_hesapla,
    hurst_hesapla,
    garch_volatilite,
    hmm_rejim_tespit,
    sortino_hesapla,
    var_cvar_hesapla,
    walk_forward_test,
)

# ─────────────────────────────────────────────
# CONFIG v6
# ─────────────────────────────────────────────
WATCHLIST = [
    # Yarı İletken & AI Liderleri
    "NVDA", "AVGO", "SOXX",
    # Veri, Yazılım & Kripto
    "PLTR", "MSTR", "IBIT",
    # Agresif Momentum Şampiyonları (Ana Kâr Motorları)
    "ASTS", "VST",
    # Savunma, İlaç & Otomotiv
    "LMT", "LLY", "TSLA",
    # Makro Koruma & Değer
    "GLD", "FXY", "META",
    "USO", "WMT", "QQQ"
]

LONG_ONLY_LIST = {
    "NVDA", "AVGO", "SOXX", "PLTR", "MSTR", "IBIT", "ASTS", "VST",
    "LMT", "LLY", "TSLA", "GLD", "FXY", "META", "USO", "WMT", "QQQ"
}

GUVENLI_LIMANLAR = {"GLD", "USO", "FXY"}

BASLANGIC_SERMAYE = 1_500
# A moving PERIOD="2y" silently re-dates every run, so no two results are
# comparable and "identical window for strategy and benchmark" is unverifiable.
BACKTEST_START    = "2024-08-29"
BACKTEST_END      = "2026-08-29"   # yfinance end is exclusive
PERIOD            = None
ISINMA_GUN        = 60
MAX_POZISYON_GUN  = 15

# V6: state_manager ile senkron eşikler
ESIK_YUKSEK = 0.45
ESIK_ORTA   = 0.28

ADX_MIN_LONG    = 20
ADX_MIN_SHORT   = 30
SHORT_ORAN_MIN  = 60

# ATR stop katsayıları
ATR_SL_YUKSEK = 1.8
ATR_SL_ORTA   = 1.5
ATR_TP_YUKSEK = 4.5
ATR_TP_ORTA   = 3.5

# V6: Kurtosis bazlı trail — varsayılan, runtime'da override edilir
ATR_TRAIL_KATSAYI_DEFAULT = 2.5

# Pyramiding
PYRAMID_TRIGGER_ATR = 1.5
PYRAMID_MAX         = 2
PYRAMID_BOYUT       = 0.50

# V6: VIX eşikleri (swan_agent ile senkron)
VIX_RISK_OFF = 30     # VIX > 30 → güvenli liman dışında HOLD


# ─────────────────────────────────────────────
# BÖLÜM 1: VERİ
# ─────────────────────────────────────────────
def veri_cek(symbol):
    try:
        df = yf.Ticker(symbol).history(start=BACKTEST_START, end=BACKTEST_END,
                                       interval="1d")
        if df.empty or len(df) < ISINMA_GUN + 10:
            return None

        close = df["Close"]; high = df["High"]; low = df["Low"]

        df["RSI"]        = RSIIndicator(close=close, window=14).rsi()
        df["SMA_20"]     = SMAIndicator(close=close, window=20).sma_indicator()
        df["SMA_50"]     = SMAIndicator(close=close, window=50).sma_indicator()
        df["SMA_200"]    = SMAIndicator(close=close, window=200).sma_indicator()

        _macd            = MACD(close=close)
        df["MACD"]       = _macd.macd()
        df["MACD_SIG"]   = _macd.macd_signal()
        df["MACD_HIST"]  = _macd.macd_diff()

        df["ATR"]        = AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range()
        _bb              = BollingerBands(close=close, window=20, window_dev=2)
        df["BB_HIGH"]    = _bb.bollinger_hband()
        df["BB_LOW"]     = _bb.bollinger_lband()
        df["BB_MID"]     = _bb.bollinger_mavg()
        df["WILLIAMS_R"] = WilliamsRIndicator(high=high, low=low, close=close, lbp=14).williams_r()
        df["HIGH_20"]    = high.rolling(20).max()
        df["LOW_20"]     = low.rolling(20).min()
        df["VOL_AVG"]    = df["Volume"].rolling(20).mean()

        _adx             = ADXIndicator(high=high, low=low, close=close, window=14)
        df["ADX"]        = _adx.adx()

        return df
    except Exception as e:
        print(f"  ❌ {symbol}: {e}")
        return None


def vix_tarihsel_cek():
    """Tarihsel VIX verisi çeker — swan simülasyonu için."""
    try:
        vix = yf.Ticker("^VIX").history(start=BACKTEST_START, end=BACKTEST_END,
                                        interval="1d")
        if vix.empty:
            return None
        return vix["Close"]
    except Exception:
        return None


# ─────────────────────────────────────────────
# BÖLÜM 2: MOCK AGENT (RSI doğru, SMA cross dahil)
# ─────────────────────────────────────────────
def mock_agent_karar(df_slice):
    if len(df_slice) < 5:
        return {"SİNYAL": "HOLD", "PUAN": 0}

    son  = df_slice.iloc[-1]
    puan = 0

    rsi       = son.get("RSI", 50)
    histogram = son.get("MACD_HIST", 0)
    macd      = son.get("MACD", 0)
    close     = float(son["Close"])
    sma20     = son.get("SMA_20")
    sma50     = son.get("SMA_50")

    if pd.notna(rsi):
        if rsi < 30:   puan += 2
        elif rsi < 45: puan += 1
        elif rsi > 70: puan -= 2
        elif rsi > 55: puan -= 1

    if pd.notna(histogram) and pd.notna(macd):
        if histogram > 0 and macd > 0:    puan += 2
        elif histogram > 0:               puan += 1
        elif histogram < 0 and macd < 0:  puan -= 2
        elif histogram < 0:               puan -= 1

    if pd.notna(sma20) and pd.notna(sma50) and sma20 > 0 and sma50 > 0:
        if close > sma20 and close > sma50:    puan += 2
        elif close > sma20:                    puan += 1
        elif close < sma20 and close < sma50:  puan -= 2
        elif close < sma20:                    puan -= 1
        puan += 1 if sma20 > sma50 else -1

    sinyal = "LONG" if puan >= 4 else "SHORT" if puan <= -4 else "HOLD"
    return {"SİNYAL": sinyal, "PUAN": puan}



# ─────────────────────────────────────────────
# BÖLÜM 4: V6 SİNYAL (5 katman + dinamik eşik)
# ─────────────────────────────────────────────
def v6_sinyal(symbol, df_slice, vix_bugun=None, hmm_carpan=1.0):
    """
    V6: 5 katmanlı sinyal üretici (state_manager ile senkron).
    - Teknik + Efsane = aktif katmanlar
    - Sentiment/Insider/Gamma = backtest'te 0 (veri yok)
    - Ağırlıkları state_manager'dan alır, eksik katmanları teknik+efsane'ye dağıtır
    - HMM rejim çarpanı eşikleri dinamik yapar
    - VIX risk-off kontrolü
    - Hurst LONG filtresi
    """
    red_flags = []
    son = df_slice.iloc[-1]
    close = float(son["Close"])

    # V6: VIX risk-off kontrolü (güvenli liman hariç)
    if vix_bugun is not None and vix_bugun > VIX_RISK_OFF and symbol not in GUVENLI_LIMANLAR:
        red_flags.append(f"VIX={vix_bugun:.1f}>RISK_OFF")
        return "HOLD", "DÜŞÜK", 0.0, red_flags

    mock_karar  = mock_agent_karar(df_slice)
    teknik_dict = {"karar": mock_karar, "veri": {"son_kapanış": close}}
    efsane_dict = efsane_oylama(symbol, df_slice)

    t_skor = teknik_skora_cevir(teknik_dict)
    e_skor = efsane_skora_cevir(efsane_dict)

    # Sentiment/Insider/Gamma = 0 (backtest'te tarihsel veri yok)
    s_skor         = 0.0
    insider_sinyal = 0.0
    gamma_sinyal   = 0.0

    # Sentiment olmadığı için ağırlığını teknik+efsane'ye dağıt
    agirlik_t = AGIRLIK_TEKNIK + AGIRLIK_SENTIMENT * 0.60
    agirlik_e = AGIRLIK_EFSANE + AGIRLIK_SENTIMENT * 0.40

    toplam = round(
        t_skor         * agirlik_t       +
        e_skor         * agirlik_e       +
        s_skor         * 0.0             +
        insider_sinyal * AGIRLIK_INSIDER +
        gamma_sinyal   * AGIRLIK_GAMMA,
        3
    )

    if catisma_var_mi(t_skor, e_skor, s_skor):
        return "HOLD", "DÜŞÜK", abs(toplam), ["Ajan çatışması"]

    # V6: Hurst LONG filtresi
    close_series = df_slice["Close"]
    if len(close_series) >= 50:
        try:
            hurst_sonuc = hurst_hesapla(close_series)
            if not hurst_sonuc.get("long_izni", True) and toplam > 0:
                red_flags.append(f"Hurst={hurst_sonuc.get('hurst', 0):.2f}<0.45 mean-reversion")
                return "HOLD", "DÜŞÜK", abs(toplam), red_flags
        except Exception:
            pass

    # V6: VIX swan çarpanı
    swan_carpan = 1.0
    if vix_bugun is not None:
        if vix_bugun > 25:
            swan_carpan = 1.5
        elif vix_bugun > 20:
            swan_carpan = 1.25

    # V6: Dinamik eşikler (HMM × Swan)
    # Güvenli liman: eşik düşer (daha kolay giriş)
    gl_carpan = 1.5 if symbol in GUVENLI_LIMANLAR else 1.0
    esik_yuksek_eff = ESIK_YUKSEK * hmm_carpan * swan_carpan / gl_carpan
    esik_orta_eff   = ESIK_ORTA   * hmm_carpan * swan_carpan / gl_carpan

    if toplam >= esik_yuksek_eff:
        ham, guven = "LONG", "YÜKSEK"
    elif toplam >= esik_orta_eff:
        ham, guven = "LONG", "ORTA"
    elif toplam <= -esik_yuksek_eff:
        ham, guven = "SHORT", "YÜKSEK"
    elif toplam <= -esik_orta_eff:
        ham, guven = "SHORT", "ORTA"
    else:
        return "HOLD", "DÜŞÜK", abs(toplam), ["Eşik altı"]

    # ADX filtresi
    adx     = son.get("ADX", 0)
    adx_min = ADX_MIN_SHORT if ham == "SHORT" else ADX_MIN_LONG
    if pd.isna(adx) or adx < adx_min:
        red_flags.append(f"ADX={adx:.1f}<{adx_min}")
        return "HOLD", "DÜŞÜK", abs(toplam), red_flags

    # LONG_ONLY listesi
    if ham == "SHORT" and symbol in LONG_ONLY_LIST:
        red_flags.append(f"{symbol} LONG_ONLY")
        return "HOLD", "DÜŞÜK", abs(toplam), red_flags

    # SHORT için SMA200 altı zorunlu (Tudor Jones)
    if ham == "SHORT":
        sma200 = son.get("SMA_200")
        sma50  = son.get("SMA_50")
        ref    = sma200 if (pd.notna(sma200) and sma200 > 0) else sma50
        if ref and not pd.isna(ref) and close > ref:
            red_flags.append("Tudor Jones: Bull trend SHORT yasak")
            return "HOLD", "DÜŞÜK", abs(toplam), red_flags

    # SHORT için legends %60+ konsensüs
    if ham == "SHORT":
        short_oran = efsane_dict.get("short_oran", 0)
        if short_oran < SHORT_ORAN_MIN:
            red_flags.append(f"Legends {short_oran}%<{SHORT_ORAN_MIN}%")
            return "HOLD", "DÜŞÜK", abs(toplam), red_flags

    return ham, guven, abs(toplam), red_flags


# ─────────────────────────────────────────────
# BÖLÜM 5: İŞLEM SİMÜLATÖRÜ v6
# V6: Kurtosis ATR trail + GARCH pozisyon ölçeği
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# BÖLÜM 5: SİNYAL ADAPTÖRÜ
# v6_sinyal is untouched. This closes over the frames plus the VIX series and
# the HMM multiplier so the simulator can call a plain (symbol, date) callback.
# ─────────────────────────────────────────────
def make_signal_fn(frames, vix_series, hmm_carpan, sayaclar, cache=None):
    """Build the (symbol, date) -> SignalDecision|None callback.

    `cache` is shared across cost-sensitivity runs. A signal depends only on
    price history, VIX and the regime multiplier — never on execution cost —
    so recomputing Hurst/kurtosis/GARCH per bar for each cost level would be
    5x the work for an identical answer.

    ATR, the kurtosis trail multiple and the GARCH position scale are all read
    on the SIGNAL date. That bar is complete at its close, when the decision is
    made; the fill happens at the next open. The old code read the ENTRY bar's
    ATR while entering at that bar's open, which required the bar's own
    High/Low/Close — a look-ahead that set every stop in every backtest.
    """
    def signal_fn(symbol, date):
        if cache is not None and (symbol, date) in cache:
            return cache[(symbol, date)]
        d = _compute(symbol, date)
        if cache is not None:
            cache[(symbol, date)] = d
        return d

    def _compute(symbol, date):
        df = frames.get(symbol)
        if df is None or date not in df.index:
            return None
        idx = df.index.get_loc(date)
        if idx < ISINMA_GUN:
            return None
        df_slice = df.iloc[:idx + 1]

        vix_bugun = None
        if vix_series is not None:
            try:
                vix_bugun = float(vix_series.asof(date))
            except Exception:
                sayaclar["vix_asof_fail"] += 1
                vix_bugun = None          # D15: unknown, NOT treated as calm

        sinyal, guven, skor, flags = v6_sinyal(symbol, df_slice, vix_bugun, hmm_carpan)
        for f in flags:
            if "VIX" in f:       sayaclar["vix_risk_off"] += 1
            elif "Hurst" in f:   sayaclar["hurst_block"] += 1
            elif "ADX" in f:     sayaclar["adx"] += 1
            elif "Ajan" in f:    sayaclar["catisma"] += 1
            else:                sayaclar["esik"] += 1
        if sinyal == "HOLD":
            return None

        close_s = df_slice["Close"]
        atr = float(df.iloc[idx].get("ATR") or 0.0)
        if not math.isfinite(atr) or atr <= 0:
            sayaclar["atr_yok"] += 1
            return None
        for _name, _v in (("open", df.iloc[idx].get("Open")),
                          ("close", df.iloc[idx].get("Close"))):
            if _v is None or not math.isfinite(float(_v)) or float(_v) <= 0:
                sayaclar[f"gecersiz_{_name}"] += 1
                return None

        try:
            trail_mult = float(kurtosis_hesapla(close_s).get("atr_carpan", 2.5))
        except Exception:
            sayaclar["kurtosis_fail"] += 1
            trail_mult = 2.5
        try:
            garch_olcek = float(garch_volatilite(close_s).get("pozisyon_olcegi", 1.0))
        except Exception:
            sayaclar["garch_fail"] += 1
            garch_olcek = 1.0

        frac = pozisyon_buyuklugu_hesapla(skor) * garch_olcek
        return SignalDecision(
            side=1 if sinyal == "LONG" else -1,
            score=float(skor), confidence=guven,
            target_fraction=float(frac),
            atr=atr, trail_multiple=trail_mult,
            initial_stop_multiple=(ATR_SL_YUKSEK if guven == "YÜKSEK"
                                   else ATR_SL_ORTA))
    return signal_fn


# ─────────────────────────────────────────────
# BÖLÜM 6: BENCHMARKS
# Scored by the SAME equity_metrics() as the strategy — required by
# docs/designs/go-no-go.md.
# ─────────────────────────────────────────────
def buy_hold_curve(frames, calendar, symbols, capital, anchor=None):
    """Equal-weight buy-and-hold equity curve over `calendar`.

    A constituent that has not begun trading by the anchor date is held as CASH
    until its first bar. Skipping it instead makes the curve start below
    `capital` and jump when the symbol appears — a fabricated gain. (Codex
    blocker 7.)
    """
    start = anchor if anchor is not None else calendar[0]
    usable = [s for s in symbols if s in frames and len(frames[s].index)]
    if not usable:
        return pd.Series(dtype=float)

    per = capital / len(usable)
    shares, cash_leg, first_date = {}, {}, {}
    for s in usable:
        idx = frames[s].index[frames[s].index >= start]
        if len(idx):
            first_date[s] = idx[0]
            shares[s] = per / float(frames[s].loc[idx[0], "Close"])
            cash_leg[s] = per          # held as cash until first_date
        else:
            cash_leg[s] = per          # never trades in-window: stays cash

    rows, dates = [], [d for d in calendar if d >= start]
    for d in dates:
        v = 0.0
        for s in usable:
            fd = first_date.get(s)
            if fd is None or d < fd:
                v += cash_leg[s]                       # not yet invested
                continue
            df = frames[s]
            if d in df.index:
                v += shares[s] * float(df.loc[d, "Close"])
            else:
                prior = df.index[df.index <= d]
                v += shares[s] * float(df.loc[prior[-1], "Close"]) if len(prior) else cash_leg[s]
        rows.append(v)
    return pd.Series(rows, index=pd.DatetimeIndex(dates))


# ─────────────────────────────────────────────
# BÖLÜM 7: RAPOR
# ─────────────────────────────────────────────
def rapor_yazdir(res, bench, sayaclar, meta, anchor=None):
    ec = res.equity_curve
    # Score from the anchor onward. The first ISINMA_GUN sessions are warmup:
    # no signal can fire, so they are artificial zero-return days that would
    # deflate volatility and inflate Sharpe. (Codex answer 5.)
    if anchor is not None and not ec.empty:
        ec = ec.loc[ec.index >= anchor]
    st = equity_metrics(ec["equity"])
    trades = res.closed_trades
    wins = [t for t in trades if (t.get("pnl_dollar") or 0) > 0]
    reasons = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1

    print(f"\n{'═'*78}")
    print(f"  🔬 TRUE BACKTEST — portfolio-v1 simulator")
    print(f"  cash ledger · {res.config.max_gross_exposure:.0%} gross cap · "
          f"{res.config.side_cost_bps:.0f} bps/side · SHORT "
          f"{'ON' if res.config.allow_short else 'OFF'}")
    print(f"{'═'*78}")

    print(f"\n  ┌─ 💰 PERFORMANS {'─'*56}")
    print(f"  │  Başlangıç      : ${res.config.initial_cash:,.2f}")
    print(f"  │  Son sermaye    : ${res.final_equity:,.2f}")
    print(f"  │  Getiri         : {st['total_return_pct']:+.2f}%")
    print(f"  │  Sharpe         : {st['sharpe']}   (günlük equity eğrisinden)")
    print(f"  │  Sortino        : {st['sortino']}")
    print(f"  │  Max Drawdown   : {st['max_drawdown_pct']:.2f}%")
    print(f"  │  İşlem sayısı   : {len(trades)}   kazanan: {len(wins)} "
          f"({len(wins)/max(len(trades),1)*100:.1f}%)")
    print(f"  │  Çıkış nedeni   : " + " · ".join(f"{k}={v}" for k, v in sorted(reasons.items())))
    print(f"  │  İşlem maliyeti : ${res.total_execution_cost:,.2f} "
          f"+ ${res.total_fees:,.2f} harç")
    print(f"  ├─ 📊 GERÇEK POZİSYON {'─'*51}")
    print(f"  │  Ort. brüt maruziyet : {ec['gross_exposure_pct'].mean()*100:5.1f}%")
    print(f"  │  Maks brüt maruziyet : {ec['gross_exposure_pct'].max()*100:5.1f}%")
    print(f"  │  100% üstü gün       : {int((ec['gross_exposure_pct']>1.0).sum())}"
          f" / {len(ec)}   (cap ile 0 olmalı)")
    print(f"  │  Reddedilen emir     : {len(res.rejections)}")

    print(f"  ├─ 🎯 KARŞILAŞTIRMA (aynı fonksiyon, aynı pencere) {'─'*22}")
    print(f"  │  {'':22s} {'Getiri':>9} {'Sharpe':>8} {'MaxDD':>8}")
    print(f"  │  {'STRATEJİ':22s} {st['total_return_pct']:>8.2f}% "
          f"{str(st['sharpe']):>8} {st['max_drawdown_pct']:>7.2f}%")
    for name, m in bench.items():
        print(f"  │  {name:22s} {m['total_return_pct']:>8.2f}% "
              f"{str(m['sharpe']):>8} {m['max_drawdown_pct']:>7.2f}%")

    print(f"  ├─ 🔍 FİLTRE / HATA SAYAÇLARI {'─'*43}")
    for k, v in sorted(sayaclar.items()):
        if v:
            flag = "  ← SESSİZ BOZULMA" if k.endswith("_fail") else ""
            print(f"  │  {k:22s}: {v}{flag}")
    print(f"  └{'─'*76}")

    # ── go / no-go, criterion fixed in docs/designs/go-no-go.md (8b4aa68d) ──
    spy = bench.get("SPY buy & hold")
    print(f"\n  📊 GO / NO-GO  (ölçüt: 8b4aa68d, simülatörden ÖNCE yazıldı)")
    if spy and st["sharpe"] is not None and spy["sharpe"] is not None:
        s_ok = st["sharpe"] > spy["sharpe"]
        d_ok = st["max_drawdown_pct"] > spy["max_drawdown_pct"]   # less negative
        print(f"     Sharpe  {st['sharpe']} > SPY {spy['sharpe']} ......... "
              f"{'✅ GEÇTİ' if s_ok else '❌ KALDI'}")
        print(f"     MaxDD   {st['max_drawdown_pct']:.2f}% > SPY "
              f"{spy['max_drawdown_pct']:.2f}% ... {'✅ GEÇTİ' if d_ok else '❌ KALDI'}")
        print(f"     SONUÇ   : {'✅ GEÇTİ' if (s_ok and d_ok) else '❌ KALDI — arşivle'}")
    else:
        print("     hesaplanamadı")
    if meta.get("hmm_lookahead"):
        print("  ⚠️  HMM rejim çarpanı hâlâ tüm pencereden hesaplanıyor (look-ahead, E5 bekliyor)")
    print(f"{'═'*78}\n")
    return st


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{'═'*78}")
    print(f"  Algoritmik Hedge Fon | TRUE BACKTEST — portfolio-v1")
    print(f"{'═'*78}\n")

    sayaclar = defaultdict(int)

    print("  📡 SPY (takvim + benchmark)...")
    spy_df = yf.Ticker("SPY").history(start=BACKTEST_START, end=BACKTEST_END,
                                      interval="1d")
    if spy_df.empty:
        raise SystemExit("SPY verisi alınamadı — takvim kurulamaz.")
    calendar = list(spy_df.index)
    print(f"  ✅ {len(calendar)} seans | {str(calendar[0])[:10]} → {str(calendar[-1])[:10]}")

    print("  📡 VIX...")
    vix_series = vix_tarihsel_cek()
    print(f"  {'✅' if vix_series is not None else '⚠️ '} VIX "
          f"{len(vix_series) if vix_series is not None else 0} gün")

    hmm_carpan, hmm_rejim = 1.0, "BİLİNMİYOR"
    try:
        h = hmm_rejim_tespit(spy_df["Close"])
        hmm_carpan = h.get("esik_carpani", 1.0)
        hmm_rejim = h.get("rejim_adi", "BİLİNMİYOR")
    except Exception as e:
        sayaclar["hmm_fail"] += 1
        print(f"  ⚠️  HMM: {e}")
    print(f"  🧠 HMM: {hmm_rejim} (×{hmm_carpan})  ⚠️ look-ahead, E5 bekliyor")

    frames = {}
    for i, s in enumerate(WATCHLIST, 1):
        print(f"[{i:>2}/{len(WATCHLIST)}] {s}...", end=" ", flush=True)
        df = veri_cek(s)
        if df is None:
            print("⚠️  veri yok"); sayaclar["veri_yok"] += 1; continue
        frames[s] = df
        print(f"✅ {len(df)} bar")

    if not frames:
        raise SystemExit("Hiç sembol verisi yok.")

    cfg = SimulatorConfig(initial_cash=BASLANGIC_SERMAYE,
                          max_gross_exposure=1.00,
                          max_holding_sessions=MAX_POZISYON_GUN,
                          allow_short=False)

    print(f"\n  ⚙️  Simülasyon: ${cfg.initial_cash:,.0f} nakit · "
          f"{cfg.max_gross_exposure:.0%} brüt tavan · {cfg.side_cost_bps:.0f} bps/yön")
    sig_cache = {}
    res = simulate_portfolio(calendar, frames,
                             make_signal_fn(frames, vix_series, hmm_carpan,
                                            sayaclar, sig_cache),
                             cfg, symbols=list(frames.keys()))

    # Every curve is rebased at the SAME anchor close, so the comparison window
    # is identical by construction rather than by assumption.
    anchor = calendar[ISINMA_GUN] if len(calendar) > ISINMA_GUN else calendar[0]
    spy_post = spy_df["Close"].loc[spy_df.index >= anchor]
    spy_curve = spy_post / float(spy_post.iloc[0]) * BASLANGIC_SERMAYE
    bench = {
        "SPY buy & hold": equity_metrics(spy_curve),
        "Watchlist eşit ağırlık": equity_metrics(
            buy_hold_curve(frames, calendar, list(frames.keys()),
                           BASLANGIC_SERMAYE, anchor=anchor)),
    }
    print(f"  ⚓ Skorlama başlangıcı (anchor): {str(anchor)[:10]} "
          f"— ilk {ISINMA_GUN} ısınma seansı metriklerden hariç")

    st = rapor_yazdir(res, bench, sayaclar, {"hmm_lookahead": True}, anchor=anchor)

    # ── cost sensitivity: 0 bps is a diagnostic, never the headline ──────────
    print("  📐 MALİYET DUYARLILIĞI")
    grid = {}
    for bps in (0.0, 5.0, 10.0, 20.0):
        c = SimulatorConfig(initial_cash=BASLANGIC_SERMAYE, max_gross_exposure=1.00,
                            max_holding_sessions=MAX_POZISYON_GUN, allow_short=False,
                            half_spread_bps=bps / 2, slippage_bps=bps / 2)
        rr = simulate_portfolio(calendar, frames,
                                make_signal_fn(frames, vix_series, hmm_carpan,
                                               defaultdict(int), sig_cache),
                                c, symbols=list(frames.keys()))
        _e = rr.equity_curve
        m = equity_metrics(_e.loc[_e.index >= anchor, "equity"])
        grid[f"{bps:.0f}bps"] = m
        tag = "  (frictionless — teşhis, manşet değil)" if bps == 0 else ""
        print(f"     {bps:>4.0f} bps/yön → {m['total_return_pct']:+7.2f}%  "
              f"Sharpe {str(m['sharpe']):>6}{tag}")
    print()

    Path("true_backtest_rapor.json").write_text(json.dumps({
        "tarih": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "versiyon": "v6", "simulator": "portfolio-v1",
        "pencere": {"baslangic": str(calendar[0])[:10], "bitis": str(calendar[-1])[:10],
                    "seans": len(calendar)},
        "config": {"initial_cash": cfg.initial_cash,
                   "max_gross_exposure": cfg.max_gross_exposure,
                   "side_cost_bps": cfg.side_cost_bps,
                   "allow_short": cfg.allow_short,
                   "hmm_lookahead_present": True},
        "strateji": st, "benchmarks": bench, "maliyet_duyarliligi": grid,
        "sayaclar": dict(sayaclar),
        "islemler": res.closed_trades,
        "reddedilen": res.rejections[:200],
        "equity_curve": [
            {"date": str(i)[:10], "equity": round(r.equity, 2),
             "cash": round(r.cash, 2),
             "gross_pct": round(r.gross_exposure_pct, 4)}
            for i, r in res.equity_curve.iterrows()],
    }, ensure_ascii=False, indent=2, default=str))
    print("  💾 true_backtest_rapor.json kaydedildi.\n")
