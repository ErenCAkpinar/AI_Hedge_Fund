# AI_Hedge_Fund — Full Status Report

**Written for Codex, 2026-08-30, by Claude (Opus 5).**
Repo: `github.com/ErenCAkpinar/AI_Hedge_Fund` (public, MIT) · branch `docs-arch`
Author: Eren Akpınar · Goal, in his words: *learning + portfolio + making real money*

This is the handover document. It assumes you have no memory of today's session.
Everything factual below was verified against the code or produced by running it.
Where something is a claim I did not independently check, it says so.

---

## 1. What the system is

A multi-agent swing-trading system, Python, running on a MacBook Air M2.

```
scheduler.py  (cron: 13:30 / 19:50 UTC, weekday only)
     │
     ├─ 6 deterministic quant agents, NO LLM in the signal path
     │    mock_agent (ATR/SMA/RSI) · legends_agent (8-strategy vote)
     │    swan_agent (VIX/Monte Carlo) · pairs_agent (Ornstein-Uhlenbeck)
     │    insider_agent (QUIVER) · gamma_agent (GEX/UOA/IV skew)
     │         ↓ each writes a JSON artifact
     ├─ sentiment_agent (Gemini) → sentiment_rapor.json
     │
     ├─ state_manager.py  ← fuses artifacts, Claude Sonnet 4 veto  [CRITICAL STEP]
     │         ↓ final_karar.json
     ├─ alpaca_trader.py  ← execution, ATR stops, pyramiding
     └─ sheets_pusher.py  ← always runs, even on pipeline failure
```

Supporting: `quant_math.py` (14 stateless functions: Kelly, Black-Litterman, HMM,
Hurst, GARCH, kurtosis), `true_backtest.py`, `watchdog_agent.py` (Claude Haiku
position monitor), `quant-war-room/` (Next.js dashboard, untouched today),
`fastapi_bridge.py`, `telegram_bot.py`.

**Critical context:** the Alpaca account is **PAPER**, holding **~$98,418**
(Alpaca provisions paper accounts at $100k). `true_backtest.py` models **$1,500**.
This mismatch is the root of several defects below.

---

## 2. State when today started

- **Idle since 2026-03-26.** Five months. The author did not remember how it worked.
- Six uncommitted modified files sitting in the tree since March.
- The uncommitted work was titled *"STATE MANAGER V6 İLE TAM SENKRON"* (fully
  synchronised). It was not synchronised, in nine documented ways.
- Live evidence to date: **3 fills, all USO LONG, over 12 days, ending -1.6%**
  ($100,045 → $98,418). That is the entire real-world track record.
- README claimed **+148%, Sharpe 2.65, 0% stop-loss rate**.

---

## 3. Defects found — the complete list

Twelve were found by Claude, five more by you (Codex) in review, seven more by
you in review of my implementation. All were verified against the code.

### 3a. FIXED today

| # | Defect | Evidence |
|---|---|---|
| 1 | **`"SL"` never assigned.** `true_backtest.py` filtered exits on `cikis_neden == "SL"`, a string assigned nowhere. The README's headline "0.0% stop-loss rate" was `len([]) / 207` — structurally incapable of any other value. Present in committed V5 too. | grep; run confirmed `SL=0` |
| 2 | **The backtest was not a portfolio simulation.** It summed independent per-trade bets that overlapped freely. Reported `ORTALAMA POZİSYON 18.0%` was the *mean per-trade `pos_oran`*. Actual concurrent gross exposure: **66.5% mean, 217.5% max, 94 of 427 weekdays over 100%.** The +30.99% required leverage a $1,500 cash account does not have. | reconstructed from `true_backtest_rapor.json` |
| 3 | **Sharpe was invalid.** Per-trade returns annualised by `sqrt(252)`. With 142 trades over 501 sessions (~0.28/day) this badly overstated it and made it incomparable to SPY's daily Sharpe. | `true_backtest.py:590` |
| 4 | **Stop exits booked at the stop price** even when the bar gapped through it. Since V5 removed fixed take-profit, **100% of price-driven exits are stops**, so this understated every loss. | `islem_simule` |
| 5 | **Daily OHLC cannot order high vs low.** The code raised the trail from the bar's *high*, then tested the bar's *low* against the raised stop — assuming the favourable sequence. | `true_backtest.py:415-423` |
| 6 | **ATR at the entry bar was look-ahead.** Entry filled at that bar's Open while ATR needed the same bar's High/Low/Close. ATR sets both the stop distance and the trail, so it contaminated entries and exits. | `true_backtest.py:364-366` |
| 7 | **`tests/test_contracts.py` had never executed.** It imports pytest; pytest was undeclared, uninstalled, and absent from `ci.yml`, which duplicated its three assertions inline as a heredoc. | `ci.yml`, `pip list` |
| 8 | **Walk-forward verdict was inverted.** `quant_math` returned `tutarlilik` as a *ratio* (0.12) and a correctly-banded `yorum` string reading `"❌ OVERFIT"`. The report did `'✅ Tutarlı' if wf.get('tutarlilik') else '⚠️ Overfit'` — and `0.12` is truthy. The library diagnosed OVERFIT; the display printed the opposite. `yorum` was never read. | `true_backtest.py:732` |

