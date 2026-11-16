"""Turn a run's numbers into Chinese prose.

Rules live here as plain constants and functions, not a DSL: eight checks do
not justify inventing a language. Every threshold carries a comment saying
where the number comes from, so the next person can judge whether it still
holds for a different market or frequency.

The output is deliberately stable wording — the point is comparing runs, and
prose that rewrites itself every time defeats that.
"""

from __future__ import annotations

from typing import Optional

import metrics as M

# ── thresholds ────────────────────────────────────────────────────────────
# IC: single factors on A-share daily data usually land at 0.02–0.03; a full
# multi-factor model reaching 0.04+ is usable, 0.06+ is strong for this period.
IC_WEAK = 0.03
IC_OK = 0.05
IC_STRONG = 0.065

# ICIR is compared on its annualised form, where 2.0 is roughly "signal is
# consistent enough to trade" and 4.0+ is high.
ICIR_ANNUAL_OK = 2.0
ICIR_ANNUAL_STRONG = 4.0

# Annualised information ratio after cost. Below 1.0 is hard to justify after
# real-world slippage.
IR_OK = 1.0
IR_STRONG = 1.5

# Cost drag as a share of the no-cost excess return. Above a third means
# turnover is the dominant lever, not signal quality.
DRAG_SHARE_WARN = 0.33

# Train/validation gap in L2. Above ~0.08 starts to look like overfitting for
# a strongly regularised GBDT.
L2_GAP_WARN = 0.08


def _pct(v: Optional[float], digits: int = 2) -> str:
    return "—" if v is None else f"{v * 100:.{digits}f}%"


def _f3(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.3f}"


def _pp(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v * 100:+.1f}pp"


def _num(v: Optional[float], digits: int = 4) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


# ── verdict ───────────────────────────────────────────────────────────────


def verdict(run: dict, s: dict, baseline: Optional[dict]) -> str:
    """One sentence: the most decision-relevant fact about this run."""
    ic = s["ic"]
    if ic is None:
        return f"{run['name']}：指标不完整，无法判断。"

    base = baseline["metrics"] if baseline and baseline["run_id"] != run["run_id"] else None
    if base and base.get("ic"):
        pct = (ic - base["ic"]) / abs(base["ic"]) * 100
        if abs(pct) >= 10:
            better = "高低" if pct > 0 else ""
            direction = "抬高" if pct > 0 else "拉低"
            return (
                f"{run['name']} 把 IC {direction} {abs(pct):.0f}%，"
                f"含成本年化 {_pct(s['ann_cost'])}。"
            )

    if ic >= IC_STRONG:
        return f"{run['name']} 的 IC 达 {_num(ic)}，明显高于可用线。"
    if ic >= IC_OK:
        return f"{run['name']} 的 IC 为 {_num(ic)}，处在可用区间。"
    if ic >= IC_WEAK:
        return f"{run['name']} 的 IC 为 {_num(ic)}，信号偏弱。"
    return f"{run['name']} 的 IC 仅 {_num(ic)}，信号不可用。"


def sub(run: dict, s: dict, baseline: Optional[dict]) -> str:
    """Second line: what explains the verdict."""
    parts = []

    drag = s.get("cost_drag")
    nocost = s.get("ann_nocost")
    if drag is not None and nocost:
        share = drag / nocost if nocost > 0 else 0
        if share >= DRAG_SHARE_WARN:
            parts.append(f"成本吃掉 {_pp(-drag).lstrip('+-')}，占无成本超额的 {share * 100:.0f}%")
        else:
            parts.append(f"成本损耗 {_pp(-drag).lstrip('+-')}，占比可控")

    l2t, l2v = run["metrics"].get("l2_train"), run["metrics"].get("l2_valid")
    if l2t is not None and l2v is not None:
        gap = l2v - l2t
        if gap >= L2_GAP_WARN:
            parts.append(f"验证集损失比训练集高 {gap:.3f}，需留意过拟合")
        else:
            parts.append("训练与验证损失接近，正则压得住")

    if run["params"].get("label"):
        parts.append(f"标签为 {run['params']['label']}")

    return "；".join(parts) + "。" if parts else ""


# ── notes ─────────────────────────────────────────────────────────────────


def notes(run: dict, s: dict, baseline: Optional[dict]) -> list[dict]:
    """Grouped Chinese commentary. Each entry is a titled block of paragraphs."""
    out = []
    m = run["metrics"]

    out.append(_signal_note(s))
    out.append(_return_note(s, run))
    over = _overfit_note(run)
    if over:
        out.append(over)
    void = _execution_note(m)
    if void:
        out.append(void)
    return [n for n in out if n]


