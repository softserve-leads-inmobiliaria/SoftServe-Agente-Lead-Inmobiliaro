"""Corre todos los guiones de harness/fixtures/*.jsonl (auto-descubrimiento, sin tocar código).

Uso:
    python harness/scripts/run_all.py                                  # fake core en proceso
    CORE_URL=http://localhost:8000 python harness/scripts/run_all.py   # contra el core por HTTP
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay import ROOT, RUN_ERRORS, configured_responder, describe, replay  # noqa: E402

FIXTURES = ROOT / "harness" / "fixtures"


def discover(fixtures: Path = FIXTURES) -> list[Path]:
    return sorted(fixtures.glob("*.jsonl"))


def main() -> int:
    scripts = discover()
    if not scripts:
        print("no hay guiones en harness/fixtures", file=sys.stderr)
        return 1
    responder = configured_responder()
    failed = 0
    for path in scripts:
        try:
            replay(path, responder)
            print(f"OK    {path.name}")
        except RUN_ERRORS as exc:
            failed += 1
            print(f"FALLA {path.name}: {describe(exc)}")
    print(f"{len(scripts) - failed}/{len(scripts)} guiones en verde")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