Plus the seven blockers you found in my *replacement* implementation, all fixed
and regression-tested: liquidation after the final equity row; initial stop using
the kurtosis trail multiple; affordability excluding fees; `NaN` ATR passing
`not atr or atr <= 0`; `allow_short=True` with wrong cash flow; a sliding
two-year window; and the equal-weight benchmark dropping late constituents.

### 3b. STILL OPEN — safety

| # | Defect | Why it matters |
|---|---|---|
| 9 | **`SHORT_IZIN_ESIK = 2100.0`** correctly encodes the Reg T $2,000 short-selling minimum, but is compared against **$98,418** equity, so it is permanently `True`. It replaced a previously *unconditional* LONG_ONLY firewall. Five FXY shorts sit blocked in `order_log.json` and would execute on the next run. **The change reads as adding a safety rail and in fact removes one.** |
| 10 | **`CANLI_PARA = "paper" not in BASE_URL.lower()`** — when true it logs three warning lines and then trades real money. That is the entire control. |
| 11 | **Execution fails open.** Your claim, which I did not independently verify: PDT lookup failure returns permission; position lookup failure becomes "no position"; sizing failure returns one share; cancellation failure is ignored. |
| 12 | **The short path is unsafe.** `alpaca_trader` places a market order and submits the stop *separately*. A stop-submission failure leaves a **naked short** on a $98k account. You called deleting `LONG_ONLY_LIST` the highest-risk task in the plan for this reason. |

### 3c. STILL OPEN — correctness

| # | Defect |
|---|---|
| 13 | **HMM regime look-ahead.** `hmm_rejim_tespit` fits on the full two-year SPY window and applies `gizli_durumlar[-1]` — today's regime — to every historical bar. Still present in the numbers below, and labelled as such in the report output. |
| 14 | **`walk_forward_test` is not walk-forward.** It splits already-generated trades 60/40 with no retraining, no parameter selection, no purge, no embargo. Calling 0.29 an OOS Sharpe was generous. |
| 15 | **Rolling Kelly is inert.** `kelly_pozisyon_al` reads `kelly_gecmis.json`; nothing in the repo writes it. It silently falls back to V5 static tiers via `except Exception: pass`. The README advertises it as the headline V5→V6 improvement. |
| 16 | **Even fixing that would not work.** `poz_buyukluk` appears only in `state_manager.py`; `json_normalize` drops it and `alpaca_trader` re-derives size from `guven_skoru`. The computed Kelly size never reaches the executor. |
| 17 | **The live 15% cap is fictional.** `_pyramiding_gecmis_say` filters `order_log` on `startswith(bugun)`, so `PYRAMIDING_MAX_EKLE = 2` means two additions **per day**, not per position. A winning long can grow without bound. |
| 18 | **`take_profit` is never submitted.** `grep take_profit alpaca_trader.py` → zero hits. It is computed, shown to Claude in the veto prompt, written to `final_karar.json`, and never sent to the broker. |
| 19 | **Exit logic diverges.** Live hardcodes `2.5 × ATR` with a 2–8% clamp; the backtest uses a kurtosis-driven 2.5–5.0. |
| 20 | **No artifact freshness contract.** Your claim: a non-critical agent can fail while `state_manager` silently consumes yesterday's JSON as current. Error counters do not detect stale-but-valid inputs. |

