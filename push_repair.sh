#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# GOLD REAPER :: finish-the-repair push (run after providing a token)
# The repair branch fix/reliability-v1 is complete and verified locally
# (83 tests green, freeze intact, 12-step health gate passed).
# The sandbox reset erased the GitHub token — create a NEW fine-grained
# token (repo: muhammadwhizz-web/gold-reaper, Contents: read+write) and run:
#   bash push_repair.sh <TOKEN>
# ═══════════════════════════════════════════════════════════════
set -e
TOKEN="${1:?usage: bash push_repair.sh <github_token>}"
REPO="muhammadwhizz-web/gold-reaper"
cd /home/z/gold-reaper

# never echo the token; embed it only in the local remote URL
git remote set-url origin "https://x-access-token:${TOKEN}@github.com/${REPO}.git"
git push -u origin fix/reliability-v1

PR_URL=$(curl -s -X POST \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/${REPO}/pulls \
  --data-binary @- <<'PRJSON' | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('html_url') or d)"
{
  "title": "Reliability repair v1: exits execute, paper state persists, honest feed/failover/config, install gate",
  "head": "fix/reliability-v1",
  "base": "main",
  "body": "## CRITICAL REPAIR & HARDENING (P0 -> P1 -> P2)\n\nStrategy frozen bit-for-bit (docs/PROTECTED_HASHES.txt, CI-enforced).\n\n### P0 (money correctness)\n- **P0-A** paper trades now CLOSE on EXIT_SL/EXIT_TP + independent stop-enforcement safety net (same-candle both-hit -> SL first, EXIT_SL_CONSERVATIVE), exactly-once closes with full records\n- **P0-B** paper account persists atomically (data/paper_account.json, tmpfile->fsync->os.replace), schema-guarded, corrupt -> .bak + refuse, DuckDB mirror, 60s heartbeat + shutdown flush\n- **P0-C** core/account_state.py single money-truth hydrated into BOTH risk layers (legacy streak gate works again)\n\n### P1 (operations honesty)\n- **P1-A** paper connect() validates the feed (present/finite/positive/fresh, >=20 hourly bars) -> DEGRADED + reason; GC=F instrument honesty (PAPER_INSTRUMENT)\n- **P1-B** failover explicit: live failure HALTS (exit 3, supervisor never restarts), live->paper swap structurally forbidden, requested-vs-active tracked + labeled\n- **P1-C** strict config validation (exit 2 naming key+value)\n- **P1-D** dashboards bind 127.0.0.1 by default, optional basic auth, red DEMO banner in mock mode, /health endpoint\n- **P1-E** installers rewritten (python chains, pinned requirements.lock, idempotent, --uninstall, service registration, desktop icons, start scripts self-heal)\n\n### P2 + gates\n- requirements split + hash-pinned lock; CI tests job (pytest + ruff + mypy, 3 OSes); installer-e2e.yml; docs/PARAMETERS.md; docs/AUDIT.md; docs/STRATEGY_DEFECTS.md (SD-1..SD-6, frozen-file defects documented, not fixed); installer/health_check.py 12-step gate (locally: ALL 12 PASS); cli.py doctor [--fix]|start|stop|status\n\n### Evidence\n- 83 tests green locally; full health gate ALL 12 STEPS PASS (real ingest 13,733 bars, feed validated, trade open/close, restart persistence, bot 30s paper run, /health 200, breakers trip)\n- protected hashes byte-identical to baseline; zero diffs to the six protected files\n- verified / partially verified / not tested strictly separated in docs/REPAIR_REPORT.md\n\n*Quiet. Honest, Repaired, Lethal.*"
}
PRJSON
)

echo "PR: $PR_URL"
echo "NOTE: this token touched git+api only; delete it after merge if it leaked anywhere."
