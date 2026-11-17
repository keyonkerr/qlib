"""Serial task queue: scan config YAMLs on disk and launch training.

The dashboard's only module that spawns processes. Everything that reads
mlruns stays in ``store.py``; this module owns "what files exist, what is
running, and what did the run print".

Why a single consumer thread and not a pool: qlib loads the whole binary
dataset (several hundred MB) per process. Two runs at once is an OOM on this
machine, so the queue is serial by construction, not by configuration.

Why the child runs with ``cwd=examples/``: ``qlib/cli/run.py`` pins the MLflow
store to ``cwd/mlruns``, and file-based ``module_path`` entries (e.g.
``path_exps/custom_handler.py``) resolve against cwd too. Launching from
anywhere else writes the run to a store the dashboard never reads.

Why the config argument uses forward slashes: ``store._run_name`` splits
``cmd-sys.argv`` on ``/`` to get the run name. A Windows path would become the
whole argv string and every config group would collapse into one.
"""

from __future__ import annotations

import itertools
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Iterator, Optional

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

# Only these directories are scanned, and only their direct *.yaml files.
# A path not in the scan result is not runnable.
CONFIG_DIRS = ("path_exps", "param_exps")

# Children inherit the parent env plus these. Mirrors path_exps/run_all.py:29-37;
# PYTHONUTF8 matters because some YAMLs carry Chinese comments and Windows
# open() would otherwise try GBK.
ENV = {
    **os.environ,
    "MLFLOW_ALLOW_FILE_STORE": "true",
    "PYTHONUTF8": "1",
    "OMP_NUM_THREADS": "2",
    "MKL_NUM_THREADS": "2",
}

# `run_all.py --repeat` rewrites exactly this one line per file. The regex is
# copied verbatim so a repeat launched here produces the same seed variants.
SEED_RE = re.compile(r"(\n\s+seed:\s*)\d+")

# path_exps is the suite with the seed knob and a --repeat workflow; param_exps
# varies one hyper-parameter per file and has none. A file without a literal
# `seed:` line cannot be varied at all (31_linear is the linear model — no seed
# knob exists there), so repeatability needs both the directory and the line.
REPEAT_DIRS = ("path_exps",)

LOG_TAIL = 2000


def scan_configs() -> list[dict]:
    """Every launchable YAML on disk, with the info the index needs.

    ``key`` matches ``store.list_configs``' group key (the file stem) and
    ``experiment`` matches the index group, so the two lists join on
    ``(experiment, key)``.
    """
    out = []
    for d in CONFIG_DIRS:
        for p in sorted((EXAMPLES / d).glob("[0-9]*.yaml")):
            rel = f"{d}/{p.name}"
            text = p.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^experiment_name:\s*(\S+)", text, re.M)
            has_seed = bool(SEED_RE.search(text))
            out.append({
                "path": rel,
                "experiment": m.group(1).strip("'\"") if m else d,
                "key": p.stem,
                "dir": d,
                "label": p.stem,
                "mtime": p.stat().st_mtime,
                "has_seed": has_seed,
                "repeatable": d in REPEAT_DIRS and has_seed,
            })
    return out


def _runnable(path: str) -> Optional[Path]:
    """Resolve a request path and refuse anything outside the scan whitelist.

    Same containment rule as ``store._resolve_in_examples``. Absolute paths and
    ``..`` both fail the relative_to test.
    """
    p = Path(path)
    if p.is_absolute():
        return None
    try:
        resolved = (EXAMPLES / p).resolve()
        resolved.relative_to(EXAMPLES.resolve())
    except (ValueError, OSError):
        return None
    if resolved.parent.parent != EXAMPLES.resolve() or not resolved.is_file():
        return None
    if resolved.parent.name not in CONFIG_DIRS or resolved.suffix != ".yaml":
        return None
    return resolved


# ── job records ───────────────────────────────────────────────────────────

