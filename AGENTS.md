# AGENTS.md

本仓库是 [microsoft/qlib](https://github.com/microsoft/qlib) 的本地克隆，用于跑通官方示例、做参数与路径实验，并在其上搭建了一个实验看板。

约定：注释与文档使用中文，代码标识符保持英文。

## 目录结构

```
qlib/                          上游 qlib 源码（通常不改动）
├── qlib/                      Python 包：数据、模型、回测、工作流
├── scripts/                   数据采集与转换脚本
├── tests/                     上游测试
└── examples/                  本地实验与工具（下面详述）

examples/
├── my_first_run.yaml          最简可跑配置（LightGBM + Alpha158 + csi300）
├── workflow_*.yaml            官方示例配置的本地副本
├── param_exps/                参数实验：13 个单变量对照组
│   ├── *.yaml                 每个配置只改一处，跑完与基线对比
│   ├── run_all.py             按序跑全部配置，支持 --only / --repeat
│   └── compare.py             读 mlruns 汇总成对比表
├── path_exps/                 路径实验：五条改进路径各改一处
│   ├── 00_baseline.yaml       基线（带完整中文注释，其余配置以它为参照）
│   ├── 11~13_*.yaml           路径①：换标签
│   ├── 21~23_*.yaml           路径②：换股票池 / 因子集
│   ├── 31~32_*.yaml           路径③：换模型
│   ├── run_all.py             批量运行
│   ├── sweep.py               复用已有预测，只重跑回测部分
│   └── compare.py             横向对比表
├── report_graphs.py           调 qlib 官方 report 接口生成 Plotly 图表，写入 run 的 artifacts/renders/
├── dashboard/                 实验看板（FastAPI + 原生 JS，无构建步骤）
├── tools/
│   └── snapshot_config.py     把配置 YAML 冻结进 run 的 artifacts（历史可复现）
└── mlruns/                    MLflow 实验记录（已 gitignore）

docs_readable/                 中文学习笔记，按「跑通 → 理解 → 实验」组织
```

## 常用命令

全部命令在 `examples/` 目录下执行，使用仓库自带的 `.venv`（Python 3.11）：

```bash
source ../.venv/bin/activate
```

跑单个配置：

```bash
python -m qlib.cli.run path_exps/00_baseline.yaml
```

批量跑路径实验：

```bash
python path_exps/run_all.py --only 1
```

生成图表（写入对应 run 的 artifacts/renders/）：

```bash
python report_graphs.py --run-id <run_id>
```

启动看板：

```bash
cd dashboard && ../../.venv/bin/uvicorn app:app --reload --port 8766
```

为存量 run 补配置快照（批量运行已自动执行，只有旧 run 需要手动补）：

```bash
MLFLOW_ALLOW_FILE_STORE=true python tools/snapshot_config.py --all
```

## 环境注意事项

- **必须用 `.venv` 里的 Python**。系统 Python 是 3.13 且没装 qlib，直接 `python` 会报 `No module named 'qlib'`。
- **必须以 `examples/` 为工作目录**跑 `qlib.cli.run`，配置里的相对路径按此解析。
- `mlruns/` 已被 `.gitignore` 忽略。实验记录体积大（含 pkl 二进制），不入库；需要版本化的是产生它们的脚本和配置。
- 日志里出现 `pa` / `pos` 恒为 0、`factor.day.bin not exists` 时，说明数据目录缺复权因子文件，撮合指标不可用。这不影响 IC、收益、回撤。

## 已知约束

- `qlib_init.kernels: 1` 和 `num_threads: 2` 是为省内存压低的配置。并发跑多个实验会撞内存墙，批量运行必须串行。
- MLflow 3.x 对 file store 后端会刷弃用警告，已在 `dashboard/store.py` 中过滤。
- 上游代码尽量不改动；本地新增内容集中在 `examples/` 和 `docs_readable/`。
