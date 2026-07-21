# Architecture

Multi-agent swing-trading system. Six deterministic quant agents produce evidence,
a Claude-backed decision engine fuses it into a single sized order, and Alpaca
executes it — orchestrated by a scheduler that gates execution on critical-step
success and market hours.

Diagrams below are generated from the live source (`scheduler.py` → `PIPELINE_ADIMLARI`),
not from an idealized design.

---

## 1. System Overview

```mermaid
flowchart TD
    SCHED["scheduler.py — orchestrator<br/>13:30 and 19:50 UTC full pipeline<br/>12:00 UTC sentiment scan<br/>weekend skip"]

    subgraph SIG ["Signal agents — deterministic quant, no LLM"]
        direction LR
        A1["Technical Analysis<br/>ATR · SMA200 · HMM regime"]
        A2["Legends Voting<br/>8 strategies · ADX/Hurst filter"]
        A3["Black Swan Engine<br/>VIX · Monte Carlo"]
        A4["Pairs + Copula Shield<br/>Ornstein-Uhlenbeck"]
        A5["Insider Tracker<br/>6-layer"]
        A6["Gamma Sentinel<br/>GEX · UOA · IV skew"]
    end

    SENT["Sentiment Scan<br/>Gemini 2.5 Flash"]
    DEC{"Decision Engine<br/>state_manager.py<br/>Claude Sonnet 4 · Rolling Kelly"}
    EXEC["Execution — Alpaca<br/>ATR stops · pyramiding"]
    REP["Reporting<br/>Telegram · Google Sheets · Next.js war-room"]
    WD["Position Watchdog<br/>Claude Haiku 4.5"]

    SCHED --> SIG
    SCHED --> SENT
    SIG --> DEC
    SENT --> DEC
    DEC --> EXEC
    EXEC --> REP
    EXEC -.-> WD
    WD -.-> REP

    style SCHED fill:#38a169,color:#fff
    style DEC fill:#2d3748,color:#fff
    style EXEC fill:#2b6cb0,color:#fff
```

---

## 2. Full Technical Flow

Solid arrows = control/data flow · dotted arrows = external tools and data sources ·
cylinders = JSON artifacts exchanged between agents · 🔴 = critical step (failure
aborts execution).

