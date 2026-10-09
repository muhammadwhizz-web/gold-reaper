# Contributing

PRs welcome. This project holds a high bar on two things: **honesty** and
**reproducibility**. Both are testable.

## Ground rules

1. **No profit promises.** Any claim must carry a reproduce command. Backtest
   numbers change → docs change in the same PR.
2. **No look-ahead leakage.** Feature at row `t` may only use data ≤ `t`.
   Labels may look forward but live in `label_*` columns, separated.
3. **Paper-first.** New strategies/params run in PAPER mode ≥ 2 weeks before
   any live-path merge.
4. **Determinism.** Demos, fixtures and asset generators must be seeded and
   reproducible (`random.Random(seed)`).
5. **One test per fix.** CI must catch the regression you fixed.

## Workflow

```bash
git clone https://github.com/muhammadwhizz-web/gold-reaper.git
cd gold-reaper
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

python data/ingest_multi_tf.py     # local data (or copy from a teammate)
python features/build_features.py
python research/backtest_apex.py   # must run before strategy PRs
python -m compileall -q .          # CI parity check
```

## PR checklist

- [ ] `pytest`/CI green
- [ ] Backtest + docs updated in the same PR (if strategy-touching)
- [ ] No secrets, no `.env`, no logs with account data
- [ ] New deps justified in the PR body
- [ ] Brand rules respected in user-facing strings (docs/BRAND.md)

## Scope guidance

Good first issues: indicator coverage in `features/build_features.py`,
broker adapters, dashboard widgets, demo polish, docs.
Strategy changes require backtest evidence in the PR description.