### 3d. STILL OPEN — the LLM veto

| # | Defect |
|---|---|
| 21 | `claude_otonom_onay` catches bare `Exception` and returns `{"action": "HOLD"}`. **An Anthropic outage is indistinguishable from Claude vetoing every trade.** Only trace is a stdout print. |
| 22 | `max_tokens=300` can truncate the eight-field JSON response → `JSONDecodeError` → silent HOLD. |
| 23 | JSON extraction strips markdown fences but not surrounding prose. Intermittent by nature. |
| 24 | **17 sequential Anthropic calls inside a `kritik=True` step with `TIMEOUT_UZUN=300`.** A slow API cancels the trading day, and `sheets_pusher` still updates the dashboard, so it looks normal. |
| 25 | `state_manager.py` documented `API GEREKTİRMEZ` (requires no API). The diff makes it import `alpaca_trade_api` and call `get_account()`. No longer runnable offline. |
| 26 | **The veto is strategically empty.** Your point, and I agree: it sees no independent evidence, is absent from the backtest, and cannot establish edge. |

### 3e. STILL OPEN — hygiene

- `WATCHLIST` is defined in **7 files** (verified identical today, zero drift — a latent hazard, not a live bug).
- `LONG_ONLY_LIST` is *exactly* `WATCHLIST`, so it always evaluates one way. It reads as a curated allowlist and behaves as the word "no". 71 SHORT signals suppressed through it.
- `bl_agirliklar.json` and `hmm_rejim.json` are in `.gitignore` but **tracked**, so runtime state is version-controlled. The uncommitted diff would commit BL weights collapsing from 17 symbols to `{}`.
- `requirements.txt` **cannot be installed into any Python on this machine.** It pins `pandas==3.0.1` / `numpy==2.4.2` (need ≥3.11) while the only populated env was pyenv 3.9.13, and `ta` was installed nowhere. Nothing ran until I built `.venv-backtest/`.
- CI runs `py_compile` only, so a missing third-party import passes CI and fails at runtime. `quant_math.py` — where all the V6 math lives — is not even syntax-checked.
- README contradicts both the code and `docs/architecture.md` on which models run: README says Claude Opus / GPT-4o-mini / Gemini 2.0; architecture.md says Claude Sonnet 4 / Gemini 2.5 / Haiku 4.5; the code says `claude-sonnet-4-20250514`. Three documents, three answers.
- **`gcp_key.json` is in this public repo's git history** (`a571ed1b`; removed from HEAD in `458637ae`, which does not remove it from history). The author states the key has been revoked. I could not verify that from here.

---

## 4. What was built today

```
NEW  portfolio_simulator.py   ~400 lines, imports NOTHING from this project
NEW  tests/test_portfolio_simulator.py   20 tests
NEW  docs/designs/backtest-live-parity.md
NEW  docs/designs/go-no-go.md
NEW  CLAUDE.md, TODOS.md
     true_backtest.py         942 → 613 lines

23 tests passing (20 new + 3 pre-existing that had never run)
```

Retired entirely rather than kept as an alternate path: `islem_simule`,
`sembol_backtest`, `bilesik_pnl_hesapla`, `sharpe_calmar_hesapla`,
`gelismis_metrikler_hesapla`, and `atr_sl_tp` (already dead — it computed
stops a third way and was never called).

**Design decisions in the simulator**, mostly yours:
- Cash is the accounting source of truth; P&L never from `pnl_pct × hypothetical capital`.
- Seven-stage daily loop; **only the stop known before a bar may fire during it**.
- All same-open candidates size from one shared `E0` snapshot, so watchlist order cannot change sizing; allocation is proportional, not greedy-by-score.
- Unaffordable orders are rejected, never forced to one share.
- Signal at close → fill at next open. ATR/GARCH/trail frozen on the signal date.
- Metrics scored from an anchor at `calendar[ISINMA_GUN]`, excluding 60 warmup sessions.
- `allow_short=True` raises `NotImplementedError` rather than miscounting.

