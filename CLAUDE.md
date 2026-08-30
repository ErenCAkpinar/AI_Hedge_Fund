# AI_Hedge_Fund

Multi-agent swing-trading system. Six deterministic quant agents write JSON artifacts,
`state_manager.py` fuses them into a decision, `alpaca_trader.py` executes via Alpaca.
See `docs/architecture.md` for the live Mermaid diagrams.

## Facts that are easy to get wrong

These bit a cold session on 2026-08-30. Check them before reasoning about sizing or risk.

- **The Alpaca PAPER account holds ~$98,000**, not the `$1,500` that `true_backtest.py`
  models via `BASLANGIC_SERMAYE`. Alpaca provisions paper accounts at $100k by default.
  Any safety gate written as an equity threshold (e.g. `SHORT_IZIN_ESIK = 2100.0`,
  which correctly encodes the Reg T $2,000 short-selling minimum) is therefore
  **permanently true** and acts as an unconditional unlock, not a gate.
- **Live position size is capped at 15%** (`MAX_POZ_PCT`, `alpaca_trader.py`). The
  backtest allows up to 35% plus pyramiding. The two do not agree.
- **Rolling Kelly is inert.** `kelly_pozisyon_al` reads `kelly_gecmis.json`; nothing in
  the repo writes it. It silently falls back to the V5 static tiers.
- **Backtest and live diverge in eight documented ways** despite the V6 commit message
  claiming "TAM SENKRON". See `docs/designs/backtest-live-parity.md`.
- **Do not trust a suspiciously clean metric.** Three separate display-layer bugs have
  made this system look better than it is: a filter comparing against a string that is
  never assigned, a stop exit booked at the stop price despite gap-through, and a
  walk-forward ratio rendered as a boolean. Grep for the literal before believing it.

## Environment

`requirements.txt` cannot be installed into any Python on this machine (it pins
pandas 3.x / numpy 2.x, which need Python >= 3.11; the populated env is pyenv 3.9.13).
A working backtest-only venv lives at `.venv-backtest/`:

```bash
.venv-backtest/bin/python true_backtest.py
```

CI runs `py_compile` only, so a missing third-party import passes CI and fails at runtime.

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
- Author a backlog-ready spec/issue → invoke /spec
