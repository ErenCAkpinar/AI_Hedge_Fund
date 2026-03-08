---
name: Feature Request
about: Suggest an improvement to the system
title: '[FEAT] '
labels: enhancement
assignees: ''
---

## Summary
One-sentence description of the feature.

## Motivation
Why does the system need this? What problem does it solve?

## Proposed Implementation
Which module(s) would be affected? How would the DAG contract change?

**New JSON fields (if any):**
```json
{
  "new_field": "description"
}
```

## Backtest Impact
Have you estimated or measured the impact on backtest performance?
- [ ] Backtested — results: ___
- [ ] Not yet backtested

## Does this break any existing contracts?
- [ ] Changes `rapor.json` schema
- [ ] Changes `legends_rapor.json` schema  
- [ ] Changes `final_karar.json` schema
- [ ] No schema changes

## Architecture Constraints Check
- [ ] Does NOT create circular imports
- [ ] Works without API keys (mock mode)
- [ ] ATR still flows through the pipeline if the module touches SL/TP
