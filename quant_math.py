"""
quant_math.py [V6 — Quant Matematik Kütüphanesi]
==================================================
Algoritmik Hedge Fon — Saf Matematik Motoru

KURAL: Bu dosya sadece saf matematik fonksiyonları içerir.
       I/O yok, API çağrısı yok, JSON okuma/yazma yok.
       Her fonksiyon numpy array alır, sayı/dict döndürür.
       mock_agent.py, state_manager.py ve true_backtest.py tarafından import edilir.

SPRINT 1 — true_backtest.py metrikleri:
    sortino_hesapla()       → Downside risk-adjusted return
    var_cvar_hesapla()      → Value at Risk & Expected Shortfall
    walk_forward_test()     → Out-of-sample gerçeklik testi

SPRINT 2 — Canlı pipeline (mock_agent → state_manager):
    kurtosis_hesapla()      → Fat tail / kriz radarı → dinamik ATR çarpanı
    hurst_hesapla()         → Trend/testere ayrımı → LONG filtresi
    garch_volatilite()      → Yarınki volatilite tahmini → pozisyon ölçekleme

SPRINT 3 — Gelişmiş filtreleme:
    kalman_smooth()         → Gürültü temizleyici fiyat filtresi
"""

import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


# ═══════════════════════════════════════════════════════════════
# SPRINT 1 — BACKTEST METRİKLERİ
# ═══════════════════════════════════════════════════════════════

def sortino_hesapla(pnl_listesi: list, sermaye: float, risk_free_yillik: float = 0.05) -> float | None:
    """
    Sortino Ratio — Sharpe'ın akıllı kardeşi.

    Sharpe hem yukarı hem aşağı volatiliteyi cezalandırır.
    Sortino sadece AŞAĞI hareketi cezalandırır.
    V5 trailing stop mimarisi yukarı volatiliteyi yakalar → Sortino bunu adil ölçer.

    Beklenti: Sortino > 3.0 (Medallion Fund seviyesi)

    Args:
        pnl_listesi: Bileşik PnL listesi (dolar cinsinden her işlem)
        sermaye:     Başlangıç sermayesi
        risk_free_yillik: Yıllık risksiz faiz (varsayılan %5 ABD T-bill)

    Returns:
        Yıllıklaştırılmış Sortino Ratio
    """
    if not pnl_listesi or len(pnl_listesi) < 5:
        return None

    getiriler = np.array(pnl_listesi) / sermaye
    hedef = risk_free_yillik / 252  # Günlük hedef getiri

    # Sadece hedefin altındaki getiriler (downside)
    asagi_sapmalar = np.minimum(getiriler - hedef, 0)
    downside_std = np.sqrt(np.mean(asagi_sapmalar ** 2))

    if downside_std == 0:
        return None

    ort_getiri = np.mean(getiriler)
    sortino = (ort_getiri - hedef) / downside_std * np.sqrt(252)
    return round(float(sortino), 2)


def var_cvar_hesapla(pnl_listesi: list, sermaye: float, alfa: float = 0.95) -> dict:
    """
    VaR (Value at Risk) & CVaR (Expected Shortfall / Conditional VaR).

    VaR %95:  "%95 güvenle 1 işlemde max $X kaybederim."
    CVaR %95: "O kötü %5'lik senaryolarda ortalama kaybım $Y."
    CVaR/VaR < 1.5 → kuyruk riski yönetilebilir ✅

    Her hedge fon raporunda bu ikili vardır.

    Args:
        pnl_listesi: İşlem bazında PnL listesi (dolar)
        sermaye:     Başlangıç sermayesi (yüzde hesabı için)
        alfa:        Güven seviyesi (0.95 = %95)

    Returns:
        dict: var_dolar, cvar_dolar, var_pct, cvar_pct, oran
    """
    if not pnl_listesi or len(pnl_listesi) < 10:
        return {}

    pnl = np.array(pnl_listesi)

    # Historical VaR — parametrik değil, gerçek dağılımdan
    var_dolar = float(np.percentile(pnl, (1 - alfa) * 100))

    # CVaR — VaR'ı aşan kötü senaryoların ortalaması
    cvar_gunler = pnl[pnl <= var_dolar]
    cvar_dolar = float(np.mean(cvar_gunler)) if len(cvar_gunler) > 0 else var_dolar

    var_pct = round(abs(var_dolar) / sermaye * 100, 2)
    cvar_pct = round(abs(cvar_dolar) / sermaye * 100, 2)
    oran = round(abs(cvar_dolar) / abs(var_dolar), 2) if var_dolar != 0 else None

    return {
        "var_dolar"  : round(var_dolar, 2),
        "cvar_dolar" : round(cvar_dolar, 2),
        "var_pct"    : var_pct,
        "cvar_pct"   : cvar_pct,
        "oran"       : oran,          # < 1.5 → kuyruk riski yönetilebilir
        "alfa"       : alfa,
    }


