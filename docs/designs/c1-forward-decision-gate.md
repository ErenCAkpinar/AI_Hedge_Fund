# C1 forward — decision gate, declared at session 5 of ~64

Committed 2026-09-09. The ledger holds **5 sessions** (2026-09-01 → 2026-09-08,
$1,500 → $1,545.84). The gate below falls on **2026-12-01**, the 64th NYSE
session of the program. So this file is written with 8% of its evidence visible
and 92% still in the future.

That is the entire point, as with [`go-no-go.md`](go-no-go.md),
[`candidate-screen-rp-a.md`](candidate-screen-rp-a.md) and
[`c1-forward-basis-divergence.md`](c1-forward-basis-divergence.md). This project
has now recorded two FAIL verdicts against bars declared in advance
([`go-no-go-verdict.md`](go-no-go-verdict.md), [`rp-a-results.md`](rp-a-results.md)).
Both are only worth anything because the bar predated the number.

## Why a gate instead of a third hypothesis

Two hypotheses have been tested and both lost. The tempting third move is a new
hypothesis class. The reason not to take it yet is not pessimism about ideas —
it is that **there is nothing clean to test them on**.

Every measurement so far ran on the 2024-11-22 → 2026-08-28 window, which
`go-no-go.md` already declares burned: its results had been seen, and the system
was being changed in response. A third hypothesis class tested on that same
window would produce a third round of the same non-evidence, at the same cost.

The only source of uncontaminated bars this project has is the forward ledger,
and it is five days old. The binding constraint is evidence, not ideas. So the
next block of work is: let the instrument run, and check that the instrument
works.

## What is being decided at the gate — and what is not

**Decided: is the C1 forward ledger a trustworthy instrument?**

Not "is C1 a good strategy". `c1-forward-basis-divergence.md`, committed
2026-09-01, names the program's purpose as *"infrastructure validation on a
baseline, not a strategy trial"*. This gate holds that framing. A gate that
triggered on forward return would quietly convert the program into the strategy
trial that document declined to call it — eight days after the fact, and after
the returns had been seen. That is the exact move this file exists to prevent.

**Not decided here:** whether to deploy capital, whether $1,500 clears the ~20%
annualised hurdle, and which hypothesis class comes next. Those re-open *after*
the gate, and only if it passes.

## PASS requires all five, checked on 2026-12-01

**M1 — Session completeness.** Every NYSE session from 2026-09-01 through the
gate appears exactly once as an `EQUITY` row. No gap, no duplicate, no session
silently absent. Expected count: 64.

**M2 — The declared basis bound holds forward.** Re-run the
`c1-forward-basis-divergence.md` comparison against **the forward ledger's own
sessions** — the live ledger versus a basis-A replication of exactly those bars —
and confirm |drift| ≤ **0.10 pp/yr**. This closes a real hole: that bound is
currently held only by `tests/test_c1_basis_equivalence.py` on committed
historical CSVs. Nothing in the repo checks it against the forward rows, which is
the comparison the document actually claims.

**M3 — Corporate actions, hand-verified.** Every dividend and split occurring in
the 17 names inside the window is checked against the issuer's actual action, not
against the code's own opinion. **If none occurs, M3 is recorded UNTESTED, never
PASS.** The RP-A window contained no split in any of these names, so the split
path has never been compared against an independent basis; pretending silence is
evidence would be the third display-layer self-deception in this repo's history.

**M4 — Monthly rebalance path.** The three rebalances (2026-10-01, 2026-11-02,
2026-12-01) each emit a complete `DECISION` → `EXECUTION` → `FILL` set, and
post-rebalance weights land within tolerance of 1/17 each.

**M5 — Operational recovery.** Every failed scheduled run self-recovers by the
next one, with no manual intervention. One failure has already occurred and
recovered — `C1ForwardError: 2026-09-03 NVDA: invalid Close` on 09-04, caught up
by the 09-07 run — which is the fail-closed policy behaving correctly. A failure
requiring hands is an M5 FAIL regardless of cause.

## Explicitly not a pass

- **Any forward return, Sharpe or drawdown figure, of any value.** 64 sessions
  cannot separate skill from noise, and this program was never a strategy trial.
  The current +3.06% over 5 sessions annualises to something absurd; it means
  nothing and will be quoted nowhere.
- A basis drift brought inside the bound by widening the bound.
- M3 marked PASS on the grounds that no corporate action happened.
- Reaching the gate date with sessions missing and calling it PASS because the
  equity curve looks continuous.

## Consequence of each outcome

**PASS** — the ledger is established as a trustworthy instrument on live bars.
The hypothesis-class question re-opens at that point with something it does not
have today: a clean, forward, uncontaminated window to pre-register against. That
is a materially better position than choosing a hypothesis class now.

**FAIL** — the named defect is fixed and the gate is re-declared with a new date.
The gate is **not** extended to absorb a failure, and a failed leg is not dropped
because the other four passed.

## Resolution of an ambiguity in this file, declared 2026-09-09

The rules above say PASS requires all five legs and that FAIL means fix and
re-declare, but M3 has a third outcome — UNTESTED, when no corporate action
occurred — and this file did not say what that yields. Recording the answer now,
at session 5, while the evidence is still in the future and the answer cannot be
chosen to suit a result:

**An UNTESTED or human-pending leg, with every other leg passing, leaves the gate
INCOMPLETE — not passed.** INCOMPLETE is a decision for a person: either accept
in writing that the corporate-action path remains unexercised and proceed anyway,
or keep the ledger running until an action occurs. It is not a verdict the code
may award itself, and it is not a FAIL either, because nothing is broken.

`c1_gate.py` implements exactly this and `tests/test_c1_gate.py` holds it.

## What the first live run already found

Run at session 5 on 2026-09-09, the gate returned INCOMPLETE — M1, M4 and M5
PASS, M2 INSUFFICIENT_SESSIONS, M3 UNTESTED — which is the expected shape this
early. Two things are worth recording because they were not expected:

- **M2 would have reported a breach without its session floor.** An absolute
  drift of 0.01 pp and a maximum equity gap of 5.4 cents annualise to 0.63 pp/yr
  across four days, six times the declared 0.10 bound. Under
  `c1-forward-basis-divergence.md` a breach means "the basis is not the
  explanation, go find a defect" — so without the floor the gate's first act
  would have been to send someone hunting a bug worth five cents.
- **M3's first run was wrong, and the ledger was right.** It flagged an LMT
  ex-date on 2026-09-01 as a missed dividend. The book was 100% cash that
  session — the program decides at the first close and fills at the next open —
  so no dividend was due, and the fill landed already ex-dividend. The check now
  exempts a dividend whose ex-date falls on a zero position, and lists it rather
  than dropping it silently.

## Freeze until 2026-12-01

No new candidate screens, no parameter search, no revival of the V6 live path —
which `go-no-go-verdict.md` archived, and whose uncommitted April changes are
parked unmerged on `wip/v6-short-unlock-april` for exactly that reason. Work that
remains legitimate in this period: the M2 comparison harness, defect fixes in
`c1_forward.py`, and documentation.

The one standing instruction between now and the gate is to leave the scheduler
alone and let it write.
