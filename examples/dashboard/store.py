"""Read mlruns into plain dicts.

The only module that imports mlflow. Everything downstream works with dicts,
so switching the tracking backend (file store -> sqlite / server) is a change
confined to this file.
"""

from __future__ import annotations

import gzip
import os
import pickle
import re
import warnings
from pathlib import Path
from typing import Optional

warnings.filterwarnings("ignore", category=FutureWarning, module="mlflow")

from mlflow.tracking import MlflowClient  # noqa: E402

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
MLRUNS = EXAMPLES / "mlruns"

# Metric keys as qlib's SignalRecord / PortAnaRecord write them.
K_IC = "IC"
K_ICIR = "ICIR"
K_RIC = "Rank IC"
K_RICIR = "Rank ICIR"
K_ANN_C = "1day.excess_return_with_cost.annualized_return"
K_IR_C = "1day.excess_return_with_cost.information_ratio"
K_DD_C = "1day.excess_return_with_cost.max_drawdown"
K_ANN_NC = "1day.excess_return_without_cost.annualized_return"
K_IR_NC = "1day.excess_return_without_cost.information_ratio"
K_STD_C = "1day.excess_return_with_cost.std"
K_FFR = "1day.ffr"
K_PA = "1day.pa"
K_POS = "1day.pos"
K_L2_TRAIN = "l2.train"
K_L2_VALID = "l2.valid"

RENDER_DIR = "renders"
RENDER_FILE = "report.html"


def client() -> MlflowClient:
    return MlflowClient(tracking_uri=f"file:{MLRUNS}")


def _run_name(params: dict, run_id: str) -> str:
    argv = params.get("cmd-sys.argv", "")
    if not argv:
        return run_id[:8]
    tail = argv.split("/")[-1]
    return tail.replace(".yaml", "") or run_id[:8]


# ── config identity ───────────────────────────────────────────────────────
# The YAML is the unit of work, not the run: one config is normally run more
# than once (seed repeats, re-runs). Grouping by run hides that; grouping by
# the config file shows whether a difference comes from the config or from
# run-to-run noise.

SEED_SUFFIX_RE = re.compile(r"_s(\d+)$")


def _config_identity(params: dict, run_id: str) -> dict:
    """Group key for runs that used the same YAML.

    `run_all.py --repeat` writes `<name>_s<seed>.yaml` and deletes it after the
    run, so the seed has to be read out of the filename before it becomes the
    group key — otherwise every repeat would look like its own config.
    """
    raw = _run_name(params, run_id)
    m = SEED_SUFFIX_RE.search(raw)
    seed = m.group(1) if m else None
    base = SEED_SUFFIX_RE.sub("", raw) if m else raw
    return {"key": base, "label": base, "seed": seed, "file": raw}


def _label(params: dict) -> Optional[str]:
    raw = params.get("dataset.kwargs.handler.kwargs.label")
    if not raw:
        return None
    # qlib stores the expression as "['Ref($close, -6)/Ref($close, -1) - 1']"
    inner = raw.strip("[]'\" ")
    return inner or None


def _has_renders(exp_id: str, run_id: str) -> bool:
    return (MLRUNS / exp_id / run_id / "artifacts" / RENDER_DIR / RENDER_FILE).is_file()


def _row(exp_name: str, exp_id: str, run) -> dict:
    m = run.data.metrics
    p = run.data.params
    cfg = _config_identity(p, run.info.run_id)
    return {
        "run_id": run.info.run_id,
        "experiment": exp_name,
        "experiment_id": exp_id,
        "name": _run_name(p, run.info.run_id),
        "config": cfg,
        "start_time": run.info.start_time,
        "status": run.info.status,
        "has_renders": _has_renders(exp_id, run.info.run_id),
        "metrics": {
            "ic": m.get(K_IC),
            "icir": m.get(K_ICIR),
            "rank_ic": m.get(K_RIC),
            "rank_icir": m.get(K_RICIR),
            "ann_cost": m.get(K_ANN_C),
            "ir_cost": m.get(K_IR_C),
            "dd_cost": m.get(K_DD_C),
            "ann_nocost": m.get(K_ANN_NC),
            "ir_nocost": m.get(K_IR_NC),
            "std_cost": m.get(K_STD_C),
            "ffr": m.get(K_FFR),
            "pa": m.get(K_PA),
            "pos": m.get(K_POS),
            "l2_train": m.get(K_L2_TRAIN),
            "l2_valid": m.get(K_L2_VALID),
        },
        "params": {
            "label": _label(p),
            "num_leaves": p.get("model.kwargs.num_leaves"),
            "learning_rate": p.get("model.kwargs.learning_rate"),
            "lambda_l1": p.get("model.kwargs.lambda_l1"),
            "lambda_l2": p.get("model.kwargs.lambda_l2"),
            "train": p.get("dataset.kwargs.segments.train"),
            "valid": p.get("dataset.kwargs.segments.valid"),
            "test": p.get("dataset.kwargs.segments.test"),
            "instruments": p.get("dataset.kwargs.handler.kwargs.instruments"),
        },
        # 详情页的完整记录：照抄 MLflow 存下来的全部条目，不做筛选和换算。
        "all_metrics": dict(sorted(m.items())),
        "all_params": dict(sorted(p.items())),
        "tags": dict(sorted(run.data.tags.items())),
    }