def walk_forward_test(islemler: list, baslangic_sermaye: float, pencere_ay: int = 6) -> dict:
    """
    Walk-Forward Validation — Overfitting Katili.

    Sistemi görmediği veriye karşı test eder.
    OOS Sharpe / IS Sharpe > 0.6 → model gerçek
    OOS Sharpe / IS Sharpe < 0.3 → OVERFIT — sistemi düzelt

    Yaklaşım:
    - Tüm işlemleri tarihe göre 2'ye böler (train=%60, test=%40)
    - Her yarıda Sharpe, Win Rate ve PF hesaplar
    - Tutarlılık skoru üretir

    Args:
        islemler:          Tüm backtest işlemleri (giris_tarihi ile sıralı)
        baslangic_sermaye: Başlangıç sermayesi
        pencere_ay:        Kullanılmıyor (gelecekte rolling window için)

    Returns:
        dict: is_sharpe, oos_sharpe, tutarlilik, yorum
    """
    if not islemler or len(islemler) < 20:
        return {"yorum": "Yetersiz işlem sayısı (minimum 20 gerekli)"}

    sirali = sorted(islemler, key=lambda x: x.get("giris_tarihi", ""))
    kesim = int(len(sirali) * 0.60)

    bolumler = {
        "IS (Train %60)" : sirali[:kesim],
        "OOS (Test %40)" : sirali[kesim:],
    }

    sonuclar = {}
    for ad, grup in bolumler.items():
        if not grup:
            continue
        pnl_l = [x.get("pnl_dolar_bilesik", x.get("pnl_dolar", 0)) for x in grup]
        dogru  = sum(1 for x in grup if x.get("dogru_karar", False))
        win_rate = dogru / len(grup) * 100
        kaz = sum(p for p in pnl_l if p > 0)
        kay = sum(abs(p) for p in pnl_l if p < 0)
        pf  = round(kaz / max(kay, 1), 2)

        # Sharpe bu periyod için
        getiriler = np.array(pnl_l) / baslangic_sermaye
        ort = np.mean(getiriler)
        std = np.std(getiriler)
        sharpe = round((ort - 0.05/252) / std * np.sqrt(252), 2) if std > 0 else None

        sonuclar[ad] = {
            "islem_sayisi" : len(grup),
            "win_rate"     : round(win_rate, 1),
            "profit_factor": pf,
            "sharpe"       : sharpe,
            "toplam_pnl"   : round(sum(pnl_l), 2),
        }

    # Tutarlılık skoru
    is_sharpe  = sonuclar.get("IS (Train %60)", {}).get("sharpe") or 0
    oos_sharpe = sonuclar.get("OOS (Test %40)", {}).get("sharpe") or 0
    tutarlilik = round(oos_sharpe / is_sharpe, 2) if is_sharpe > 0 else None

    if tutarlilik is None:
        yorum = "⚠️  Hesaplanamadı"
    elif tutarlilik >= 0.8:
        yorum = "✅ MÜKEMMEL — OOS performansı IS ile neredeyse aynı. Overfit yok."
    elif tutarlilik >= 0.6:
        yorum = "✅ GERÇEK — Model out-of-sample'da tutarlı çalışıyor."
    elif tutarlilik >= 0.4:
        yorum = "⚠️  KABUL EDİLEBİLİR — Hafif bozulma var, paper trading ile doğrula."
    elif tutarlilik >= 0.2:
        yorum = "🔴 ZAYIF — Parametre seçimi in-sample'a optimize edilmiş olabilir."
    else:
        yorum = "❌ OVERFIT — OOS performansı çok düşük. Sistemi gözden geçir."

    return {
        "bolumler"    : sonuclar,
        "is_sharpe"   : is_sharpe,
        "oos_sharpe"  : oos_sharpe,
        "tutarlilik"  : tutarlilik,
        "yorum"       : yorum,
    }


# ═══════════════════════════════════════════════════════════════
# SPRINT 2 — CANLI PIPELINE (mock_agent → state_manager)
# ═══════════════════════════════════════════════════════════════

def kurtosis_hesapla(close_series: pd.Series, pencere: int = 60) -> dict:
    """
    Excess Kurtosis — Fat Tail / Kriz Radarı.

    Piyasadaki Siyah Kuğu riskini ölçer.
    Normal dağılım → K = 0
    K > 3 → Şişman kuyruk, kriz modu → ATR trailing'i genişlet

    ATR Multiplier Kararı:
        K ≤ 0          → ATR_TRAIL = 2.5  (normal V5)
        K ∈ (0, 1.5]   → ATR_TRAIL = 3.0  (dikkatli)
        K ∈ (1.5, 3]   → ATR_TRAIL = 4.0  (yüksek risk)
        K > 3           → ATR_TRAIL = 5.0  (kriz modu)

    Args:
        close_series: Günlük kapanış fiyatları (pd.Series)
        pencere:      Kaç günlük veri (varsayılan 60)

    Returns:
        dict: kurtosis, atr_carpan, risk_seviyesi
    """
    if close_series is None or len(close_series) < pencere:
        return {"kurtosis": 0.0, "atr_carpan": 2.5, "risk_seviyesi": "NORMAL"}

    son = close_series.iloc[-pencere:]
    gunluk_getiri = son.pct_change().dropna()

    if len(gunluk_getiri) < 10:
        return {"kurtosis": 0.0, "atr_carpan": 2.5, "risk_seviyesi": "NORMAL"}

    # Excess kurtosis (Fisher definition: normal = 0)
    n = len(gunluk_getiri)
    mu = gunluk_getiri.mean()
    sigma = gunluk_getiri.std()

    if sigma == 0:
        return {"kurtosis": 0.0, "atr_carpan": 2.5, "risk_seviyesi": "NORMAL"}

    standardize = ((gunluk_getiri - mu) / sigma) ** 4
    k_raw = standardize.mean()

    # Fisher excess kurtosis düzeltmesi
    excess_k = float(k_raw - 3.0)

    # ATR çarpanı kararı
    if excess_k <= 0:
        carpan, seviye = 2.5, "NORMAL"
    elif excess_k <= 1.5:
        carpan, seviye = 3.0, "DİKKATLİ"
    elif excess_k <= 3.0:
        carpan, seviye = 4.0, "YÜKSEK_RİSK"
    else:
        carpan, seviye = 5.0, "KRİZ_MODU"

    return {
        "kurtosis"    : round(excess_k, 3),
        "atr_carpan"  : carpan,
        "risk_seviyesi": seviye,
    }


