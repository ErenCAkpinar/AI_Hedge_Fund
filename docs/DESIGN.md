# Design notes

Component-level notes written during the V5 → V6 development, corrected where the code has changed since. The [README](../README.md) has the overview, the current pipeline and the backtest results. Components added after V6 (`swan_agent.py`, `insider_agent.py`, `gamma_agent.py`, `watchdog_agent.py`, `fastapi_bridge.py`, `quant-war-room/`) are summarised in the README.

## Contents

- [V5 → V6: What Changed](#v5--v6-what-changed)
- [System Components](#system-components)
- [Signal Pipeline (DAG)](#signal-pipeline-dag)
- [The 8 Legends Voting System](#the-8-legends-voting-system)
- [V5 Core Algorithms](#v5-core-algorithms)
- [V6 Quant Arsenal](#v6-quant-arsenal)
- [Watchlist](#watchlist)
- [Edge Case Handling](#edge-case-handling)

---

## V5 → V6: What Changed

> This section documents what V5 looked like, what structural gaps remained, and exactly how V6 addresses each one. The backtest numbers and how to read them are in the [README](../README.md#backtest-v5).

### The V5 Baseline

In the V5 backtest ($1,500 starting capital, about two years of daily data, no transaction costs) the rules made +$1,476 with fixed position sizing (+98.4%), or +148% in the compounded replay, and all 17 symbols ended with positive P&L. The core V5 ideas were ATR-based dynamic stops, Kelly-style position sizing, pyramiding, and compounding.

But V5 had five structural gaps:

| Gap | Problem | Impact |
|---|---|---|
| **Static Kelly tiers** | Position size used fixed score brackets (35%/25%/15%/10%), not actual trade history | Ignored real edge per symbol |
| **No regime awareness** | Same entry thresholds in BULL and BEAR markets | Overtraded during downturns |
| **No trend quality filter** | ADX alone cannot distinguish a trending from a mean-reverting market | False breakouts on MSTR, QQQ |
| **No portfolio correlation check** | During crisis periods all positions fell together | No automatic safe-haven rotation |
| **No options market input** | Ignored what institutional money was pricing via IV | Missed forward-looking fear signals |

### V5 → V6: Side by Side

| Dimension | V5 | V6 |
|---|---|---|
| **Math library** | Inline calculations scattered across files | `quant_math.py` — dedicated stateless functions |
| **Position sizing** | Fixed score tiers: 35% / 25% / 15% / 10% | Rolling Kelly from last 50 actual trades: `f* = (b×p−q)/b × 0.5` |
| **Market regime** | None — identical thresholds in all conditions | 3-state HMM on SPY: BULL ×1.0 / SIDE ×1.2 / BEAR ×1.5 threshold scaling |
| **Trend quality** | ADX filter only (≥ 20 for LONG, ≥ 30 for SHORT) | ADX + **Hurst Exponent**: H < 0.45 → mean-reverting → LONG suppressed |
| **ATR multiplier** | Fixed 2.5× for all assets | Dynamic via Kurtosis: K ≤ 0 → 2.5× / K ∈ (0,3] → 3.5× / K > 3 → 5.0× |
| **Price signal input** | Raw close prices into SMA/EMA | Kalman-filtered close → fewer false crossovers |
| **Volatility forecast** | None | GARCH(1,1): `σ²ₜ = ω + α×ε²ₜ₋₁ + β×σ²ₜ₋₁` → position scaling |
| **Sentiment sources** | 5 (Finviz, StockTwits, Reddit, News, Fear&Greed) | 6 — adds **IV Radar** (Black-Scholes): IV/HV > 1.5 → crisis signal; StockTwits was later replaced by Finnhub |
| **Portfolio protection** | None | **Copula shield**: ρ̄ > 0.60 → auto-rotate into GLD / USO / FXY |
| **Portfolio weights** | Equal across watchlist | **Black-Litterman**: AI sentiment views → Bayesian optimal weights |
| **Second strategy** | None — single-stock only | **Pairs trading** via Ornstein-Uhlenbeck: 5 pairs, half-life filter [2–30 days] |
| **Risk reporting** | Sharpe + Calmar + Max Drawdown | Sortino, VaR/CVaR and walk-forward functions added to `quant_math.py` (not yet reported by `true_backtest.py`) |
| **JSON files per run** | 4 | 8 (+`pairs_rapor`, `hmm_rejim`, `copula_durum`, `bl_agirliklar`) |
| **New files** | — | `quant_math.py` + `pairs_agent.py` |

### What Did NOT Change

The core V5 architecture is untouched. The 5-layer DAG pipeline, 8 legends voting system, ATR-based SL/TP formulas, pyramiding engine, compounding logic, all 7 edge case handlers in `alpaca_trader.py`, and the GitHub Actions CI pipeline are **identical**. V6 adds depth on top of the V5 foundation; it does not rebuild it.

---

## System Components

### `scanner.py`
Lightweight price data fetcher using `yfinance`. Pulls latest close prices for all 17 watchlist assets. Acts as the system health check — if `yfinance` is unavailable, all subsequent steps are skipped.

### `quant_math.py` ← **V6 New Central Library**
Pure mathematics — no file I/O, no API calls. Stateless and importable by any module in the pipeline. All functions take data in and return results out; no side effects.

| Function | Origin | Role |
|---|---|---|
| `sortino_hesapla()` | Frank Sortino | Downside-only risk-adjusted return |
| `var_cvar_hesapla()` | Basel III standard | Value at Risk + Expected Shortfall |
| `walk_forward_test()` | — | Out-of-sample overfitting detector |
| `kurtosis_hesapla()` | Nassim Taleb | Fat tail detection → dynamic ATR multiplier |
| `hurst_hesapla()` | Harold Hurst (1951) | Trend vs mean-reversion classifier |
| `garch_volatilite()` | Engle (Nobel 2003) | Next-day volatility forecast → position scaling |
| `kalman_smooth()` | Rudolf Kálmán / NASA Apollo | Price noise filter |
| `kelly_dinamik_hesapla()` | John Kelly (1956) | Rolling optimal position size from trade history |
| `hmm_rejim_tespit()` | Baum-Welch | BULL / BEAR / SIDE regime detector |
| `iv_radar_hesapla()` | Black & Scholes (Nobel 1997) | Implied vs realized volatility radar |
| `copula_korelasyon_kalkan()` | — | Portfolio crisis correlation shield |
| `black_litterman_agirliklar()` | Goldman Sachs (1990) | AI-view Bayesian portfolio weights |
| `ou_spread_analizi()` | Ornstein-Uhlenbeck | Pairs spread Z-score + half-life |
| `sembol_quant_metrikleri()` | — | Combined: Kurtosis + Hurst + GARCH + Kalman |

### `mock_agent.py` ← **V6 Updated**
The **primary technical signal engine** for development and live trading without a paid AI API. Implements a deterministic, multi-indicator scoring system:

- **RSI_14** — Oversold/overbought detection (±2 pts at extremes)
- **MACD Histogram** — Momentum direction and acceleration (±2 pts)
- **SMA20/50 position** — Price relative to moving averages (±2 pts)
- **SMA20/50 cross** — Golden/Death cross detection (±1 pt)
- **SMA_200 trend filter** — Paul Tudor Jones rule: no LONG below 200 SMA (±1 pt) ← *V5*
- **ATR_14** — Exported to `rapor.json` for downstream SL/TP calculation ← *V5*

Score range: `-10` to `+10`. Thresholds at `±5` for directional signal, `±2` for bias.

**V6 additions:** Each symbol is enriched with `sembol_quant_metrikleri()` — Kurtosis, Hurst, GARCH forecast, and Kalman-smoothed close are all written to `rapor.json`. Additionally, once daily, `mock_agent` runs HMM regime detection on SPY → `hmm_rejim.json` and Black-Litterman optimization using current sentiment scores as views → `bl_agirliklar.json`.

### `analyst_agent.py`
CrewAI-powered analyst that wraps `mock_agent`'s data enrichment with a **Claude Sonnet 4** (`anthropic/claude-sonnet-4-20250514`) Lead Quant agent. Receives ATR, SMA_200 trend, and all technical indicators in the prompt. Enforces V5 rules: no LONG in BEARISH trend, wider stops for high-ATR assets.

> **Swap point:** Replace `_mock_karar_motoru()` call in `mock_agent.py` with `run_analyst_agent()` once `ANTHROPIC_API_KEY` is configured.

### `legends_agent.py` ← **V6 Updated**
Implements **8 legendary trader strategies** as independent Python functions. Each evaluates the same market data and casts a weighted vote. The consensus output includes ATR (V5 addition) for downstream pipeline use.

**V6 addition — ADX + Hurst LONG filter applied after consensus:**

```
ADX ≥ 20 AND Hurst ≥ 0.55  →  LONG confirmed (trend established + persistent)
Hurst < 0.45               →  Mean-reverting zone → HOLD override
Hurst ≥ 0.45 but ADX < 20  →  Conviction downgraded: YÜKSEK → ORTA
```

The filter result, Hurst value, and ADX value are written to `legends_rapor.json` for traceability.

### `sentiment_agent.py` ← **V6 Updated**
Aggregates sentiment from **6 sources** (was 5 in V5) with weighted scoring:

| Source | Weight | Notes |
|---|---|---|
| Finviz analyst ratings | 35% | Analyst recommendations and price targets |
| yfinance news | 15% | Corporate news flow |
| Reddit (r/stocks, r/wallstreetbets) | 15% | Retail crowd sentiment |
| Finnhub social sentiment | 15% | Replaced StockTwits |
| CNN Fear & Greed Index | 10% | Macro market environment |
| **IV Radar (Black-Scholes)** | **10%** | **V6: options-implied fear signal** |

IV Radar reads the nearest-expiry ATM options chain and solves Black-Scholes in reverse for implied volatility. IV/HV < 0.8 → calm market (+0.15 boost). IV/HV > 1.5 → crisis expected (−0.40 penalty, triggers GLD/USO rotation). No options trading required — the chain is used as an information source only.

News and Reddit text is scored by Gemini 2.5 Flash when `GEMINI_API_KEY` is set; otherwise the keyword engine is used.

### `pairs_agent.py` ← **V6 New Module**
Standalone pairs trading engine using Ornstein-Uhlenbeck spread analysis. Scans 5 pre-configured pairs and runs the portfolio Copula shield across all watchlist assets.

**Pairs scanned:**

| Pair | Rationale |
|---|---|
| NVDA / SOXX | Semiconductor leader vs sector ETF |
| GLD / USO | Gold vs Crude Oil — macro hedge pair |
| AVGO / NVDA | Two semiconductor giants |
| PLTR / META | Software / data companies |
| LMT / LLY | Defense vs Healthcare — macro hedge |

Entry rule: |Z-score| > 2.0 · Exit rule: |Z-score| < 0.5 · Half-life filter: [2, 30] days only (actionable timeframe). Writes `pairs_rapor.json` and `copula_durum.json`.

### `state_manager.py` ← **V6 Updated**
**The decision core.** Merges five signal layers into a single weighted score:

```
final_score = technical × 0.35 + legends × 0.25 + sentiment × 0.15 + insider × 0.15 + gamma × 0.10
```

Entry thresholds are scaled by the HMM regime, the insider and black-swan reports, and the safe-haven factor. When `ANTHROPIC_API_KEY` is set, Claude Sonnet 4 approves each decision before it is passed on for execution; without the key the decision is HOLD.

Conflict detection: if technical and legends signals are strongly opposing (threshold 0.15), result is forced to `HOLD`.

**V5:** Replaced `sl_tp_hesapla()` with `atr_sl_tp_hesapla()`:

```
High confidence:   SL = 1.8 × ATR   |   TP = 4.5 × ATR   →   R/R ≈ 1:2.5
Medium confidence: SL = 1.5 × ATR   |   TP = 3.5 × ATR   →   R/R ≈ 1:2.3
Fallback (no ATR): fixed percentage (V4 backward compatibility)
```

**V6 additions — three new layers before the final signal is issued:**

**1. Rolling Kelly position sizing** (replaces static score tiers):
```python
f* = (b × p − q) / b      # from last 50 trades per symbol in kelly_gecmis.json
f_kelly = f* × 0.5         # half-Kelly safety buffer (max 40% cap enforced)
# Falls back to score-based tiers when < 10 historical trades exist per symbol
```

**2. HMM adaptive threshold scaling:**
```python
ESIK_effective = ESIK_YUKSEK × hmm_carpan   # from hmm_rejim.json
# BULL → ×1.0  |  SIDE → ×1.2  |  BEAR → ×1.5
# In BEAR regime: thresholds rise → fewer trades → capital preserved
```

**3. Copula safe-haven routing:**
```python
# GLD, USO, FXY: ESIK ÷ guvenli_liman_agirlik  (from copula_durum.json)
# High portfolio correlation → safe havens get lower entry threshold
# → automatic rotation without any manual intervention
```

### `alpaca_trader.py`
Full trade execution engine with 7 edge cases handled. See [Edge Case Handling](#edge-case-handling). **V5 Pyramiding** replaces the old `DUPLICATE_SKIP` logic. **Unchanged in V6.**

### `scheduler.py`
Runs the complete pipeline on NYSE schedule:

| Time (TR) | NYSE (ET) | Action |
|---|---|---|
| 12:00 | — | Independent sentiment scan |
| 16:30 | 09:30 | Full pipeline (all steps) |
| 22:50 | 15:50 | Full pipeline (before the close) |

Pipeline order: `mock_agent → legends_agent → swan_agent → pairs_agent → insider_agent → gamma_agent → state_manager → alpaca_trader → sheets_pusher`. Market-closed guard: the signal steps always run; `alpaca_trader` is skipped when the market is closed.

### `sheets_pusher.py`
Writes `rapor.json` output to a Google Sheets dashboard with two tabs:
- **Rapor** — Full per-asset signal table with color-coded LONG/SHORT/HOLD cells
- **Özet** — Aggregate signal count and ratio summary

### `telegram_bot.py`
Sends formatted HTML messages to the operator's Telegram. Features exponential backoff retry (3 attempts), rate-limit handling (HTTP 429), and non-blocking failure — a Telegram outage never stops trade execution.

### `true_backtest.py`
Historical simulation that reuses the live pipeline's legends vote and `state_manager` scoring functions. It simulates the four V5 mechanisms (trailing stop, Kelly-style sizing, pyramiding, compounding) and reports Sharpe, Calmar and max drawdown. Sortino, VaR/CVaR and walk-forward functions exist in `quant_math.py` but are not reported by the backtest yet.

---

## Signal Pipeline (DAG)

V5 produced 4 JSON files per run. V6 produces **8**; later versions add `swan_rapor.json`, `insider_rapor.json` and `gamma_rapor.json`, which `state_manager.py` also reads:

```
rapor.json
  └─ ATR_14, SMA_200, ana_trend                        ← V5
  └─ kurtosis, hurst, garch_tahmini, kalman_son        ← V6

legends_rapor.json
  └─ konsensus, atr                                    ← V5
  └─ hurst, adx, hurst_filtre                         ← V6

sentiment_rapor.json
  └─ sentiment_skoru                                   ← V5
  └─ iv_sinyal, iv_hv_orani, iv_skoru                 ← V6

pairs_rapor.json                                       ← V6 NEW
  └─ ciftler[]: z_skoru, yarim_omur_gun, sinyal

hmm_rejim.json                                         ← V6 NEW
  └─ rejim_adi, esik_carpani, yorum

copula_durum.json                                      ← V6 NEW
  └─ ort_korelasyon, sinyal, guvenli_liman_agirlik

bl_agirliklar.json                                     ← V6 NEW
  └─ {sembol: optimal_weight}

final_karar.json
  └─ final_sinyal, stop_loss, take_profit              ← V5
  └─ atr_degeri, poz_buyukluk, sl_tipi                ← V5
  └─ v6_quant: {atr_carpan, garch_olcek,              ← V6
                hurst_long_izni, kurtosis_seviye,
                hmm_rejim, copula_gl_carpan,
                kelly_f, esik_yuksek_eff, esik_orta_eff}
```

---

## The 8 Legends Voting System

Each strategy is an independent Python function evaluating the same market data. Votes are weighted and aggregated into a consensus signal.

| # | Trader | Weight | Strategy |
|---|---|---|---|
| 1 | **Stanley Druckenmiller** | 20% | Asymmetric momentum — MACD zero-cross + RSI acceleration |
| 2 | **Paul Tudor Jones** | 18% | 200 SMA trend filter — no counter-trend trades |
| 3 | **Jesse Livermore** | 15% | 20-day pivot breakout with volume confirmation |
| 4 | **Richard Dennis** | 12% | Turtle breakout — 20-day high/low + 2×ATR stop |
| 5 | **Michael Burry** | 10% | Contrarian dip hunting — RSI<32 + Bollinger Band lower |
| 6 | **George Soros** | 10% | Reflexivity — 5-day consecutive move + volume expansion |
| 7 | **Larry Williams** | 8% | Williams %R — oversold/overbought exit + 3-day momentum |
| 8 | **Andrea Unger** | 7% | Volatility breakout — ATR expansion + Bollinger squeeze |

**Consensus thresholds:**
- Weighted score ≥ 55% → `YÜKSEK` (High conviction)
- Weighted score 40–55% → `ORTA` (Medium conviction)
- Weighted score < 40% → `HOLD`

**V6 post-consensus LONG filter:**
```
ADX ≥ 20 AND Hurst ≥ 0.55  →  LONG confirmed
Hurst < 0.45               →  HOLD override (mean-reverting, not a trend)
Hurst ≥ 0.45 but ADX < 20  →  Conviction downgraded HIGH → MEDIUM
```

**SHORT-side extra guardrails (unchanged from V5):**
- ADX ≥ 30 (trend must be established and strong)
- Price must be below SMA200 (Tudor Jones rule)
- Legends SHORT consensus ≥ 60%

---

## V5 Core Algorithms

### 1. ATR-Based Dynamic SL/TP

Replaces the V4 fixed-percentage stops with volatility-adjusted levels:

```python
# High confidence (guven_skoru >= 0.40)
stop_loss   = entry_price - (ATR_14 * 1.8)    # LONG
take_profit = entry_price + (ATR_14 * 4.5)    # LONG  →  R/R ≈ 1:2.5

# Medium confidence
stop_loss   = entry_price - (ATR_14 * 1.5)
take_profit = entry_price + (ATR_14 * 3.5)    #        →  R/R ≈ 1:2.3
```

**Backtest note:** V3 had a 62.8% stop-loss hit rate with fixed stops. In V5 the initial ATR stop becomes the trailing stop, so the backtest reports no fixed stop-loss exits (0.0%); losing trades now exit through the trailing stop instead. 151 of 207 exits (72.9%) were trailing-stop exits and the other 56 hit the 15-day time limit.

### 2. Kelly Criterion Position Sizing

V5 used static score-based tiers:

```python
score >= 0.60  →  35% of account equity
score >= 0.40  →  25% of account equity
score >= 0.30  →  15% of account equity
score  < 0.30  →  10% of account equity  (minimum floor)
```

`alpaca_trader.py` reads `poz_buyukluk` from `final_karar.json` and multiplies by live account equity from Alpaca's API. Average position size in V5 backtest: **30.3% of equity**.

> V6 replaces this with rolling Kelly from actual per-symbol trade history. See [V6 Quant Arsenal](#v6-quant-arsenal).

### 3. Pyramiding (Add to Winners)

Inspired by Richard Dennis's Turtle Trading system. Triggered when all three conditions are met simultaneously:

```python
condition_1 = unrealized_pl > 0                            # Position is profitable
condition_2 = current_price > avg_entry + (1.5 * ATR_14)  # Advanced 1.5 ATR from entry
condition_3 = pyramid_count_today < 2                      # Below daily maximum
```

Action: add 50% of original position size. **V5 backtest recorded 173 pyramid entries** across 207 total trades.

Enable/disable via `.env`:
```env
PYRAMIDING_AKTIF=true
PYRAMIDING_ATR_KATSAYI=1.5
```

### 4. ATR-Based Trailing Stop (Alpaca)

```python
trail_percent = (ATR_14 / current_price) * 100 * 2.5
# Hard clamp: minimum 2.0%, maximum 8.0%
```

Sent to Alpaca's `trailing_stop` order type. A low-volatility stock (LMT ATR≈$15 on a $450 stock = 3.3%) gets a tight trail; a high-volatility stock (ASTS ATR≈$10 on a $25 stock = 40% → clamped to 8%) gets room to breathe.

### 5. Compounding

All trades processed in chronological order. Each P&L updates the running equity before the next position size is calculated. In live trading, compounding is automatic because `alpaca_trader.py` fetches live `account.equity` on every execution cycle.

**V5 backtest:** the compounded replay grows $1,500 to $3,720.62 (+148.04%); the same trades with fixed sizing make +$1,476.08 (+98.4%). The replay does not cap total exposure across overlapping positions, so treat the compounded figure as an upper bound.

---

## V6 Quant Arsenal

The functions live in `quant_math.py`. Pure math — no side effects, no API calls. Each one closes a specific gap that existed in V5.

### Kurtosis → Dynamic ATR Multiplier

*Solves: V5's fixed 2.5× ATR multiplier treated low-vol LMT and high-tail ASTS identically.*

```
K ≤ 0      → ATR_TRAIL × 2.5  (normal distribution — V5 default)
K ∈ (0, 3] → ATR_TRAIL × 3.5  (fat tails — cautious)
K > 3      → ATR_TRAIL × 5.0  (crisis-level tails — maximum stop width)
```

### Hurst Exponent → Trend vs Noise Classifier

*Solves: ADX alone flags a volatile choppy market as tradeable when it is in fact mean-reverting.*

```
H > 0.55  → persistent trend — LONG + pyramiding permitted
H = 0.50  → random walk — normal trading
H < 0.45  → mean-reverting — suppress LONG signals
```

In the V5 backtest GLD (PF 4.5x) behaved like a persistent trend while MSTR (PF 1.0x) looked like noise; the Hurst filter is meant to capture that difference.

### GARCH(1,1) → Next-Day Volatility Forecast

*Solves: V5 position sizing ignored tomorrow's expected volatility entirely.*

```
σ²ₜ = ω + α × ε²ₜ₋₁ + β × σ²ₜ₋₁     (α + β < 1, stationarity condition)
```

High-vol forecast → smaller Kelly position. Prevents oversizing into volatile periods automatically.

### Kalman Filter → Price Noise Removal

*Solves: SMA/EMA inputs on raw close prices generated false crossover signals from tick noise.*

Kalman filter applied to daily closes. Reduces the false breakout signals that previously entered the pipeline as legitimate LONG trades.

### Rolling Kelly Criterion

*Solves: static score tiers (35%/25%/15%/10%) had no relationship to each symbol's actual historical edge.*

```python
f* = (b × p − q) / b      # Kelly formula
f_kelly = f* × 0.5         # half-Kelly safety buffer
# b = avg_win / avg_loss   from last 50 trades per symbol
# p = win rate             from last 50 trades per symbol
```

Updates every time a trade closes and is recorded to `kelly_gecmis.json`.

### HMM Regime Detection → Adaptive Thresholds

*Solves: V5 used identical entry thresholds regardless of whether the broad market was in a BULL or BEAR regime.*

3-state Hidden Markov Model trained on SPY daily returns:

```
BULL → thresholds × 1.0   (trade normally)
SIDE → thresholds × 1.2   (fewer trades, higher bar for entry)
BEAR → thresholds × 1.5   (capital preservation, GLD/USO priority)
```

Written to `hmm_rejim.json` daily by `mock_agent.py`. Read by `state_manager.py` at decision time.

### IV Radar (Black-Scholes) → Options Fear Signal

*Solves: V5 ignored the forward-looking information embedded in options pricing.*

Reads nearest-expiry ATM options chain via `yfinance`. Solves Black-Scholes in reverse for implied volatility σ_IV. Compares to realized HV_20 from historical closes.

```
IV/HV < 0.8   → market calm → +0.15 sentiment boost
IV/HV > 1.2   → big money buying protection → −0.20
IV/HV > 1.5   → crisis anticipated → −0.40, GLD/USO rotation triggered
```

Weighted at 10% in `sentiment_agent.py`. No options trading required.

### Ornstein-Uhlenbeck Pairs Trading

*Solves: V5 was a single-strategy system with no second uncorrelated return stream.*

```
dX = θ(μ − X)dt + σdW       Half-life = ln(2) / θ
Entry: |Z| > 2.0   |   Exit: |Z| < 0.5   |   Filter: half-life ∈ [2, 30] days
```

Slow-reverting pairs (> 30 days) tie up capital too long. Fast-reverting pairs (< 2 days) generate excessive transaction costs. Only pairs within the window are traded.

### Copula Portfolio Shield

*Solves: during high-correlation periods in V5, all positions fell together with no automatic response.*

Daily pairwise correlation across all holdings:

```
ρ̄ < 0.40 → diversified — normal trading
ρ̄ > 0.60 → increase GLD/USO/FXY weight via lower entry threshold
ρ̄ > 0.80 → crisis — scale down all positions, maximum safe-haven priority
```

Written to `copula_durum.json` by `pairs_agent.py`. Read by `state_manager.py`.

### Black-Litterman Portfolio Optimization

*Solves: V5 applied no mathematical framework to decide relative weight between symbols.*

```
E[R] = [(τΣ)⁻¹ + PᵀΩ⁻¹P]⁻¹ × [(τΣ)⁻¹Π + PᵀΩ⁻¹Q]

Π = CAPM market equilibrium returns (baseline)
Q = AI sentiment scores converted to ±15% return views per symbol
Σ = Ledoit-Wolf shrinkage covariance matrix
```

Gemini's conviction score per symbol becomes a mathematical portfolio view via Bayesian updating. GLD and LMT receive higher weight automatically. MSTR and QQQ receive near-zero weight automatically. Written to `bl_agirliklar.json` daily.

---

## Watchlist

17 assets across 5 categories:

| Category | Symbols |
|---|---|
| Semiconductors & AI | `NVDA` `AVGO` `SOXX` |
| Data, Software & Crypto | `PLTR` `MSTR` `IBIT` |
| Momentum Champions | `ASTS` `VST` |
| Defense, Healthcare & Auto | `LMT` `LLY` `TSLA` |
| Macro Hedge & Value | `GLD` `FXY` `META` `USO` `WMT` `QQQ` |

All 17 assets are in `LONG_ONLY_LIST` by default. SHORT positions require: price below SMA200 + ADX ≥ 30 + 60%+ legends SHORT consensus. In the V5 backtest, 0 SHORT trades were executed — all 207 trades were LONG.

> QQQ and MSTR stay on the watchlist; their low Hurst scores suppress LONG entries through the legends filter, and Black-Litterman gives them near-zero weight.

---

## Edge Case Handling

`alpaca_trader.py` handles 7 critical trading scenarios — **unchanged from V5:**

| # | Scenario | Behavior |
|---|---|---|
| 1 | **Market closed** | `api.get_clock()` check before any order; no orders during off-hours |
| 2 | **SHORT signal + existing LONG** | ⚠️ **Critical Rule 1:** Close LONG with market order. Do NOT open SHORT. |
| 3 | **LONG signal + existing SHORT** | Close SHORT position, then open LONG |
| 4 | **Same-direction signal (V5)** | Pyramiding: profitable + price > entry+1.5×ATR → add 50% of original size |
| 5 | **Open pending orders** | Cancel all open orders for symbol before submitting new order |
| 6 | **Insufficient buying power** | Scale position down to `buying_power × 0.95` |
| 7 | **PDT protection** | Block if `daytrade_count >= 3` and equity < $25,000 |

> **Critical Rule 1 — Design Intent:** A SHORT signal against an existing LONG closes the LONG but does NOT open a new SHORT. The signal is interpreted as "reduce exposure," not "flip direction." This prevents double-exposure and the system fighting itself. The trailing stop on the former LONG handles final cleanup.
