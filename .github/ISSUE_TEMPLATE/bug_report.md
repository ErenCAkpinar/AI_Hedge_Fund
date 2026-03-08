---
name: Bug Report
about: Something is broken in the pipeline
title: '[BUG] '
labels: bug
assignees: ''
---

## Description
A clear description of the bug.

## Affected Module
- [ ] `scanner.py`
- [ ] `mock_agent.py`
- [ ] `analyst_agent.py`
- [ ] `legends_agent.py`
- [ ] `sentiment_agent.py`
- [ ] `state_manager.py`
- [ ] `alpaca_trader.py`
- [ ] `scheduler.py`
- [ ] `sheets_pusher.py`
- [ ] `telegram_bot.py`
- [ ] `true_backtest.py`

## Steps to Reproduce
1. Run `python ...`
2. See error

## Expected Behavior
What should happen.

## Actual Behavior
What actually happens. Paste the full error traceback:

```
paste traceback here
```

## Environment
- OS:
- Python version: (`python --version`)
- Mode: Paper / Live / Mock (no API)
- Relevant `.env` values (redact actual keys):

```
ALPACA_BASE_URL=
PYRAMIDING_AKTIF=
```

## JSON State (if relevant)
Paste relevant section of `final_karar.json` or `rapor.json` if the bug is data-related.