def list_runs() -> list[dict]:
    """Every finished run across all experiments, newest first within an experiment."""
    c = client()
    rows = []
    for exp in c.search_experiments():
        if exp.name == "Default":
            continue
        for run in c.search_runs(experiment_ids=[exp.experiment_id]):
            rows.append(_row(exp.name, exp.experiment_id, run))
    rows.sort(key=lambda r: (r["experiment"], -(r["start_time"] or 0)))
    return rows


# 一个配置反复跑时，判断「改配置带来的差异」和「随机种子带来的差异」需要
# 知道同一配置多次运行之间的波动范围。这里按配置聚合并给出极差。
_NOISE_KEYS = ("ic", "ann_cost", "ir_cost", "dd_cost")


def _aggregate(runs: list[dict]) -> dict:
    out = {}
    for k in _NOISE_KEYS:
        vals = [r["metrics"].get(k) for r in runs]
        vals = [v for v in vals if v is not None]
        if len(vals) < 2:
            continue
        spread = max(vals) - min(vals)
        # 浮点累加误差不该显示成「有波动」：小于 1e-12 视为完全相同
        if spread < 1e-12:
            spread = 0.0
        out[k] = {
            "n": len(vals),
            "min": min(vals),
            "max": max(vals),
            "mean": sum(vals) / len(vals),
            "spread": spread,
        }
    return out


def list_configs() -> list[dict]:
    """Runs grouped by the YAML they used — the unit someone actually edits.

    Within one config, repeats (seed variants) are collapsed: the group key is
    the base name, the seed is carried on the run.
    """
    groups: dict[tuple, list[dict]] = {}
    for r in list_runs():
        if r["status"] != "FINISHED":
            continue
        cfg = r["config"]
        groups.setdefault((r["experiment"], cfg["key"]), []).append(r)

    out = []
    for (exp, key), runs in groups.items():
        runs.sort(key=lambda r: -(r["start_time"] or 0))
        newest = runs[0]
        seeds = sorted({r["config"]["seed"] for r in runs if r["config"]["seed"] is not None})
        out.append({
            "key": key,
            "label": newest["config"]["label"],
            "experiment": exp,
            "runs": runs,
            "run_count": len(runs),
            "seeds": seeds,
            "newest_run_id": newest["run_id"],
            "aggregate": _aggregate(runs),
            "has_renders": any(r["has_renders"] for r in runs),
            "summary": newest["metrics"],
        })
    out.sort(key=lambda g: (g["experiment"], -(g["runs"][0]["start_time"] or 0)))
    return out


def _artifacts(run_id: str) -> list[dict]:
    """Flat list of every artifact under the run, with its size."""
    c = client()
    out = []

    def walk(prefix: str) -> None:
        for art in c.list_artifacts(run_id, prefix):
            if art.is_dir:
                out.append({"path": art.path, "is_dir": True, "size": None})
                walk(art.path)
            else:
                out.append({"path": art.path, "is_dir": False, "size": art.file_size})

    walk("")
    out.sort(key=lambda a: (a["path"].count("/"), a["path"]))
    return out


# ── artifact contents ─────────────────────────────────────────────────────
# qlib logs pickle blobs; the numbers a reader actually wants (training curve,
# daily IC/return series, risk table) live inside them, not in the mlflow
# metric table. Reads are confined to the run's own artifacts directory.

MAX_SERIES_POINTS = 400


def _art_dir(exp_id: str, run_id: str) -> Path:
    return MLRUNS / exp_id / run_id / "artifacts"


def _load_pickle(path: Path):
    raw = path.read_bytes()
    try:
        return pickle.loads(raw)
    except Exception:
        return pickle.loads(gzip.decompress(raw))


def _downsample(values: list, n: int = MAX_SERIES_POINTS) -> list:
    if len(values) <= n:
        return [None if v != v else v for v in values]  # NaN -> None for JSON
    step = len(values) / n
    return [values[min(int(i * step), len(values) - 1)] for i in range(n)]


def _series_from_obj(obj) -> Optional[dict]:
    """A pandas Series as {dates, values, stats}.

    ``values`` is downsampled for the sparkline, but ``stats`` is computed on
    the full series so the summary numbers match the pickle exactly.
    """
    try:
        dates = [str(d)[:10] for d in obj.index]

        out_vals = []
        for v in obj.tolist():
            try:
                f = float(v)
                out_vals.append(None if f != f else f)
            except (TypeError, ValueError):
                out_vals.append(None)

        finite = [v for v in out_vals if v is not None]
        stats = None
        if finite:
            ordered = sorted(finite)
            n = len(ordered)

            def q(p: float) -> float:
                return ordered[min(n - 1, int(p * n))]

            stats = {
                "n": len(out_vals),
                "mean": sum(finite) / n,
                "std": (sum((v - sum(finite) / n) ** 2 for v in finite) / n) ** 0.5,
                "min": ordered[0],
                "p05": q(0.05),
                "p95": q(0.95),
                "max": ordered[-1],
                "pos_ratio": len([v for v in finite if v > 0]) / n,
            }

        if len(dates) > MAX_SERIES_POINTS:
            step = len(dates) / MAX_SERIES_POINTS
            pick = [min(int(i * step), len(dates) - 1) for i in range(MAX_SERIES_POINTS)]
            dates = [dates[i] for i in pick]
            out_vals = [out_vals[i] for i in pick]

        return {"dates": dates, "values": out_vals, "total": len(obj), "stats": stats}
    except Exception:
        return None


