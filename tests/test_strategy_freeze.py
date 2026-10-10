"""PHASE 0 :: strategy freeze guard.

Fails if ANY protected file (signal generation, entry, exit geometry,
sizing, ensemble voting) changes so much as one byte during the
reliability repair. The authoritative hashes live in
docs/PROTECTED_HASHES.txt, committed at the repair baseline.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HASH_FILE = ROOT / "docs" / "PROTECTED_HASHES.txt"

LINE_RE = re.compile(r"^([0-9a-f]{64})\s{2}(.+)$")


def _protected() -> dict[str, str]:
    """expected sha256 -> path map parsed from PROTECTED_HASHES.txt."""
    expected: dict[str, str] = {}
    for raw in HASH_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = LINE_RE.match(line)
        if m:
            expected[m.group(2).strip()] = m.group(1)
    return expected


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def test_protected_hash_manifest_exists() -> None:
    assert HASH_FILE.exists(), (
        f"{HASH_FILE} missing - the freeze manifest must be committed "
        "before any repair work")


def test_protected_manifest_is_parseable() -> None:
    expected = _protected()
    assert len(expected) >= 6, (
        "manifest must pin at least the six audited protected files, "
        f"got {len(expected)}")


def test_no_protected_file_changed() -> None:
    expected = _protected()
    assert expected, "empty freeze manifest"
    drifted: list[str] = []
    missing: list[str] = []
    for rel, want in sorted(expected.items()):
        p = ROOT / rel
        if not p.exists():
            missing.append(rel)
            continue
        got = _sha256(p)
        if got != want:
            drifted.append(f"{rel}: expected {want[:12]}… got {got[:12]}…")
    assert not missing, f"protected files deleted: {missing}"
    assert not drifted, (
        "STRATEGY FREEZE VIOLATED - protected files were modified:\n  "
        + "\n  ".join(drifted)
        + "\nRevert the diff or re-baseline explicitly with a written "
          "justification in docs/STRATEGY_DEFECTS.md.")


def test_strategy_freeze_covers_core_surfaces() -> None:
    expected = _protected()
    required = {
        "core/strategy.py", "core/strategy_apex.py", "core/config.py",
        "core/risk.py", "core/risk_apex.py", "core/indicators.py",
    }
    assert required.issubset(expected.keys()), (
        f"manifest missing required surfaces: {required - expected.keys()}")
