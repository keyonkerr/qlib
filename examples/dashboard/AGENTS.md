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
| `GET /api/runs` | 全部 run + 每个 run 的 `summary`（已算好的 delta、年化值） |
| `GET /api/runs/{run_id}` | `run` / `summary` / `verdict` / `sub` / `notes` / `renders` 清单 |
| `GET /renders/{run_id}/{filename}` | run 的 `artifacts/renders/` 下的 html |

## 关键设计决策

**指标口径只在一处计算。** `metrics.py` 是唯一的计算来源，API 返回算好的 delta 和年化值，前端不做任何指标运算。这样表格和解读文案不可能对不上。

**解读是规则表，不是 LLM。** `analysis.py` 里是常量阈值 + 函数，每条带注释说明经验依据。理由：措辞稳定才能跨 run 对比；每次调模型会让历史 run 的解读失去可比性。改阈值只需要动文件顶部那几个常量。

**阈值写的是 A 股日频的经验值**（IC 合格线 0.02~0.03、IR 过 1 可用），换市场或频率需要重新标定。

**撮合指标失效有专门识别。** `pa` 和 `pos` 恰好为 0（不是接近 0）是 qlib 退化成复权价模式的签名，`analysis.py` 会直接指出 `factor.day.bin` 缺失，避免误读成「成交完美」。

**store.py 是唯一的数据边界。** 换 SQLite 或远程 MLflow server 只改这一个文件。所有上层模块只认 dict。

**路径穿越防护在 store.render_path。** 只允许 `artifacts/renders/` 下的 `.html`，拒绝含斜杠或点开头的文件名。`/renders` 用专门路由而不是静态挂载 `mlruns/`，避免暴露 `pred.pkl` 等二进制。

## 做与不做

**做**：直接搬静态文件、复用 `report_graphs.py`、串行任务队列。

**不做**：前端框架（状态只有三个变量）、数据库（文件系统就是索引）、构建步骤（改一行要等编译不值得）、用户系统（本机自用）。

## 后续要加的东西

1. **出图触发**：`runner.py` + SSE 日志流。比训练触发简单，但能立刻解决大部分 run 没图的现实问题。
2. **训练触发**：同一个队列，换命令行。**必须串行**——qlib 每次加载数据吃几百 MB，并发会 OOM。
3. **换手率与成本**：需要读 `report_normal_1day.pkl`，做成懒加载 + 内存缓存。