```mermaid
flowchart TD
    T1(["NYSE open · 13:30 UTC"]) --> SCHED
    T2(["Pre-close · 19:50 UTC"]) --> SCHED
    T3(["Daily · 12:00 UTC"]) --> SENT

    SCHED["scheduler.py<br/>sequential runner · per-step timeout · weekend skip"]

    SCHED --> S1
    S1["1 · mock_agent.py 🔴<br/>Technical Analysis — ATR + SMA200"]
    S2["2 · legends_agent.py 🔴<br/>8-Legend Weighted Voting"]
    S3["3 · swan_agent.py<br/>Black Swan Engine — VIX + Monte Carlo"]
    S4["4 · pairs_agent.py<br/>Pairs Scan + Copula Shield"]
    S5["5 · insider_agent.py<br/>Insider Tracker — 6 layers"]
    S6["6 · gamma_agent.py<br/>Gamma Sentinel — GEX + UOA + IV skew"]
    S1 --> S2 --> S3 --> S4 --> S5 --> S6 --> S7

    S7["7 · state_manager.py 🔴<br/>Final Decision Engine"]
    GATE{"critical steps OK<br/>and market open?"}
    S8["8 · alpaca_trader.py<br/>Execution + Pyramiding"]
    HOLD["execution skipped<br/>orders held for next run"]

    S9["9 · sheets_pusher.py<br/>Dashboard update — always runs, even if pipeline halts"]

    S7 --> GATE
    GATE -->|no| HOLD
    GATE -->|yes| S8
    S8 --> S9
    HOLD -->|always| S9

    SENT["sentiment_agent.py<br/>Multi-source sentiment + IV radar"]

    D1[("rapor.json<br/>hmm_rejim.json<br/>bl_agirliklar.json")]
    D2[("legends_rapor.json")]
    D3[("swan_rapor.json")]
    D4[("pairs_rapor.json<br/>copula_durum.json")]
    D5[("insider_rapor.json")]
    D6[("gamma_rapor.json")]
    D7[("sentiment_rapor.json")]
    D8[("final_karar.json<br/>kelly_gecmis.json")]
    D9[("order_log.json")]

    S1 --> D1 --> S7
    S2 --> D2 --> S7
    S3 --> D3 --> S7
    S4 --> D4 --> S7
    S5 --> D5 --> S7
    S6 --> D6 --> S7
    SENT --> D7 --> S7
    S7 --> D8 --> S8
    S8 --> D9

    QM["quant_math.py<br/>Rolling Kelly · Black-Litterman<br/>ATR SL/TP · kurtosis to ATR multiplier<br/>HMM regime"]
    QM -.-> S1
    QM -.-> S7

    YF[(yfinance)]
    ALP[(Alpaca API)]
    FIN[(Finnhub + finviz)]
    QUI[(QUIVER API)]

    YF -.-> S1
    YF -.-> S3
    YF -.-> S4
    YF -.-> S6
    QUI -.-> S5
    FIN -.-> SENT
    ALP -.-> S7
    ALP -.-> S8

    LLM1{{"Claude Sonnet 4"}}
    LLM2{{"Gemini 2.5 Flash"}}
    LLM3{{"Claude Haiku 4.5"}}
    LLM1 -.-> S7
    LLM2 -.-> SENT

    WD["watchdog_agent.py<br/>Position Watchdog"]
    LLM3 -.-> WD
    D8 -.-> WD
    ALP -.-> WD

    CREW["analyst_agent.py — CrewAI sleeve<br/>Agent: Lead Quant · Task · Process.sequential"]
    LLM1 -.-> CREW
    YF -.-> CREW

    SHEETS[(Google Sheets<br/>dashboard)]
    S9 --> SHEETS
    D9 -.-> REP
    REP["Out-of-pipeline surfaces<br/>telegram_bot.py · fastapi_bridge.py<br/>quant-war-room (Next.js)"]

    style SCHED fill:#38a169,color:#fff
    style S7 fill:#2d3748,color:#fff
    style GATE fill:#d69e2e,color:#fff
    style S8 fill:#2b6cb0,color:#fff
    style HOLD fill:#742a2a,color:#fff
```

---

## Architecture notes

- **File-based message passing.** Agents are standalone processes that communicate
  through versioned JSON artifacts rather than in-memory objects. Any step can be
  re-run in isolation, and every decision is reproducible from the artifacts on disk.
- **Critical-step gating.** `mock_agent`, `legends_agent` and `state_manager` are
  marked critical. If one fails, the scheduler refuses to place orders instead of
  trading on partial evidence — a fail-closed default.
- **Timeout guards.** Each step runs under its own timeout and is terminated if it
  overruns, so one hung data provider cannot stall the trading window.
- **Fail-visible dashboard.** `sheets_pusher.py` is on an always-run list: even when
  a critical failure halts trading, the dashboard is still updated — a failed run is
  never silent.
- **Hybrid LLM routing.** The six signal agents are fully deterministic quant code —
  no LLM in the hot path. Language models are used only where judgment beats
  arithmetic: final decision synthesis (Claude Sonnet 4), news/sentiment extraction
  (Gemini 2.5 Flash), and position supervision (Claude Haiku 4.5). This keeps cost
  and non-determinism out of signal generation.
- **Market-aware execution.** Order placement is skipped when the market is closed;
  the decision is preserved and acted on at the next open.
- **Shared quant core.** `quant_math.py` is pure, stateless math (Rolling Kelly,
  Black-Litterman, ATR-based stops, kurtosis-scaled risk multipliers, HMM regime)
  imported by both the signal and decision layers, so sizing rules cannot drift
  between them.

> Paper-trading system. Nothing here is investment advice.