def _series_from_pickle(exp_id: str, run_id: str, rel: str, column: Optional[str] = None) -> Optional[dict]:
    path = _art_dir(exp_id, run_id) / rel
    if not path.is_file():
        return None
    try:
        obj = _load_pickle(path)
        if column is not None:
            obj = obj[column]
        return _series_from_obj(obj)
    except Exception:
        return None


def _excess_series(exp_id: str, run_id: str) -> dict:
    """Cumulative excess return, with and without cost.

    qlib computes the daily excess exactly as ``return - bench`` and
    ``return - bench - cost`` (qlib/workflow/record_temp.py), so this follows
    the same convention. The cumulative sum is done on the *full* series and
    only then downsampled — the comparison view must not reshape the curve.
    """
    path = _art_dir(exp_id, run_id) / "portfolio_analysis" / "report_normal_1day.pkl"
    out = {"without_cost": None, "with_cost": None}
    if not path.is_file():
        return out
    try:
        df = _load_pickle(path)
        ret = df["return"]
        bench = df["bench"]
    except Exception:
        return out

    # 两列各自取，缺 cost 列时无成本那条仍然画得出来
    try:
        out["without_cost"] = _series_from_obj((ret - bench).cumsum())
    except Exception:
        out["without_cost"] = None
    try:
        out["with_cost"] = _series_from_obj((ret - bench - df["cost"]).cumsum())
    except Exception:
        out["with_cost"] = None
    return out


def _training_curve(run_id: str) -> Optional[dict]:
    """qlib logs l2.train / l2.valid on every boosting round."""
    c = client()
    out = {}
    for key in (K_L2_TRAIN, K_L2_VALID):
        hist = c.get_metric_history(run_id, key)
        if hist:
            out[key] = [
                {"step": h.step, "value": h.value, "timestamp": h.timestamp}
                for h in hist
            ]
    if not out:
        return None
    n = max(len(v) for v in out.values())
    out["rounds"] = n
    return out


def _risk_table(exp_id: str, run_id: str) -> Optional[list[dict]]:
    """port_analysis_1day.pkl: the excess-return risk decomposition."""
    path = _art_dir(exp_id, run_id) / "portfolio_analysis" / "port_analysis_1day.pkl"
    if not path.is_file():
        return None
    try:
        df = _load_pickle(path)
        rows = []
        for idx, row in df.iterrows():
            group, stat = (idx if isinstance(idx, tuple) else ("", idx))
            try:
                val = float(row.iloc[0])
            except (TypeError, ValueError):
                val = None
            rows.append({"group": str(group), "stat": str(stat), "value": val})
        return rows
    except Exception:
        return None


def _dataset_inputs(exp_id: str, run_id: str) -> Optional[dict]:
    """The serialized DatasetH: handler class and the three date segments."""
    path = _art_dir(exp_id, run_id) / "dataset"
    if not path.is_file():
        return None
    try:
        ds = _load_pickle(path)
        segs = getattr(ds, "segments", None) or {}
        return {
            "class": type(ds).__name__,
            "handler": type(getattr(ds, "handler", None)).__name__,
            "segments": {
                k: [str(x)[:10] for x in v] for k, v in segs.items()
            },
        }
    except Exception:
        return None


def _text_artifact(exp_id: str, run_id: str, name: str, limit: int = 20000) -> Optional[str]:
    path = _art_dir(exp_id, run_id) / name
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[:limit] if text else None
    except Exception:
        return None


# ── 撮合过程序列 ──────────────────────────────────────────────────────────
# indicators_normal_1day.pkl 是 871×6 的 DataFrame（ffr/pa/pos/deal_amount/
# value/count）。此前只取了 ffr/pa/pos 三个聚合数，逐日的成交金额和笔数
# 一直没有暴露出来——而它们才是「执行质量」的过程证据。


def _indicators_frame(exp_id: str, run_id: str) -> Optional[dict]:
    path = _art_dir(exp_id, run_id) / "portfolio_analysis" / "indicators_normal_1day.pkl"
    if not path.is_file():
        return None
    try:
        df = _load_pickle(path)
    except Exception:
        return None
    out = {}
    for col in ("deal_amount", "value", "count", "ffr"):
        if col not in df.columns:
            continue
        try:
            out[col] = _series_from_obj(df[col])
        except Exception:
            continue
    return out or None


# ── 模型身份与超参 ────────────────────────────────────────────────────────
# 前端此前硬编码了 LightGBM 的 7 个键，跑 DoubleEnsemble 时真正定义模型的
# 参数（num_models/epochs/enable_sr/...）一个都显示不出来。改成按
# model.class 从 params 里动态取该模型类型的键，新模型加进白名单即可。

MODEL_HYPER_KEYS = {
    "LGBModel": [
        "loss", "learning_rate", "num_leaves", "max_depth", "colsample_bytree",
        "subsample", "lambda_l1", "lambda_l2", "num_threads", "seed",
    ],
    "DEnsembleModel": [
        "base_model", "loss", "num_models", "epochs", "enable_sr", "enable_fs",
        "alpha1", "alpha2", "bins_sr", "bins_fs", "decay", "sample_ratios",
        "sub_weights", "learning_rate", "num_leaves", "max_depth",
        "colsample_bytree", "subsample", "lambda_l1", "lambda_l2",
        "num_threads", "seed", "verbosity",
    ],
    "LinearModel": ["estimator"],
    "XGBModel": [
        "learning_rate", "max_depth", "n_estimators", "subsample",
        "colsample_bytree", "reg_alpha", "reg_lambda", "seed",
    ],
    "CatBoostModel": [
        "loss_function", "learning_rate", "depth", "iterations", "l2_leaf_reg", "random_seed",
    ],
}


