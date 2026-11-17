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

from fastapi import FastAPI, HTTPException, Query  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import analysis  # noqa: E402
import compare  # noqa: E402
import metrics  # noqa: E402
import runner  # noqa: E402
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
    """Runs grouped by the YAML they came from, with run-to-run spread.

    Group order follows the disk scan: every YAML on disk appears, whether or
    not it has been run yet, and configs whose file is gone (a deleted temp seed
    file, a renamed YAML) are kept at the end with ``runnable: false`` so their
    history stays readable.
    """
    runs = store.list_runs()
    groups = store.list_configs()
    disk = runner.scan_configs()
    by_key = {(d["experiment"], d["key"]): d for d in disk}

    out = []
    seen = set()
    for g in groups:
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
        d = by_key.get((g["experiment"], g["key"]))
        seen.add((g["experiment"], g["key"]))
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
            "path": d["path"] if d else None,
            "dir": d["dir"] if d else None,
            "mtime": d["mtime"] if d else None,
            "repeatable": bool(d and d["repeatable"]),
            "runnable": d is not None,
            "job": runner.running_job_for(d["path"]) if d else None,
        })

    # Configs that exist on disk but have never finished a run.
    for d in disk:
        if (d["experiment"], d["key"]) in seen:
            continue
        out.append({
            "key": d["key"],
            "label": d["label"],
            "experiment": d["experiment"],
            "run_count": 0,
            "seeds": [],
            "newest_run_id": None,
            "has_renders": False,
            "aggregate": {},
            "runs": [],
            "path": d["path"],
            "dir": d["dir"],
            "mtime": d["mtime"],
            "repeatable": d["repeatable"],
            "runnable": True,
            "job": runner.running_job_for(d["path"]),
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


# ── 明细端点 ──────────────────────────────────────────────────────────────
# 看板此前只读聚合指标。以下端点把 run 里逐日、逐股的数据直接暴露出来：
# 预测分数、实际持仓、预测 vs 实际、产物预览、特征重要性。数据都按日期或
# 字段切片，不会把 pred.pkl 的 26 万行一次性返回。


def _run_or_404(run_id: str) -> dict:
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return run


@app.get("/api/runs/{run_id}/predictions")
def api_predictions(
    run_id: str,
    date: str | None = Query(None, description="YYYY-MM-DD，缺省取最后一个交易日"),
    top: int | None = Query(None, description="只返回分数最高的 N 只，缺省返回全部"),
) -> dict:
    run = _run_or_404(run_id)
    out = store.predictions(run["experiment_id"], run_id, date=date, top=top)
    if out is None:
        raise HTTPException(status_code=404, detail="这个 run 没有 pred.pkl")
    return out


@app.get("/api/runs/{run_id}/positions")
def api_positions(
    run_id: str,
    date: str | None = Query(None, description="YYYY-MM-DD，缺省取最后一个交易日"),
) -> dict:
    run = _run_or_404(run_id)
    out = store.positions(run["experiment_id"], run_id, date=date)
    if out is None:
        raise HTTPException(status_code=404, detail="这个 run 没有 positions_normal_1day.pkl")
    return out


@app.get("/api/runs/{run_id}/position-signal")
def api_position_signal(
    run_id: str,
    date: str | None = Query(None, description="持仓日 YYYY-MM-DD，缺省取最后一个交易日"),
    topk: int = Query(50, description="信号 topK，应与配置的 topk 一致"),
) -> dict:
    run = _run_or_404(run_id)
    out = store.position_signal_compare(run["experiment_id"], run_id, date=date, topk=topk)
    if out is None:
        raise HTTPException(status_code=404, detail="缺少持仓或预测数据")
    return out


@app.get("/api/runs/{run_id}/prediction-vs-label")
def api_prediction_vs_label(
    run_id: str,
    date: str | None = Query(None, description="YYYY-MM-DD，缺省取最后一个交易日"),
) -> dict:
    run = _run_or_404(run_id)
    out = store.prediction_vs_label(run["experiment_id"], run_id, date=date)
    if out is None:
        raise HTTPException(status_code=404, detail="这个 run 没有 pred.pkl")
    return out


@app.get("/api/runs/{run_id}/artifact")
def api_artifact(
    run_id: str,
    path: str = Query(..., description="run 产物目录下的相对路径"),
) -> dict:
    run = _run_or_404(run_id)
    out = store.artifact_preview(run["experiment_id"], run_id, path)
    if out is None:
        raise HTTPException(status_code=404, detail=f"产物不存在或路径越界：{path}")
    return out


@app.get("/api/runs/{run_id}/feature-importance")
def api_feature_importance(
    run_id: str,
    top: int = Query(40, description="返回前 N 个因子"),
) -> dict:
    run = _run_or_404(run_id)
    out = store.feature_importance(run["experiment_id"], run_id, top=top)
    if out is None:
        raise HTTPException(status_code=404, detail="这个 run 没有 params.pkl")
    return out



@app.get("/api/compare")
def api_compare(runs: str = Query(..., description="逗号分隔的 run id，2–4 个")) -> dict:
    """Two to four runs, aligned row by row and marked where they differ."""
    ids = [r for r in (t.strip() for t in runs.split(",")) if r]
    if len(ids) < 2:
        raise HTTPException(status_code=400, detail="至少选择 2 个 run")
    if len(ids) > compare.MAX_RUNS:
        raise HTTPException(status_code=400, detail=f"最多对比 {compare.MAX_RUNS} 个 run")
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=400, detail="run 不能重复")

    run_list = []
    for rid in ids:
        run = store.get_run(rid)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run {rid} not found")
        run_list.append(run)
    return compare.build(run_list)


@app.get("/api/configs/{path:path}")
def api_config(path: str) -> dict:
    """The YAML as it stands on disk, plus the runs it has produced.

    Deliberately reads the source file, not a run's snapshot: this view answers
    "what does this config say right now". The snapshot is what a *run* used,
    and lives on that run's page.
    """
    p = runner._runnable(path)
    if p is None:
        raise HTTPException(status_code=404, detail=f"{path} 不是可运行的配置")

    text = p.read_text(encoding="utf-8", errors="replace")
    rel = f"{p.parent.name}/{p.name}"
    scan = next((d for d in runner.scan_configs() if d["path"] == rel), None)
    if scan is None:
        raise HTTPException(status_code=404, detail=f"{rel} 不在扫描结果里")

    all_runs = store.list_runs()
    runs = []
    for g in store.list_configs():
        if g["key"] != scan["key"] or g["experiment"] != scan["experiment"]:
            continue
        base = metrics.baseline_for(all_runs, g["experiment"])
        for run in g["runs"]:
            s = metrics.summary(run, base)
            runs.append({
                "run_id": run["run_id"],
                "seed": run["config"]["seed"],
                "start_time": run["start_time"],
                "status": run["status"],
                "summary": s,
            })
    runs.sort(key=lambda r: -(r["start_time"] or 0))

    return {
        "path": rel,
        "label": p.stem,
        "dir": p.parent.name,
        "experiment": scan["experiment"],
        "mtime": scan["mtime"],
        "lines": text.count("\n") + 1,
        "yaml": text,
        "repeatable": scan["repeatable"],
        "runs": runs,
        "job": runner.running_job_for(rel),
    }


class RunRequest(BaseModel):
    path: str
    repeat: int = 1


@app.post("/api/run")
def api_run(req: RunRequest) -> dict:
    if runner._runnable(req.path) is None:
        raise HTTPException(status_code=400, detail="只允许运行 path_exps / param_exps 下的 YAML")
    if not 1 <= req.repeat <= 5:
        raise HTTPException(status_code=400, detail="重复次数只支持 1–5")
    job_id = runner.submit(req.path, req.repeat)
    if job_id is None:
        raise HTTPException(
            status_code=400,
            detail="这份配置没有 seed 变量，不能重复运行（只支持 1 次）",
        )
    return {"job_id": job_id}


@app.get("/api/run/{job_id}")
def api_job(job_id: str) -> dict:
    state = runner.job_state(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    state["position"] = runner.queue_position(job_id)
    return state


@app.get("/api/run/{job_id}/logs")
def api_job_logs(job_id: str) -> StreamingResponse:
    if runner.job_state(job_id) is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return StreamingResponse(
        runner.stream(job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/run/{job_id}/cancel")
def api_job_cancel(job_id: str) -> dict:
    if not runner.cancel(job_id):
        raise HTTPException(status_code=400, detail="这个任务已经结束或无法取消")
    return {"ok": True}


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
