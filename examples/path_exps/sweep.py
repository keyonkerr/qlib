"""Backtest-only sweeps for path (4) portfolio construction and path (5) execution.

Paths 4/5 do not change the model, so they reuse the finished baseline prediction
(pred.pkl from path_exps/00_baseline) and only re-run the backtest. Each sweep
writes one row to path_exps/results/sweep_<which>.csv.

Prerequisites: run_all.py --only 00 must have finished.

Usage (from examples/):

    python path_exps/sweep.py --which 4       # strategy parameters
    python path_exps/sweep.py --which 5       # execution / exchange costs
    python path_exps/sweep.py --which all
"""

import argparse
import csv
import os
import sys
from pathlib import Path

import pandas as pd

os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")  # mlflow>=3 refuses the file store without this
os.environ.setdefault("PYTHONUTF8", "1")

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE.parent
RESULTS = HERE / "results"
EXPERIMENT = "path_exps"

STRATEGY_BASE = {
    "class": "TopkDropoutStrategy",
    "module_path": "qlib.contrib.strategy",
    "kwargs": {"topk": 50, "n_drop": 5},
}
EXECUTOR = {
    "class": "SimulatorExecutor",
    "module_path": "qlib.backtest.executor",
    "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True},
}
EXCHANGE_BASE = {
    "limit_threshold": 0.095,
    "deal_price": "close",
    "open_cost": 0.0005,
    "close_cost": 0.0015,
    "min_cost": 5,
}
BACKTEST = {"start_time": "2017-01-01", "end_time": "2020-08-01", "account": 100000000, "benchmark": "SH000300"}


def init_qlib():
    """Same setup as qlib.cli.run: data provider + mlflow store under examples/mlruns."""
    import qlib
    from qlib.config import C

    exp_manager = C["exp_manager"]
    exp_manager["kwargs"]["uri"] = "file:" + str((EXAMPLES / "mlruns").resolve())
    qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region="cn", kernels=1, exp_manager=exp_manager)


def find_baseline_pred():
    from qlib.workflow import R

    exp = R.get_exp(experiment_name=EXPERIMENT)
    for recorder in exp.list_recorders().values():
        param = recorder.list_params().get("cmd-sys.argv", "")
        if "00_baseline" not in param:
            continue
        if recorder.status != "FINISHED":
            continue
        return recorder.load_object("pred.pkl")
    sys.exit("baseline prediction not found -- run `python path_exps/run_all.py --only 00` first")


def backtest_one(pred, strategy, exchange_kwargs):
    from qlib.backtest import backtest
    from qlib.contrib.evaluate import risk_analysis

    portfolio_dict, indicator_dict = backtest(
        strategy=strategy,
        executor=EXECUTOR,
        exchange_kwargs=exchange_kwargs,
        **BACKTEST,
    )
    report = portfolio_dict["1day"][0]  # tuple: (DataFrame, positions dict)
    indicator_df = indicator_dict["1day"][0]

    def excess(r):
        return risk_analysis(r, freq="day")["risk"]

    excess_cost = excess(report["return"] - report["bench"] - report["cost"])
    gross = excess(report["return"] - report["bench"])
    return {
        "turnover": float(report["turnover"].mean()),
        "cost": float(report["cost"].mean()),
        "excess_ann": float(excess_cost["annualized_return"]),
        "excess_ir": float(excess_cost["information_ratio"]),
        "excess_mdd": float(excess_cost["max_drawdown"]),
        "gross_excess_ann": float(gross["annualized_return"]),
        "ffr": float(indicator_df["ffr"].mean()),
        "pa": float(indicator_df["pa"].mean()),
        "pos": float(indicator_df["pos"].mean()),
    }


def strategy_config(**overrides):
    import copy

    cfg = copy.deepcopy(STRATEGY_BASE)
    cfg["kwargs"].update(overrides)
    return cfg


SWEEP4 = [
    # (name, strategy override dict)
    ("topk50_ndrop5", {}),  # baseline reference
    ("topk50_ndrop5_hold2", {"hold_thresh": 2}),
    ("topk50_ndrop5_hold5", {"hold_thresh": 5}),
    ("topk30_ndrop5", {"topk": 30}),
    ("topk100_ndrop10", {"topk": 100, "n_drop": 10}),
    # NOTE: n_drop 5->2 is deliberately NOT here: docs 08 measured it and it failed
    # (excess 8.07% -> 2.60%). It still costs one backtest to see why -- enable with:
    # ("topk50_ndrop2", {"n_drop": 2}),
]

SWEEP5 = [
    # (name, exchange override dict)
    ("base_cost", {}),
    ("zero_cost", {"open_cost": 0.0, "close_cost": 0.0, "min_cost": 0}),
    ("double_cost", {"open_cost": 0.001, "close_cost": 0.003}),
    # execution timing / price choice (path 5)
    ("deal_vwap", {"deal_price": "vwap"}),
    # capacity limit: each order may take at most 1% of that day's volume.
    # Expressed in Python because volume_threshold needs a tuple, which YAML cannot carry.
    ("vol_limit_1pct", {"volume_threshold": {"all": ("current", "0.01 * $volume")}}),
    ("vol_limit_5pct", {"volume_threshold": {"all": ("current", "0.05 * $volume")}}),
]


def run_sweep(which, pred):
    rows = []
    if which in ("4", "all"):
        for name, override in SWEEP4:
            print(f"[path4] {name} ...", flush=True)
            row = {"experiment": "path4_strategy", "variant": name}
            row.update(backtest_one(pred, strategy_config(**override), EXCHANGE_BASE))
            rows.append(row)
            print(f"        turnover={row['turnover']:.3f} excess_ann={row['excess_ann']:.4f}")
    if which in ("5", "all"):
        for name, override in SWEEP5:
            print(f"[path5] {name} ...", flush=True)
            exch = dict(EXCHANGE_BASE)
            exch.update(override)
            row = {"experiment": "path5_execution", "variant": name}
            row.update(backtest_one(pred, strategy_config(), exch))
            rows.append(row)
            print(f"        turnover={row['turnover']:.3f} excess_ann={row['excess_ann']:.4f}")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--which", choices=["4", "5", "all"], required=True)
    args = ap.parse_args()

    init_qlib()
    pred = find_baseline_pred()
    rows = run_sweep(args.which, pred)

    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"sweep_{args.which}.csv"
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nwrote {out}")
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda x: f"{x:.4f}"))


if __name__ == "__main__":
    main()