def hurst_hesapla(close_series: pd.Series, min_pencere: int = 10, max_pencere: int = 50) -> dict:
    """
    Hurst Üssü — Trend mi, Testere mi?

    H > 0.55 → Trendin devam edeceği beklentisi (LONG için yeşil ışık)
    H = 0.5  → Random walk (yön tahmin edilemez)
    H < 0.45 → Mean-reversion (ortalamaya dönüş bekleniyor)

    R/S Analizi yöntemi kullanılır.

    Args:
        close_series: Günlük kapanış fiyatları
        min_pencere:  Minimum lag değeri
        max_pencere:  Maximum lag değeri

    Returns:
        dict: hurst, yorum, long_izni
    """
    if close_series is None or len(close_series) < max_pencere + 10:
        return {"hurst": 0.5, "yorum": "YETERSİZ_VERİ", "long_izni": True}

    close = close_series.dropna().values
    if len(close) < max_pencere + 10:
        return {"hurst": 0.5, "yorum": "YETERSİZ_VERİ", "long_izni": True}

    log_getiri = np.log(close[1:] / close[:-1])

    rs_listesi = []
    pencere_listesi = []

    for pencere in range(min_pencere, min(max_pencere, len(log_getiri) // 2)):
        alt_seriler = [log_getiri[i:i+pencere] for i in range(0, len(log_getiri) - pencere, pencere)]
        if len(alt_seriler) < 2:
            continue

        rs_degerleri = []
        for alt in alt_seriler:
            ort = np.mean(alt)
            sapma = np.cumsum(alt - ort)
            r = np.max(sapma) - np.min(sapma)
            s = np.std(alt, ddof=1)
            if s > 0:
                rs_degerleri.append(r / s)

        if rs_degerleri:
            rs_listesi.append(np.mean(rs_degerleri))
            pencere_listesi.append(pencere)

    if len(rs_listesi) < 3:
        return {"hurst": 0.5, "yorum": "HESAPLANAMADI", "long_izni": True}

    # Log-log regresyon ile H tahmini
    log_rs = np.log(rs_listesi)
    log_n  = np.log(pencere_listesi)
    H = float(np.polyfit(log_n, log_rs, 1)[0])
    H = max(0.0, min(1.0, H))  # [0, 1] aralığına sıkıştır

    # Yorum
    if H >= 0.65:
        yorum, long_izni = "GÜÇLÜ_TREND", True
    elif H >= 0.55:
        yorum, long_izni = "TREND_VAR", True
    elif H >= 0.45:
        yorum, long_izni = "RANDOM_WALK", True
    elif H >= 0.35:
        yorum, long_izni = "MEAN_REVERSION", False
    else:
        yorum, long_izni = "GÜÇLÜ_MEAN_REV", False

    return {
        "hurst"     : round(H, 3),
        "yorum"     : yorum,
        "long_izni" : long_izni,  # False → state_manager HOLD'a çekebilir
    }


def garch_volatilite(close_series: pd.Series, omega: float = 0.000002,
                     alpha: float = 0.08, beta: float = 0.90) -> dict:
    """
    GARCH(1,1) — Yarınki Volatilite Tahmini.

    σ²ₜ = ω + α × ε²ₜ₋₁ + β × σ²ₜ₋₁

    Dünün fırtınası yarınkini öngörür.
    Volatilite kümelenmesini yakalar.

    Yüksek GARCH tahmini → ATR'yi ölçekle → Pozisyonu küçült

    Args:
        close_series: Günlük kapanış fiyatları
        omega:        Uzun vadeli varyans sabiti
        alpha:        Şok etkisi katsayısı (0.05-0.10 tipik)
        beta:         Kalıcılık katsayısı (0.85-0.92 tipik)

    Returns:
        dict: sigma_yarin, sigma_ort, volatilite_orani, pozisyon_olcegi
    """
    if close_series is None or len(close_series) < 30:
        return {"sigma_yarin": None, "pozisyon_olcegi": 1.0}

    # Kontrol: alpha + beta < 1 (stationarity)
    if alpha + beta >= 1.0:
        alpha, beta = 0.08, 0.90

    close = close_series.dropna().values
    getiriler = np.diff(np.log(close))

    if len(getiriler) < 20:
        return {"sigma_yarin": None, "pozisyon_olcegi": 1.0}

    # GARCH(1,1) recursive hesaplama
    sigma2 = np.var(getiriler)  # Başlangıç varyansı
    for ret in getiriler:
        epsilon2 = ret ** 2
        sigma2 = omega + alpha * epsilon2 + beta * sigma2

    sigma_yarin = float(np.sqrt(sigma2))  # Günlük standart sapma
    sigma_ort   = float(np.std(getiriler[-60:]))  # Son 60 günlük ortalama

    # Volatilite oranı: yüksekse pozisyonu küçült
    oran = sigma_yarin / sigma_ort if sigma_ort > 0 else 1.0

    if oran <= 1.0:
        olcek, vol_seviye = 1.0, "NORMAL"
    elif oran <= 1.3:
        olcek, vol_seviye = 0.85, "YUKSELIYOR"
    elif oran <= 1.6:
        olcek, vol_seviye = 0.70, "YÜKSEK"
    else:
        olcek, vol_seviye = 0.50, "KRİZ"

    return {
        "sigma_yarin"   : round(sigma_yarin * 100, 4),  # Yüzde olarak
        "sigma_ort"     : round(sigma_ort * 100, 4),
        "volatilite_orani": round(oran, 3),
        "volatilite_seviyesi": vol_seviye,
        "pozisyon_olcegi": olcek,  # Kelly pozisyonunu bu çarpanla ölçekle
    }


# ═══════════════════════════════════════════════════════════════
# SPRINT 3 — GELİŞMİŞ FİLTRELEME
# ═══════════════════════════════════════════════════════════════

def kalman_smooth(close_series: pd.Series,
                  q_gurultu: float = 1e-5,
                  r_gurultu: float = 0.01) -> pd.Series:
    """
    Kalman Filtresi — Roket Bilimi Gürültü Temizleyici.

    NASA Apollo programında kullanılan algoritma.
    Sahte kırılımları ve bear trap'leri filtreler.
    SMA hesaplamalarına ham fiyat yerine filtered price ver → daha az sahte crossover.

    q / r oranı:
        Küçük q/r → Ham fiyata yakın (duyarlı)
        Büyük q/r → Pürüzsüz, trendlere yakın (gecikmeli ama temiz)

    Args:
        close_series: Günlük kapanış fiyatları
        q_gurultu:    Süreç gürültüsü (piyasa belirsizliği)
        r_gurultu:    Ölçüm gürültüsü (tick noise)

    Returns:
        pd.Series: Kalman filtered fiyat serisi (orijinalle aynı index)
    """
    if close_series is None or len(close_series) < 5:
        return close_series

    close = close_series.dropna().values
    n = len(close)

    # Kalman state
    x_est = close[0]      # İlk tahmin: ilk fiyat
    p_est = 1.0           # İlk hata kovaryansı

    filtered = np.zeros(n)
    filtered[0] = x_est

    for i in range(1, n):
        # Tahmin aşaması
        x_pred = x_est
        p_pred = p_est + q_gurultu

        # Kalman kazancı
        K = p_pred / (p_pred + r_gurultu)

        # Güncelleme aşaması
        x_est = x_pred + K * (close[i] - x_pred)
        p_est = (1 - K) * p_pred

        filtered[i] = x_est

    # Orijinal index'i koru
    result = close_series.copy()
    result.iloc[-n:] = filtered
    return result


# ═══════════════════════════════════════════════════════════════
# YARDIMCI: Tüm metrikleri tek seferde hesapla (mock_agent için)
# ═══════════════════════════════════════════════════════════════

def sembol_quant_metrikleri(close_series: pd.Series) -> dict:
    """
    Bir sembol için tüm Sprint 2/3 metriklerini tek çağrıda hesaplar.
    mock_agent.py'nin fetch_and_enrich() fonksiyonu bunu çağırır.

    Returns:
        dict: kurtosis_veri, hurst_veri, garch_veri, kalman_son_fiyat
    """
    kurtosis_veri = kurtosis_hesapla(close_series)
    hurst_veri    = hurst_hesapla(close_series)
    garch_veri    = garch_volatilite(close_series)

    # Kalman filtered son fiyat (raw close yerine kullanılabilir)
    kalman_seri   = kalman_smooth(close_series)
    kalman_son    = round(float(kalman_seri.iloc[-1]), 4) if kalman_seri is not None else None

    return {
        "kurtosis"          : kurtosis_veri,
        "hurst"             : hurst_veri,
        "garch"             : garch_veri,
        "kalman_son_fiyat"  : kalman_son,
    }


# ═══════════════════════════════════════════════════════════════
# SPRINT 4 — CANLI PİPELİNE GELİŞMİŞ MODÜLLER
# ═══════════════════════════════════════════════════════════════

def kelly_dinamik_hesapla(gecmis_islemler: list, kelly_katsayi: float = 0.5) -> dict:
    """
    Dinamik Kelly Kriteri — Son işlemlerden hesaplanan gerçek p ve b.

    Sabit skor eşiği yerine: her sembolün son N işlemindeki
    gerçek kazanma oranı (p) ve ortalama kazanç/kayıp oranı (b) kullanılır.

    f* = (b×p - q) / b
    Fractional Kelly = f* × kelly_katsayi  (güvenlik için 0.5 önerilen)

    Args:
        gecmis_islemler: Son N işlem listesi — her dict: {pnl_dolar, dogru_karar}
        kelly_katsayi:   Fractional Kelly çarpanı (0.5 = half-Kelly)

    Returns:
        dict: f_star, f_kelly, p, b, yorum
    """
    if not gecmis_islemler or len(gecmis_islemler) < 10:
        return {"f_kelly": 0.10, "p": None, "b": None, "yorum": "YETERSİZ_VERİ — varsayılan %10"}

    kazananlar = [x for x in gecmis_islemler if x.get("pnl_dolar", 0) > 0]
    kaybedenler = [x for x in gecmis_islemler if x.get("pnl_dolar", 0) <= 0]

    if not kazananlar or not kaybedenler:
        return {"f_kelly": 0.10, "p": None, "b": None, "yorum": "KAZANAN/KAYBEDEN YOK"}

    p = len(kazananlar) / len(gecmis_islemler)
    q = 1 - p

    avg_kazanc = sum(x["pnl_dolar"] for x in kazananlar) / len(kazananlar)
    avg_kayip  = abs(sum(x["pnl_dolar"] for x in kaybedenler) / len(kaybedenler))

    if avg_kayip == 0:
        return {"f_kelly": 0.15, "p": round(p, 3), "b": None, "yorum": "KAYIP=0 anomali"}

    b = avg_kazanc / avg_kayip  # R/R oranı

    # Kelly formülü
    f_star = (b * p - q) / b
    f_star = max(0.0, f_star)  # Negatif Kelly = işlem açma

    # Fractional Kelly (güvenlik tamponu)
    f_kelly = round(min(f_star * kelly_katsayi, 0.40), 4)  # Max %40

    if f_star <= 0:
        yorum = "❌ NEGATIF EDGE — Bu sembolde pozisyon açma"
    elif f_kelly < 0.05:
        yorum = "⚠️  Zayıf edge — mini pozisyon"
    elif f_kelly < 0.15:
        yorum = "✅ Normal Kelly pozisyonu"
    else:
        yorum = "💪 Güçlü edge — tam pozisyon"

    return {
        "f_star"  : round(f_star, 4),
        "f_kelly" : f_kelly,           # Kullanılacak pozisyon fraksiyonu
        "p"       : round(p, 3),
        "b"       : round(b, 3),
        "yorum"   : yorum,
        "n_islem" : len(gecmis_islemler),
    }


def hmm_rejim_tespit(index_serisi: pd.Series, n_rejim: int = 3) -> dict:
    """
    Gizli Markov Modeli — Piyasa Rejim Dedektörü.

    SPY/QQQ günlük getirilerini 3 gizli rejime ayırır:
        Rejim 0 → BULL  (düşük vol, pozitif getiri)
        Rejim 1 → SIDE  (orta vol, nötr getiri)
        Rejim 2 → BEAR  (yüksek vol, negatif getiri)

    state_manager.py: ESIK_YUKSEK / ESIK_ORTA rejime göre ayarlanır.

    Args:
        index_serisi: SPY veya QQQ günlük kapanış fiyatları
        n_rejim:      Rejim sayısı (varsayılan 3)

    Returns:
        dict: rejim_adi, rejim_no, esik_carpani, yorum
    """
    try:
        from hmmlearn import hmm as hmmlib
    except ImportError:
        return {"rejim_adi": "NÖTR", "rejim_no": 1, "esik_carpani": 1.0, "yorum": "hmmlearn yok"}

    if index_serisi is None or len(index_serisi) < 60:
        return {"rejim_adi": "NÖTR", "rejim_no": 1, "esik_carpani": 1.0, "yorum": "YETERSİZ_VERİ"}

    close  = index_serisi.dropna().values
    getiri = np.diff(np.log(close)).reshape(-1, 1)

    if len(getiri) < 50:
        return {"rejim_adi": "NÖTR", "rejim_no": 1, "esik_carpani": 1.0, "yorum": "YETERSİZ_VERİ"}

    try:
        model = hmmlib.GaussianHMM(
            n_components=n_rejim,
            covariance_type="full",
            n_iter=100,
            random_state=42
        )
        model.fit(getiri)
        gizli_durumlar = model.predict(getiri)

        # Son rejimi al
        son_rejim = int(gizli_durumlar[-1])

        # Her rejimin ortalama getirisi → BULL/SIDE/BEAR etiketle
        rejim_ortalamalar = {}
        for r in range(n_rejim):
            maske = gizli_durumlar == r
            if maske.sum() > 0:
                rejim_ortalamalar[r] = float(np.mean(getiri[maske]))

        # Ortalamalara göre sırala: en yüksek → BULL, en düşük → BEAR
        sirali = sorted(rejim_ortalamalar.items(), key=lambda x: x[1], reverse=True)
        bull_rejim = sirali[0][0]
        bear_rejim = sirali[-1][0]

        if son_rejim == bull_rejim:
            rejim_adi, esik_carpani = "BULL", 1.0
            yorum = "🟢 BULL — Normal ticaret, pyramiding açık"
        elif son_rejim == bear_rejim:
            rejim_adi, esik_carpani = "BEAR", 1.5
            yorum = "🔴 BEAR — LONG yasak, eşikler yükseltildi, sadece GLD/USO"
        else:
            rejim_adi, esik_carpani = "SIDE", 1.2
            yorum = "🟡 SIDEWAYS — Eşikleri %20 artır, az işlem"

        return {
            "rejim_adi"     : rejim_adi,
            "rejim_no"      : son_rejim,
            "esik_carpani"  : esik_carpani,  # state_manager ESIK × bu çarpan
            "yorum"         : yorum,
            "gizli_durum_sayisi": n_rejim,
        }

    except Exception as e:
        return {"rejim_adi": "NÖTR", "rejim_no": 1, "esik_carpani": 1.0, "yorum": f"HMM hata: {e}"}


def iv_radar_hesapla(symbol: str, close_serisi: pd.Series) -> dict:
    """
    Black-Scholes Zımni Volatilite Radarı.

    Opsiyon piyasasının beklentisini ölçer.
    IV/HV > 1.2 → Büyük para kötü bir şey bekliyor → DİKKAT
    IV/HV > 1.5 → Kriz beklentisi → GLD/USO moduna geç

    Yöntem:
        1. yfinance'tan yakın vadeli ATM opsiyonlarını çek
        2. scipy.optimize.brentq ile Black-Scholes'ü tersine çöz → IV
        3. HV (60 günlük realized vol) ile karşılaştır

    Args:
        symbol:       Hisse sembolü
        close_serisi: Günlük kapanış fiyatları (HV hesabı için)

    Returns:
        dict: iv, hv, iv_hv_orani, sinyal, sinyal_skoru
    """
    import math
    from scipy.optimize import brentq
    from scipy.stats import norm

    def bs_call(S, K, T, r, sigma):
        if sigma <= 0 or T <= 0:
            return max(S - K, 0)
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)

    def bul_iv(piyasa_fiyati, S, K, T, r=0.05):
        try:
            def hedef(sigma):
                return bs_call(S, K, T, r, sigma) - piyasa_fiyati
            return brentq(hedef, 1e-6, 10.0, xtol=1e-6)
        except Exception:
            return None

    # Historical Volatility (60 günlük)
    hv = None
    if close_serisi is not None and len(close_serisi) >= 60:
        son60 = close_serisi.dropna().iloc[-60:]
        log_ret = np.diff(np.log(son60.values))
        hv = float(np.std(log_ret) * np.sqrt(252))

    # Implied Volatility — yfinance options chain
    iv_ortalama = None
    try:
        import yfinance as yf_mod
        ticker = yf_mod.Ticker(symbol)
        tarihler = ticker.options
        if tarihler:
            # En yakın vade
            yakin_vade = tarihler[0]
            zincir = ticker.option_chain(yakin_vade)
            calls = zincir.calls
            puts  = zincir.puts
            son_fiyat = float(close_serisi.iloc[-1]) if close_serisi is not None and len(close_serisi) > 0 else None

            if son_fiyat and not calls.empty:
                # ATM call: grevlerden en yakın olanı
                calls = calls[calls["ask"] > 0.05].copy()
                if not calls.empty:
                    calls["atm_uzaklik"] = abs(calls["strike"] - son_fiyat)
                    atm_call = calls.nsmallest(3, "atm_uzaklik").iloc[0]

                    K = float(atm_call["strike"])
                    piyasa_fiyati = float(atm_call["ask"])

                    # Vade süresini yıl cinsinden hesapla
                    import datetime as dt_mod
                    vade_dt = dt_mod.datetime.strptime(yakin_vade, "%Y-%m-%d")
                    bugun = dt_mod.datetime.now()
                    T = max((vade_dt - bugun).days / 365.0, 1/365.0)

                    iv_val = bul_iv(piyasa_fiyati, son_fiyat, K, T)
                    if iv_val and 0.01 < iv_val < 5.0:
                        iv_ortalama = iv_val
    except Exception:
        iv_ortalama = None

    # IV/HV oranı ve sinyal
    if iv_ortalama and hv and hv > 0:
        oran = round(iv_ortalama / hv, 3)
        if oran < 0.8:
            sinyal, sinyal_skoru = "LONG_YESIL", 0.15
            yorum = "Piyasa sakin bekliyor — LONG için yeşil ışık"
        elif oran <= 1.2:
            sinyal, sinyal_skoru = "NORMAL", 0.0
            yorum = "Normal IV/HV — beklenti nötr"
        elif oran <= 1.5:
            sinyal, sinyal_skoru = "DIKKAT", -0.20
            yorum = "Büyük para volatilite satın alıyor — DİKKAT"
        else:
            sinyal, sinyal_skoru = "KRIZ", -0.40
            yorum = "Kriz beklentisi — sadece GLD, USO"
    else:
        oran = None
        sinyal, sinyal_skoru = "VERİ_YOK", 0.0
        yorum = "Opsiyon verisi alınamadı — nötr kabul"

    return {
        "iv"           : round(iv_ortalama * 100, 2) if iv_ortalama else None,
        "hv"           : round(hv * 100, 2) if hv else None,
        "iv_hv_orani"  : oran,
        "sinyal"       : sinyal,
        "sinyal_skoru" : sinyal_skoru,  # sentiment_agent ağırlıklı skora ekler
        "yorum"        : yorum,
    }


def copula_korelasyon_kalkan(sembol_listesi: list, close_dict: dict, pencere: int = 20) -> dict:
    """
    Copula Tabanlı Portföy Korelasyon Kalkanı.

    Kriz anında tüm hisseler birlikte düşer (tail dependence).
    Ortalama pairwise korelasyon bu durumu erken tespit eder.

    Korelasyon eşikleri:
        ρ̄ < 0.4  → Portföy çeşitlenmiş — normal işlem
        ρ̄ > 0.6  → GLD/USO ağırlığını artır — DİKKAT
        ρ̄ > 0.8  → Tüm pozisyonları küçült — KRİZ

    Args:
        sembol_listesi: Portföydeki semboller
        close_dict:     {sembol: pd.Series} kapanış fiyatları
        pencere:        Korelasyon hesap penceresi (gün)

    Returns:
        dict: ort_korelasyon, kriz_skoru, ort_korelasyon_sinyal, guvenli_limanlar
    """
    getiri_dict = {}
    for sem in sembol_listesi:
        seri = close_dict.get(sem)
        if seri is not None and len(seri) >= pencere + 1:
            log_ret = np.diff(np.log(seri.dropna().iloc[-(pencere+1):].values))
            if len(log_ret) >= pencere:
                getiri_dict[sem] = log_ret

    if len(getiri_dict) < 3:
        return {
            "ort_korelasyon"  : 0.0,
            "kriz_skoru"      : 0.0,
            "sinyal"          : "YETERSİZ_VERİ",
            "guvenli_liman_agirlik": 1.0,
        }

    # Pairwise Pearson korelasyon matrisi
    semboller   = list(getiri_dict.keys())
    n           = len(semboller)
    korelasyonlar = []

    for i in range(n):
        for j in range(i + 1, n):
            r1 = getiri_dict[semboller[i]]
            r2 = getiri_dict[semboller[j]]
            min_len = min(len(r1), len(r2))
            if min_len >= 5:
                rho = float(np.corrcoef(r1[:min_len], r2[:min_len])[0, 1])
                if not np.isnan(rho):
                    korelasyonlar.append(rho)

    if not korelasyonlar:
        return {"ort_korelasyon": 0.0, "kriz_skoru": 0.0, "sinyal": "HESAPLANAMADI", "guvenli_liman_agirlik": 1.0}

    ort_rho = float(np.mean(korelasyonlar))

    # Kriz skoru ve güvenli liman ağırlığı
    if ort_rho < 0.40:
        sinyal = "NORMAL"
        kriz_skoru = 0.0
        gl_agirlik = 1.0   # GLD/USO normal ağırlık
        yorum = "Portföy çeşitlenmiş — normal işlem"
    elif ort_rho < 0.60:
        sinyal = "DİKKAT"
        kriz_skoru = (ort_rho - 0.40) / 0.20
        gl_agirlik = 1.3
        yorum = "Korelasyon yüksek — GLD/USO ağırlığını artır"
    elif ort_rho < 0.80:
        sinyal = "KRİZ_BAŞLANGIÇ"
        kriz_skoru = 0.5 + (ort_rho - 0.60) / 0.20 * 0.3
        gl_agirlik = 1.6
        yorum = "Yüksek korelasyon — tüm pozisyonları küçült, GLD artır"
    else:
        sinyal = "KRİZ"
        kriz_skoru = 1.0
        gl_agirlik = 2.0
        yorum = "KRİZ! Ortalama korelasyon > 0.8 — maksimum savunma"

    return {
        "ort_korelasyon"         : round(ort_rho, 3),
        "kriz_skoru"             : round(kriz_skoru, 3),
        "sinyal"                 : sinyal,
        "guvenli_liman_agirlik"  : gl_agirlik,  # state_manager GLD/USO ESIK'ini bu oranla düşür
        "yorum"                  : yorum,
        "n_cift"                 : len(korelasyonlar),
    }


def black_litterman_agirliklar(
    sembol_listesi : list,
    getiri_dict    : dict,
    goruc_dict     : dict,   # {sembol: sentiment_skoru}  -1.0 → +1.0
    tau            : float = 0.05,
    delta          : float = 2.5,
) -> dict:
    """
    Black-Litterman Portföy Optimizasyonu.

    Piyasa denge getirilerini (CAPM) baseline alır,
    AI sentiment skorlarını Bayesian olarak karıştırır.

    E[R] = [(τΣ)⁻¹ + PᵀΩ⁻¹P]⁻¹ × [(τΣ)⁻¹Π + PᵀΩ⁻¹Q]

    Args:
        sembol_listesi: Portföy sembolleri
        getiri_dict:    {sembol: günlük getiri serisi (np.array)}
        goruc_dict:     AI sentiment skoru per sembol (-1 to +1)
        tau:            Belirsizlik skalası (tipik 0.05)
        delta:          Piyasa risk-aversion katsayısı

    Returns:
        dict: {sembol: ağırlık} — normalize edilmiş portföy ağırlıkları
    """
    try:
        from pypfopt import BlackLittermanModel, risk_models, expected_returns
        pass  # pandas already imported
    except Exception:
        try:
            import pandas as pd_mod
        except Exception:
            pd_mod = pd

    try:
        # Getiri matrisini DataFrame'e dönüştür
        min_len = min(len(v) for v in getiri_dict.values() if len(v) > 10)
        min_len = min(min_len, 252)  # En fazla 1 yıl

        ret_df = {}
        for s in sembol_listesi:
            if s in getiri_dict and len(getiri_dict[s]) >= min_len:
                ret_df[s] = getiri_dict[s][-min_len:]

        if len(ret_df) < 2:
            # Eşit ağırlık fallback
            n = len(sembol_listesi)
            return {s: round(1/n, 4) for s in sembol_listesi}

        ret_df = pd.DataFrame(ret_df)

        # Kovaryans matrisi (Ledoit-Wolf shrinkage)
        S = risk_models.CovarianceShrinkage(ret_df, returns_data=True).ledoit_wolf()

        # Piyasa denge getirileri (eşit ağırlık varsayımı)
        w_market = pd.Series({s: 1/len(ret_df.columns) for s in ret_df.columns})
        pi = delta * S.dot(w_market)

        # AI görüşlerini Q vektörüne dönüştür
        # Mutlak görüşler: "sembol X, Y% getiri sağlar"
        gorucler = {}
        for s in ret_df.columns:
            if s in goruc_dict:
                # Sentiment -1/+1 → tahmini yıllık fazla getiri ±%15
                gorucler[s] = goruc_dict[s] * 0.15

        if not gorucler:
            # Görüş yok → piyasa dengesini kullan (eşit ağırlık)
            n = len(sembol_listesi)
            return {s: round(1/n, 4) for s in sembol_listesi}

        bl = BlackLittermanModel(
            S,
            pi=pi,
            absolute_views=gorucler,
            tau=tau,
        )
        rets = bl.bl_returns()

        # Minimum varyans optimizasyonu ile ağırlıklar
        from pypfopt import EfficientFrontier
        ef = EfficientFrontier(rets, S)
        ef.add_constraint(lambda w: w >= 0.02)   # min %2
        ef.add_constraint(lambda w: w <= 0.40)   # max %40
        ef.max_sharpe(risk_free_rate=0.05)
        agirliklar = ef.clean_weights()

        return dict(agirliklar)

    except Exception as e:
        # Fallback: eşit ağırlık
        n = max(len(sembol_listesi), 1)
        return {s: round(1/n, 4) for s in sembol_listesi}


# ═══════════════════════════════════════════════════════════════
# SPRINT 5 — BLACK SWAN ENGINE
# ═══════════════════════════════════════════════════════════════

def monte_carlo_sim(
    getiriler          : pd.Series,
    n_sim              : int   = 10000,
    ufuk               : int   = 252,
    baslangic_sermaye  : float = 1500.0,
) -> dict:
    """
    GARCH volatilite clustering ile Monte Carlo simülasyonu.

    10.000 senaryo × 252 günlük yol üretir. Vectorized — hızlı.

    Args:
        getiriler:         Günlük log getiri serisi
        n_sim:             Simülasyon sayısı
        ufuk:              Kaç günlük ileriye bak (252 = 1 yıl)
        baslangic_sermaye: Portföy başlangıç değeri ($)

    Returns:
        dict: median_sonuc, var_95, cvar_95, iflas_olasiligi,
              hedef_olasiligi, en_kotu_10, en_iyi_10, n_sim, ufuk_gun
    """
    getiri_arr  = np.array(getiriler.dropna())
    if len(getiri_arr) < 30:
        return {
            "median_sonuc"   : baslangic_sermaye,
            "var_95"         : baslangic_sermaye * 0.85,
            "cvar_95"        : baslangic_sermaye * 0.75,
            "iflas_olasiligi": 0.10,
            "hedef_olasiligi": 0.40,
            "en_kotu_10"     : baslangic_sermaye * 0.80,
            "en_iyi_10"      : baslangic_sermaye * 1.20,
            "n_sim"          : n_sim,
            "ufuk_gun"       : ufuk,
        }

    mu          = float(np.mean(getiri_arr))
    sigma       = float(np.std(getiri_arr))

    # GARCH(1,1) ile yarınki sigma tahmini
    garch       = garch_volatilite(pd.Series(getiri_arr))
    sigma_yarin = garch.get("sigma_yarin")
    if sigma_yarin:
        sigma_yarin = sigma_yarin / 100.0  # % → oran
    else:
        sigma_yarin = sigma

    np.random.seed(42)
    rastgele = np.random.normal(mu, sigma_yarin, (n_sim, ufuk))

    # Volatilite clustering: her günün vol'u bir öncekinden etkilenir
    for t in range(1, ufuk):
        vol_t = 0.10 + 0.85 * np.abs(rastgele[:, t - 1])
        with np.errstate(invalid="ignore"):
            carpan = np.where(sigma_yarin > 0, vol_t / sigma_yarin, 1.0)
        rastgele[:, t] = rastgele[:, t] * np.clip(carpan, 0.5, 3.0)

    yollar       = baslangic_sermaye * np.exp(np.cumsum(rastgele, axis=1))
    son_degerler = yollar[:, -1]

    iflas_esigi  = baslangic_sermaye * 0.50   # %50 kayıp = iflas
    hedef_esigi  = baslangic_sermaye * 1.50   # %50 kazanç = hedef

    pct5         = float(np.percentile(son_degerler, 5))
    cvar_vals    = son_degerler[son_degerler <= pct5]

    return {
        "median_sonuc"    : round(float(np.median(son_degerler)), 2),
        "var_95"          : round(pct5, 2),
        "cvar_95"         : round(float(np.mean(cvar_vals)) if len(cvar_vals) else pct5, 2),
        "iflas_olasiligi" : round(float(np.mean(son_degerler < iflas_esigi)), 4),
        "hedef_olasiligi" : round(float(np.mean(son_degerler > hedef_esigi)), 4),
        "en_kotu_10"      : round(float(np.percentile(son_degerler, 10)), 2),
        "en_iyi_10"       : round(float(np.percentile(son_degerler, 90)), 2),
        "n_sim"           : n_sim,
        "ufuk_gun"        : ufuk,
    }


def kriz_stres_testi(portfoy_degeri: float) -> dict:
    """
    5 tarihi kriz şokunu portföye uygular.

    S&P 500 peak-to-trough gerçek düşüş verileri.

    Args:
        portfoy_degeri: Anlık portföy değeri ($)

    Returns:
        dict: krizler (detay), en_kotu, min_kalan, risk_seviyesi
    """
    KRIZLER = {
        "2008_finansal_kriz": {
            "dusus"    : -0.565,
            "sure_gun" : 365,
            "aciklama" : "Lehman Brothers iflası",
        },
        "2020_covid": {
            "dusus"    : -0.340,
            "sure_gun" : 33,
            "aciklama" : "COVID pandemisi",
        },
        "2022_fed_artirimi": {
            "dusus"    : -0.252,
            "sure_gun" : 282,
            "aciklama" : "FED 475bps faiz artışı",
        },
        "2001_dotcom": {
            "dusus"    : -0.490,
            "sure_gun" : 546,
            "aciklama" : "DotCom balonu",
        },
        "2018_q4_dusus": {
            "dusus"    : -0.196,
            "sure_gun" : 95,
            "aciklama" : "FED QT + Çin ticaret savaşı",
        },
    }

    sonuclar = {}
    for kriz_adi, kv in KRIZLER.items():
        kayip = portfoy_degeri * kv["dusus"]
        kalan = portfoy_degeri + kayip
        sonuclar[kriz_adi] = {
            "dusus_pct"    : round(kv["dusus"] * 100, 1),
            "kayip_dolar"  : round(kayip, 2),
            "kalan_dolar"  : round(kalan, 2),
            "sure_gun"     : kv["sure_gun"],
            "aciklama"     : kv["aciklama"],
            "hayatta_kaldi": kalan > 0,
        }

    en_kotu_adi  = min(sonuclar, key=lambda k: sonuclar[k]["kalan_dolar"])
    min_kalan    = sonuclar[en_kotu_adi]["kalan_dolar"]
    risk_seviyesi = "YUKSEK" if min_kalan < portfoy_degeri * 0.50 else "ORTA"

    return {
        "krizler"      : sonuclar,
        "en_kotu"      : en_kotu_adi,
        "min_kalan"    : round(min_kalan, 2),
        "risk_seviyesi": risk_seviyesi,
    }


def vix_stres_hesapla(vix_skoru: float, portfoy_degeri: float) -> dict:
    """
    VIX seviyesine göre beklenen maksimum kayıp ve risk kararı.

    VIX < 15   → DÜŞÜK  : normal işlem
    VIX 15-25  → ORTA   : dikkat
    VIX 25-40  → YÜKSEK : savunmaya geç
    VIX > 40   → PANİK  : Risk-Off

    Args:
        vix_skoru:      Güncel VIX değeri
        portfoy_degeri: Anlık portföy değeri ($)

    Returns:
        dict: vix, risk_adi, beklenen_dusus, beklenen_kayip,
              tavsiye, risk_off_aktif
    """
    if vix_skoru < 15:
        beklenen_dusus = -0.05
        risk_adi       = "DUSUK"
        tavsiye        = "Normal işlem"
    elif vix_skoru < 25:
        beklenen_dusus = -0.12
        risk_adi       = "ORTA"
        tavsiye        = "Pozisyon büyüklüklerini %20 küçült"
    elif vix_skoru < 40:
        beklenen_dusus = -0.25
        risk_adi       = "YUKSEK"
        tavsiye        = "GLD/USO ağırlığını artır, yeni LONG alma"
    else:
        beklenen_dusus = -0.45
        risk_adi       = "PANIK"
        tavsiye        = "Risk-Off: tüm pozisyonları kapat, sadece GLD/USO/FXY"

    return {
        "vix"            : vix_skoru,
        "risk_adi"       : risk_adi,
        "beklenen_dusus" : beklenen_dusus,
        "beklenen_kayip" : round(portfoy_degeri * beklenen_dusus, 2),
        "tavsiye"        : tavsiye,
        "risk_off_aktif" : vix_skoru >= 40,
    }


def ou_spread_analizi(fiyat1: pd.Series, fiyat2: pd.Series,
                      sembol1: str = "A", sembol2: str = "B") -> dict:
    """
    Ornstein-Uhlenbeck Spread Analizi — Pairs Trading Motoru.

    dX = θ(μ - X)dt + σdW

    Spread = log(P1) - log(P2)
    θ (mean-reversion hızı) ve yarı-ömür hesaplar.
    Entry/exit sinyalleri üretir.

    Args:
        fiyat1, fiyat2: İki hissenin kapanış fiyat serileri
        sembol1/2:      Sembol isimleri (görüntü için)

    Returns:
        dict: spread_son, mu, sigma, yarim_omur_gun, sinyal, z_skoru
    """
    if fiyat1 is None or fiyat2 is None:
        return {"sinyal": "VERİ_YOK"}
    if len(fiyat1) < 60 or len(fiyat2) < 60:
        return {"sinyal": "YETERSİZ_VERİ"}

    # Serileri hizala
    min_len = min(len(fiyat1), len(fiyat2))
    s1 = np.log(fiyat1.dropna().values[-min_len:])
    s2 = np.log(fiyat2.dropna().values[-min_len:])

    spread = s1 - s2

    # OU parametrelerini OLS ile tahmin et
    # dX_t ≈ a + b*X_{t-1} + ε  →  θ = -b, μ = a / θ
    X_lag = spread[:-1]
    dX    = np.diff(spread)

    try:
        # OLS: dX = a + b*X_lag
        A = np.vstack([np.ones_like(X_lag), X_lag]).T
        koef, _, _, _ = np.linalg.lstsq(A, dX, rcond=None)
        a_koef, b_koef = koef[0], koef[1]

        theta = -b_koef  # Mean-reversion hızı
        if theta <= 0:
            return {"sinyal": "TREND_YAPMIYOR", "parcalar": f"{sembol1}/{sembol2}"}

        mu = a_koef / theta  # Uzun vadeli denge
        yarim_omur = np.log(2) / theta  # Gün

        # Sigma (residual std)
        residuals = dX - (a_koef + b_koef * X_lag)
        sigma_ou  = float(np.std(residuals))

        # Son spread ve Z-skoru
        spread_son = float(spread[-1])
        z_skoru    = (spread_son - mu) / (sigma_ou / np.sqrt(2 * theta)) if theta > 0 and sigma_ou > 0 else 0

        # Sinyal: z > 2 → short spread, z < -2 → long spread
        if z_skoru > 2.0:
            sinyal = "SHORT_SPREAD"   # S1 pahalı, S2 ucuz
            aciklama = f"Short {sembol1} + Long {sembol2}"
        elif z_skoru < -2.0:
            sinyal = "LONG_SPREAD"    # S1 ucuz, S2 pahalı
            aciklama = f"Long {sembol1} + Short {sembol2}"
        elif abs(z_skoru) < 0.5:
            sinyal = "KAPAT"          # Spread normale döndü — pozisyonu kapat
            aciklama = "Spread μ'ya yakın — çık"
        else:
            sinyal = "BEKLE"
            aciklama = "Z-skoru henüz eşiği geçmedi"

        return {
            "cift"         : f"{sembol1}/{sembol2}",
            "spread_son"   : round(spread_son, 5),
            "mu"           : round(float(mu), 5),
            "sigma_ou"     : round(sigma_ou, 5),
            "theta"        : round(float(theta), 4),
            "yarim_omur_gun": round(float(yarim_omur), 1),
            "z_skoru"      : round(float(z_skoru), 3),
            "sinyal"       : sinyal,
            "aciklama"     : aciklama,
        }

    except Exception as e:
        return {"sinyal": "HESAP_HATASI", "hata": str(e)}