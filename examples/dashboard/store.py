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
        "excess_series": _excess_series(exp_id, run_id),
        "risk_table": _risk_table(exp_id, run_id),
        "dataset_inputs": _dataset_inputs(exp_id, run_id),
        "config_summary": _config_summary(exp_id, run_id),
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
