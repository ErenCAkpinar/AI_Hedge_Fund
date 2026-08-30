# TODOS

Deferred work with enough context to pick up cold. Each entry records why it was
deferred, not just what it is.

---

## T-001 — Live-vs-backtest sizing reconciliation log

**What:** On each live run, record what the backtest would have sized for the same
signal versus what live actually sized, and log the gap.

**Why:** `tests/test_parity.py` proves the two systems agree on *configured constants*.
It cannot catch divergence caused by things configuration does not express: buying-power
clamps (`alpaca_trader.py:252`), partial fills, or price movement between decision and
execution. Static parity can pass while real behaviour diverges.

**Pros:** Catches the class of drift the parity test structurally cannot. Accumulates the
dataset that makes the replay harness (T-002) tractable.

**Cons:** Instruments a system that is not currently running, against numbers not yet
trusted. Adds a log to maintain.

**Context:** Deferred in `/plan-ceo-review` on 2026-08-30 (decision D11.7). Sequencing was
the reason, not value. The judgement was: prove static parity on paper first, then measure
dynamic drift. Revisit once `tests/test_parity.py` is green and the scheduler is running
again.

**Adopted concept (2026-08-30, decision D23):** TradingAgents (102k stars) persists a
Decision Log that, on each subsequent run, fetches the realised return — raw *and alpha
versus SPY* — then writes a one-paragraph reflection and injects recent same-ticker
decisions as context. Fold that shape into this task: per-trade realised return, alpha vs
SPY, and a short reflection line. That turns the reconciliation log from a debugging
artifact into an actual scoreboard.

**Effort:** M (human ~3h) → with CC+gstack: S (~20min)
**Priority:** P2
**Depends on:** Approach B landed and green; system actively running

---

## T-002 — Replay harness (Approach C)

**What:** Make `true_backtest.py` drive the real live decision path instead of
reimplementing it. Feed historical bars through `state_manager`'s decision function and
`alpaca_trader.pozisyon_boyutu_hesapla` against a mock broker.

**Why:** This is the permanent fix for the eight divergences found on 2026-08-30. Approach
B makes divergence *detectable*. Approach C makes it *impossible*, because there is only
one code path.

**Pros:** One code path, one truth. No parity test needed because there is nothing to
diverge. Strongest long-term architecture available here.

**Cons:** Requires injecting a broker interface and a clock into live code. The Claude
veto makes a 2-year replay non-deterministic and expensive to run. Large change to
order-placing code.

**Context:** Evaluated and explicitly chosen as the destination, not the first move, in
`/plan-ceo-review` on 2026-08-30 (decision D9). The reasoning: refactoring the code that
places orders while cold on the codebase after five months away is where serious bugs are
born. Approach B was selected to build the understanding and the safety net first.
`quant_math.py` is already the right precedent for the shared-core pattern this needs.

**Effort:** XL (human ~1 week) → with CC+gstack: L (~4h)
**Priority:** P3
**Depends on:** T-001 (its reconciliation data makes the mock broker realistic); Approach B

---

## T-003 — Evaluate a bull/bear debate layer (TradingAgents pattern)

**What:** Assess whether adding opposed researcher agents — one arguing the long case, one
arguing the short case, debating before the final decision — improves outcomes over the
current single-pass Claude veto.

**Why:** TradingAgents uses this to reduce single-model bias, and it is the most
architecturally interesting idea in that project. Structured disagreement is a genuinely
different mechanism from a single approve/veto call.

**Pros:** Reduces single-point model bias. High learning value. Directly relevant to the
portfolio goal, since it is the part of the design that is actually novel.

**Cons:** TradingAgents reports $0.30–$0.50 per analysis and 22% drawdowns in its own
results. Adds substantial non-deterministic LLM surface. Multiplies the reproducibility
problem the current single veto already introduces.

**Context:** Explicitly deferred in `/plan-ceo-review` on 2026-08-30 (decision D23). The
reasoning was sequencing, not merit: adding agents to a system that cannot yet tell you
whether it beats SPY means scaling something unmeasured. Revisit **only after** the SPY and
equal-weight benchmarks are live and the parity test is green — at that point you can
evaluate the debate layer against a scoreboard that works, which is the only way the
evaluation means anything.

**Effort:** XL (human ~1 week) → with CC+gstack: L (~4h)
**Priority:** P3
**Depends on:** Approach B green; benchmarks reporting; T-001

---

## Reference

- TradingAgents — https://github.com/TauricResearch/TradingAgents
- AI trading agent landscape — https://pinggy.io/blog/best_ai_trading_agents/
  (source of the transaction-cost and backtest-to-live-gap framing that surfaced the
  gap-through fill defect, task T23)
