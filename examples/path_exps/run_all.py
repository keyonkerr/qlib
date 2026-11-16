"""Run the path_exps training configs one by one.

Usage (from the examples/ directory):

    python path_exps/run_all.py --list
    python path_exps/run_all.py                       # all 9 configs, baseline first
    python path_exps/run_all.py --only 00 11 31       # prefix filter
    python path_exps/run_all.py --only 00 --repeat 3  # 3 seeds (42,43,44) to gauge noise

Each config is a full qrun training run: results go to examples/mlruns under
experiment "path_exps", logs to path_exps/logs/<name>.log.

Paths 4 (portfolio) and 5 (execution) are backtest-only and need a finished
baseline; run them with `python path_exps/sweep.py --which all`.
"""

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE.parent
LOGS = HERE / "logs"

ENV = dict(
    os.environ,
    MLFLOW_ALLOW_FILE_STORE="true",  # mlflow>=3 refuses the file store without this
    # qlib opens YAML with the locale encoding. 00_baseline.yaml has Chinese comments
    # (UTF-8), which GBK cannot decode on Chinese Windows - keep this explicit.
    PYTHONUTF8="1",
    OMP_NUM_THREADS="2",
    MKL_NUM_THREADS="2",
)

SEED_RE = re.compile(r"(\n\s+seed:\s*)\d+")


def all_configs():
    return sorted(p for p in HERE.glob("[0-9]*.yaml"))


def select(files, prefixes):
    if not prefixes:
        return files
    out = []
    for p in files:
        if any(p.stem.startswith(pre) for pre in prefixes):
            out.append(p)
    missing = [pre for pre in prefixes if not any(p.stem.startswith(pre) for p in out)]
    if missing:
        sys.exit(f"no config matches: {missing}")
    return out


def run_one(yaml_path: Path, seed=None):
    """Run one config; with `seed`, run a temporary copy whose `seed:` value is replaced."""
    name = yaml_path.stem
    tmp = None
    if seed is not None:
        text = yaml_path.read_text(encoding="utf-8")
        new_text, n = SEED_RE.subn(rf"\g<1>{seed}", text)
        if n != 1:
            # configs without a seed key (e.g. linear) simply run as-is
            print(f"  [warn] {name}: found {n} `seed:` entries, running unmodified copy")
        tmp = HERE / f"{name}_s{seed}.yaml"
        tmp.write_text(new_text, encoding="utf-8", newline="\n")
        yaml_path, name = tmp, tmp.stem

    rel = f"path_exps/{yaml_path.name}"
    log = LOGS / f"{name}.log"
    LOGS.mkdir(exist_ok=True)
    started = time.time()
    print(f"=== [{time.strftime('%H:%M:%S')}] start {name}", flush=True)
    with log.open("w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.run(
            [sys.executable, "-m", "qlib.cli.run", rel],
            cwd=EXAMPLES,
            env=ENV,
            stdout=fh,
            stderr=subprocess.STDOUT,
        )
    elapsed = (time.time() - started) / 60
    ok = proc.returncode == 0
    print(f"=== [{time.strftime('%H:%M:%S')}] {'done' if ok else 'FAILED'} {name} "
          f"({elapsed:.1f} min){'' if ok else f' -- see {log}'}", flush=True)
    if tmp is not None:
        tmp.unlink(missing_ok=True)
    return ok, elapsed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None, help="only configs whose name starts with these prefixes")
    ap.add_argument("--repeat", type=int, default=1, help="repeat each config with seeds 42..42+N-1")
    ap.add_argument("--list", action="store_true", help="list configs and exit")
    args = ap.parse_args()

    configs = all_configs()
    if args.list:
        for p in configs:
            print(p.name)
        return

    configs = select(configs, args.only)
    total_ok = total_fail = 0
    started = time.time()
    for p in configs:
        seeds = [None] if args.repeat <= 1 else range(42, 42 + args.repeat)
        for seed in seeds:
            ok, _ = run_one(p, seed=seed)
            total_ok += ok
            total_fail += not ok
    mins = (time.time() - started) / 60
    print(f"\nfinished: {total_ok} ok, {total_fail} failed, {mins:.1f} min total")
    print("compare training runs:  python path_exps/compare.py")
    print("paths 4/5 (backtest sweep): python path_exps/sweep.py --which all")


if __name__ == "__main__":
    main()
