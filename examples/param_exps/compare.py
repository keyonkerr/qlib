"""对比 param_exps 实验的结果。

用法（在 examples 目录下）：
    ../.venv/bin/python param_exps/compare.py
    ../.venv/bin/python param_exps/compare.py --csv results.csv
    ../.venv/bin/python param_exps/compare.py --recorder <recorder_id>

默认读取 examples/mlruns 下实验名为 param_exps 的所有 run。
"""

import argparse
import os
import re
import sys

import pandas as pd

try:
    from mlflow.tracking import MlflowClient
except ImportError:
    sys.exit("需要 mlflow：../.venv/bin/python param_exps/compare.py")

DEFAULT_URI = "file:" + os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mlruns")
EXPERIMENT = "param_exps"

METRICS = [
    ("IC", "IC"),
    ("ICIR", "ICIR"),
    ("Rank IC", "Rank IC"),
    ("换手", "turnover"),
    ("计成本超额年化", "1day.excess_return_with_cost.annualized_return"),
    ("计成本IR", "1day.excess_return_with_cost.information_ratio"),
    ("最大回撤", "1day.excess_return_with_cost.max_drawdown"),
]

# 每个 run 记录下的关键参数，用于说明改了什么（键是 mlflow 里的完整参数名）
PARAM_LABELS = [
    ("model.kwargs.learning_rate", "learning_rate"),
    ("model.kwargs.max_depth", "max_depth"),
    ("model.kwargs.num_leaves", "num_leaves"),
    ("model.kwargs.early_stopping_rounds", "early_stopping_rounds"),
    ("model.kwargs.lambda_l1", "lambda_l1"),
    ("model.kwargs.lambda_l2", "lambda_l2"),
    ("dataset.kwargs.handler.class", "handler"),
    ("dataset.kwargs.handler.kwargs.label", "label"),
    ("port_analysis_config.strategy.kwargs.topk", "topk"),
    ("market", "market"),
]


def config_name(run) -> str:
    """从 run 的参数里推断配置文件的用途（优先读 cmd-sys.argv 的文件名）。"""
    argv = run.data.params.get("cmd-sys.argv", "")
    m = re.search(r"(param_exps/[\w\-.]+\.yaml)", argv)
    if m:
        return os.path.basename(m.group(1)).replace(".yaml", "")
    return run.info.run_id[:8]


def load_turnover(client, run_id):
    """turnover 不在 metrics 里，从 portfolio 报告里算；取不到就留空。"""
    try:
        artifacts = client.list_artifacts(run_id, "portfolio_analysis")
        names = [a.path for a in artifacts]
        pkl = [n for n in names if n.endswith("report_normal_1day.pkl")]
        if not pkl:
            return None
        local = client.download_artifacts(run_id, pkl[0])
        return float(pd.read_pickle(local)["turnover"].mean())
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", default=DEFAULT_URI, help="mlflow tracking uri")
    ap.add_argument("--experiment", default=EXPERIMENT, help="实验名")
    ap.add_argument("--recorder", default=None, help="只对比指定的 run id")
    ap.add_argument("--csv", default=None, help="同时写出一份 CSV")
    args = ap.parse_args()

    client = MlflowClient(tracking_uri=args.uri)
    exp = client.get_experiment_by_name(args.experiment)
    if exp is None:
        sys.exit(f"找不到实验 {args.experiment!r}，先运行 run_all.sh")

    runs = client.search_runs(
        [exp.experiment_id], order_by=["attributes.start_time ASC"], max_results=200
    )
    if args.recorder:
        runs = [r for r in runs if r.info.run_id == args.recorder]

    rows = []
    for run in runs:
        if run.info.status != "FINISHED":
            continue
        row = {"配置": config_name(run), "run_id": run.info.run_id[:8]}
        for label, key in METRICS:
            if key == "turnover":
                row[label] = load_turnover(client, run.info.run_id)
            else:
                row[label] = run.data.metrics.get(key)
        for key, label in PARAM_LABELS:
            v = run.data.params.get(key)
            if v is not None:
                row[label] = str(v).replace("Ref($close, -6)/Ref($close, -1) - 1", "5日收益")
        rows.append(row)

    if not rows:
        sys.exit("没有已完成的 run。")

    df = pd.DataFrame(rows)

    # 以基线为参照，算出每个指标的变化
    base = df[df["配置"].str.startswith("00_")]
    if not base.empty:
        b = base.iloc[0]
        for label, _ in METRICS:
            if label == "换手":
                continue
            if label in df and pd.notna(b.get(label)):
                df[f"{label}变化"] = df[label] - b[label]

    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 40)

    show = ["配置", "IC", "ICIR", "Rank IC", "换手", "计成本超额年化", "计成本IR", "最大回撤"]
    show += [f"{lbl}变化" for lbl in ["IC", "计成本超额年化", "计成本IR"] if f"{lbl}变化" in df]
    print(df[show].to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    print("\n各 run 相对基线的参数差异（没列出的表示与基线相同）：")
    base_row = df[df["配置"].str.startswith("00_")].iloc[0] if not base.empty else None
    param_cols = ["learning_rate", "max_depth", "num_leaves", "early_stopping_rounds",
                  "lambda_l1", "lambda_l2", "handler", "label"]
    for _, r in df.iterrows():
        diff = {}
        for k in param_cols:
            if k not in r or pd.isna(r[k]):
                continue
            base_val = base_row.get(k) if base_row is not None else None
            if base_row is None or pd.isna(base_val) or str(r[k]) != str(base_val):
                diff[k] = r[k]
        print(f"  {r['配置']:<20} {diff if diff else '（与基线相同）'}")

    if args.csv:
        df.to_csv(args.csv, index=False)
        print(f"\n已写入 {args.csv}")


if __name__ == "__main__":
    main()
