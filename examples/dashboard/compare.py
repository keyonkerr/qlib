"""Compare N runs side by side.

Pure functions over the dicts ``store.get_run`` returns — no I/O here, so the
thresholds and the "who wins" direction table can be unit-tested. The HTTP
layer only fetches runs and hands them over.

Two jobs:

1. **Align.** Every metric / param / tag / artifact across the selected runs
   becomes one row with one value per run, so the frontend renders columns
   without deciding anything.
2. **Judge.** Mark which cells differ (``same``), which cell is the best per
   row (``best``), whether the runs are comparable at all (``warnings``), and
   summarise the difference in one sentence (``verdict``).

Equality uses the same 1e-12 tolerance as ``store._aggregate``: seed repeats of
a deterministic config come out bit-identical, and a tiny float drift should
not be dressed up as a difference.
"""

from __future__ import annotations

from typing import Optional

import metrics as M

MAX_RUNS = 4
EPS = 1e-12

# ── comparability ─────────────────────────────────────────────────────────
# These decide whether two runs' numbers may be read against each other at
# all. A different test window or instrument pool makes the IC values measure
# different things; the run is still valid on its own, just not comparable.


def _id(extras: dict) -> dict:
    ds = extras.get("dataset_inputs") or {}
    seg = ds.get("segments") or {}
    cfg = extras.get("config_summary") or {}
    bt = (cfg.get("port_analysis_config") or {}).get("backtest") or {}
    qi = cfg.get("qlib_init") or {}
    return {
        "test": " → ".join(seg.get("test") or []) or bt.get("end_time"),
        "instruments": cfg.get("market") or (cfg.get("data_handler_config") or {}).get("instruments"),
        "benchmark": cfg.get("benchmark") or bt.get("benchmark"),
        "provider": qi.get("provider_uri"),
        "region": qi.get("region"),
    }


_LABELS = {
    "test": "测试段",
    "instruments": "股票池",
    "benchmark": "基准",
    "provider": "数据源",
    "region": "市场",
}


def _warnings(runs: list[dict]) -> list[dict]:
    """Fields that differ and make the metric columns non-comparable."""
    ids = [_id(r.get("extras") or {}) for r in runs]
    out = []
    for key, label in _LABELS.items():
        vals = [i.get(key) for i in ids]
        if any(v is None for v in vals):
            continue
        if len(set(vals)) > 1:
            out.append({"field": key, "label": label, "values": vals})
    return out


# ── rows ──────────────────────────────────────────────────────────────────
# (key, label, term, kind, direction)
# kind drives formatting on the client:
#   float   — 4 decimals            pct    — ×100 with 2 decimals
#   pct1    — ×100 with 1 decimal   count  — integer
#   date    — YYYY-MM-DD HH:MM
#   text    — verbatim string
# direction is +1 higher-is-better, -1 lower-is-better, 0 no winner.
#
# Fields in IDENTITY_FIELDS always differ by construction (they name the run,
# they are not measurements) — marking them ochre would drown the real signal.

IDENTITY_FIELDS = {"run_id", "start", "duration"}

