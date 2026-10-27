"""Точность роутера единого окна на tests/cases/router.yaml.

    python -m eval.router              # каждый кейс 1 раз
    python -m eval.router --repeat 3   # кейс засчитан, только если все прогоны верны
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor

from app.config import ROOT, load_env, load_passports, read_yaml
from app.router import Router

CASES = ROOT / "tests" / "cases" / "router.yaml"


def history(case: dict) -> list[dict]:
    roles = {"Посетитель": "user", "Ассистент": "assistant"}
    return [{"role": roles[who], "content": text} for who, text in case.get("history", [])]


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser(description="Точность роутера")
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()

    router = Router(load_passports())
    cases = read_yaml(CASES)["cases"]
    jobs = [c for c in cases for _ in range(args.repeat)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        routes = list(pool.map(lambda c: router.route(c["active"], history(c), c["text"]), jobs))

    passed = 0
    for i, case in enumerate(cases):
        got = routes[i * args.repeat:(i + 1) * args.repeat]
        ok = all(r.assistant == case["expect"] for r in got)
        passed += ok
        answers = ", ".join(sorted({r.assistant for r in got}))
        note = "" if ok else f" — ждали {case['expect']}, получили {answers}: {got[0].reason}"
        print(f"  {'✅' if ok else '❌'} {case['id']} {case['active']:>7} → {answers:<14} {case['text'][:50]}{note}")
    print(f"Точность роутера: {passed}/{len(cases)} ({100 * passed / len(cases):.0f} %)")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