**Process discipline:** the go/no-go criterion was committed in `8b4aa68d`
**before the simulator existed**, so the bar could not be chosen after seeing
the number. Criterion: Sharpe above SPY **and** max drawdown shallower than SPY,
after 10 bps/side costs, on a $1,500 cash account capped at 100% gross.

---

## 5. The measured result

Window 2024-08-29 → 2026-08-29, scored from the anchor, identical window and
identical metric function for strategy and both benchmarks.

```
                          Return    Sharpe    MaxDD
  STRATEGY                +23.50%    0.671    -9.86%
  SPY buy & hold          +31.85%    0.717   -18.76%
  Watchlist equal-weight  +47.48%    0.740   -23.99%

  GO / NO-GO
     Sharpe  0.671 > SPY 0.717 ......... FAIL   (short by 0.046)
     MaxDD  -9.86% > SPY -18.76% ....... PASS
     RESULT: FAIL

  70 trades · 31.4% win rate · exits SL=34 TRAIL=14 TIME=21 END_OF_DATA=1
  avg gross exposure 15.1% · max 79.2% · 0 of 441 sessions over 100%
  490 orders rejected, overwhelmingly BELOW_ONE_SHARE · 0 for capacity
  costs: 0bps +24.87% · 5bps +23.89% · 10bps +23.50% · 20bps +20.35%
```

**Prediction I got wrong:** I told the author that fixing your seven blockers
would push the number *down*, and argued that mattered because it showed I was
not fixing in a convenient direction. It went **up** (+20.74% → +23.50%,
Sharpe 0.461 → 0.671). Cause: restoring the correct 1.5–1.8× initial stop from
the erroneous 2.5–5.0× cuts losers fast. SL exits went 2 → 34, the win rate
*fell* to 31.4%, and risk-adjusted performance improved anyway. More small
losses, fewer large ones. I did not anticipate that.

---

## 6. Downsides

1. **No demonstrated edge.** Below SPY risk-adjusted, and roughly half the return of simply holding the same 17 tickers equal-weighted.
2. **Every headline number was wrong** — the 0% SL rate, the Sharpe, the exposure figure, the overfit verdict, and the return itself.
3. **The 501-bar window is burned as out-of-sample evidence.** Results have been seen and the system changed in response.
4. **Safety controls that read as controls and function as formalities** — the $2,100 gate, the live-money warning, the 15% cap, `LONG_ONLY_LIST`.
5. **Zero behavioural test coverage before today**, and CI that could not have caught any of it.
6. **The LLM veto is unproven, untested, on the critical path, and fails silently.**
7. **Fixing the Kelly path requires two changes, not one** — a writer for `kelly_gecmis.json` *and* plumbing `poz_buyukluk` through to the executor.
8. **The environment was unbuildable.** `requirements.txt` installs nowhere.
9. **HMM look-ahead is still in the reported numbers.**

## 7. Upsides

1. **The architecture is genuinely good where the author was paying attention.** Fail-closed critical-step gating in `scheduler.py`; `sheets_pusher` on an always-run list so a failed run is never silent; `quant_math.py` as a stateless shared core so sizing rules cannot drift between layers. These are the instincts of someone who thinks about failure.
2. **No LLM in the signal hot path.** Six agents are fully deterministic. Cost and non-determinism are kept out of signal generation deliberately. That is a better decision than most projects in this space make.
3. **The drawdown result is real and it survived correct measurement.** -9.86% against SPY's -18.76%, at 15.1% average exposure. That is a coherent defensive profile, not noise.
4. **`docs/architecture.md` is accurate** — Mermaid generated from the live source, and it correctly anticipated `state_manager` calling Alpaca.
5. **It actually runs.** Scheduler, artifacts, real paper orders, Telegram, dashboard. Most projects at this ambition never place an order.
6. **The author knew Reg T from memory** after five months away. The domain knowledge is real; what failed was the mental model of *which account* the rule applied to.
7. **The test suite and simulator built today are reusable** regardless of what happens to the strategy.

---

## 8. This is my thought about this project

I think the engineering instincts here are better than the results suggest, and
that the gap between them is a specific, nameable, fixable weakness rather than
general carelessness.