SPEC = [
    ("identity", "身份", [
        ("run_id", "run id", None, "text", 0),
        ("config", "配置", None, "text", 0),
        ("seed", "seed", None, "text", 0),
        ("start", "开始时间", None, "date", 0),
        ("duration", "耗时", None, "text", 0),
        ("status", "状态", None, "text", 0),
        ("has_renders", "官方图表", None, "text", 0),
    ]),
    ("setting", "实验设置", [
        ("dataset", "数据集", None, "text", 0),
        ("handler", "数据处理器", None, "text", 0),
        ("train", "训练段", "segments", "text", 0),
        ("valid", "验证段", "segments", "text", 0),
        ("test", "测试段", "segments", "text", 0),
        ("market", "股票池", "instruments", "text", 0),
        ("benchmark", "基准", "benchmark", "text", 0),
        ("provider", "数据源", None, "text", 0),
        ("strategy", "策略", "topk", "text", 0),
        ("backtest", "回测区间", None, "text", 0),
        ("account", "初始资金", None, "count", 0),
        ("open_cost", "开仓成本", "cost", "text", 0),
        ("close_cost", "平仓成本", "cost", "text", 0),
        ("min_cost", "最低成本", "cost", "text", 0),
        ("limit_threshold", "限价阈值", None, "text", 0),
        ("deal_price", "成交价", None, "text", 0),
    ]),
    ("signal", "信号", [
        ("ic", "IC", "IC", "float", 1),
        ("icir", "ICIR", "ICIR", "float", 1),
        ("rank_ic", "Rank IC", "Rank IC", "float", 1),
        ("rank_icir", "Rank ICIR", "Rank ICIR", "float", 1),
        ("ic_pos", "IC 为正占比", "IC", "pct1", 1),
        ("ic_p05", "IC 5% 分位", "IC", "float", 1),
    ]),
    ("execution", "执行", [
        ("ffr", "ffr 成交率", "ffr", "float", 1),
        ("pa", "pa 价格优势", "pa", "float", 1),
        ("pos", "pos 胜率", "pos", "float", 1),
        ("turnover_mean", "平均换手率", "turnover", "pct", -1),
        ("cost_mean", "平均每日成本", "cost", "pct", -1),
    ]),
    ("outcome", "结果", [
        ("ann_cost", "含成本年化超额", "annualized_return", "pct", 1),
        ("ann_nocost", "无成本年化超额", "annualized_return", "pct", 1),
        ("ir_cost", "含成本 IR", "information_ratio", "float", 1),
        ("ir_nocost", "无成本 IR", "information_ratio", "float", 1),
        ("dd_cost", "最大回撤", "max_drawdown", "pct", 1),
        ("std_cost", "日超额标准差", "std", "float", -1),
        ("cost_drag", "成本损耗", "cost", "pct", 1),
    ]),
    ("training", "训练", [
        ("rounds", "训练轮数", None, "count", 0),
        ("l2_train", "末轮训练损失", "l2.train", "float", 0),
        ("l2_valid", "末轮验证损失", "l2.valid", "float", 0),
        ("l2_gap", "末轮训练-验证差距", "l2.train", "float", -1),
        ("l2_valid_min", "验证损失最低", "l2.valid", "float", -1),
    ]),
]


def _dur(ms) -> Optional[str]:
    if ms is None:
        return None
    sec = round(ms / 1000)
    return f"{sec} 秒" if sec < 60 else f"{sec // 60} 分 {sec % 60} 秒"


def _core(run: dict, extras: dict) -> dict:
    """Everything the spec rows read, flattened out of the nested dicts."""
    m = run.get("all_metrics") or {}
    s = extras.get("ic_series") or {}
    t = extras.get("turnover_series") or {}
    c = extras.get("cost_series") or {}
    cfg = extras.get("config_summary") or {}
    ds = extras.get("dataset_inputs") or {}
    seg = ds.get("segments") or {}
    port = cfg.get("port_analysis_config") or {}
    bt = port.get("backtest") or {}
    sty = port.get("strategy") or {}
    ek = bt.get("exchange_kwargs") or {}
    tc = extras.get("training_curve") or {}
    qi = cfg.get("qlib_init") or {}

    def pair(vals):
        return "  →  ".join(vals) if vals else None

    def curve_end(key):
        hist = tc.get(key) or []
        return hist[-1]["value"] if hist else None

    def curve_min(key):
        hist = tc.get(key) or []
        return min(h["value"] for h in hist) if hist else None

    ann_c = m.get("1day.excess_return_with_cost.annualized_return")
    ann_nc = m.get("1day.excess_return_without_cost.annualized_return")
    st = s.get("stats") or {}
    turn = t.get("stats") or {}
    cst = c.get("stats") or {}
    train, valid = curve_end("l2.train"), curve_end("l2.valid")

    return {
        "run_id": run["run_id"][:12],
        "config": run.get("config", {}).get("label") or run.get("name"),
        "seed": (run.get("all_params") or {}).get("model.kwargs.seed")
                or run.get("config", {}).get("seed"),
        "start": (run.get("start_time") or 0) / 1000 if run.get("start_time") else None,
        "duration": _dur(run.get("duration_ms")),
        "status": run.get("status"),
        "has_renders": "有" if run.get("renders") else "无",
        "dataset": ds.get("class"),
        "handler": ds.get("handler"),
        "train": pair(seg.get("train")),
        "valid": pair(seg.get("valid")),
        "test": pair(seg.get("test")),
        "market": cfg.get("market"),
        "benchmark": cfg.get("benchmark") or bt.get("benchmark"),
        "provider": qi.get("provider_uri"),
        "strategy": f'{sty.get("class")} · topk {(sty.get("kwargs") or {}).get("topk", "?")}'
                    if sty.get("class") else None,
        "backtest": f'{bt.get("start_time")} → {bt.get("end_time")}' if bt.get("start_time") else None,
        "account": bt.get("account"),
        "open_cost": ek.get("open_cost"),
        "close_cost": ek.get("close_cost"),
        "min_cost": ek.get("min_cost"),
        "limit_threshold": ek.get("limit_threshold"),
        "deal_price": ek.get("deal_price"),
        "ic": m.get("IC"),
        "icir": m.get("ICIR"),
        "rank_ic": m.get("Rank IC"),
        "rank_icir": m.get("Rank ICIR"),
        "ic_pos": st.get("pos_ratio"),
        "ic_p05": st.get("p05"),
        "ffr": m.get("1day.ffr"),
        "pa": m.get("1day.pa"),
        "pos": m.get("1day.pos"),
        "turnover_mean": turn.get("mean"),
        "cost_mean": cst.get("mean"),
        "ann_cost": ann_c,
        "ann_nocost": ann_nc,
        "ir_cost": m.get("1day.excess_return_with_cost.information_ratio"),
        "ir_nocost": m.get("1day.excess_return_without_cost.information_ratio"),
        "dd_cost": m.get("1day.excess_return_with_cost.max_drawdown"),
        "std_cost": m.get("1day.excess_return_with_cost.std"),
        "cost_drag": M.cost_drag(ann_nc, ann_c),
        "rounds": tc.get("rounds"),
        "l2_train": train,
        "l2_valid": valid,
        "l2_gap": valid - train if train is not None and valid is not None else None,
        "l2_valid_min": curve_min("l2.valid"),
    }


