## Summary
Brief description of what this PR does.

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Refactor (no behavior change)
- [ ] Documentation update
- [ ] Performance improvement

## Modules Changed
List the `.py` files modified and why.

## Architecture Constraints Checklist
- [ ] No circular imports introduced (DAG direction preserved)
- [ ] ATR exported in JSON output if module calculates ATR
- [ ] Works in mock mode (no API keys required)
- [ ] `catisma_var_mi()` threshold unchanged (or justified with backtest data)
- [ ] Critical Rule 1 behavior in `alpaca_trader.py` preserved

## Testing
- [ ] `python -m py_compile <module>.py` passes
- [ ] Full mock pipeline runs: `python mock_agent.py && python legends_agent.py && python state_manager.py`
- [ ] JSON contracts valid (ATR_14 in rapor.json, atr_degeri in final_karar.json)
- [ ] Backtest re-run if signal logic changed — results: ___

## Breaking Changes
Does this change any JSON field names or remove any outputs? If yes, list them.
