"""Freeze the config YAML into the run's own artifacts.

qlib records the *resolved* config (`artifacts/config`) but never the source
file, so the comments that explain every knob are lost, and a later edit to
the YAML silently rewrites what an old run appears to have used. Batch runs
are worse: `run_all.py --repeat` writes a temporary seed-modified YAML and
deletes it afterwards, so there is no file left to point at.

This tool copies the YAML into `artifacts/<run_id>/artifacts/config_source.yaml`
right after the run finishes, so the run carries its own definition.

Usage (from examples/):

    # after one run — looks up the newest run with no snapshot yet
    python tools/snapshot_config.py

    # a specific run
    python tools/snapshot_config.py --run-id <run_id>

    # every finished run missing a snapshot
    python tools/snapshot_config.py --all

    # dry run
    python tools/snapshot_config.py --all --dry-run

Existing snapshots are never overwritten: they are the record of what ran.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE.parent

SNAPSHOT_NAME = "config_source.yaml"


def _load_store():
    import warnings

    warnings.filterwarnings("ignore", category=FutureWarning, module="mlflow")
    from mlflow.tracking import MlflowClient

    return MlflowClient(tracking_uri=f"file:{EXAMPLES / 'mlruns'}")


def _config_token(params: dict) -> str | None:
    argv = params.get("cmd-sys.argv") or ""
    tok = None
    for t in argv.split():
        if t.lower().endswith((".yaml", ".yml")):
            tok = t
    return tok


def _resolve(tok: str) -> Path | None:
    p = Path(tok)
    p = (EXAMPLES / p).resolve() if not p.is_absolute() else p.resolve()
    try:
        p.relative_to(EXAMPLES.resolve())
    except (ValueError, OSError):
        return None
    return p if p.is_file() else None


def snapshot(client, run, dry_run: bool) -> str:
    """Returns one of: 'ok', 'exists', 'no-config', 'missing-file'."""
    run_id = run.info.run_id
    exp_id = run.info.experiment_id
    dest = EXAMPLES / "mlruns" / exp_id / run_id / "artifacts" / SNAPSHOT_NAME

    if dest.exists():
        return "exists"

    tok = _config_token(run.data.params)
    if not tok:
        return "no-config"
    src = _resolve(tok)
    if src is None:
        return "missing-file"

    if dry_run:
        print(f"  would snapshot {src.name} -> {run_id[:8]}")
        return "ok"

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(src.read_text(encoding="utf-8", errors="replace"),
                    encoding="utf-8", newline="\n")
    print(f"  {src.name} -> {exp_id}/{run_id}/artifacts/{SNAPSHOT_NAME}")
    return "ok"


def main() -> None:
    ap = argparse.ArgumentParser(description="freeze config YAML into run artifacts")
    ap.add_argument("--run-id", help="only this run")
    ap.add_argument("--all", action="store_true", help="every finished run missing a snapshot")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    client = _load_store()
    runs = []
    for exp in client.search_experiments():
        for r in client.search_runs(experiment_ids=[exp.experiment_id]):
            if r.info.status != "FINISHED":
                continue
            if args.run_id and r.info.run_id != args.run_id:
                continue
            runs.append(r)

    if not runs:
        sys.exit("no finished run matched")

    if args.run_id is None and not args.all:
        runs = runs[:1]

    counts = {}
    for r in runs:
        outcome = snapshot(client, r, args.dry_run)
        counts[outcome] = counts.get(outcome, 0) + 1

    print("\nsummary:")
    for k in ("ok", "exists", "no-config", "missing-file"):
        if counts.get(k):
            print(f"  {k:14} {counts[k]}")


if __name__ == "__main__":
    main()
