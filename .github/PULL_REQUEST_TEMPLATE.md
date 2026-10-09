<!--
Gold Reaper PR contract:
- PAPER mode stays the default. Always.
- Strategy/risk changes must be walk-forward validated (worst-fold rule,
  see research/tune_v3.py) and logged in CHANGELOG.md.
- No hype language, no profit promises, no unverified numbers (docs/BRAND.md).
- Cross-platform: CI must stay green on Windows + Linux + macOS.
-->

## What

<!-- one or two sentences: what changes and why -->

## Why

<!-- link the issue, diagnosis finding, or tuning result -->

## Evidence

<!-- for strategy/risk changes: worst-fold before → after, fold nets,
     and the command that reproduces it. For visuals: screenshot/GIF. -->

## Checklist

- [ ] `ruff check .` clean
- [ ] `mypy` clean (scoped config in pyproject.toml)
- [ ] CI matrix green (linux / macos / windows)
- [ ] CHANGELOG.md updated (if user-visible)
- [ ] No profit promises / hype added
- [ ] PAPER default untouched
