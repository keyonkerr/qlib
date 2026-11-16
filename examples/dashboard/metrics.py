"""Metric conventions and derived numbers.

Every unit conversion lives here so the API and the rule engine cannot drift
apart. The conventions qlib actually uses:

- Annualisation is arithmetic (mean * 238), not compounding. See
  ``qlib/contrib/evaluate.py::risk_analysis``.
- ``ICIR`` stored by SigAnaRecord is a daily value; the familiar annualised
  figure is ``icir * sqrt(238)``.
- ``information_ratio`` stored by PortAnaRecord is already annualised.
"""

from __future__ import annotations

import math
from typing import Optional

TRADING_DAYS = 238


def annualize_icir(icir: Optional[float]) -> Optional[float]:
    if icir is None:
        return None
    return icir * math.sqrt(TRADING_DAYS)


def delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return a - b


def baseline_for(runs: list[dict], experiment: str) -> Optional[dict]:
    """The run named 00_baseline in the same experiment, newest if several."""
    hits = [r for r in runs if r["experiment"] == experiment and r["name"] == "00_baseline"]
    if not hits:
        return None
    return max(hits, key=lambda r: r["start_time"] or 0)


def cost_drag(ann_nocost: Optional[float], ann_cost: Optional[float]) -> Optional[float]:
    """How much annualised excess return trading costs eat, in return units."""
    return delta(ann_nocost, ann_cost)


def summary(run: dict, baseline: Optional[dict]) -> dict:
    """The numbers the comparison view needs, already normalised."""
    m = run["metrics"]
    b = baseline["metrics"] if baseline and baseline["run_id"] != run["run_id"] else None

    def pair(key):
        cur = m.get(key)
        base = b.get(key) if b else None
        return cur, delta(cur, base)

    ic, d_ic = pair("ic")
    icir, d_icir = pair("icir")
    ann, d_ann = pair("ann_cost")
    ir, d_ir = pair("ir_cost")
    dd, d_dd = pair("dd_cost")

    return {
        "ic": ic,
        "ic_delta": d_ic,
        "rank_ic": m.get("rank_ic"),
        "rank_icir": m.get("rank_icir"),
        "icir": icir,
        "icir_annual": annualize_icir(icir),
        "icir_delta": d_icir,
        "ann_cost": ann,
        "ann_delta": d_ann,
        "ir_cost": ir,
        "ir_delta": d_ir,
        "dd_cost": dd,
        "dd_delta": d_dd,
        "ann_nocost": m.get("ann_nocost"),
        "cost_drag": cost_drag(m.get("ann_nocost"), ann),
        "has_baseline": b is not None,
        "baseline_run_id": baseline["run_id"] if b else None,
    }