def _model_info(params: dict) -> Optional[dict]:
    """模型类别 + 该类别下实际记录的超参。"""
    cls = params.get("model.class")
    if not cls:
        return None
    prefix = "model.kwargs."
    hyper = {}
    keys = MODEL_HYPER_KEYS.get(cls)
    if keys:
        for k in keys:
            v = params.get(prefix + k)
            if v is not None:
                hyper[k] = v
    else:
        # 未知模型：把所有 model.kwargs.* 原样给出，至少不丢信息
        for k, v in params.items():
            if k.startswith(prefix):
                hyper[k[len(prefix):]] = v
    return {
        "class": cls,
        "module_path": params.get("model.module_path"),
        "hyper": hyper,
        "known": keys is not None,
    }


# ── 完整配置树 ────────────────────────────────────────────────────────────
# config_summary 只留了 5 个顶层键。这里给出解析后的完整配置（含 task 树），
# 让「YAML 里的锚点 *market 到底解析成了什么」也能查到。

CONFIG_FULL_MAX_DEPTH = 8
CONFIG_FULL_MAX_ITEMS = 200


def _trim(v, depth: int = 0):
    if depth > CONFIG_FULL_MAX_DEPTH:
        return "…"
    if isinstance(v, dict):
        items = list(v.items())[:CONFIG_FULL_MAX_ITEMS]
        out = {str(k): _trim(x, depth + 1) for k, x in items}
        if len(v) > len(items):
            out["…"] = f"（还有 {len(v) - len(items)} 项）"
        return out
    if isinstance(v, (list, tuple)):
        vals = list(v)[:CONFIG_FULL_MAX_ITEMS]
        out = [_trim(x, depth + 1) for x in vals]
        if len(v) > len(vals):
            out.append(f"…（还有 {len(v) - len(vals)} 项）")
        return out
    return _jsonable(v, depth)


def _config_full(exp_id: str, run_id: str) -> Optional[dict]:
    path = _art_dir(exp_id, run_id) / "config"
    if not path.is_file():
        return None
    try:
        cfg = _load_pickle(path)
        if not isinstance(cfg, dict):
            return None
        return _trim(cfg)
    except Exception:
        return None


# ── 预测分数与持仓明细 ────────────────────────────────────────────────────
# 这是自建看板相对 MLflow 界面的核心增量：每次训练产出的 26 万条逐日分数、
# 871 天的实际持仓，MLflow 只显示聚合后的 IC，看不到「今天选了什么、买了
# 什么」。pred.pkl 3MB / positions 3MB，按日期切片后单次只有几百行，
# 所以用进程内 LRU 缓存，避免每次请求都反序列化。

from collections import OrderedDict as _OrderedDict

_PRED_CACHE: "_OrderedDict[tuple, object]" = _OrderedDict()
_POS_CACHE: "_OrderedDict[tuple, object]" = _OrderedDict()
_LABEL_CACHE: "_OrderedDict[tuple, object]" = _OrderedDict()
_CACHE_MAX = 4  # 最多缓存几个 run 的数据（每个约 3–30MB）


def _cached(cache: _OrderedDict, key: tuple, loader):
    if key in cache:
        cache.move_to_end(key)
        return cache[key]
    obj = loader()
    if obj is not None:
        cache[key] = obj
        while len(cache) > _CACHE_MAX:
            cache.popitem(last=False)
    return obj


def _load_pred(exp_id: str, run_id: str):
    def loader():
        p = _art_dir(exp_id, run_id) / "pred.pkl"
        return _load_pickle(p) if p.is_file() else None
    return _cached(_PRED_CACHE, (exp_id, run_id), loader)


def _load_label(exp_id: str, run_id: str):
    def loader():
        p = _art_dir(exp_id, run_id) / "label.pkl"
        return _load_pickle(p) if p.is_file() else None
    return _cached(_LABEL_CACHE, (exp_id, run_id), loader)


def _load_positions(exp_id: str, run_id: str):
    def loader():
        p = _art_dir(exp_id, run_id) / "portfolio_analysis" / "positions_normal_1day.pkl"
        return _load_pickle(p) if p.is_file() else None
    return _cached(_POS_CACHE, (exp_id, run_id), loader)


def pred_dates(exp_id: str, run_id: str) -> list[str]:
    pred = _load_pred(exp_id, run_id)
    if pred is None:
        return []
    try:
        return [str(d)[:10] for d in pred.index.get_level_values(0).unique()]
    except Exception:
        return []


