# 🤖 AI_Hedge_Fund — Multi-Agent Swing-Trading Research System

A Python research project that runs a daily decision pipeline over a 17-symbol watchlist of US stocks and ETFs. Rule-based agents score technicals, a vote of eight trading rule sets, market risk, alternative data and options positioning. A decision engine combines them with ATR-based stops and Kelly-style sizing, and orders go to Alpaca's **paper-trading** API only after an LLM approval step.

> **Status:** research and paper-trading project. It is not a fund, it has no live-money track record, and nothing here is investment advice.

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Alpaca](https://img.shields.io/badge/Broker-Alpaca%20paper-FECD45?style=flat-square)](https://alpaca.markets)
[![CI](https://github.com/ErenCAkpinar/AI_Hedge_Fund/actions/workflows/ci.yml/badge.svg)](https://github.com/ErenCAkpinar/AI_Hedge_Fund/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](LICENSE)

---

## Contents

- [How it works](#how-it-works)
- [AI integrations](#ai-integrations)
- [Backtest (V5)](#backtest-v5)
- [Run it](#run-it)
- [Project structure](#project-structure)
- [Roadmap](#roadmap)
- [Disclaimer](#disclaimer)

Component-level design notes are in [docs/DESIGN.md](docs/DESIGN.md); contribution rules are in [CONTRIBUTING.md](CONTRIBUTING.md).

---

## How it works

`scheduler.py` runs the pipeline twice a day (13:30 and 19:50 UTC) and skips the order step when the US market is closed. Each step writes a JSON report that later steps read.

| Step | Module | Writes | What it does |
|---|---|---|---|
| 1 | `mock_agent.py` | `rapor.json`, `hmm_rejim.json`, `bl_agirliklar.json` | Technical score (RSI, MACD, SMA 20/50/200, ATR) with Kurtosis, Hurst, GARCH and Kalman metrics per symbol; HMM market regime on SPY; Black-Litterman weights |
| 2 | `legends_agent.py` | `legends_rapor.json` | Weighted vote of eight rule sets named after well-known traders, with an ADX + Hurst trend filter |
| 3 | `swan_agent.py` | `swan_rapor.json` | VIX-based risk score, historical crisis stress tests, Monte Carlo simulation |
| 4 | `pairs_agent.py` | `pairs_rapor.json`, `copula_durum.json` | Ornstein-Uhlenbeck pairs signals and a portfolio-correlation check |
| 5 | `insider_agent.py` | `insider_rapor.json` | Alternative data from public sources such as SEC Form 4 filings, congressional trade disclosures and FINRA volume |
| 6 | `gamma_agent.py` | `gamma_rapor.json` | Options positioning from yfinance chains: gamma exposure, unusual activity, IV skew |
| 7 | `state_manager.py` | `final_karar.json` | Weighted final score (technical 35%, legends 25%, sentiment 15%, insider 15%, gamma 10%); entry thresholds scaled by regime and risk reports; conflicting signals → HOLD; ATR-based stop-loss and take-profit; position size; LLM approval |
| 8 | `alpaca_trader.py` | Alpaca orders | Paper orders with ATR trailing stops, pyramiding into winners, market-hours and pattern-day-trader guards |
| 9 | `sheets_pusher.py` | Google Sheet | Dashboard update |

Alongside the pipeline:

- `sentiment_agent.py` runs on its own schedule (12:00 Türkiye time): news, Reddit, Finnhub social sentiment, Finviz analyst ratings, CNN Fear & Greed and an implied-volatility signal → `sentiment_rapor.json`
- `watchdog_agent.py` checks open positions every 2 minutes during extended US hours and escalates price alerts
- `fastapi_bridge.py` serves the JSON reports over HTTP to `quant-war-room/`, a Next.js dashboard
- `telegram_bot.py` sends operator alerts
- `quant_math.py` holds the shared math as pure functions (Kelly, HMM, Hurst, GARCH, Kalman, Black-Litterman, OU spreads, Sortino, VaR/CVaR and others)

## AI integrations

The signal steps run without any API keys. Order execution does not: without an Anthropic key, `state_manager.py` turns every decision into HOLD and nothing is sent to Alpaca.

| Module | Model called in code | Role | Without an API key |
|---|---|---|---|
| `sentiment_agent.py` | Gemini 2.5 Flash | Scores news and Reddit text | Keyword scoring |
| `state_manager.py` | Claude Sonnet 4 | Final approval of each trade decision | Decision becomes HOLD |
| `watchdog_agent.py` | Claude Haiku 4.5 | On a price alert: close the position, re-run the pipeline, or wait | Waits |
| `analyst_agent.py` | Claude Sonnet 4 via CrewAI | Optional LLM version of the rule-based `mock_agent.py` step | Not part of the default schedule |

## Backtest (V5)

`true_backtest.py` replays the V5 rules on daily data for the 17-symbol watchlist. It reuses the live pipeline's legends vote and `state_manager` scoring functions.

| Setting | Value |
|---|---|
| Data | yfinance daily bars, `period="2y"`, run in March 2026 (about March 2024 – March 2026); the first 60 trading days are warm-up |
| Starting capital | $1,500 |
| Positions | Long only in practice (0 short trades); one position per symbol at a time; up to 2 pyramid adds; exit by trailing stop or after 15 trading days |
| Costs | Not modelled: no commissions, spread or slippage |
| Benchmark | None |

Results, from the script output in [docs/backtest_v5_output.png](docs/backtest_v5_output.png):

| Metric | Value |
|---|---|
| Trades | 207 (win rate 40.6%) |
| Net P&L, fixed position sizing | +$1,476.08 (+98.4%) |
| Net P&L, compounded replay | $1,500 → $3,720.62 (+148.0%), about +57.5% a year (CAGR over two years) |
| Profit factor | 1.79 |
| Max drawdown, fixed sizing | −$253.92 |
| Exits | 151 trailing stop, 56 time limit |
| Sharpe / Calmar, as computed by the script | 2.65 / 1.57 |

How to read these numbers:

- **Two P&L figures.** The fixed-sizing figure adds up each trade's dollar P&L. The compounded replay processes trades in entry-date order and sizes each one from the running equity. Trades on different symbols overlap in time, so the replay can commit more than 100% of equity at once and books each result before later-starting trades finish. Treat +148% as an optimistic upper bound.
- **Profit factor** is gross profit divided by gross loss. With a 40.6% win rate, a profit factor of 1.79 means the average winner was about 2.6 times the average loser.
- **"SL rate 0%"** in the script output does not mean there were no losing exits. The initial ATR stop becomes the trailing stop, so losing trades exit as `TRAIL`; several of the last 15 trades in the output are losses.
- **Sharpe and Calmar** are computed from per-trade returns (annualised with √252) and mix fixed and compounded P&L, so they are not comparable with ratios computed from daily equity.
- The watchlist was hand-picked and all results are in-sample.

Earlier versions for context (earlier runs with $100,000 starting capital): V1 768 trades, +31.6%, profit factor 1.22; V3 368 trades, +20.0%, profit factor 1.33.

To reproduce: `python true_backtest.py`. yfinance's `period="2y"` is a rolling window, so a new run covers a different period and gives different numbers.

## Run it

Requirements: Python 3.11+.

```bash
git clone https://github.com/ErenCAkpinar/AI_Hedge_Fund.git
cd AI_Hedge_Fund
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
pip install alpaca-trade-api        # only needed for order execution
cp .env.example .env                # Alpaca paper keys, optional LLM keys, Telegram, Google Sheets
```

Run the signal steps once, in order (no API keys needed):

```bash
python mock_agent.py
python legends_agent.py
python sentiment_agent.py
python pairs_agent.py
python state_manager.py             # without ANTHROPIC_API_KEY every decision is HOLD
```

Automated schedule:

```bash
python scheduler.py                 # or in the background:
nohup python scheduler.py > scheduler_out.log 2>&1 &
```

Other entry points: `python true_backtest.py`, `python watchdog_agent.py`, `python telegram_bot.py --test`, `python alpaca_trader.py --test`.

Tests: `pytest tests/` runs the JSON contract tests. CI runs syntax checks, a secret scan and the contract checks on pushes and pull requests.

## Project structure

```
AI_Hedge_Fund/
├── scheduler.py           # Daily pipeline runner
├── mock_agent.py          # Technical signals, market regime, portfolio weights
├── analyst_agent.py       # Optional CrewAI + Claude version of the technical step
├── legends_agent.py       # Vote of eight trading rule sets
├── sentiment_agent.py     # Multi-source sentiment
├── swan_agent.py          # Stress tests and Monte Carlo
├── pairs_agent.py         # Pairs trading and correlation check
├── insider_agent.py       # Alternative data
├── gamma_agent.py         # Options positioning
├── state_manager.py       # Final decision
├── alpaca_trader.py       # Order execution
├── watchdog_agent.py      # Intraday position monitor
├── quant_math.py          # Shared math functions
├── true_backtest.py       # Historical simulation
├── scanner.py             # Price fetch and health check
├── sheets_pusher.py       # Google Sheets dashboard
├── telegram_bot.py        # Operator alerts
├── fastapi_bridge.py      # HTTP API for the dashboard
├── quant-war-room/        # Next.js dashboard
├── examples/              # Sample JSON outputs
├── tests/                 # JSON contract tests
└── docs/                  # Design notes and backtest output
```

Runtime reports, logs, `.env` and `gcp_key.json` are git-ignored; never commit credentials.

## Roadmap

- [ ] Out-of-sample and walk-forward backtest with transaction costs
- [ ] Report Sortino and VaR/CVaR from `quant_math.py` in `true_backtest.py`
- [ ] Longer paper-trading record with the full pipeline
- [ ] `kelly_gecmis.json` feedback loop from closed paper trades
- [ ] Multi-timeframe signals (4H + 1D)

## Disclaimer

This software is for **educational and research purposes only**. It is not financial advice. Trading involves significant risk of loss, including the possible loss of all invested capital. Past backtest performance does not guarantee future results.

Never deploy this system with live capital before completing an extended paper trading validation period. The authors accept no responsibility for any financial losses incurred through use of this software.
