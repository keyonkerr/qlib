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
├── compare.py          对照：多 run 对齐、差异与最优判定（纯函数）
├── runner.py           磁盘扫描 + 串行训练队列 + SSE 日志（唯一起子进程的模块）
└── static/             前端，见 static/AGENTS.md
    ├── index.html
    ├── app.js
    └── style.css
```

依赖方向是单向的：`app.py` → `store.py` / `metrics.py` / `analysis.py` / `compare.py` / `runner.py`。`metrics.py` 和 `analysis.py` 是纯函数，无 I/O，可以直接单测。`compare.py` 同样是纯函数——它只认 `store.get_run` 返回的 dict，不发请求、不读文件。`runner.py` 只管磁盘与进程，不读 mlruns 记录（要判断「这个 run 是不是刚跑出来的」，它调 `store.list_runs` 取差集，这是唯一的交叉点）。

## 启动

```bash
cd examples/dashboard && ../../.venv/bin/uvicorn app:app --port 8766
```

**不要加 `--reload`。** 训练队列和子进程都在 uvicorn 进程的内存里，reload 会在每次改代码时杀掉正在跑的训练。代价是改后端代码需要手动重启——比起悄悄中断一次 5 分钟的训练，这个代价更低。

Windows 上：

```bash
cd examples/dashboard && MLFLOW_ALLOW_FILE_STORE=true python -m uvicorn app:app --port 8766
```

`MLFLOW_ALLOW_FILE_STORE` 是 MLflow 3.x 对 file store 后端的硬性要求，不设会直接抛异常。`.claude/launch.json` 已经配好一个 `dashboard-win` 条目。

## API

| 端点 | 返回 |
|---|---|
| `GET /api/configs` | **主入口**：按 YAML 分组的 run，每组带 `runs` 列表、`seeds`、`aggregate`（多次运行的极差） |
| `GET /api/runs` | 全部 run（扁平）+ 每个 run 的 `summary` 与 `config` 身份 |
| `GET /api/runs/{run_id}` | `run` / `summary` / `verdict` / `sub` / `notes` |
| `GET /api/runs/{run_id}/predictions?date=&top=` | 某交易日逐股预测分数（`pred.pkl`）：`rows`（排名/代码/分数）、`stats`、`dates`、`exec_date`、`held` |
| `GET /api/runs/{run_id}/positions?date=` | 某日实际持仓（`positions_normal_1day.pkl`）：`holdings`（股数/成本价/权重/持仓天数）、现金、总资产、`signal_date` |
| `GET /api/runs/{run_id}/position-signal?date=&topk=` | 持仓日 vs 信号日 topK 的差集：`overlap`、`top_not_held`、`held_not_top` |
| `GET /api/runs/{run_id}/prediction-vs-label?date=` | 预测分 vs 实际收益（`label.pkl`）：`rows`、五等分 `buckets`、`label_available` |
| `GET /api/runs/{run_id}/artifact?path=` | 单个产物内容预览：类型/shape/列名/前几行，文本文件直接给原文 |
| `GET /api/runs/{run_id}/feature-importance?top=` | 从 `params.pkl` 读模型的特征重要性；模型不可反序列化时返回 `available: false` 与原因 |
| `GET /api/compare?runs=a,b[,c,d]` | 2–4 个 run 的对照：`runs`（槽位 A–D）、`warnings`（可比性）、`sections`（八节逐行）、`param_diff` / `param_same`、`tags`、`artifacts`、`risk`、`charts`、`verdict`、`yaml` |
| `GET /api/configs/{path}` | 磁盘上一份 YAML 的当前内容 + 它的 run 列表 + 正在跑的任务 |
| `POST /api/run` | body `{path, repeat}`，入队并返回 `{job_id}` |
| `GET /api/run/{job_id}` | 任务状态快照（轮询兜底）：`queued` / `running` / `done` / `failed` |
| `GET /api/run/{job_id}/logs` | SSE 日志流，逐行推送，结束发 `event: end` |
| `POST /api/run/{job_id}/cancel` | 终止运行中的进程，或把排队中的任务直接出队 |
| `GET /renders/{run_id}/{filename}` | run 的 `artifacts/renders/` 下的 html |

`/api/configs` 的每个分组除原有字段外还带 `path` / `dir` / `mtime` / `repeatable` / `runnable` / `job`。**磁盘上有、但还没跑完过的 YAML 也会出现在列表里**（`run_count: 0`），前端把它们归到「未运行」组。`runnable: false` 表示源文件已不在磁盘上（临时 seed 文件被删、或 YAML 改了名），这类分组只保留历史查看，不显示运行按钮。

**配置身份从文件名解析。** `store._config_identity` 把 `00_baseline_s43.yaml` 归一成 key `00_baseline` + seed `43`。`run_all.py --repeat` 生成的临时文件跑完即删，只有这一步能在它消失前把 seed 提出来，否则每次重复运行都会变成一个独立配置。

`run` 里除摘要指标外还带以下原样数据，供详情页展示：

| 字段 | 内容 |
|---|---|
| `all_metrics` / `all_params` / `tags` | MLflow 记录的每一个条目，不做筛选 |
| `artifacts` | 产物目录树（路径、是否目录、大小） |
| `extras.training_curve` | `l2.train` / `l2.valid` 的逐轮历史（`get_metric_history`） |
| `extras.*_series` | IC / Rank IC / 收益 / 基准 / 换手 / 成本 / **总资产 / 持仓市值 / 现金**的逐日序列，含 `stats` |
| `extras.deal_series` | `indicators_normal_1day.pkl` 的逐日 `deal_amount` / `value` / `count` / `ffr` |
| `extras.model` | `model.class` + 该模型类型实际生效的超参（按 `MODEL_HYPER_KEYS` 白名单取） |
| `extras.config_full` | 解析后的完整配置树（含 `task`），带深度与条目上限 |
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

**对照是「行是字段、列是 run」。** `compare.py` 的 `SPEC` 表把八个章节的字段、格式（`kind`）和判优方向（`direction`）写在一处；前端只按这些标记格式化和着色，不做任何比较运算。两条原则：

- **相同判定用 1e-12 容差**（同 `store._aggregate`）。确定性配置的 seed 重复会差 1e-16，那不是差异。
- **身份字段天然不同**（run id、开始时间、耗时），在 `IDENTITY_FIELDS` 里，不上赭色——否则真信号被淹没。

**叠加图按日期对齐，不按数组下标。** 两个 run 的回测起点常差几天；按 index 对齐会让曲线整体错位。`compare._chart` 取日期并集，缺点为 `null`，训练曲线按轮次对齐。

**累计超额在后端算。** qlib 的口径是 `return - bench` 与 `return - bench - cost`（`qlib/workflow/record_temp.py`），`store._excess_series` 先在**全量序列**上累加再下采样，和序列统计同一条规则。两列各自 try：缺 `cost` 列时无成本那条仍然画得出来。

**git commit 差异必须显示。** 同一份配置在两个 commit 上跑，不是同一个实验。`compare._tags` 只跳过 `mlflow.user` / `mlflow.runName`，`mlflow.source.git.commit` 保留（显示前 8 位，相同判定用完整值）。

**撮合指标失效有专门识别。** `pa` 和 `pos` 恰好为 0（不是接近 0）是 qlib 退化成复权价模式的签名，`analysis.py` 会直接指出 `factor.day.bin` 缺失，避免误读成「成交完美」。

**信号日与执行日相差一个交易日。** `TopkDropoutStrategy` 在 T 日交易时用 T-1 日信号（`qlib/contrib/strategy/signal_strategy.py:141` 的 `shift=1`）。所以：

- `predictions` 默认取**倒数第二个**交易日——最后一天的分数没有执行日，标不出「持有/未持有」；
- `positions` 默认取最后一天（账本最完整）；
- 前端拉持仓时用 `predictions.exec_date` 对齐，不自己推算日期；
- 每行「持有」标签由后端 `predictions.held` 给出，不用当日持仓自己算——否则标签数与差集 `overlap` 对不上。

实测过：拿错日子会让「持有」数和 overlap 差出 9 个，页面数字自相矛盾。

**持仓数可以不是 topk。** 涨跌停或停牌会导致少数委托当天没成交，实测某日 49 只而非 50。页面上 `overlap + top_not_held` 应等于 topk（信号侧），`overlap + held_not_top` 应等于实际持仓数（持仓侧），两个恒等式都写进说明里，便于自查。

**明细数据懒加载 + 进程内 LRU。** `pred.pkl` / `label.pkl` / `positions` 各 2–3MB，按日期切片后单次只有几百行。`store.py` 用 `_PRED_CACHE` / `_POS_CACHE` / `_LABEL_CACHE` 三个 OrderedDict 做 LRU（上限 4 个 run）。首屏只拉 `/api/runs/{id}`，明细由 `loadDetails()` 并行补拉，失败只少一节、不阻塞整页。

**产物预览必须防路径穿越。** `store.resolve_artifact` 拒绝绝对路径和 `..` 段，并把解析结果限制在该 run 自己的 artifacts 目录内。

**store.py 是唯一的数据边界。** 换 SQLite 或远程 MLflow server 只改这一个文件。所有上层模块只认 dict。

**产物解析也收在 store.py。** 逐日序列、训练曲线、风险表、config、git 状态都从 run 的 artifacts 目录读 pkl / 文本，用 `_load_pickle`（兼容 gzip）。前端不碰二进制，只拿 JSON。读取限定在该 run 自己的 artifacts 目录内。

**配置原文必须是快照，不能是活引用。** qlib 只把解析后的 config 记进 run，注释全部丢失。如果看板按路径实时读源文件，改动 YAML 会让历史 run 显示成另一份配置——历史就被篡改了；批量跑时更糟：`run_all.py --repeat` 写一个临时改 seed 的 YAML，跑完立刻删除，根本没有文件可引用。

所以运行时把 YAML 拷贝进 `artifacts/config_source.yaml`（`examples/tools/snapshot_config.py`，两个 `run_all` 都已接入）。读取优先级：快照 → 回读磁盘 + 一致性校验。回读时用 `_params_agree` 把 YAML 的 `task` 子树反嵌套成点号键，和 MLflow 记录的 params 比对，不一致就在页面上标红说明「这份不代表当时跑的配置」。

存量 run 补快照：`cd examples && MLFLOW_ALLOW_FILE_STORE=true python tools/snapshot_config.py --all`。已存在的快照不会被覆盖。

**路径穿越防护在 store.render_path。** 只允许 `artifacts/renders/` 下的 `.html`，拒绝含斜杠或点开头的文件名。`/renders` 用专门路由而不是静态挂载 `mlruns/`，避免暴露 `pred.pkl` 等二进制。

**训练触发是串行队列，不是并发池。** `runner.py` 只有一个消费者线程。qlib 每次加载二进制行情数据要吃几百 MB，两个任务并发就是 OOM——这是从 `qlib_init.kernels: 1` / `num_threads: 2` 那组省内存配置直接推出来的硬约束，不是保守。

**子进程的 cwd 必须是 `examples/`。** 三个独立原因，任何一个错了都会坏：

1. `qlib/cli/run.py` 把 MLflow 目录定为 `cwd/mlruns`，cwd 错了 run 就写进另一个 store，看板完全看不见；
2. `23_custom_factors.yaml` 用 `module_path: path_exps/custom_handler.py`，`qlib/utils/mod.py` 按进程 cwd 解析；
3. 所有配置里的相对路径都按 `examples/` 解释。

**命令行参数用正斜杠相对路径。** `store._run_name` 从 `cmd-sys.argv` 按 `/` 切出文件名作为 run 名，传 Windows 反斜杠会把整条 argv 当成名字，索引分组会全部坍缩成一个。

**重复运行要照抄 `path_exps/run_all.py` 的三步顺序。** 改 seed 写临时 `<name>_s<seed>.yaml` → 跑 → **先快照再删临时文件**。顺序反了，`snapshot_config.py` 找不到源文件，这个 run 的「配置原文」就永久缺失。`snapshot_config.py` 无参时只处理 `runs[:1]`，所以必须传 `--run-id`，运行前记下实验的 run id 集合、跑完取差集。

**`_s<seed>` 后缀不是装饰。** `store._config_identity` 靠它把重复运行归到同一个配置下并提取 seed。临时文件用别的名字，每次重复都会变成一个独立配置。

**只有 `path_exps` 支持重复次数。** `param_exps` 的 YAML 没有实验级 seed（那里的 `seed` 是 LightGBM 超参），`run_all.sh` 也没有 `--repeat`。前端只在 `repeatable: true` 时显示次数下拉，不是靠目录名在 UI 里硬编码判断。

**队列只存内存。** 看板进程重启，排队和运行中的任务全丢。这是有意的取舍：持久化长任务队列要处理孤儿进程、状态恢复、重复提交，成本远高于收益——真需要长跑就用 `run_all.py`。页面上会写明这一点。

**SSE 先重放再续流。** 晚订阅的客户端会先收到已缓冲的日志（最近 2000 行），再接实时行；空闲时每 15 秒发一个注释行保活，避免代理掐连接。

**配置详情页读磁盘，不读快照。** 那一页回答的是「这份文件现在是什么」，运行按钮用的也是它。每个 run 的「配置原文」才是当时冻结的快照（`随 run 冻结` 徽章）。两者语义不同，不要合并。

## 做与不做

**做**：直接搬静态文件、复用 `report_graphs.py`、串行任务队列。

**不做**：前端框架（状态只有三个变量）、数据库（文件系统就是索引）、构建步骤（改一行要等编译不值得）、用户系统（本机自用）。

## 后续要加的东西

1. **出图触发**：复用 `runner.py` 的队列，跑 `report_graphs.py --run-id`。比训练触发简单，但能立刻解决大部分 run 没图的现实问题。
2. **队列持久化**：进程重启后恢复排队任务。要做的话得先决定孤儿进程怎么认领——`runner.py` 现在跑完不管，重启后无法判断上次的子进程是否还活着。
3. **换手率与成本**：需要读 `report_normal_1day.pkl`，做成懒加载 + 内存缓存。