def _score_frame(exp_id: str, run_id: str, date: Optional[str]):
    """pred.pkl 的单日切片，返回 ``(scores, days, day)``。

    默认取**倒数第二个**交易日：最后一天的分数还没有对应的执行日，
    没法标「持有 / 未持有」，而前一日的分数可以完整对照次日持仓。
    """
    pred = _load_pred(exp_id, run_id)
    if pred is None:
        return None
    try:
        days = [str(d)[:10] for d in pred.index.get_level_values(0).unique()]
    except Exception:
        return None
    if not days:
        return None
    if date in days:
        day = date
    elif len(days) >= 2:
        day = days[-2]
    else:
        day = days[-1]
    try:
        df = pred.xs(day, level=0)
    except Exception:
        return None
    col = "score" if "score" in df.columns else df.columns[0]
    return df[col], days, day


def predictions(exp_id: str, run_id: str, date: Optional[str] = None, top: Optional[int] = None) -> Optional[dict]:
    """某一天的逐股预测分数。

    top=None 返回全部；top=N 只返回分数最高的 N 只。排名按当日分数降序。

    同时给出「这些分数被用于哪个交易日的持仓」（``exec_date``）以及该日
    实际持有集合（``held``）。两者相差一个交易日，因为
    TopkDropoutStrategy 在 T 日交易时用 T-1 日信号（signal_strategy.py:141
    的 ``shift=1``）。前端据此给每行打「持有 / 未持有」标签，避免拿错日子。
    """
    got = _score_frame(exp_id, run_id, date)
    if got is None:
        return None
    scores, days, day = got
    s = scores.sort_values(ascending=False)
    total = len(s)
    if top is not None and top > 0:
        s = s.head(top)

    rows = []
    for i, (inst, val) in enumerate(s.items(), start=1):
        f = None
        try:
            fv = float(val)
            f = None if fv != fv else fv
        except (TypeError, ValueError):
            pass
        rows.append({"rank": i, "instrument": str(inst), "score": f})

    full = scores.astype(float)
    finite = full[full.notna()]
    stats = None
    if len(finite):
        ordered = sorted(finite.tolist())
        n = len(ordered)

        def q(p: float) -> float:
            return ordered[min(n - 1, int(p * n))]

        stats = {
            "n": int(len(finite)),
            "mean": sum(ordered) / n,
            "min": ordered[0],
            "p05": q(0.05),
            "median": q(0.50),
            "p95": q(0.95),
            "max": ordered[-1],
            "pos_ratio": len([v for v in ordered if v > 0]) / n,
        }

    # 这批分数被拿去交易的那一天，以及那天收盘后的真实持仓
    idx = days.index(day)
    exec_date = days[idx + 1] if idx + 1 < len(days) else None
    held = None
    if exec_date is not None:
        pos = positions(exp_id, run_id, exec_date)
        if pos is not None:
            held = {h["instrument"] for h in pos["holdings"]}

    return {
        "date": day,
        "dates": days,
        "index": idx,
        "exec_date": exec_date,
        "held": sorted(held) if held is not None else None,
        "total": total,
        "shown": len(rows),
        "rows": rows,
        "stats": stats,
    }


def positions(exp_id: str, run_id: str, date: Optional[str] = None) -> Optional[dict]:
    """某一天的账户状态与实际持仓。

    同时给出交易该日所用的信号日及其 topk（qlib 的 TopkDropoutStrategy 在
    signal_strategy.py:141 用 ``shift=1`` 取信号：D 日交易用的是 D-1 日分数），
    让「分数 top50」和「实际持仓」的对比建立在正确的时间错位上。

    date 为空时取最后一个交易日；这与 predictions 的默认值不同——那边取
    倒数第二天是为了能标出「次日持有状态」，这里取最后一天是因为那是
    账本最完整的时点。前端用 predictions.exec_date 对齐两者。
    """
    pos = _load_positions(exp_id, run_id)
    if pos is None:
        return None
    try:
        days = [str(d)[:10] for d in pos.keys()]
    except Exception:
        return None
    if not days:
        return None
    day = date if date in days else days[-1]

    original_key = None
    for k in pos.keys():
        if str(k)[:10] == day:
            original_key = k
            break
    if original_key is None:
        return None
    p = pos[original_key]
    raw = getattr(p, "position", {}) or {}

    cash = raw.get("cash")
    total = raw.get("now_account_value")
    holdings = []
    for code, v in raw.items():
        if code in ("cash", "now_account_value") or not isinstance(v, dict):
            continue

        def f(x):
            try:
                xv = float(x)
                return None if xv != xv else xv
            except (TypeError, ValueError):
                return None

        holdings.append({
            "instrument": str(code),
            "amount": f(v.get("amount")),
            "price": f(v.get("price")),
            "weight": f(v.get("weight")),
            "count_day": v.get("count_day"),
        })
    holdings.sort(key=lambda r: -(r["amount"] or 0))

    idx = days.index(day)
    signal_day = days[idx - 1] if idx > 0 else None
    return {
        "date": day,
        "dates": days,
        "index": idx,
        "signal_date": signal_day,
        "cash": float(cash) if cash is not None else None,
        "total_value": float(total) if total is not None else None,
        "holdings": holdings,
        "count": len(holdings),
    }


