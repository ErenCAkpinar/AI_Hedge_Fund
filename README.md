# 🤖 Hybrid AI Multi-Agent Swing Trading System

> **A fully autonomous, multi-agent quantitative trading system** that combines 3 AI models, 8 legendary trader strategies, and a 5-layer signal pipeline to make institutional-grade swing trading decisions — running entirely on a MacBook Air M2.

<br>

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Alpaca](https://img.shields.io/badge/Broker-Alpaca-FECD45?style=flat-square)](https://alpaca.markets)
[![Strategy](https://img.shields.io/badge/Strategy-Swing%20Trading-00C851?style=flat-square)]()
[![Status](https://img.shields.io/badge/Status-Paper%20Trading-orange?style=flat-square)]()
[![CI](https://github.com/ErenCAkpinar/AI_Hedge_Fund/actions/workflows/ci.yml/badge.svg)](https://github.com/ErenCAkpinar/AI_Hedge_Fund/actions/workflows/ci.yml)[![License](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](LICENSE)

---

## Table of Contents

- [Overview](#overview)
- [Backtest Results — V5](#backtest-results--v5)
- [Architecture](#architecture)
- [System Components](#system-components)
- [Signal Pipeline (DAG)](#signal-pipeline-dag)
- [The 8 Legends Voting System](#the-8-legends-voting-system)
- [V5 Core Algorithms](#v5-core-algorithms)
- [Watchlist](#watchlist)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the System](#running-the-system)
- [Project Structure](#project-structure)
- [Edge Case Handling](#edge-case-handling)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [Disclaimer](#disclaimer)

---

## Overview

This project is a **Hybrid AI Swing Trading System** designed around a Directed Acyclic Graph (DAG) architecture. It uses three AI models at different stages of the pipeline to produce final trade decisions:

| Layer | Model | Role |
|---|---|---|
| Sentiment | **Gemini 2.0 Flash** | Parses news, Reddit, StockTwits, Finviz + Fear & Greed Index |
| Technical | **GPT-4o-mini** (via CrewAI) | RSI, MACD, SMA20/50/200, ATR signal generation |
| Final Decision | **Claude Opus** | Autonomous veto and execution approval |

**Key design principles:**
- **Sniper, not sprayer** — 17-asset watchlist, waits for high-conviction signals only
- **DAG, not loop** — zero risk of circular execution; each module is stateless and idempotent
- **Mock-first** — entire pipeline runs without any API keys for development and testing
- **Conflict = HOLD** — if the 3 signal layers disagree, no trade is opened

---

## Backtest Results — V5

> **2-year backtest** on 17 assets. Starting capital: **$1,500**. Period: 2024–2026.

### Summary

| Version | Trades | Win Rate | Net P&L | Profit Factor | SL Rate | Key Change |
|---|---|---|---|---|---|---|
| V1 (buggy) | 768 | 32.6% | +$31,621 | 1.22x | 66.1% | Baseline — 6 critical bugs |
| V3 (RSI fixed) | 368 | 36.4% | +$19,999 | 1.33x | 62.8% | SHORT accuracy 23.5% |
| **V5 (current)** | **207** | **40.6%** | **+$1,476** | **1.79x** | **0.0%** | Trailing + Kelly + Pyramid + Compound |

> **Why does V5 have lower absolute P&L than V1/V3?**  
> V1 and V3 had 66%+ stop-loss rates — meaning they were profitable on paper *despite* being stopped out constantly, because of overfitting to the test window. V5 introduces trailing stops (replacing fixed TP) which eliminated stop-outs entirely (0.0% SL rate) and uses a 2-year clean backtest window without lookahead bias corrections present in V1/V3.

### V5 Compounding Curve

```
Starting Capital  : $1,500.00
Final Capital     : $3,720.62
Net Return        : +$2,220.62  (+148.04%)
Annualized        : ≈ +74% / year (2-year period)
```

### V5 Key Metrics

```
Total Trades      : 207      Max Drawdown     : -$253.92
Win Rate          : 40.6%    Avg Position     : 30.3% of equity
Profit Factor     : 1.79x    Pyramid Entries  : 173
Stop-Loss Hits    : 0        Trail Exits      : 151 / 207 (72.9%)
```

> **The core insight:** Win rate is only 40.6% — the system is wrong on 6 out of 10 trades. Yet **every single one of the 17 symbols is profitable** and the overall return is +148% over 2 years. This is the asymmetric R/R system working as designed: losers are cut by the trailing stop before they compound; winners are held until the trend reverses. Profit Factor 1.79x means winning trades are on average 79% larger than losing trades.

### Terminal Output

![V5 Backtest Terminal Output](docs/backtest_v5_output.png)

### Per-Symbol Performance

| Symbol | Trades | Win Rate | P&L | PF | Notes |
|---|---|---|---|---|---|
| ASTS | 20 | 30% | +$257 | 1.6x | Highest absolute P&L despite lowest win rate |
| PLTR | 19 | 47% | +$224 | 2.1x | Most consistent momentum |
| GLD | 18 | 44% | +$167 | **4.5x** | Best risk/reward ratio in portfolio |
| WMT | 18 | 39% | +$160 | 3.3x | Reliable low-vol compounder |
| VST | 9 | 44% | +$106 | 2.0x | Fewest trades, strong efficiency |
| AVGO | 13 | 46% | +$104 | 1.9x | Consistent semi leader |
| LMT | 7 | **57%** | +$83 | **8.0x** | Best win rate & highest PF — defense plays well |
| NVDA | 11 | 55% | +$83 | 2.3x | Only ✅ accuracy with meaningful P&L |
| USO | 5 | **60%** | +$76 | 5.0x | Fewest trades but highest win rate |
| TSLA | 15 | 33% | +$59 | 1.3x | Volatile but trailing stop contains damage |
| LLY | 12 | 33% | +$55 | 1.6x | Pharmaceutical sector working |
| FXY | 8 | 38% | +$25 | 2.1x | Macro hedge contributing |
| SOXX | 10 | 40% | +$23 | 1.4x | ETF smoothes sector exposure |
| META | 13 | 38% | +$23 | 1.2x | Marginal — watch for removal |
| IBIT | 6 | 33% | +$20 | 1.6x | Crypto proxy working |
| QQQ | 12 | 42% | +$7 | 1.1x | Near-zero edge — consider removing |
| MSTR | 11 | 27% | +$4 | 1.0x | Near-breakeven — review |

> **Portfolio signals:** QQQ (1.1x PF) and MSTR (1.0x PF, 27% win rate) are the two weakest assets and candidates for watchlist removal in V6. GLD (4.5x PF) and LMT (8.0x PF) are the highest quality signals and candidates for increased allocation.

### Filter Impact

The signal pipeline rejected **5,281 signals** that passed the raw indicator threshold:

| Filter | Blocked Signals | Purpose |
|---|---|---|
| Score threshold | 2,936 | Signal not strong enough |
| ADX (LONG < 20) | 829 | Trend not established |
| ADX (SHORT < 30) | 776 | SHORT requires stronger trend |
| Agent conflict | 515 | Technical ↔ Legends disagreement |
| LONG_ONLY list | 225 | Asset blocked from SHORT |

This 5,281-signal filter is the system working as designed: **quality over quantity.**

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                   SCHEDULER (16:30 TR / 23:00 TR)                   │
└────────────────────────────┬────────────────────────────────────────┘
                             │  triggers
         ┌───────────────────▼────────────────────┐
         │             DAG PIPELINE               │
         │                                        │
         │  ┌──────────────────────────────────┐  │
         │  │  1. mock_agent / analyst_agent   │  │  RSI, MACD, SMA20/50/200
         │  │     Technical Signal Engine      │  │  ATR_14 → rapor.json
         │  └──────────────┬───────────────────┘  │
         │                 │ ATR_14, SMA_200      │
         │  ┌──────────────▼───────────────────┐  │
         │  │  2. legends_agent.py             │  │  8 legendary strategies
         │  │     Weighted Voting System       │  │  ATR → legends_rapor.json
         │  └──────────────┬───────────────────┘  │
         │                 │ consensus + ATR      │
         │  ┌──────────────▼───────────────────┐  │
         │  │  3. sentiment_agent.py           │  │  Gemini Flash AI
         │  │     Multi-Source Sentiment       │  │  → sentiment_rapor.json
         │  └──────────────┬───────────────────┘  │
         │                 │ sentiment_skoru      │
         │  ┌──────────────▼───────────────────┐  │
         │  │  4. state_manager.py             │  │  40% Technical
         │  │     Final Decision Engine        │  │  35% Legends
         │  │     ATR x 1.8/4.5 SL/TP          │  │  25% Sentiment
         │  └──────────────┬───────────────────┘  │
         │                 │ final_karar.json     │
         │  ┌──────────────▼───────────────────┐  │
         │  │  5. alpaca_trader.py             │  │  State Awareness rules
         │  │     Trade Execution + Pyramiding │  │  ATR Trailing Stop
         │  └──────────────┬───────────────────┘  │
         │                 │                      │
         │  ┌──────────────▼───────────────────┐  │
         │  │  sheets_pusher + telegram_bot    │  │  Google Sheets Dashboard
         │  │     Reporting & Notifications    │  │  Operator Telegram Alerts
         │  └──────────────────────────────────┘  │
         └────────────────────────────────────────┘
```

---

## System Components

### `scanner.py`
Lightweight price data fetcher using `yfinance`. Pulls latest close prices for all 17 watchlist assets. Acts as the system health check — if `yfinance` is unavailable, all subsequent steps are skipped.

### `mock_agent.py`
The **primary technical signal engine** for development and live trading without a paid AI API. Implements a deterministic, multi-indicator scoring system:

- **RSI_14** — Oversold/overbought detection (±2 pts at extremes)
- **MACD Histogram** — Momentum direction and acceleration (±2 pts)
- **SMA20/50 position** — Price relative to moving averages (±2 pts)
- **SMA20/50 cross** — Golden/Death cross detection (±1 pt)
- **SMA_200 trend filter** — Paul Tudor Jones rule: no LONG below 200 SMA (±1 pt) ← *V5*
- **ATR_14** — Exported to `rapor.json` for downstream SL/TP calculation ← *V5*

Score range: `-10` to `+10`. Thresholds at `±5` for directional signal, `±2` for bias.

### `analyst_agent.py`
CrewAI-powered analyst that wraps `mock_agent`'s data enrichment with a **GPT-4o-mini** Lead Quant agent. Receives ATR, SMA_200 trend, and all technical indicators in the prompt. Enforces V5 rules: no LONG in BEARISH trend, wider stops for high-ATR assets.

> **Swap point:** Replace `_mock_karar_motoru()` call in `mock_agent.py` with `run_analyst_agent()` once `OPENAI_API_KEY` is configured.

### `legends_agent.py`
Implements **8 legendary trader strategies** as independent Python functions. Each evaluates the same market data and casts a weighted vote. The consensus output includes ATR (V5 addition) for downstream pipeline use.

### `sentiment_agent.py`
Aggregates sentiment from 5 sources with weighted scoring:

| Source | Weight | Notes |
|---|---|---|
| Finviz Analyst Rating | 35% | Most reliable institutional signal |
| StockTwits Bull/Bear | 20% | Real-time trader crowd sentiment |
| Reddit (r/stocks, r/wsb) | 15% | Retail crowd psychology |
| yfinance News | 15% | Corporate news flow |
| CNN Fear & Greed Index | 15% | Macro market environment |

Keyword engine is active by default. Activate Gemini Flash by swapping `keyword_sentiment_hesapla()` with `gemini_sentiment_hesapla()` once `GEMINI_API_KEY` is set.

### `state_manager.py`
**The decision core.** Merges 3 signal layers into a single weighted score:

```
final_score = (technical_score × 0.40) + (legends_score × 0.35) + (sentiment_score × 0.25)
```

Conflict detection: if technical and legends signals are strongly opposing (threshold 0.15), result is forced to `HOLD`.

**V5:** Replaced `sl_tp_hesapla()` with `atr_sl_tp_hesapla()`:

```
High confidence:   SL = 1.8 × ATR   |   TP = 4.5 × ATR   →   R/R ≈ 1:2.5
Medium confidence: SL = 1.5 × ATR   |   TP = 3.5 × ATR   →   R/R ≈ 1:2.3
Fallback (no ATR): fixed percentage (V4 backward compatibility)
```

### `alpaca_trader.py`
Full trade execution engine with 7 edge cases handled. See [Edge Case Handling](#edge-case-handling). **V5 Pyramiding** replaces the old `DUPLICATE_SKIP` logic.

### `scheduler.py`
Runs the complete 5-step pipeline on NYSE schedule:

| Time (TR) | NYSE (ET) | Action |
|---|---|---|
| 12:00 | — | Independent sentiment scan |
| 16:30 | 09:30 | Full pipeline (all 5 steps) |
| 23:00 | 16:00 | Full pipeline (pre-close) |

Market-closed guard: steps 1–3 and 5 always run; step 4 (`alpaca_trader`) is skipped when market is closed.

### `sheets_pusher.py`
Writes `rapor.json` output to a Google Sheets dashboard with two tabs:
- **Rapor** — Full per-asset signal table with color-coded LONG/SHORT/HOLD cells
- **Özet** — Aggregate signal count and ratio summary

### `telegram_bot.py`
Sends formatted HTML messages to the operator's Telegram. Features exponential backoff retry (3 attempts), rate-limit handling (HTTP 429), and non-blocking failure — a Telegram outage never stops trade execution.

### `true_backtest.py` (V5)
Full historical simulation using the live pipeline's actual logic (`legends_agent`, `state_manager`). All 4 V5 enhancements are simulated:

1. **Trailing Stop** — replaces fixed TP; `ATR_TRAIL_KATSAYI = 2.5`
2. **Kelly Position Sizing** — dynamic allocation based on conviction score
3. **Pyramiding** — adds to winning position every `1.5 × ATR`, max 2 additions at 50% of original size
4. **Compounding** — profits rolled into equity; next trade position size calculated from updated balance

---

## Signal Pipeline (DAG)

The system produces 4 JSON files per run, each consumed by the next stage:

```
rapor.json
  └─ ATR_14         ← V5: feeds state_manager's dynamic SL/TP
  └─ SMA_200        ← V5: feeds SMA200 trend filter
  └─ ana_trend      ← BULLISH / BEARISH / NÖTR

legends_rapor.json
  └─ konsensus      ← feeds state_manager weighted scoring
  └─ atr            ← V5: fallback ATR if rapor.json ATR is null

sentiment_rapor.json
  └─ sentiment_skoru ← feeds state_manager weighted scoring

final_karar.json
  └─ final_sinyal   ← LONG / SHORT / HOLD
  └─ stop_loss      ← ATR-based, V5
  └─ take_profit    ← ATR-based, V5
  └─ atr_degeri     ← passed through to alpaca_trader
  └─ poz_buyukluk   ← Kelly fraction for position sizing
  └─ sl_tipi        ← "ATR×1.8" or "SABİT %3.0 (fallback)"
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

**SHORT-side extra guardrails:**
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

**Backtest validation:** V3 had a 62.8% stop-loss hit rate with fixed stops. V5 ATR-based trailing eliminated stop-outs entirely (0.0% SL rate). 72.9% of exits are now trailing stop exits — meaning the system holds winners until the trend breaks.

### 2. Kelly Criterion Position Sizing

```python
score >= 0.60  →  35% of account equity
score >= 0.40  →  25% of account equity
score >= 0.30  →  15% of account equity
score  < 0.30  →  10% of account equity  (minimum floor)
```

`alpaca_trader.py` reads `poz_buyukluk` from `final_karar.json` and multiplies by live account equity from Alpaca's API. Average position size in V5 backtest: **30.3% of equity**.

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

**V5 backtest result:** $1,500 → $3,720.62 (+148.04%) over 2 years, entirely from compounding on a 40.6% win-rate system.

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

> **V6 watchlist candidates for removal:** `QQQ` (PF 1.1x) and `MSTR` (PF 1.0x, 27% win rate). Candidates for increased weight: `GLD` (PF 4.5x) and `LMT` (PF 8.0x).

---

## Installation

### Prerequisites
- Python 3.11+
- pip

### Clone and Setup

```bash
git clone https://github.com/ErenCAkpinar/AI_Hedge_Fund.git
cd AI_Hedge_Fund

# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate      # macOS/Linux
# venv\Scripts\activate       # Windows

# Install dependencies
pip install -r requirements.txt
```

### Core Dependencies

```
yfinance>=0.2.36        pandas>=2.0.0           numpy>=1.24.0
ta>=0.11.0              requests>=2.31.0         beautifulsoup4>=4.12.0
python-dotenv>=1.0.0    schedule>=1.2.0          gspread>=6.0.0
google-auth>=2.28.0     alpaca-trade-api>=3.3.0  crewai>=0.28.0
google-generativeai>=0.5.0    anthropic>=0.25.0  openai>=1.30.0
```

---

## Configuration

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

```env
# ── Alpaca Broker ──────────────────────────────────────────────────────
ALPACA_API_KEY=PKXXXXXXXXXXXXXXXX
ALPACA_SECRET_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ALPACA_BASE_URL=https://paper-api.alpaca.markets    # Safe default
# ALPACA_BASE_URL=https://api.alpaca.markets        # Live — use only after validation

# ── Risk Management ────────────────────────────────────────────────────
TRAILING_STOP_PCT=3.0          # ATR-unavailable fallback trailing %
MAX_POZISYON_PCT=0.15          # Max position as fraction of equity

# ── V5 Pyramiding ──────────────────────────────────────────────────────
PYRAMIDING_AKTIF=true
PYRAMIDING_ATR_KATSAYI=1.5

# ── AI APIs (all optional — system runs without them) ──────────────────
GEMINI_API_KEY=AIzaSy...
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# ── Notifications ──────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=7xxx:AAF_xxx
TELEGRAM_CHAT_ID=123456789

# ── Google Sheets Dashboard ────────────────────────────────────────────
SHEET_ID=your_google_sheet_id_here
GCP_KEY_PATH=gcp_key.json
```

> ⚠️ **Security:** `gcp_key.json` and `.env` are listed in `.gitignore`. Never commit them. If accidentally committed, rotate credentials immediately.

---

## Running the System

### Development Mode (No API Keys Required)

```bash
# Step 1: Technical signals (produces rapor.json)
python mock_agent.py

# Step 2: Legends voting (produces legends_rapor.json)
python legends_agent.py

# Step 3: Sentiment analysis (produces sentiment_rapor.json)
python sentiment_agent.py

# Step 4: Final decision (produces final_karar.json)
python state_manager.py

# Step 5: Dashboard (requires GCP service account key)
python sheets_pusher.py
```

### Connection Tests

```bash
python telegram_bot.py --test    # Verify Telegram connection
python alpaca_trader.py --test   # Verify Alpaca + market status
python telegram_bot.py "System startup test"  # Custom message
```

### Full Automated Pipeline

```bash
# Foreground — for initial testing
python scheduler.py

# Background — for production
nohup python scheduler.py > scheduler_out.log 2>&1 &
echo $! > scheduler.pid

# Monitor
tail -f scheduler.log
tail -f alpaca_trader.log

# Stop
kill $(cat scheduler.pid)
```

### Backtest

```bash
python true_backtest.py
# Output: true_backtest_rapor.json + performance table in terminal
```

---

## Project Structure

```
AI_Hedge_Fund/
│
├── .github/
│   ├── workflows/
│   │   └── ci.yml               # Syntax check + secret scan + JSON contract validation
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug_report.md
│   │   └── feature_request.md
│   └── PULL_REQUEST_TEMPLATE.md
│
├── scanner.py               # Price data fetcher (yfinance health check)
├── mock_agent.py            # Technical signal engine — API-free [V5]
├── analyst_agent.py         # CrewAI + GPT-4o-mini analyst agent [V5]
├── legends_agent.py         # 8 legendary trader strategy voting [V5]
├── sentiment_agent.py       # Multi-source sentiment + Gemini AI
├── state_manager.py         # Final decision + ATR SL/TP engine [V5]
├── alpaca_trader.py         # Trade execution + Pyramiding [V5]
├── scheduler.py             # Automated pipeline — 4-step DAG [V5]
├── sheets_pusher.py         # Google Sheets dashboard writer
├── telegram_bot.py          # Operator notification system
├── true_backtest.py         # Historical simulation engine [V5]
│
├── examples/
│   ├── rapor.example.json         # Sample technical signal output
│   └── final_karar.example.json   # Sample trade decision output
│
├── gcp_key.json             # [secret] Google Cloud service account ← .gitignore
├── .env                     # [secret] API keys and config ← .gitignore
├── .env.example             # Safe template for contributors
├── .gitignore
├── requirements.txt
└── README.md
```

> **Note:** `rapor.json`, `legends_rapor.json`, `sentiment_rapor.json`, `final_karar.json`, `order_log.json`, and all `*.log` files are in `.gitignore`. They are generated at runtime. Sample outputs are in `examples/`.

---

## Edge Case Handling

`alpaca_trader.py` handles 7 critical trading scenarios:

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

---

## Roadmap

### Completed (V5)
- [x] ATR-based dynamic SL/TP across full pipeline
- [x] Pyramiding engine replacing `DUPLICATE_SKIP` in `alpaca_trader.py`
- [x] SMA_200 trend filter in all signal agents
- [x] 4-step scheduler pipeline (was 2-step in V4.5)
- [x] V5 backtest: $1,500 → $3,720 (+148%) over 2 years
- [x] ATR data contract: `rapor.json[ATR_14]` → `final_karar.json[atr_degeri]`
- [x] GitHub Actions CI (syntax + secret scan + JSON contract)

### Next Sprint
- [ ] Paper trading activation (Alpaca API keys connected)
- [ ] Gemini Flash sentiment upgrade (`GEMINI_API_KEY`)
- [ ] `mock_agent` → `analyst_agent` swap (`OPENAI_API_KEY`)
- [ ] Claude Opus final veto activation (`ANTHROPIC_API_KEY`)
- [ ] Remove QQQ and MSTR from watchlist (V6 candidate — PF < 1.2x)
- [ ] Increase GLD and LMT allocation weight (PF > 4x)

### Future
- [ ] Hardware upgrade: Mac Mini M4 Pro
- [ ] Multi-timeframe signals (4H + 1D confluence)
- [ ] Options flow as additional sentiment source
- [ ] Walk-forward optimization for ATR multiplier constants
- [ ] Web dashboard (Next.js) replacing Google Sheets
- [ ] True out-of-sample validation (data beyond backtest window)

---

## Contributing

Contributions are welcome. Before opening a PR, please read the constraints below — they are design decisions, not oversights.

### Architecture Constraints

**1. DAG only — no circular imports.**
Flow direction is strictly: `mock_agent → legends_agent → state_manager → alpaca_trader`. Any module that imports from a downstream module will be rejected.

**2. ATR must flow through the pipeline.**
Any module that calculates ATR must export it in its JSON output:
- `rapor.json` must contain `ATR_14` per asset in the `veri` dict
- `legends_rapor.json` must contain `atr` per symbol
- `final_karar.json` must contain `atr_degeri` and `atr_kullanildi` per decision

**3. Mock-first development.**
Every feature must work without API keys. If your change requires a live API, add a keyword/rule-based fallback that activates when the key is missing.

**4. Conflict = HOLD is a safety rail.**
The `catisma_var_mi()` threshold in `state_manager.py` is 0.15. Do not lower it without a backtest run showing improved Sharpe ratio or reduced max drawdown.

**5. Critical Rule 1 is intentional.**
The behavior where a SHORT signal against an existing LONG only closes the LONG must not be changed to a flip behavior. This is documented risk management policy.

### Development Workflow

```bash
# Verify full mock pipeline
python mock_agent.py && python legends_agent.py && python state_manager.py

# Validate JSON contracts
python -c "
import json
r = json.load(open('rapor.json'))
assert r.get('varlıklar'), 'rapor.json: no assets'
assert 'ATR_14' in r['varlıklar'][0]['veri'], 'ATR_14 missing'
assert 'SMA_200' in r['varlıklar'][0]['veri'], 'SMA_200 missing'
f = json.load(open('final_karar.json'))
assert 'atr_degeri' in f['kararlar'][0], 'atr_degeri missing'
print('✅ All JSON contracts valid')
"

# Syntax check before commit
python -m py_compile mock_agent.py analyst_agent.py legends_agent.py \
    sentiment_agent.py state_manager.py alpaca_trader.py scheduler.py \
    sheets_pusher.py telegram_bot.py scanner.py true_backtest.py
echo "✅ Syntax OK"
```

### Commit Message Convention

```
feat(legends): add ADX filter to Dennis Turtle SHORT entries
fix(alpaca): handle PositionNotFound on partial fill edge case
perf(backtest): vectorize ATR trailing stop watermark update
docs(readme): add V5 per-symbol performance breakdown
refactor(state): extract ATR fallback into standalone helper
test(mock): add assertion for ATR_14 presence in rapor.json
```

---

## Disclaimer

This software is for **educational and research purposes only**. It is not financial advice. Trading involves significant risk of loss, including the possible loss of all invested capital. Past backtest performance does not guarantee future results.

Never deploy this system with live capital before completing an extended paper trading validation period. The authors accept no responsibility for any financial losses incurred through use of this software.

---

<div align="center">

**Built with discipline. Tested with data. Deployed with caution.**

*"I never guess. I wait and react."* — Stanley Druckenmiller

</div>