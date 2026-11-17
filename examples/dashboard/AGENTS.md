# AGENTS.md · dashboard

实验看板。读 `examples/mlruns`，把 MLflow 记录变成可横向对比的 run 列表，并把每个 run 的内部结构（图表）和中文解读放在同一页。

前端部分（视觉风格、配色、字体、交互）的约定见 `static/AGENTS.md`。

## 目录结构

```
dashboard/
├── app.py              FastAPI 应用：路由、静态挂载
├── store.py            唯一 import mlflow 的模块，把 run 读成 dict
├── metrics.py          口径换算：年化、delta、成本损耗
├── analysis.py         规则引擎：指标 → 中文解读
└── static/             前端，见 static/AGENTS.md
    ├── index.html
    ├── app.js
    └── style.css
```

依赖方向是单向的：`app.py` → `store.py` / `metrics.py` / `analysis.py`。`metrics.py` 和 `analysis.py` 是纯函数，无 I/O，可以直接单测。

## 启动

```bash
cd examples/dashboard && ../../.venv/bin/uvicorn app:app --reload --port 8766
```

## API

| 端点 | 返回 |
|---|---|
| `GET /api/configs` | **主入口**：按 YAML 分组的 run，每组带 `runs` 列表、`seeds`、`aggregate`（多次运行的极差） |
| `GET /api/runs` | 全部 run（扁平）+ 每个 run 的 `summary` 与 `config` 身份 |
| `GET /api/runs/{run_id}` | `run` / `summary` / `verdict` / `sub` / `notes` |
| `GET /renders/{run_id}/{filename}` | run 的 `artifacts/renders/` 下的 html |

**配置身份从文件名解析。** `store._config_identity` 把 `00_baseline_s43.yaml` 归一成 key `00_baseline` + seed `43`。`run_all.py --repeat` 生成的临时文件跑完即删，只有这一步能在它消失前把 seed 提出来，否则每次重复运行都会变成一个独立配置。

`run` 里除摘要指标外还带以下原样数据，供详情页展示：

| 字段 | 内容 |
|---|---|
| `all_metrics` / `all_params` / `tags` | MLflow 记录的每一个条目，不做筛选 |
| `artifacts` | 产物目录树（路径、是否目录、大小） |
| `extras.training_curve` | `l2.train` / `l2.valid` 的逐轮历史（`get_metric_history`） |
| `extras.*_series` | IC / Rank IC / 收益 / 基准 / 换手 / 成本的逐日序列，含 `stats` |
| `extras.risk_table` | `port_analysis_1day.pkl` 的风险拆解 |
| `extras.dataset_inputs` | 反序列化的 DatasetH：handler 类名与三段日期 |
| `extras.config_summary` | config 产物里的顶层配置（不含 task 树） |
| `extras.config_yaml` | 配置原文。优先读 run 自带的快照，无快照时回读源文件并标注 |
| `extras.code_diff` / `code_status` | 运行时 git 状态与未提交改动 |

**序列的统计在后端算。** `extras.*_series` 的 `values` 会下采样到 400 点以控制 JSON 体积，但 `stats`（均值、分位、为正占比）是在**全量序列**上算的——前端不做任何指标运算，这条规则在序列这里同样成立。

## 关键设计决策

**指标口径只在一处计算。** `metrics.py` 是唯一的计算来源，API 返回算好的 delta 和年化值，前端不做任何指标运算。这样表格和解读文案不可能对不上。

**解读是规则表，不是 LLM。** `analysis.py` 里是常量阈值 + 函数，每条带注释说明经验依据。理由：措辞稳定才能跨 run 对比；每次调模型会让历史 run 的解读失去可比性。改阈值只需要动文件顶部那几个常量。

**阈值写的是 A 股日频的经验值**（IC 合格线 0.02~0.03、IR 过 1 可用），换市场或频率需要重新标定。

**撮合指标失效有专门识别。** `pa` 和 `pos` 恰好为 0（不是接近 0）是 qlib 退化成复权价模式的签名，`analysis.py` 会直接指出 `factor.day.bin` 缺失，避免误读成「成交完美」。

**store.py 是唯一的数据边界。** 换 SQLite 或远程 MLflow server 只改这一个文件。所有上层模块只认 dict。

**产物解析也收在 store.py。** 逐日序列、训练曲线、风险表、config、git 状态都从 run 的 artifacts 目录读 pkl / 文本，用 `_load_pickle`（兼容 gzip）。前端不碰二进制，只拿 JSON。读取限定在该 run 自己的 artifacts 目录内。

**配置原文必须是快照，不能是活引用。** qlib 只把解析后的 config 记进 run，注释全部丢失。如果看板按路径实时读源文件，改动 YAML 会让历史 run 显示成另一份配置——历史就被篡改了；批量跑时更糟：`run_all.py --repeat` 写一个临时改 seed 的 YAML，跑完立刻删除，根本没有文件可引用。

所以运行时把 YAML 拷贝进 `artifacts/config_source.yaml`（`examples/tools/snapshot_config.py`，两个 `run_all` 都已接入）。读取优先级：快照 → 回读磁盘 + 一致性校验。回读时用 `_params_agree` 把 YAML 的 `task` 子树反嵌套成点号键，和 MLflow 记录的 params 比对，不一致就在页面上标红说明「这份不代表当时跑的配置」。

存量 run 补快照：`cd examples && MLFLOW_ALLOW_FILE_STORE=true python tools/snapshot_config.py --all`。已存在的快照不会被覆盖。

**路径穿越防护在 store.render_path。** 只允许 `artifacts/renders/` 下的 `.html`，拒绝含斜杠或点开头的文件名。`/renders` 用专门路由而不是静态挂载 `mlruns/`，避免暴露 `pred.pkl` 等二进制。

## 做与不做

**做**：直接搬静态文件、复用 `report_graphs.py`、串行任务队列。

**不做**：前端框架（状态只有三个变量）、数据库（文件系统就是索引）、构建步骤（改一行要等编译不值得）、用户系统（本机自用）。

## 后续要加的东西

1. **出图触发**：`runner.py` + SSE 日志流。比训练触发简单，但能立刻解决大部分 run 没图的现实问题。
2. **训练触发**：同一个队列，换命令行。**必须串行**——qlib 每次加载数据吃几百 MB，并发会 OOM。
3. **换手率与成本**：需要读 `report_normal_1day.pkl`，做成懒加载 + 内存缓存。