def position_signal_compare(
    exp_id: str, run_id: str, date: Optional[str] = None, topk: int = 50
) -> Optional[dict]:
    """持仓日 D 的实际持仓 vs 交易该日所用信号日 D-1 的 topK。

    两个集合的差集各有含义：
      - ``bought_from_top``：「信号 topK 里、且已持有」——策略认可且拿住了
      - ``top_not_held``  ：「信号 topK 里、但没持有」——受 n_drop 每日换手上限
                            所限还在排队，不是策略出错
      - ``held_not_top`` ：「持有但已跌出信号 topK」——尚未被换掉
    """
    p = positions(exp_id, run_id, date)
    if p is None:
        return None
    sig_day = p["signal_date"]
    if sig_day is None:
        return {"date": p["date"], "signal_date": None, "available": False}

    got = _score_frame(exp_id, run_id, sig_day)
    if got is None:
        return {"date": p["date"], "signal_date": sig_day, "available": False}
    scores, _days, _day = got
    s = scores.astype(float).sort_values(ascending=False)
    top = [str(x) for x in s.head(topk).index]

    held = [h["instrument"] for h in p["holdings"]]
    held_set = set(held)
    top_set = set(top)
    score_of = {str(k): (None if float(v) != float(v) else float(v)) for k, v in s.items()}

    return {
        "date": p["date"],
        "signal_date": sig_day,
        "available": True,
        "topk": topk,
        "overlap": len(top_set & held_set),
        "top_not_held": [
            {"instrument": c, "score": score_of.get(c)}
            for c in top if c not in held_set
        ],
        "held_not_top": [
            {"instrument": c, "score": score_of.get(c)}
            for c in held if c not in top_set
        ],
    }


