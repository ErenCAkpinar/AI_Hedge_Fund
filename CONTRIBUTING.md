# Contributing

Contributions are welcome. Before opening a PR, please read the constraints below — they are design decisions, not oversights.

## Architecture Constraints

**1. DAG only — no circular imports.**
Flow direction is strictly: `mock_agent → legends_agent → swan_agent → pairs_agent → insider_agent → gamma_agent → state_manager → alpaca_trader` (`sentiment_agent` runs on its own schedule). Any module that imports from a downstream module will be rejected.

**2. `quant_math.py` is stateless.**
No file I/O, no API calls inside `quant_math.py`. Functions take data in, return results out. This is a hard requirement for unit testability.

**3. ATR must flow through the pipeline.**
Any module that calculates ATR must export it in its JSON output:
- `rapor.json` must contain `ATR_14` per asset in the `veri` dict
- `legends_rapor.json` must contain `atr` per symbol
- `final_karar.json` must contain `atr_degeri` and `atr_kullanildi` per decision

**4. Mock-first development.**
Every feature must work without API keys. If your change requires a live API, add a keyword/rule-based fallback that activates when the key is missing.

**5. Conflict = HOLD is a safety rail.**
The `catisma_var_mi()` threshold in `state_manager.py` is 0.15. Do not lower it without a backtest run showing improved Sharpe ratio or reduced max drawdown.

**6. Critical Rule 1 is intentional.**
The behavior where a SHORT signal against an existing LONG only closes the LONG must not be changed to a flip behavior. This is documented risk management policy.

## Development Workflow

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
python -m py_compile quant_math.py mock_agent.py analyst_agent.py legends_agent.py \
    sentiment_agent.py pairs_agent.py state_manager.py alpaca_trader.py \
    scheduler.py sheets_pusher.py telegram_bot.py scanner.py true_backtest.py
echo "✅ Syntax OK"
```

## Commit Message Convention

```
feat(quant_math): add Monte Carlo stress test function
fix(alpaca): handle PositionNotFound on partial fill edge case
perf(legends): vectorize Hurst R/S calculation
docs(readme): update V6 quant arsenal section
refactor(state): extract HMM carpan into standalone helper
test(quant): add Sortino assertion for zero-downside case
```
