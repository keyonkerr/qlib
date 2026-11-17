"""Experiment dashboard HTTP layer.

Run it from the repository root:

    .venv/bin/uvicorn examples.dashboard.app:app --reload --port 8766

or from ``examples/dashboard``:

    ../../.venv/bin/uvicorn app:app --reload --port 8766
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

import analysis  # noqa: E402
import metrics  # noqa: E402
import store  # noqa: E402

app = FastAPI(title="qlib experiment dashboard", docs_url="/api/docs")

STATIC = HERE / "static"


@app.get("/api/runs")
def api_runs() -> list[dict]:
    """Every finished run with the numbers the comparison rail needs."""
    runs = store.list_runs()
    out = []
    for run in runs:
        base = metrics.baseline_for(runs, run["experiment"])
        s = metrics.summary(run, base)
        out.append(
            {
                "run_id": run["run_id"],
                "experiment": run["experiment"],
                "experiment_id": run["experiment_id"],
                "name": run["name"],
                "config": run["config"],
                "start_time": run["start_time"],
                "status": run["status"],
                "has_renders": run["has_renders"],
                "summary": s,
            }
        )
    return out


@app.get("/api/configs")
def api_configs() -> list[dict]:
    """Runs grouped by the YAML they came from, with run-to-run spread."""
    runs = store.list_runs()
    out = []
    for g in store.list_configs():
        base = metrics.baseline_for(runs, g["experiment"])
        items = []
        for run in g["runs"]:
            s = metrics.summary(run, base)
            items.append(
                {
                    "run_id": run["run_id"],
                    "name": run["name"],
                    "seed": run["config"]["seed"],
                    "start_time": run["start_time"],
                    "status": run["status"],
                    "has_renders": run["has_renders"],
                    "summary": s,
                }
            )
        out.append({
            "key": g["key"],
            "label": g["label"],
            "experiment": g["experiment"],
            "run_count": g["run_count"],
            "seeds": g["seeds"],
            "newest_run_id": g["newest_run_id"],
            "has_renders": g["has_renders"],
            "aggregate": g["aggregate"],
            "runs": items,
        })
    return out


@app.get("/api/runs/{run_id}")
def api_run(run_id: str) -> dict:
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    runs = store.list_runs()
    base = metrics.baseline_for(runs, run["experiment"])
    s = metrics.summary(run, base)

    return {
        "run": run,
        "summary": s,
        "verdict": analysis.verdict(run, s, base),
        "sub": analysis.sub(run, s, base),
        "notes": analysis.notes(run, s, base),
    }


@app.get("/renders/{run_id}/{filename}")
def get_render(run_id: str, filename: str) -> FileResponse:
    run = store.find_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    path = store.render_path(run["experiment_id"], run_id, filename)
    if path is None:
        raise HTTPException(status_code=404, detail=f"render {filename} not found")
    return FileResponse(path, media_type="text/html")


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