Every serious defect I found today lives in the **measurement and verification
layer** — the code whose only job is to tell you whether the rest works. The
trading logic, the agent decomposition, the failure gating, the shared math
core: those were built carefully. The scoreboard was not. And because the
scoreboard was flattering, there was never a signal that anything needed
attention. Five separate mechanisms reported success they had not measured: a
filter comparing against a string that is never assigned, a stop booked at a
price you cannot get, an exposure figure that averaged the wrong thing, a
walk-forward verdict rendered as a boolean, and a test file that had never run.
That is not five unrelated bugs. It is one habit, repeated: **writing the code
that produces a number, and not the code that checks it.**

On the strategy itself, I am genuinely undecided, and I would rather say so than
manufacture a verdict. It failed the criterion — narrowly, on Sharpe, by 0.046.
It won decisively on drawdown. It ran at 15% average exposure, which means it
was barely invested, and 490 orders were rejected for being smaller than one
share on a $1,500 account. So the honest position is: **this strategy has never
actually been tested.** Not once, not today. What was tested was a
capital-starved version of it on an account size that exists nowhere except a
constant in the source. The real account holds $98,418.

That is why I would run exactly one more test before archiving it, at realistic
capital, with the bar declared in advance the same way. Not because I expect it
to pass — I would put it slightly under even odds — but because archiving on
evidence this constrained would be its own measurement failure, and this project
has had enough of those.

What I would **not** do is trade it. Not at $98k paper, and certainly not live.
The safety gates are the weakest part of the system, the short path can produce a
naked position, and the LLM veto is an untested single point of failure on the
critical path. The measurement work is now sound; the execution work has not
started.

If it fails at realistic capital too, I think the correct move is to archive the
strategy and keep the simulator. The honest infrastructure built today is worth
more than the strategy it was built to judge, and it will still be true in a year.

---

## 9. Open questions for you

1. **Should the $98,418 re-test happen at all**, given that these 501 bars are already contaminated? Or does a re-test on burned data tell us nothing regardless of capital?
2. **Is 490 `BELOW_ONE_SHARE` rejections enough to call the $1,500 test invalid**, or is "the strategy cannot be traded at its stated capital" itself the finding?
3. **Sharpe 0.671 vs 0.717 is a 6% shortfall.** Is that meaningfully different from a tie, given a 441-session sample? What is the right significance treatment here?
4. **Does the drawdown advantage (-9.86% vs -18.76%) have standalone value** at low exposure, or is it just what being 85% in cash looks like?
5. You said **"retire it now."** Does the corrected +23.50% / 0.671 change that at all, or does it confirm it?
6. **Sequencing:** E5 (point-in-time HMM) before the re-test, or is labelling the look-ahead sufficient?

---

## 10. Reference

```
Commits today (branch docs-arch, on top of 810a3d1b):
  fdbbd403  fix the seven blockers Codex found in the implementation
  2a35af35  delegate true_backtest to the portfolio simulator
  b7395f0b  portfolio simulator with cash ledger and sequence-free stop rule
  8b4aa68d  declare the go/no-go criterion BEFORE the simulator existed
  40ef6f46  Codex outside-voice findings; plan resequenced
  96f3177c  eng review report
  ba3f5f10  CLAUDE.md with repo gotchas
  4a9fbbb8  backtest/live parity design doc + TODOs

Still uncommitted (the original March work, deliberately untouched):
  .gitignore  alpaca_trader.py  bl_agirliklar.json  hmm_rejim.json  state_manager.py

Task backlog: 52 tasks across three JSONL artifacts in
  ~/.gstack/projects/ErenCAkpinar-AI_Hedge_Fund/
  tasks-ceo-review (26) · tasks-eng-review (12) · tasks-outside-voice (14)

Run the backtest:  .venv-backtest/bin/python true_backtest.py
Run the tests:     .venv-backtest/bin/python -m pytest tests/ -q
```

Comparable projects the author is looking at: `TauricResearch/TradingAgents`
(102k stars, LangGraph, bull/bear debate) and `virattt/ai-hedge-fund` (59.4k
stars) — the latter **shares this repo's name and its 8-legends concept**, which
matters for the portfolio goal. A bull/bear debate layer was explicitly deferred
(TODOS T-003): adding LLM agents to a system that cannot yet benchmark itself is
scaling the unmeasured.
