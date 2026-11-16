"""Compare all path_exps results in one table, grouped by improvement path.

Reads:
  - training runs  : experiments/mlruns, experiment name path_exps
  - path 4/5 sweeps: path_exps/results/sweep_*.csv written by sweep.py

Usage (from examples/):

    python path_exps/compare.py
    python path_exps/compare.py --csv path_exps/results/path_summary.csv
    python path_exps/compare.py --repeat-sd          # show seed noise when repeats exist
"""

import argparse
import os
import re
import sys
from pathlib import Path

import pandas as pd

os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")  # mlflow>=3 refuses the file store without this

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE.parent
EXPERIMENT = "path_exps"

METRIC_COLS = [
    ("IC", "IC"),
    ("ICIR", "ICIR"),
    ("Rank IC", "Rank IC"),
    ("gross_ann", "1day.excess_return_without_cost.annualized_return"),
    ("excess_ann", "1day.excess_return_with_cost.annualized_return"),
    ("excess_ir", "1day.excess_return_with_cost.information_ratio"),
    ("excess_mdd", "1day.excess_return_with_cost.max_drawdown"),
]

PATH_NAMES = {
    "00": "path0 baseline",
    "11": "path1 label",
    "12": "path1 label",
    "13": "path1 label",
    "21": "path2 features",
    "22": "path2 features",
    "23": "path2 features",
    "31": "path3 model",
    "32": "path3 model",
}


def load_turnover(client, run_id):
    """turnover / cost are not logged as metrics; read them from the backtest report."""
    try:
        artifacts = client.list_artifacts(run_id, "portfolio_analysis")
        names = [a.path for a in artifacts]
        pkl = [n for n in names if n.endswith("report_normal_1day.pkl")]
        if not pkl:
            return None, None
        local = client.download_artifacts(run_id, pkl[0])
        df = pd.read_pickle(local)
        return float(df["turnover"].mean()), float(df["cost"].mean())
    except Exception:
        return None, None


def config_name(run):
    argv = run.data.params.get("cmd-sys.argv", "")
    m = re.search(r"path_exps/([\w\-.]+)\.yaml", argv)
    raw = m.group(1) if m else run.info.run_id[:8]
    return re.sub(r"_s\d+$", "", raw)  # seed repeats collapse onto the base config


def load_training_runs(with_sd):
    try:
        from mlflow.tracking import MlflowClient
    except ImportError:
        sys.exit("mlflow is required: python path_exps/compare.py")

    uri = "file:" + str((EXAMPLES / "mlruns").resolve())
    client = MlflowClient(tracking_uri=uri)
    exp = client.get_experiment_by_name(EXPERIMENT)
    if exp is None:
        print(f"[skip] experiment {EXPERIMENT!r} not found -- run run_all.py first")
        return pd.DataFrame()

    runs = client.search_runs([exp.experiment_id], order_by=["attributes.start_time ASC"], max_results=500)
    rows = []
    for run in runs:
        if run.info.status != "FINISHED":
            continue
        row = {"name": config_name(run)}
        for label, key in METRIC_COLS:
            row[label] = run.data.metrics.get(key)
        row["turnover"], row["cost"] = load_turnover(client, run.info.run_id)
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    if with_sd and df["name"].duplicated().any():
        agg = {c: ["mean", "std"] for c in df.columns if c != "name"}
        out = df.groupby("name").agg(agg)
        out.columns = [f"{c}_{s}" for c, s in out.columns]
        return out.reset_index()
    return df.groupby("name", as_index=False).mean(numeric_only=True)


def load_sweeps():
    frames = []
    for path in sorted((HERE / "results").glob("sweep_*.csv")):
        df = pd.read_csv(path)
        df["name"] = df["experiment"] + ":" + df["variant"]
        frames.append(df.drop(columns=["experiment", "variant"]))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def path_of(name):
    for prefix, label in PATH_NAMES.items():
        if name.startswith(prefix):
            return label
    if name.startswith("path4"):
        return "path4 portfolio"
    if name.startswith("path5"):
        return "path5 execution"
    return "other"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None, help="also write the combined table here")
    ap.add_argument("--repeat-sd", action="store_true", help="aggregate seed repeats as mean/std")
    args = ap.parse_args()

    frames = [f for f in (load_training_runs(args.repeat_sd), load_sweeps()) if not f.empty]
    if not frames:
        sys.exit("no finished runs -- run run_all.py / sweep.py first")

    df = pd.concat(frames, ignore_index=True, sort=False)
    df["path"] = df["name"].map(path_of)
    df = df.sort_values(["path", "name"], key=lambda s: s.str.lower())

    cols = ["path", "name"] + [lbl for lbl, _ in METRIC_COLS] + ["turnover", "cost"]
    cols = [c for c in cols if c in df.columns]
    view = df[cols].copy()

    base = df.loc[df["name"] == "00_baseline"]
    if not base.empty:
        b = base.iloc[0]
        for delta in ("IC", "excess_ann", "excess_ir", "turnover"):
            if delta in view.columns and pd.notna(b.get(delta)):
                view[f"d_{delta}"] = view[delta] - b[delta]

    pd.set_option("display.width", 240)
    pd.set_option("display.max_columns", 40)
    print(view.to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    if args.csv:
        out = Path(args.csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        view.to_csv(out, index=False)
        print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
