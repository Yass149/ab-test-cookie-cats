#!/usr/bin/env python
"""Assert that every headline number in README.md still matches results.json.

Notebook 01 writes results.json when it runs. This script checks the prose
against it, so the README cannot quietly drift away from the analysis after a
re-run. Run it after executing the notebooks:

    python scripts/check_consistency.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    results_path = ROOT / "results.json"
    if not results_path.exists():
        print("results.json not found - run notebooks/01_cookie_cats_ab_test.ipynb first.")
        return 1

    r = json.loads(results_path.read_text())

    # The README uses typographic minus signs (U+2212); Python formats ASCII
    # hyphens. Normalise so typography cannot trigger a false alarm.
    def normalise(text: str) -> str:
        return text.replace("\u2212", "-").replace("\u2013", "-")

    readme = normalise((ROOT / "README.md").read_text())

    d7, d1 = r["metrics"]["retention_7"], r["metrics"]["retention_1"]

    # Each claim is (description, the exact string the README must contain).
    claims: list[tuple[str, str]] = [
        ("total users", f"{r['n_total']:,}"),
        ("control n", f"{r['n_control']:,}"),
        ("treatment n", f"{r['n_treatment']:,}"),
        ("SRM p-value", f"p = {r['srm_p']:.4f}"),
        ("SRM chi-square", f"{r['srm_chi2']:.2f}"),
        ("day-7 control rate", f"{d7['control_rate']:.2%}"),
        ("day-7 treatment rate", f"{d7['treatment_rate']:.2%}"),
        ("day-7 absolute effect", f"{d7['absolute_diff_pp']:.2f}pp"),
        ("day-7 interval", f"[{d7['ci_pp'][0]:.2f}, {d7['ci_pp'][1]:.2f}]pp"),
        ("day-7 relative effect", f"{abs(d7['relative_diff']):.1%}"),
        ("day-7 p-value", f"p = {d7['p_value']:.4f}"),
        ("day-7 Holm p-value", f"{r['holm_p']['retention_7']:.4f}"),
        ("day-1 absolute effect", f"{d1['absolute_diff_pp']:.2f}pp"),
        ("day-1 interval", f"[{d1['ci_pp'][0]:.2f}, +{d1['ci_pp'][1]:.2f}]pp"),
        ("day-1 control rate", f"{d1['control_rate']:.2%}"),
        ("day-1 treatment rate", f"{d1['treatment_rate']:.2%}"),
        ("peeking false positive rate", f"{r['peeking_false_positive_rate']:.1%}"),
        ("day-7 MDE", f"{abs(r['mde_relative']['retention_7']):.1%}"),
        ("day-1 MDE", f"{abs(r['mde_relative']['retention_1']):.1%}"),
        ("zero-round guardrail p", f"p = {r['guardrails']['zero_round_share_p']:.2f}"),
        ("rounds-played guardrail p", f"p = {r['guardrails']['mann_whitney_p_rounds']:.4f}"),
    ]

    failures = [(what, expected) for what, expected in claims if normalise(expected) not in readme]

    for what, expected in claims:
        status = "FAIL" if (what, expected) in failures else "ok  "
        print(f"  [{status}] {what}: {expected!r}")

    if failures:
        print(f"\n{len(failures)} README claim(s) no longer match results.json.")
        return 1

    if r["recommendation"] != "do not ship":
        print(f"\nRecommendation changed to {r['recommendation']!r} - the README needs rewriting.")
        return 1

    print(f"\nAll {len(claims)} headline numbers in README.md match results.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