def _signal_note(s: dict) -> dict:
    ic, icir_ann = s["ic"], s["icir_annual"]
    paras = []

    if ic is not None:
        if ic >= IC_STRONG:
            grade = "明显高于日频多因子的合格线（0.02~0.03）"
        elif ic >= IC_OK:
            grade = "处在日频多因子的可用区间"
        elif ic >= IC_WEAK:
            grade = "接近但略低于常用可用线（0.05）"
        else:
            grade = "基本没有预测力"
        paras.append(f"IC <b>{_num(ic)}</b>，{grade}。")

    if icir_ann is not None:
        if icir_ann >= ICIR_ANNUAL_STRONG:
            desc = "IC 在绝大多数交易日同号，稳定性高"
        elif icir_ann >= ICIR_ANNUAL_OK:
            desc = "IC 稳定性尚可，不是靠少数几天撑起来的"
        else:
            desc = "IC 波动较大，信号时灵时不灵"
        paras.append(
            f"ICIR <b>{_num(s['icir'])}</b> 是日均值，年化约 <b>{icir_ann:.2f}</b>，{desc}。"
        )

    rank_ic = s.get("rank_ic")
    if rank_ic is not None and s["ic"]:
        diff = abs(rank_ic - s["ic"])
        if diff < 0.005:
            paras.append("Rank IC 与 IC 几乎相同，说明没有极端值在主导预测。")

    delta_ic = s.get("ic_delta")
    if delta_ic is not None and s.get("has_baseline"):
        paras.append(f"相对同组基线，IC 变化 {delta_ic:+.4f}。")

    return {"title": "信号质量", "paras": paras, "warn": bool(s["ic"] is not None and s["ic"] < IC_WEAK)}


def _return_note(s: dict, run: dict) -> dict:
    paras = []
    ann, nocost, drag = s["ann_cost"], s.get("ann_nocost"), s.get("cost_drag")

    if nocost is not None and ann is not None:
        paras.append(
            f"无成本年化超额 {_pct(nocost)}，含成本 <b>{_pct(ann)}</b>，"
            f"成本损耗 <b>{_pp(-drag).lstrip('+-')}</b>。"
        )
    elif ann is not None:
        paras.append(f"含成本年化超额 <b>{_pct(ann)}</b>。")

    ir, dd = s["ir_cost"], s["dd_cost"]
    if ir is not None and dd is not None:
        if ir >= IR_STRONG:
            desc = "IR 在这一档数据里相当好"
        elif ir >= IR_OK:
            desc = "IR 过 1，已属可用"
        else:
            desc = "IR 低于 1，叠加真实滑点后难以成立"
        paras.append(f"含成本 IR <b>{ir:.2f}</b>，最大回撤 {_pct(dd)}，{desc}。")
    delta_ann = s.get("ann_delta")
    if delta_ann is not None and s.get("has_baseline"):
        paras.append(f"相对同组基线，含成本年化变化 {_pp(delta_ann)}。")

    warn = bool(ir is not None and ir < IR_OK)
    return {"title": "收益与成本", "paras": paras, "warn": warn}


def _overfit_note(run: dict) -> Optional[dict]:
    m = run["metrics"]
    train, valid = m.get("l2_train"), m.get("l2_valid")
    if train is None or valid is None:
        return None

    gap = valid - train
    paras = [f"训练集 L2 {train:.3f}，验证集 L2 {valid:.3f}，差距 {gap:.3f}。"]

    if gap >= L2_GAP_WARN:
        paras.append("差距偏大，继续加大模型容量或延长标签周期时要盯住这个数。")
        warn = True
    elif gap <= 0.01:
        paras.append("两者几乎贴合，正则明显偏强，模型容量可能没吃满——放松正则是直接的改进方向。")
        warn = False
    else:
        paras.append("差距在正常范围，过拟合风险可控。")
        warn = False

    return {"title": "过拟合检查", "paras": paras, "warn": warn}


def _execution_note(m: dict) -> Optional[dict]:
    ffr, pa, pos = m.get("ffr"), m.get("pa"), m.get("pos")
    if ffr is None and pa is None:
        return None

    # pa == 0 exactly (not merely small) is the signature of qlib falling back
    # to adjusted_price mode because factor.day.bin is missing.
    if pa == 0 and pos == 0:
        return {
            "title": "撮合指标失效",
            "paras": [
                "撮合指标 <code>pa</code> 和 <code>pos</code> 都是 0，而 <code>ffr</code> 是 "
                f"{_num(ffr, 1)}。这是数据目录缺 <code>factor.day.bin</code> 导致的：qlib 退化成"
                "复权价模式后算不出基准价，pa 恒为 0，pos 也随之归零。",
                "结论：这三个数不能用来评价成交质量。IC、收益、回撤不受影响，它们走另一条计算路径。",
            ],
            "warn": True,
        }

    if ffr is not None and ffr < 0.95:
        return {
            "title": "成交受阻",
            "paras": [
                f"成交率 {_pct(ffr, 1)}，部分委托没能成交。常见原因是涨跌停封板或流动性不足，"
                "会拉低实际收益与理论值的差距。",
            ],
            "warn": True,
        }

    return None
