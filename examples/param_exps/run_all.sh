#!/usr/bin/env bash
# 批量运行 param_exps 下的参数对照实验。
#
#   ./run_all.sh                 # 跑全部（约 50 分钟）
#   ./run_all.sh 01 03 11        # 只跑编号以这些前缀开头的配置
#   ./run_all.sh --list          # 列出所有配置
#
# 每个配置都是一次独立的 qrun；结果写入 examples/mlruns 下的 param_exps 实验。

set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

PY=../.venv/bin/python
export MLFLOW_ALLOW_FILE_STORE=true
export OMP_NUM_THREADS=2
export MKL_NUM_THREADS=2

if [[ "${1:-}" == "--list" ]]; then
    for f in param_exps/*.yaml; do basename "$f" .yaml; done
    exit 0
fi

shopt -s nullglob
files=()
if [[ $# -eq 0 ]]; then
    files=(param_exps/*.yaml)
else
    for prefix in "$@"; do
        matched=(param_exps/"${prefix}"*.yaml)
        if [[ ${#matched[@]} -eq 0 ]]; then
            echo "没有匹配的配置: $prefix" >&2
        fi
        files+=("${matched[@]}")
    done
fi
shopt -u nullglob

if [[ ${#files[@]} -eq 0 ]]; then
    echo "没有可运行的配置。" >&2
    exit 1
fi

echo "将运行 ${#files[@]} 个配置："
printf '  %s\n' "${files[@]}"
echo

ok=0
fail=0
for f in "${files[@]}"; do
    name=$(basename "$f" .yaml)
    log="param_exps/${name}.log"
    printf '=== [%s] 开始 %s\n' "$(date +%H:%M:%S)" "$name"
    if $PY -m qlib.cli.run "$f" > "$log" 2>&1; then
        echo "=== [$(date +%H:%M:%S)] 完成 $name"
        ok=$((ok + 1))
    else
        echo "=== [$(date +%H:%M:%S)] 失败 $name（详见 $log）"
        tail -5 "$log"
        fail=$((fail + 1))
    fi
done

echo
echo "完成 $ok 个，失败 $fail 个。"
echo "查看对比结果: $PY param_exps/compare.py"