def prediction_vs_label(exp_id: str, run_id: str, date: Optional[str] = None) -> Optional[dict]:
    """某一天的预测分 vs 实际收益（label.pkl 的 LABEL0）。

    标签是未来 N 日收益，序列末尾若干天没有标签（Ref($close,-N) 要往后看），
    那些天返回空 rows 并标注 ``label_available=False``，不是错误。
    """
    got = _score_frame(exp_id, run_id, date)
    if got is None:
        return None
    scores, days, day = got

    label = _load_label(exp_id, run_id)
    rows = []
    label_available = False
    if label is not None:
        try:
            lab = label.xs(day, level=0)
            col = "LABEL0" if "LABEL0" in lab.columns else lab.columns[0]
            lab = lab[col].astype(float)
            joined = scores.astype(float).to_frame("score").join(lab.rename("label"), how="inner")
            for inst, r in joined.iterrows():
                sc, lb = float(r["score"]), float(r["label"])
                if sc != sc or lb != lb:
                    continue
                rows.append({"instrument": str(inst), "score": sc, "label": lb})
            label_available = True
        except Exception:
            rows = []

    # 按预测分五等分后的平均实际收益：检查单调性
    buckets = []
    if rows:
        rs = sorted(rows, key=lambda r: r["score"])
        n = len(rs)
        for i in range(5):
            seg = rs[i * n // 5:(i + 1) * n // 5]
            if not seg:
                continue
            buckets.append({
                "group": i + 1,
                "n": len(seg),
                "avg_score": sum(x["score"] for x in seg) / len(seg),
                "avg_label": sum(x["label"] for x in seg) / len(seg),
            })

    return {
        "date": day,
        "dates": days,
        "label_available": label_available,
        "rows": rows,
        "buckets": buckets,
    }



def _config_summary(exp_id: str, run_id: str) -> Optional[dict]:
    """Top-level knobs from the logged config pickle (not the full task tree)."""
    path = _art_dir(exp_id, run_id) / "config"
    if not path.is_file():
        return None
    try:
        cfg = _load_pickle(path)
        if not isinstance(cfg, dict):
            return None
        keep = {}
        for key in ("experiment_name", "market", "benchmark", "qlib_init", "data_handler_config"):
            if key in cfg:
                keep[key] = _jsonable(cfg[key])
        port = cfg.get("port_analysis_config") or {}
        if isinstance(port, dict):
            keep["port_analysis_config"] = _jsonable(port)
        return keep
    except Exception:
        return None


# ── the YAML that defined the run ─────────────────────────────────────────
# A run is only reproducible if the config it used is frozen with it. qlib
# logs the *resolved* config pickle and nothing else; the source YAML keeps
# living on disk and may be edited, or (with run_all.py --repeat) never exist
# at all because a temporary seed file was deleted after the run.
#
# So the YAML is snapshotted into the run's own artifacts at launch
# (tools/snapshot_config.py). This reader prefers that snapshot. Only when a
# run predates snapshots does it fall back to the file on disk, and then it
# says so and checks the file still agrees with the params MLflow recorded.

YAML_MAX_BYTES = 120_000
SNAPSHOT_NAME = "config_source.yaml"


def _resolve_in_examples(rel: str) -> Optional[Path]:
    p = Path(rel)
    p = (EXAMPLES / p).resolve() if not p.is_absolute() else p.resolve()
    try:
        p.relative_to(EXAMPLES.resolve())
    except (ValueError, OSError):
        return None
    return p if p.is_file() else None


def _yaml_path(params: dict) -> Optional[Path]:
    """The config file a run was launched with, staying inside examples/."""
    argv = params.get("cmd-sys.argv") or ""
    cand = None
    for tok in argv.split():
        if tok.lower().endswith((".yaml", ".yml")):
            cand = tok
    return _resolve_in_examples(cand) if cand else None


def _snapshot_path(exp_id: str, run_id: str) -> Path:
    return _art_dir(exp_id, run_id) / SNAPSHOT_NAME


def _params_agree(yaml_text: str, recorded: dict) -> Optional[bool]:
    """Does this YAML still produce the params the run recorded?

    qlib logs only the `task.*` subtree (plus cmd-sys.argv), as dotted keys
    like `model.kwargs.num_leaves`, while the YAML keeps them nested. So the
    comparison is done on the task subtree, un-nested.

    Returns None when the check cannot be made (no PyYAML, unparseable file).
    """
    try:
        import yaml as _yaml
    except ImportError:
        return None
    try:
        loaded = _yaml.safe_load(yaml_text)
    except Exception:
        return None
    if not isinstance(loaded, dict) or not isinstance(loaded.get("task"), dict):
        return None

    flat = {}

    def walk(node, prefix):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{prefix}.{k}" if prefix else str(k))
        elif isinstance(node, (list, tuple)):
            flat[prefix] = str(list(node))
        elif node is not None:
            flat[prefix] = str(node)

    walk(loaded["task"], "")

    keys = [k for k in flat if k in recorded]
    if not keys:
        return None
    agree = sum(1 for k in keys if recorded[k] == flat[k])
    return agree / len(keys) >= 0.85


def _config_yaml(params: dict, exp_id: str, run_id: str) -> Optional[dict]:
    """The config source for this run: snapshot first, disk fallback second."""
    snap = _snapshot_path(exp_id, run_id)
    if snap.is_file():
        try:
            text = snap.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = None
        if text is not None:
            return {
                "source": "snapshot",
                "path": SNAPSHOT_NAME,
                "text": text[:YAML_MAX_BYTES],
                "truncated": len(text) > YAML_MAX_BYTES,
                "lines": text.count("\n") + 1,
            }

    p = _yaml_path(params)
    if p is None:
        return None
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return {
        "source": "disk",
        "path": str(p.relative_to(EXAMPLES.resolve())).replace("\\", "/"),
        "text": text[:YAML_MAX_BYTES],
        "truncated": len(text) > YAML_MAX_BYTES,
        "lines": text.count("\n") + 1,
        "matches_run": _params_agree(text, params),
    }


def _jsonable(v, depth: int = 0):
    """Dates, paths and qlib objects -> plain JSON-safe values."""
    if depth > 6:
        return str(v)
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, dict):
        return {str(k): _jsonable(x, depth + 1) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x, depth + 1) for x in v]
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def _extras(exp_id: str, run_id: str, params: dict) -> dict:
    return {
        "config_yaml": _config_yaml(params, exp_id, run_id),
        "training_curve": _training_curve(run_id),
        "ic_series": _series_from_pickle(exp_id, run_id, "sig_analysis/ic.pkl"),
        "rank_ic_series": _series_from_pickle(exp_id, run_id, "sig_analysis/ric.pkl"),
        "return_series": _series_from_pickle(
            exp_id, run_id, "portfolio_analysis/report_normal_1day.pkl", "return"
        ),
        "turnover_series": _series_from_pickle(
            exp_id, run_id, "portfolio_analysis/report_normal_1day.pkl", "turnover"
        ),
        "cost_series": _series_from_pickle(
            exp_id, run_id, "portfolio_analysis/report_normal_1day.pkl", "cost"
        ),
        "bench_series": _series_from_pickle(
            exp_id, run_id, "portfolio_analysis/report_normal_1day.pkl", "bench"
        ),
        # 账户层：总资产、持仓市值、现金。看板此前只读了 return/bench/turnover/cost 四列。
        "account_series": _series_from_pickle(
            exp_id, run_id, "portfolio_analysis/report_normal_1day.pkl", "account"
        ),
        "value_series": _series_from_pickle(
            exp_id, run_id, "portfolio_analysis/report_normal_1day.pkl", "value"
        ),
        "cash_series": _series_from_pickle(
            exp_id, run_id, "portfolio_analysis/report_normal_1day.pkl", "cash"
        ),
        # 撮合过程：逐日成交金额 / 成交笔数。此前只取了聚合后的 ffr/pa/pos。
        "deal_series": _indicators_frame(exp_id, run_id),
        "excess_series": _excess_series(exp_id, run_id),
        "risk_table": _risk_table(exp_id, run_id),
        "dataset_inputs": _dataset_inputs(exp_id, run_id),
        "config_summary": _config_summary(exp_id, run_id),
        "config_full": _config_full(exp_id, run_id),
        "model": _model_info(params),
        "code_diff": _text_artifact(exp_id, run_id, "code_diff.txt"),
        "code_status": _text_artifact(exp_id, run_id, "code_status.txt"),
    }


def get_run(run_id: str) -> Optional[dict]:
    c = client()
    try:
        run = c.get_run(run_id)
    except Exception:
        return None
    exp = c.get_experiment(run.info.experiment_id)
    row = _row(exp.name, exp.experiment_id, run)
    row["renders"] = list_renders(row["experiment_id"], run_id)
    row["artifacts"] = _artifacts(run_id)
    row["end_time"] = run.info.end_time
    row["duration_ms"] = (
        run.info.end_time - run.info.start_time
        if run.info.end_time and run.info.start_time
        else None
    )
    row["user"] = run.info.user_id
    row["run_name"] = run.info.run_name
    row["artifact_uri"] = run.info.artifact_uri
    row["extras"] = _extras(row["experiment_id"], run_id, run.data.params)
    return row


# ── 产物内容预览 ──────────────────────────────────────────────────────────
# 产物树此前只显示文件名和大小，点不开。这里让每个 pkl 至少能回答
# 「这个文件里有什么」：类型、shape、列名、前几行。

