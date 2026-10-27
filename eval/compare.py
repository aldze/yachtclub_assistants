"""Сравнение двух прогонов eval «до и после» правки промпта.

    python -m eval.compare <до>.json <после>.json

Файлы — build/eval_<assistant>.json из python -m eval.run (перед правкой скопируйте их в сторону).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare(before: dict, after: dict) -> str:
    old = {c["id"]: c for c in before["cases"]}
    new = {c["id"]: c for c in after["cases"]}
    sb, sa = before["summary"], after["summary"]
    lines = [
        f"{after['assistant']}: {sb['cases_passed']}/{sb['cases']} ({sb['percent']} %) → "
        f"{sa['cases_passed']}/{sa['cases']} ({sa['percent']} %), приемка "
        f"{'✅' if sb['accepted'] else '❌'} → {'✅' if sa['accepted'] else '❌'}",
    ]

    def runs(case: dict) -> str:
        return f" {case['runs_passed']}/{case['runs']}" if case.get("runs", 1) > 1 else ""

    for cid in sorted(old.keys() | new.keys()):
        was, now = old.get(cid), new.get(cid)
        if was is None or now is None:
            lines.append(f"  {cid}: {'новый кейс' if was is None else 'кейс удален'}")
        elif was["passed"] != now["passed"]:
            arrow = "исправлен ✅" if now["passed"] else "СЛОМАН ❌"
            lines.append(f"  {cid}: {arrow} (было{runs(was) or ' ' + ('✅' if was['passed'] else '❌')},"
                         f" стало{runs(now) or ' ' + ('✅' if now['passed'] else '❌')})")
            if not now["passed"]:
                lines += [f"      - {c['text']}: {c['reason']}" for c in now["criteria"] if not c["passed"]]
        elif not now["passed"]:
            lines.append(f"  {cid}: по-прежнему ❌ (было{runs(was) or ' ❌'}, стало{runs(now) or ' ❌'})")
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    print(compare(load(sys.argv[1]), load(sys.argv[2])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