def _same(values: list) -> bool:
    present = [v for v in values if v is not None]
    if len(present) < 2:
        return True  # nothing to differ from
    first = present[0]
    if isinstance(first, (int, float)) and not isinstance(first, bool):
        return all(abs(v - first) <= EPS for v in present[1:])
    return all(v == first for v in present[1:])


def _best(values: list, direction: int) -> Optional[int]:
    if direction == 0:
        return None
    present = [(i, v) for i, v in enumerate(values) if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if len(present) < 2:
        return None
    # a row where every value ties has no winner to underline
    if _same([v for _, v in present]):
        return None
    target = max(v for _, v in present) if direction > 0 else min(v for _, v in present)
    return next(i for i, v in present if abs(v - target) <= EPS)


# ── the three big sections ─────────────────────────────────────────────────


def _rows(run_list: list[dict], cores: list[dict]) -> list[dict]:
    out = []
    for sec in SPEC:
        sec_key, sec_label, spec = sec[0], sec[1], sec[2]
        rows = []
        for key, label, term, kind, direction in spec:
            values = [c.get(key) for c in cores]
            rows.append({
                "key": key,
                "label": label,
                "term": term,
                "kind": kind,
                "values": values,
                "same": _same(values),
                "best": _best(values, direction),
                "direction": direction,
                # 身份字段天然不同，不是可比的测量值
                "always_diff": key in IDENTITY_FIELDS,
            })
        out.append({"key": sec_key, "label": sec_label, "rows": rows})
    return out


def _diff_rows(run_list: list[dict], cores: list[dict]) -> list[dict]:
    """Parameter keys that differ across runs — the 'what changed' list."""
    keys = set()
    for r in run_list:
        keys.update((r.get("all_params") or {}).keys())
    rows, identical = [], []
    for key in sorted(keys):
        if key == "cmd-sys.argv":
            continue
        values = [(r.get("all_params") or {}).get(key) for r in run_list]
        row = {
            "key": key,
            "label": _key_label(key),
            "term": _term_for(key),
            "kind": "text",
            "values": values,
            "same": _same(values),
            "best": None,
            "direction": 0,
        }
        (identical if row["same"] else rows).append(row)
    return rows, identical


def _key_label(key: str) -> str:
    """`model.kwargs.lambda_l2` -> `model.kwargs.lambda_l2`, but a bare
    `model.class` is just the model choice, which reads better as `模型`."""
    return {"model.class": "模型", "dataset.class": "数据集"}.get(key, key)


def _term_for(key: str) -> Optional[str]:
    """Map a param key to a glossary term the client already knows."""
    tail = key.split(".")[-1]
    known = {
        "num_leaves", "learning_rate", "max_depth", "lambda_l1", "lambda_l2",
        "colsample_bytree", "subsample", "instruments", "instruments",
        "seed", "loss", "class", "module_path", "topk", "n_drop",
    }
    if tail in known:
        return tail
    if "segments" in key:
        return "segments"
    if "label" in tail:
        return "label"
    if tail in ("start_time", "end_time", "fit_start_time", "fit_end_time"):
        return "segments"
    return None


def _tags(run_list: list[dict]) -> list[dict]:
    """Tags, keeping the provenance ones that explain a difference.

    ``mlflow.source.git.commit`` stays in on purpose: two runs of the same
    config on different commits are NOT the same experiment, and that is
    exactly the kind of difference the comparison page exists to surface.
    Only the per-invocation noise is dropped.
    """
    keys = set()
    for r in run_list:
        keys.update((r.get("tags") or {}).keys())
    skip = {"mlflow.user", "mlflow.runName"}
    rows = []
    for key in sorted(keys - skip):
        raw = [(r.get("tags") or {}).get(key) for r in run_list]
        # 相同判定用完整值；只在显示时截短 git commit
        same = _same(raw)
        values = [v[:8] if key == "mlflow.source.git.commit" and isinstance(v, str) else v
                  for v in raw]
        rows.append({"key": key, "values": values, "same": same})
    return rows


def _artifacts(run_list: list[dict]) -> list[dict]:
    """Files present in some runs and absent in others (the meaningful diff)."""
    sets = []
    for r in run_list:
        art = r.get("artifacts") or []
        sets.append({a["path"] for a in art if not a.get("is_dir")})
    union = set().union(*sets) if sets else set()
    rows = []
    for path in sorted(union):
        present = [path in s for s in sets]
        if all(present) or not any(present):
            continue
        rows.append({"path": path, "present": present})
    return rows


def _risk(run_list: list[dict]) -> list[dict]:
    """Risk decomposition, one row per (group, stat), one value per run."""
    out = {}
    for i, r in enumerate(run_list):
        for row in (r.get("extras") or {}).get("risk_table") or []:
            ledger = out.setdefault((row["group"], row["stat"]), [None] * len(run_list))
            ledger[i] = row["value"]
    rows = []
    for (group, stat), values in out.items():
        rows.append({
            "group": group,
            "stat": stat,
            "term": stat,
            "values": values,
            "same": _same(values),
            "best": _best(values, {"max_drawdown": 1, "std": -1, "mean": 1,
                                   "annualized_return": 1, "information_ratio": 1}.get(stat, 0)),
        })
    return rows


# ── verdict ───────────────────────────────────────────────────────────────


def _name(cores: list[dict], i: int) -> str:
    """Slot + config + seed, so two runs of the same YAML stay distinguishable."""
    c = cores[i]
    base = c["config"] or c["run_id"]
    seed = c.get("seed")
    return f"{'ABCD'[i]} {base}" + (f"(seed {seed})" if seed else "")


def _verdict(run_list: list[dict], cores: list[dict], diffs: list[dict]) -> str:
    names = [_name(cores, i) for i in range(len(cores))]
    if len(run_list) != 2:
        return (f"{len(run_list)} 个 run 并列：" + "、".join(names) +
                "。不同点用赭色标出，行内最优带靛蓝下划线。")

    if not diffs:
        # same config, possibly different seed
        a, b = cores
        if a["ic"] is not None and b["ic"] is not None and abs(a["ic"] - b["ic"]) > EPS:
            return (f"两份 run 的配置参数完全相同，只有结果不同（IC {a['ic']:.4f} vs {b['ic']:.4f}）。"
                    "这属于同配置重跑，差异来自训练随机性或时间窗口，不是配置改动。")
        return "两份 run 的配置与结果完全一致，是同一次配置的重复运行。"

    changed = [d["label"] for d in diffs if d["key"] != "model.module_path"]
    if not changed:
        changed = [d["label"] for d in diffs]
    if len(changed) > 3:
        head = "、".join(changed[:3]) + f" 等 {len(changed)} 项"
    else:
        head = "、".join(changed)

    parts = [f"{_name(cores, 0)} → {_name(cores, 1)} 相差：{head}。"]
    a, b = cores
    deltas = []
    if a["ic"] is not None and b["ic"] is not None:
        deltas.append(f"IC {a['ic']:.4f} → {b['ic']:.4f}（{b['ic'] - a['ic']:+.4f}）")
    if a["ann_cost"] is not None and b["ann_cost"] is not None:
        deltas.append(f"含成本年化 {a['ann_cost'] * 100:.2f}% → {b['ann_cost'] * 100:.2f}%")
    if deltas:
        parts.append("；".join(deltas) + "。")
    parts.append("若差异幅度不超过该配置的种子极差，还不能算配置真的起作用。")
    return "".join(parts)


# ── series for the overlay plates ─────────────────────────────────────────

# Each chart is (id, title, note, path into extras, which sub-series).
CHARTS = [
    ("ic", "IC 逐日走势", "同一个交易日，各 run 的模型打分与真实涨跌的相关性。零线以上是当天方向对。",
     ("ic_series", None)),
    ("rank_ic", "Rank IC 逐日走势", "只看排名的相关性。和上一张贴得越近，说明预测没有被极端值主导。",
     ("rank_ic_series", None)),
    ("excess", "累计超额收益（含成本）", "逐日超额 = 持仓收益 − 基准 − 成本，再累加。线的斜率就是赚超额的快慢。",
     ("excess_series", "with_cost")),
    ("turnover", "逐日换手率", "每天换掉多少仓位。形状相同的两条线说它们调仓节奏一样。",
     ("turnover_series", None)),
    ("cost", "逐日交易成本", "手续费加印花税。和换手率一起看，高换手的那几天成本会一起冲高。",
     ("cost_series", None)),
    ("l2_valid", "验证损失，逐轮", "验证集上的 L2。过早回升意味着后面的轮次在拟合噪声。",
     ("training_curve", "l2.valid")),
]


def _chart(run_list: list[dict], chart) -> dict:
    """Chart series aligned by date (or by round, for training curves).

    Aligning by array index would silently shift a line whenever one run's
    backtest starts later; the union of dates keeps every point on its own
    day and leaves the missing ones as null.
    """
    cid, title, note, (path, sub) = chart
    raw = []   # (run_id, [(label, value), ...])
    for r in run_list:
        x = r.get("extras") or {}
        obj = x.get(path)
        if path == "training_curve":
            hist = (obj or {}).get(sub) or []
            raw.append((r["run_id"], [(str(h["step"]), h["value"]) for h in hist]))
            continue
        if path == "excess_series":
            obj = (obj or {}).get(sub)
        if not obj or not obj.get("values"):
            raw.append((r["run_id"], None))
            continue
        raw.append((r["run_id"], list(zip(obj.get("dates") or [], obj["values"]))))

    labels = []
    seen = set()
    for _, pts in raw:
        for label, _v in pts or []:
            if label not in seen:
                seen.add(label)
                labels.append(label)

    series = []
    for run_id, pts in raw:
        if not pts:
            series.append({"run_id": run_id, "dates": None, "values": None})
            continue
        lookup = dict(pts)
        series.append({
            "run_id": run_id,
            "dates": labels,
            "values": [lookup.get(label) for label in labels],
        })
    return {"id": cid, "title": title, "note": note, "labels": labels, "series": series}


# ── entry point ───────────────────────────────────────────────────────────


def build(run_list: list[dict]) -> dict:
    """One comparison payload for the client.

    ``run_list`` comes from ``store.get_run`` in the order the user picked.
    """
    if len(run_list) > MAX_RUNS:
        raise ValueError(f"at most {MAX_RUNS} runs can be compared")
    cores = [_core(r, r.get("extras") or {}) for r in run_list]
    diff, same = _diff_rows(run_list, cores)

    return {
        "runs": [
            {
                "run_id": r["run_id"],
                "name": r.get("name"),
                "config": r.get("config", {}).get("label"),
                "seed": (r.get("all_params") or {}).get("model.kwargs.seed")
                        or r.get("config", {}).get("seed"),
                "experiment": r.get("experiment"),
                "start_time": r.get("start_time"),
                "slot": "ABCD"[i],
            }
            for i, r in enumerate(run_list)
        ],
        "warnings": _warnings(run_list),
        "sections": _rows(run_list, cores),
        "param_diff": diff,
        "param_same": same,
        "tags": _tags(run_list),
        "artifacts": _artifacts(run_list),
        "risk": _risk(run_list),
        "charts": [_chart(run_list, c) for c in CHARTS],
        "verdict": _verdict(run_list, cores, diff),
        "yaml": _yaml_pair(run_list),
    }


def _yaml_pair(run_list: list[dict]) -> Optional[dict]:
    """Both YAML texts, only when exactly two runs have one (client diffs)."""
    if len(run_list) != 2:
        return None
    out = []
    for r in run_list:
        y = (r.get("extras") or {}).get("config_yaml")
        if not y:
            return None
        out.append({"run_id": r["run_id"], "path": y.get("path"), "source": y.get("source"),
                    "text": y.get("text")})
    return {"a": out[0], "b": out[1]}