class Job:
    __slots__ = (
        "id", "path", "repeat", "status", "lines", "exit_code",
        "created", "started", "ended", "proc", "error",
    )

    def __init__(self, job_id: str, path: str, repeat: int) -> None:
        self.id = job_id
        self.path = path
        self.repeat = repeat
        self.status = "queued"       # queued | running | done | failed
        self.lines: list[str] = []
        self.exit_code: Optional[int] = None
        self.created = time.time()
        self.started: Optional[float] = None
        self.ended: Optional[float] = None
        self.proc: Optional[subprocess.Popen] = None
        self.error: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "path": self.path,
            "repeat": self.repeat,
            "status": self.status,
            "exit_code": self.exit_code,
            "created": self.created,
            "started": self.started,
            "ended": self.ended,
            "error": self.error,
        }


_jobs: dict[str, Job] = {}
_order: "queue.Queue[str]" = queue.Queue()
_lock = threading.Lock()
_counter = itertools.count(1)
_worker_started = False

# Live SSE listeners; each gets every line of every job it is watching.
_listeners: dict[str, list[queue.Queue]] = {}
_listen_lock = threading.Lock()


def _publish(job: Job, line: str) -> None:
    with _listen_lock:
        for q in _listeners.get(job.id, []):
            q.put(line)


def _emit(job: Job, line: str) -> None:
    job.lines.append(line)
    if len(job.lines) > LOG_TAIL:
        del job.lines[: len(job.lines) - LOG_TAIL]
    _publish(job, line)


def _finish_listeners(job: Job) -> None:
    with _listen_lock:
        for q in _listeners.get(job.id, []):
            q.put(None)          # sentinel: stream ends
        _listeners.pop(job.id, None)


def _seed_variant(src: Path, seed: int) -> Path:
    """Write ``<stem>_s<seed>.yaml`` next to the original, same as run_all.py.

    ``_s<seed>`` is not cosmetic: ``store._config_identity`` reads the seed out
    of that suffix and groups the run under the base name. Any other name turns
    each repeat into a new config in the index.
    """
    text = src.read_text(encoding="utf-8")
    new_text, n = SEED_RE.subn(rf"\g<1>{seed}", text)
    tmp = src.with_name(f"{src.stem}_s{seed}.yaml")
    tmp.write_text(new_text, encoding="utf-8", newline="\n")
    return tmp


def _run_ids(experiment: str) -> set[str]:
    """Run ids in one experiment, used to find the run a launch just created."""
    import store
    return {r["run_id"] for r in store.list_runs() if r["experiment"] == experiment}


