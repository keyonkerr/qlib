"""Generate qlib.contrib.report graphical reports from a recorded experiment.

By default the html files are logged into the run's own artifact directory
(``mlruns/<exp>/<run>/artifacts/renders/``), so each experiment keeps its
charts next to its metrics.

Usage:
    cd examples
    ../.venv/bin/python report_graphs.py                # latest successful run
    ../.venv/bin/python report_graphs.py --run-id <id>  # a specific run
    ../.venv/bin/python report_graphs.py --all          # every finished run
    ../.venv/bin/python report_graphs.py --out renders  # write locally instead
    ../.venv/bin/python report_graphs.py --open         # open in the browser
"""

import argparse
import tempfile
from pathlib import Path

import pandas as pd

import qlib
from qlib.contrib.report import analysis_model, analysis_position
from qlib.workflow import R

RENDER_DIR = "renders"


def load_pred_label(recorder: R) -> pd.DataFrame:
    pred = recorder.load_object("pred.pkl")
    label = recorder.load_object("label.pkl")
    pred_label = pd.concat([label, pred], axis=1, sort=True).reindex(label.index)
    # report functions require exactly these two column names
    pred_label.columns = ["label", "score"]
    return pred_label


def iter_recorders(args):
    if args.experiment:
        exps = [R.get_exp(experiment_id=args.experiment)]
    else:
        exps = list(R.list_experiments().values())
    for exp in exps:
        for rec in exp.list_recorders().values():
            yield exp, rec


def is_renderable(rec) -> bool:
    if rec.info["status"] != "FINISHED":
        return False
    artifacts = rec.list_artifacts()
    return "pred.pkl" in artifacts and "label.pkl" in artifacts


def find_recorder(run_id: str, args):
    for _exp, rec in iter_recorders(args):
        if rec.id == run_id:
            return rec
    raise SystemExit(f"run {run_id} not found (experiment filter: {args.experiment or 'all'})")


def select_recorders(args) -> list:
    if args.run_id:
        return [find_recorder(args.run_id, args)]
    if args.recorder:
        if not args.experiment:
            raise SystemExit("--recorder requires --experiment; use --run-id to look up a run directly")
        return [R.get_recorder(recorder_id=args.recorder, experiment_id=args.experiment)]

    candidates = [(rec.info["start_time"], rec) for _exp, rec in iter_recorders(args) if is_renderable(rec)]
    if not candidates:
        raise SystemExit("no finished recorder with pred.pkl/label.pkl found")
    candidates.sort(key=lambda item: item[0], reverse=True)
    if args.all:
        return [rec for _, rec in candidates]
    return [candidates[0][1]]


def build_jobs(recorder: R) -> dict:
    pred_label = load_pred_label(recorder)
    print(f"  pred_label {pred_label.shape}, {pred_label.index.names}")

    jobs = {}

    print("  model performance ...")
    jobs["model_performance"] = analysis_model.model_performance_graph(
        pred_label,
        graph_names=["group_return", "pred_ic", "pred_autocorr", "pred_turnover"],
        show_notebook=False,
    )

    print("  score ic ...")
    jobs["score_ic"] = analysis_position.score_ic_graph(pred_label, show_notebook=False)

    try:
        report_normal = recorder.load_object("portfolio_analysis/report_normal_1day.pkl")
    except Exception as exc:  # noqa: BLE001 - record without backtest
        print(f"  skip backtest graphs: {exc}")
        return jobs

    print("  backtest report ...")
    jobs["report"] = analysis_position.report_graph(report_normal, show_notebook=False)

    print("  risk analysis ...")
    try:
        port_analysis = recorder.load_object("portfolio_analysis/port_analysis_1day.pkl")
    except Exception:  # noqa: BLE001 - older records may lack it
        port_analysis = None
    jobs["risk_analysis"] = analysis_position.risk_analysis_graph(
        analysis_df=port_analysis,
        report_normal_df=report_normal,
        show_notebook=False,
    )
    return jobs


def write_figures(figures, out_dir: Path, name: str) -> list:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, fig in enumerate(figures):
        suffix = "" if len(figures) == 1 else f"_{i}"
        path = out_dir / f"{name}{suffix}.html"
        fig.write_html(path, include_plotlyjs="cdn")
        paths.append(path)
    return paths


def log_renders(recorder: R, jobs: dict) -> int:
    """Write the figures to a temp dir, then attach them to the run's artifacts."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for name, figures in jobs.items():
            if figures:
                write_figures(figures, tmp_dir, name)
        files = sorted(tmp_dir.glob("*.html"))
        for path in files:
            recorder.log_artifact(str(path), RENDER_DIR)
    print(f"  logged {len(files)} html to artifacts/{RENDER_DIR}/")
    return len(files)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-uri", default="~/.qlib/qlib_data/cn_data")
    parser.add_argument("--mlruns", default="mlruns")
    parser.add_argument("--experiment", default=None, help="experiment id; default: search all experiments")
    parser.add_argument("--run-id", default=None, help="run id; searched across experiments unless --experiment is set")
    parser.add_argument("--recorder", default=None, help="kept for compatibility; prefer --run-id")
    parser.add_argument("--all", action="store_true", help="render every finished run, newest first")
    parser.add_argument("--out", default=None, help="write html to this local dir instead of logging into mlruns")
    parser.add_argument("--open", action="store_true", help="open figures in the browser")
    args = parser.parse_args()

    qlib.init(provider_uri=args.provider_uri, region="cn")

    recorders = select_recorders(args)
    print(f"{len(recorders)} run(s) to render")

    total = 0
    for recorder in recorders:
        print(f"recorder {recorder.id}  (experiment {recorder.info['experiment_id']})")
        jobs = build_jobs(recorder)

        if args.open:
            import plotly.io as pio

            for figures in jobs.values():
                for fig in figures or []:
                    pio.show(fig)
            continue

        if args.out:
            out_dir = Path(args.out)
            if len(recorders) > 1:
                out_dir = out_dir / recorder.id
            for name, figures in jobs.items():
                if figures:
                    for path in write_figures(figures, out_dir, name):
                        print(f"  saved {path}")
            continue

        total += log_renders(recorder, jobs)

    if not args.out and not args.open:
        print(f"\n{total} figures logged. View them under the run's Artifacts/renders tab, "
              f"or pass --out DIR to write locally.")


if __name__ == "__main__":
    main()