PREVIEW_ROWS = 5
PREVIEW_MAX_CHARS = 4000


def resolve_artifact(exp_id: str, run_id: str, rel: str) -> Optional[Path]:
    """解析 run 产物目录下的相对路径，拒绝任何越界访问。"""
    if not rel or rel.startswith(("/", "\\")) or ".." in rel.replace("\\", "/").split("/"):
        return None
    base = (_art_dir(exp_id, run_id)).resolve()
    path = (base / rel).resolve()
    if base not in path.parents and path != base:
        return None
    return path if path.is_file() else None


def _preview_value(v):
    """把单元格值转成可 JSON 化的短字符串。"""
    try:
        f = float(v)
        if f != f:
            return None
        return round(f, 6)
    except (TypeError, ValueError):
        s = str(v)
        return s[:60] + ("…" if len(s) > 60 else "")


def artifact_preview(exp_id: str, run_id: str, rel: str) -> Optional[dict]:
    path = resolve_artifact(exp_id, run_id, rel)
    if path is None:
        return None

    size = path.stat().st_size
    suffix = path.suffix.lower()

    if suffix in (".pkl", ".pickle"):
        try:
            obj = _load_pickle(path)
        except Exception as e:
            return {"path": rel, "size": size, "kind": "pickle", "error": f"无法反序列化：{e}"}

        out = {"path": rel, "size": size, "kind": "pickle", "type": type(obj).__name__}

        if hasattr(obj, "shape") and hasattr(obj, "columns"):
            out["shape"] = [int(x) for x in obj.shape]
            out["columns"] = [str(c) for c in obj.columns][:60]
            names = getattr(obj.index, "names", None)
            out["index_names"] = [str(x) if x is not None else None for x in (names or [])]
            try:
                head = obj.head(PREVIEW_ROWS)
                out["rows"] = [
                    {str(k): _preview_value(v) for k, v in row.items()}
                    for _, row in head.iterrows()
                ]
            except Exception:
                pass
        elif hasattr(obj, "shape"):
            out["shape"] = [int(x) for x in obj.shape]
            try:
                out["sample"] = [_preview_value(v) for v in list(obj[:PREVIEW_ROWS])]
            except Exception:
                pass
        elif isinstance(obj, dict):
            out["items"] = [str(k) for k in list(obj)[:60]]
            out["item_count"] = len(obj)
        else:
            out["sample"] = str(obj)[:600]
        return out

    if suffix in (".yaml", ".yml", ".txt", ".json", ".md", ".py", ".log", ".csv"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return {"path": rel, "size": size, "kind": "text", "error": str(e)}
        return {
            "path": rel,
            "size": size,
            "kind": "text",
            "text": text[:PREVIEW_MAX_CHARS],
            "truncated": len(text) > PREVIEW_MAX_CHARS,
            "lines": text.count("\n") + 1,
        }

    return {"path": rel, "size": size, "kind": "binary", "note": "二进制文件，无预览"}


# ── 特征重要性 ────────────────────────────────────────────────────────────
# params.pkl 里存着训练好的模型对象。LightGBM / DoubleEnsemble 都实现了
# get_feature_importance()。反序列化需要能 import 到当时的模型类，缺依赖
# （CatBoost/PyTorch）或类已改动时会失败，页面要如实说明而不是空白。

def feature_importance(exp_id: str, run_id: str, top: int = 40) -> Optional[dict]:
    path = _art_dir(exp_id, run_id) / "params.pkl"
    if not path.is_file():
        return None
    try:
        model = _load_pickle(path)
    except Exception as e:
        return {"available": False, "reason": f"无法反序列化模型对象：{e}"}

    fn = getattr(model, "get_feature_importance", None)
    if not callable(fn):
        return {
            "available": False,
            "reason": f"{type(model).__name__} 没有 get_feature_importance()",
            "model_class": type(model).__name__,
        }
    try:
        imp = fn()
    except Exception as e:
        return {"available": False, "reason": f"get_feature_importance() 失败：{e}"}

    try:
        items = [(str(k), float(v)) for k, v in imp.items()]
        items.sort(key=lambda kv: -abs(kv[1]))
        total = sum(abs(v) for _, v in items) or 1.0
        return {
            "available": True,
            "model_class": type(model).__name__,
            "count": len(items),
            "rows": [
                {"feature": k, "importance": v, "share": abs(v) / total}
                for k, v in items[:top]
            ],
        }
    except Exception as e:
        return {"available": False, "reason": f"无法整理重要性：{e}"}


def list_renders(exp_id: str, run_id: str) -> list[str]:
    d = MLRUNS / exp_id / run_id / "artifacts" / RENDER_DIR
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.glob("*.html"))


def render_path(exp_id: str, run_id: str, filename: str) -> Optional[Path]:
    """Resolve a render file, rejecting anything that escapes the directory."""
    if not filename.endswith(".html") or "/" in filename or "\\" in filename or filename.startswith("."):
        return None
    d = (MLRUNS / exp_id / run_id / "artifacts" / RENDER_DIR).resolve()
    path = (d / filename).resolve()
    if d not in path.parents or not path.is_file():
        return None
    return path


def find_run(run_id: str) -> Optional[dict]:
    for r in list_runs():
        if r["run_id"] == run_id:
            return r
    return None