def _snapshot(run_id: str) -> None:
    """Freeze the YAML into this run's artifacts.

    Must happen while the temp seed file still exists: the snapshot tool reads
    the file on disk, and the temp file is deleted right after.
    """
    subprocess.run(
        [sys.executable, "tools/snapshot_config.py", "--run-id", run_id],
        cwd=EXAMPLES, env=ENV,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _execute(job: Job) -> None:
    src = EXAMPLES / job.path
    experiment = _experiment_of(src)
    before = _run_ids(experiment)

    seeds = [None] if job.repeat <= 1 else list(range(42, 42 + job.repeat))
    for seed in seeds:
        tmp: Optional[Path] = None
        target = src
        if seed is not None:
            tmp = _seed_variant(src, seed)
            target = tmp
        rel = f"{job.path.rsplit('/', 1)[0]}/{target.name}"
        _emit(job, f"$ python -m qlib.cli.run {rel}")
        proc = subprocess.Popen(
            [sys.executable, "-m", "qlib.cli.run", rel],
            cwd=EXAMPLES, env=ENV,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, encoding="utf-8", errors="replace",
        )
        job.proc = proc
        assert proc.stdout is not None
        for line in proc.stdout:
            _emit(job, line.rstrip("\n"))
        code = proc.wait()
        job.proc = None

        if code == 0:
            new_ids = _run_ids(experiment) - before
            for rid in new_ids:
                _snapshot(rid)
            before |= new_ids
            _emit(job, f"— 完成，退出码 0，新增 {len(new_ids)} 个 run")
        else:
            _emit(job, f"— 失败，退出码 {code}")
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        if code != 0:
            job.status = "failed"
            job.exit_code = code
            return

    job.status = "done"
    job.exit_code = 0


def _experiment_of(src: Path) -> str:
    text = src.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^experiment_name:\s*(\S+)", text, re.M)
    return m.group(1).strip("'\"") if m else src.parent.name


def _worker() -> None:
    while True:
        job_id = _order.get()
        with _lock:
            job = _jobs[job_id]
        job.status = "running"
        job.started = time.time()
        try:
            _execute(job)
        except Exception as exc:            # a crash must not kill the queue
            job.status = "failed"
            job.error = repr(exc)
            _emit(job, f"— 中断：{exc!r}")
        finally:
            job.ended = time.time()
            job.proc = None
            _finish_listeners(job)
            _order.task_done()


def _ensure_worker() -> None:
    global _worker_started
    with _lock:
        if _worker_started:
            return
        _worker_started = True
    t = threading.Thread(target=_worker, name="qlib-runner", daemon=True)
    t.start()


def submit(path: str, repeat: int = 1) -> Optional[str]:
    """Queue a launch. Returns the job id, or None when the path is refused."""
    resolved = _runnable(path)
    if resolved is None:
        return None
    repeat = max(1, min(int(repeat), 5))
    # A config without a seed line cannot be varied: copying it under five
    # names would just run the identical thing five times and pollute the index.
    if repeat > 1:
        scan = next((c for c in scan_configs() if c["path"] == path), None)
        if scan is None or not scan["repeatable"]:
            return None
    _ensure_worker()
    job_id = f"job{next(_counter)}"
    with _lock:
        _jobs[job_id] = Job(job_id, path, repeat)
    _order.put(job_id)
    return job_id


def job_state(job_id: str) -> Optional[dict]:
    with _lock:
        job = _jobs.get(job_id)
    return job.as_dict() if job else None


def job_log(job_id: str) -> Optional[list[str]]:
    with _lock:
        job = _jobs.get(job_id)
    return list(job.lines) if job else None


def queue_position(job_id: str) -> Optional[int]:
    """1-based position among jobs still waiting; None when already started."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None or job.status != "queued":
            return None
        pending = [j.id for j in _jobs.values() if j.status == "queued"]
    try:
        return pending.index(job_id) + 1
    except ValueError:
        return None


def running_job_for(path: str) -> Optional[dict]:
    """The queued/running job for this exact file, if any."""
    with _lock:
        for job in _jobs.values():
            if job.path == path and job.status in ("queued", "running"):
                return job.as_dict()
    return None


def cancel(job_id: str) -> bool:
    """Drop a queued job, or terminate the process of the running one."""
    with _lock:
        job = _jobs.get(job_id)
    if job is None or job.status in ("done", "failed"):
        return False
    if job.status == "queued":
        job.status = "failed"
        job.error = "cancelled"
        job.ended = time.time()
        _emit(job, "— 已取消（排队中）")
        _finish_listeners(job)
        return True
    proc = job.proc
    if proc is not None:
        proc.terminate()
        return True
    return False


def stream(job_id: str) -> Iterator[str]:
    """Yield log lines as they arrive, then stop when the job ends.

    Replays what is already buffered first, so a late subscriber sees the whole
    log. Sends a keepalive comment when idle so proxies do not close the
    connection during the quiet stretches of a training run.
    """
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        return
    backlog = list(job.lines)
    q: "queue.Queue" = queue.Queue()
    with _listen_lock:
        already_done = job.status in ("done", "failed")
        if not already_done:
            _listeners.setdefault(job_id, []).append(q)

    for line in backlog:
        yield _sse(line)
    if already_done:
        yield _sse_end()
        return

    while True:
        try:
            item = q.get(timeout=15)
        except queue.Empty:
            yield ": keepalive\n\n"
            continue
        if item is None:
            break
        yield _sse(item)
    yield _sse_end()


def _sse(line: str) -> str:
    # One SSE `data:` field per log line; a line may contain anything, but not
    # a newline (readline already split it).
    return f"data: {line}\n\n"


def _sse_end() -> str:
    return "event: end\ndata: \n\n"
