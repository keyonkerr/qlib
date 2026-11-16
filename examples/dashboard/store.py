"""Read mlruns into plain dicts.

The only module that imports mlflow. Everything downstream works with dicts,
so switching the tracking backend (file store -> sqlite / server) is a change
confined to this file.
"""

from __future__ import annotations

import os
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
    return {
        "run_id": run.info.run_id,
        "experiment": exp_name,
        "experiment_id": exp_id,
        "name": _run_name(p, run.info.run_id),
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


def get_run(run_id: str) -> Optional[dict]:
    c = client()
    try:
        run = c.get_run(run_id)
    except Exception:
        return None
    exp = c.get_experiment(run.info.experiment_id)
    row = _row(exp.name, exp.experiment_id, run)
    row["renders"] = list_renders(row["experiment_id"], run_id)
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
