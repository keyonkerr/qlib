# Qlib 官方示例：逐步骤拆解

这份指南带你从零跑通官方 LightGBM + Alpha158 示例，并解释每一步在做什么、产出什么、结果从哪来。

---

## 前提条件（已完成，无需重做）

| 项目 | 状态 | 说明 |
|---|---|---|
| qlib 安装 | ✅ editable | `git clone` 后 `pip install -e .`，改源码立即生效 |
| Cython 扩展 | ✅ 已编译 | `qlib/data/_libs/*.pyd`，删掉会 import 失败 |
| 数据 | ✅ 已下载 | `~/.qlib/qlib_data/cn_data`（A 股日频，约 12 年） |
| lightgbm | ✅ 4.7.0 | |

---

## 第 0 步：为什么上次会卡死

这是理解后面所有配置的前提。

**qlib 默认并行度**（`qlib/config.py:182`）：

```python
"kernels": NUM_USABLE_CPU,   # NUM_USABLE_CPU = CPU核数 - 2
```

你的机器 24 核 → 默认 22 个 worker 进程。

**致命的组合**（Windows 特有）：
1. Windows 没有 `fork`，joblib 用 `spawn`
2. `spawn` 会**重新 import 主脚本**
3. 如果主脚本没有 `if __name__ == "__main__":` 守卫，每个子进程都会**重新执行整个脚本**
4. 子进程又去 spawn 22 个子孙进程 → 指数爆炸

**所以三条铁律**：
- 用 `kernels: 1`
- 脚本必须有 `if __name__ == "__main__":`
- 不要在 qlib 仓库根目录跑（会 import 到未编译源码）

**验证 `kernels: 1` 真的不 spawn**（我实测过）：

```python
from joblib import Parallel, delayed
import os
res = Parallel(n_jobs=1, backend="multiprocessing")(delayed(os.getpid)() for _ in range(3))
print(set(res), os.getpid())   # {父pid} —— 子任务也在父进程里跑
```

**`kernels: 0` 不能用**——joblib 直接抛 `ValueError: n_jobs == 0 in Parallel has no meaning`。

---

## 第 1 步：写配置文件

创建 `examples/my_first_run.yaml`：

```yaml
qlib_init:
    provider_uri: "~/.qlib/qlib_data/cn_data"
    region: cn
    kernels: 1                  # 关键：单进程

market: &market csi300
benchmark: &benchmark SH000300

data_handler_config: &data_handler_config
    start_time: 2008-01-01
    end_time: 2020-08-01
    fit_start_time: 2008-01-01
    fit_end_time: 2014-12-31
    instruments: *market

port_analysis_config: &port_analysis_config
    strategy:
        class: TopkDropoutStrategy
        module_path: qlib.contrib.strategy
        kwargs:
            signal: <PRED>
            topk: 50
            n_drop: 5
    backtest:
        start_time: 2017-01-01
        end_time: 2020-08-01
        account: 100000000
        benchmark: *benchmark
        exchange_kwargs:
            limit_threshold: 0.095
            deal_price: close
            open_cost: 0.0005
            close_cost: 0.0015
            min_cost: 5

task:
    model:
        class: LGBModel
        module_path: qlib.contrib.model.gbdt
        kwargs:
            loss: mse
            colsample_bytree: 0.8879
            learning_rate: 0.2
            subsample: 0.8789
            lambda_l1: 205.6999
            lambda_l2: 580.9768
            max_depth: 8
            num_leaves: 210
            num_threads: 2      # 24核机器上限制到2
    dataset:
        class: DatasetH
        module_path: qlib.data.dataset
        kwargs:
            handler:
                class: Alpha158
                module_path: qlib.contrib.data.handler
                kwargs: *data_handler_config
            segments:
                train: [2008-01-01, 2014-12-31]
                valid: [2015-01-01, 2016-12-31]
                test: [2017-01-01, 2020-08-01]
    record:
        - class: SignalRecord
          module_path: qlib.workflow.record_temp
          kwargs:
            model: <MODEL>
            dataset: <DATASET>
        - class: SigAnaRecord
          module_path: qlib.workflow.record_temp
          kwargs:
            ana_long_short: False
            ann_scaler: 252
        - class: PortAnaRecord
          module_path: qlib.workflow.record_temp
          kwargs:
            config: *port_analysis_config
```

### `&` 和 `*` 是什么

YAML 的**锚点（anchor）**和**引用（alias）**：

```yaml
market: &market csi300        # 定义锚点 market，值是 csi300
...
    instruments: *market      # 引用，展开成 csi300
```

好处：把 `market` 改成 `csi500`，所有引用它的地方一起变。做对照实验极方便。

---

## 第 2 步：运行

```bash
cd examples
```

```bash
MLFLOW_ALLOW_FILE_STORE=true OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python -m qlib.cli.run my_first_run.yaml
```

**为什么要 `MLFLOW_ALLOW_FILE_STORE=true`**：qlib 用 MLflow 记录实验，写 `./mlruns/` 目录。新版 MLflow 认为"文件后端"进入维护模式，默认拒绝。这个环境变量是官方给的逃生口。

**为什么建议用 `python -m qlib.cli.run` 而不是 `qrun`**：`qrun` 是 pyenv 的 shim，在 Git Bash 下有时会被搞坏（我就是这么撞上的）。`python -m` 更可靠。

---

## 第 3 步：每一步在做什么（核心）

`qrun` 内部执行的是 `qlib/model/trainer.py:44` 的 `_exe_task`。按顺序：

### ① 初始化

```python
qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region="cn", kernels=1)
```

**产出**：全局配置对象 `C` 被设置；内存缓存 `H` 被清空；数据路径校验通过。

**日志**：`qlib successfully initialized based on client settings.`

### ② 构造 Dataset（耗时最长：123 秒）

```python
dataset = init_instance_by_config(task_config["dataset"])
```

内部三步：

**(a) 构造 Alpha158 Handler**

```python
handler = Alpha158(instruments="csi300",
                   start_time="2008-01-01", end_time="2020-08-01",
                   fit_start_time="2008-01-01", fit_end_time="2014-12-31")
```

它生成 **158 个因子的表达式**（`qlib/contrib/data/loader.py`）：

| 类别 | 数量 | 例子 | 含义 |
|---|---|---|---|
| **kbar** | 9 | `($close-$open)/$open` | K线形态（实体占比、上下影线） |
| **price** | 4 | `$open/$close` | 原始价格比例 |
| **rolling** × 5 窗口 | 145 | `Mean($close,5)/$close` | 5/10/20/30/60 日的 29 种算子 |

29 种 rolling 算子里包含：
- `ROC/MA/STD`（动量、均线、波动率）
- `BETA/RSQR/RESI`（线性回归斜率、R²、残差——趋势强度）
- `MAX/MIN/QTLU/QTLD/RANK/RSV`（分位数、排名、超买超卖）
- `IMAX/IMIN/IMXD`（距最高/最低点的天数）
- `CORR/CORD`（与成交量的相关性）
- `CNTP/CNTN/CNTD`（上涨天数占比等）
- `SUMP/SUMN/SUMD`（涨跌幅分解）
- `VMA/VSTD/WVMA/VSUMP/VSUMN/VSUMD`（成交量类）

**标签**：`Ref($close, -2)/Ref($close, -1) - 1`

翻译：**T+1 收盘价 → T+2 收盘价 的涨跌幅**。这是 T 日能观测到数据时，实际可交易的收益（A股 T+1 制度）。

**(b) 加载原始数据**

对 csi300 的每只股票，把 158 个表达式编译成算子树，逐个算出来。

**产出**：一个 MultiIndex DataFrame
```
                        feature(158列)                    label
instrument  datetime    KMID  KLEN  ...  ROC60  ...       LABEL0
SH600000    2008-01-02  0.01  0.05  ...  -0.03  ...        0.0042
```

**日志**：`Time cost: 122.937s | Loading data Done`

**(c) Processor 加工**

`DataHandlerLP` 有两条数据流（`qlib/data/dataset/handler.py:552`）：

```
原始数据 → shared_processors → infer_processors → _infer（推理用）
                            ↘                    → learn_processors → _learn（训练用）
```

Alpha158 默认：

| 用途 | Processor | 作用 |
|---|---|---|
| infer | `ProcessInf` | 无穷值 → 该列均值 |
| infer | `ZScoreNorm` | 时序 z-score（用 fit 区间学 mean/std） |
| infer | `Fillna` | 缺失填 0 |
| learn | `DropnaLabel` | 丢掉标签缺失的行 |
| learn | `CSZScoreNorm` | **截面** z-score（同一天跨股票标准化） |

**关键**：`ZScoreNorm` 的 mean/std **只用 2008-2014 学**（`fit_start_time`/`fit_end_time`），然后套用到全部数据。这就是"可学习 Processor"的含义——避免用未来信息做标准化。

**日志**：
```
Time cost: 0.186s | DropnaLabel Done
Time cost: 1.452s | CSZScoreNorm Done
Time cost: 124.961s | Init data Done
```

**(d) DatasetH 切分**

```python
DatasetH(handler=handler, segments={
    "train": ("2008-01-01", "2014-12-31"),
    "valid": ("2015-01-01", "2016-12-31"),
    "test":  ("2017-01-01", "2020-08-01"),
})
```

它只是**按时间区间取切片**，不复制数据。

### ③ 训练模型（约 60 秒）

```python
model = LGBModel(loss="mse", learning_rate=0.2, max_depth=8, num_leaves=210, ...)
model.fit(dataset)
```

`LGBModel.fit` 内部（`qlib/contrib/model/gbdt.py:25`）：

```python
df = dataset.prepare("train", col_set=["feature","label"], data_key=DataHandlerLP.DK_L)
#                                                              ^^^^^ 用 learn 数据
x, y = df["feature"], df["label"]
lgb.Dataset(x.values, label=y)
```

**注意 `DK_L`**：训练用的是 learn 数据（含 `CSZScoreNorm` 截面标准化）。而**推理时** `predict` 用 `DK_I`（infer 数据）。两者差异是数据集设计的一部分——训练需要截面可比，推理需要单点标准化。

**超参含义**：

| 参数 | 值 | 作用 |
|---|---|---|
| `loss` | mse | 回归任务（预测收益率） |
| `learning_rate` | 0.2 | 学得快，易过拟合（官方调优值） |
| `max_depth` | 8 | 树深 |
| `num_leaves` | 210 | 叶子数 |
| `lambda_l1/l2` | 205 / 581 | **强正则化**——金融数据信噪比极低，必须重压 |
| `num_boost_round` | 1000（默认） | 最多 1000 棵树 |
| `early_stopping_rounds` | 50（默认） | valid 上 50 轮不提升就停 |

**产出**：一个训练好的 LightGBM 模型，保存为 `params.pkl`。

### ④ Record 三连（约 10 秒）

`qlib/model/trainer.py:55` 先做占位符替换：

```python
placehorder_value = {"<MODEL>": model, "<DATASET>": dataset}
task_config = fill_placeholder(task_config, placehorder_value)
```

然后**按列表顺序**执行三个 Record：

#### `SignalRecord` — 存预测

```python
pred = model.predict(dataset)          # 对 test 段预测
self.save(**{"pred.pkl": pred})
raw_label = dataset.prepare("test", col_set="label", data_key=DK_R)  # 原始标签
self.save(**{"label.pkl": raw_label})
```

**产出**：
- `pred.pkl`：`(datetime, instrument) → score` 的 Series
- `label.pkl`：真实标签（**用 DK_R 原始数据**，没经过标准化）

**为什么要原始标签**：算 IC 时预测和标签必须同一尺度。如果标签被 z-score 过，IC 会失真。

#### `SigAnaRecord` — 算 IC

```python
pred = self.load("pred.pkl")     # 依赖 SignalRecord
label = self.load("label.pkl")
ic, ric = calc_ic(pred, label)
metrics = {
    "IC": ic.mean(),
    "ICIR": ic.mean() / ic.std(),
    "Rank IC": ric.mean(),
    "Rank ICIR": ric.mean() / ric.std(),
}
```

`calc_ic` 的实现（`qlib/contrib/eva/alpha.py:160`）：

```python
df = pd.DataFrame({"pred": pred, "label": label})
ic  = df.groupby("datetime").apply(lambda d: d["pred"].corr(d["label"]))
ric = df.groupby("datetime").apply(lambda d: d["pred"].corr(d["label"], method="spearman"))
```

**逐日算截面相关系数**，然后取均值。

**产出**：
- `ic.pkl` / `ric.pkl`：**每一日的** IC 序列（约 880 个值）
- metrics：`IC=0.0484`, `Rank IC=0.0503` 等标量

**这就是你看到的 IC 的来源。**

#### `PortAnaRecord` — 回测

```python
strategy = TopkDropoutStrategy(signal=<PRED>, topk=50, n_drop=5)
backtest(strategy, start="2017-01-01", end="2020-08-01", benchmark="SH000300", ...)
```

`TopkDropoutStrategy` 的每日逻辑：

1. 用当日预测分数给所有股票排名
2. 目标：持有 **topk=50** 只分数最高的
3. 每天最多换 **n_drop=5** 只（卖掉持仓中分数最低的5只，买入候选中分数最高的5只）

**为什么要 `n_drop` 限制**：不限制的话每天全换仓，手续费吃光收益。

`Exchange` 撮合时检查：

| 检查 | 参数 | 作用 |
|---|---|---|
| 涨跌停 | `limit_threshold: 0.095` | 涨跌停附近不能成交（A股±10%） |
| 手续费 | `open_cost 5bp / close_cost 15bp` | 买入 0.05%，卖出 0.15% |
| 最低费 | `min_cost: 5` | 每笔至少 5 元 |
| 成交量 | 无（用当日全部可用量） | |

**产出**：
- `report_normal_1day.pkl`：每日收益、持仓、换手
- `positions_normal_1day.pkl`：每日持仓明细
- metrics：三组绩效指标

**这就是你看到的收益/回撤的来源。**

---

## 第 4 步：结果怎么读

### 信号指标（3 个数字）

```
IC        = 0.0484    ← 预测分数与真实收益的相关系数，逐日算再平均
Rank IC   = 0.0503    ← 同上，但用排名相关性（Spearman）
ICIR      = 0.3966    ← IC 均值 / IC 标准差，衡量"稳定性"
```

**判断标准**（日频）：

| IC | 评价 |
|---|---|
| < 0.02 | 基本没用 |
| 0.02 ~ 0.03 | 勉强，需配合其他信号 |
| **0.03 ~ 0.05** | **可用** |
| > 0.05 | 好 |
| > 0.10 | 可疑（大概率有前视偏差） |

`ICIR > 0.3` 说明信号比较稳定（不会某些月很好、某些月完全失效）。

### 回测指标（三组对比）

```
基准（沪深300）       年化 11.36%   IR 0.60   回撤 -37.05%
超额收益（不计成本）   年化 17.29%   IR 1.92   回撤 -9.81%
超额收益（计成本）     年化 12.63%   IR 1.40   回撤 -10.82%
```

**三行的意义**：

- **第1行**：什么都不做，买沪深300ETF。回撤 -37% 是 2018 年熊市。
- **第2行**：模型的**纯选股能力**。IR 1.92 是很高的水平（实盘 IR > 1 已属优秀）。
- **第3行**：**实盘能拿到的**。

**最关键的一行是第2→3行的差**：

```
17.29%  →  12.63%      损失 4.66 个点（-27%）
```

这是交易成本（主要是 `close_cost=0.15%` 的卖出费 + 每次换仓的摩擦）。**这个落差告诉你：调参时换手率比模型精度更重要。**

### 指标定义

| 指标 | 计算 |
|---|---|
| `mean` | 每日收益均值 |
| `std` | 每日收益标准差 |
| `annualized_return` | `mean × 252` |
| `information_ratio` | `mean / std × sqrt(252)` |
| `max_drawdown` | 净值曲线最大回撤 |

---

## 第 5 步：产物在哪、怎么看

全部在 `examples/mlruns/<exp_id>/<recorder_id>/`：

```
artifacts/
  config                       ← 这次实验的完整配置（YAML）
  task                         ← task 部分
  params.pkl                   ← 训练好的 LightGBM 模型
  pred.pkl                     ← 预测分数 DataFrame
  label.pkl                    ← 真实标签
  dataset                      ← 数据集对象
  sig_analysis/
    ic.pkl                     ← 每日 IC 序列
    ric.pkl                    ← 每日 Rank IC
  portfolio_analysis/
    report_normal_1day.pkl     ← 每日收益明细
    positions_normal_1day.pkl  ← 每日持仓
    indicators_normal_1day.pkl ← 每日指标
metrics/                       ← 标量指标（MLflow 格式）
```

**自己读这些产物**：

```python
import pandas as pd
pred  = pd.read_pickle("examples/mlruns/<exp>/<rec>/artifacts/pred.pkl")
print(pred.head())
#                             score
# datetime   instrument
# 2017-01-03 SH600000       0.0231
#            SH600004      -0.0112

ic = pd.read_pickle(".../sig_analysis/ic.pkl")
print(ic.describe())          # 每日 IC 的分布
print((ic > 0).mean())        # IC 为正的天数占比

report = pd.read_pickle(".../portfolio_analysis/report_normal_1day.pkl")
print(report[["return", "cost", "turnover"]].tail())
```

---

## 第 6 步：验证实验真的有效（进阶自查）

跑完不要只看数字，做三个检查：

**① IC 分布是否稳定**

```python
ic = pd.read_pickle(".../sig_analysis/ic.pkl")
print(ic.groupby(ic.index.year).mean())    # 逐年 IC
```

如果某一年 IC 是负的，说明模型可能只是拟合了特定市场环境。

**② 预测值分布是否合理**

```python
pred = pd.read_pickle(".../pred.pkl")
print(pred["score"].describe())
print(pred.groupby("datetime")["score"].mean().describe())  # 每日截面均值应接近 0
```

**③ 回测是否用了未来信息**

检查清单：
- `fit_start_time` / `fit_end_time` 是否只覆盖训练期？ ✅ 2008-2014
- 标签是否是 `Ref($close,-2)` 而非 `Ref($close,-1)`？ ✅ 正确用了 T+2
- 回测起点是否晚于测试集起点？ ✅ 都是 2017-01-01

---

## 常见问题

| 现象 | 原因 | 解决 |
|---|---|---|
| `ValueError: n_jobs == 0` | `kernels: 0` | 改 `kernels: 1` |
| `MlflowException: filesystem tracking backend ... maintenance mode` | 新版 MLflow | `MLFLOW_ALLOW_FILE_STORE=true` |
| `ModuleNotFoundError: qlib.data._libs.rolling` | Cython 未编译 | 重新编译 `build_ext --inplace` |
| 进程爆炸 / 卡死 | `kernels` 太大 + 缺 `__main__` 守卫 | `kernels: 1` + 加守卫 |
| 内存持续增长 | 数据全量载入内存 | 缩小时间区间或股票池 |

---

## 下次实验怎么做

复制配置，改一个变量，跑一次，对比结果：

```bash
cp examples/my_first_run.yaml examples/exp_csi500.yaml
# 把 market: &market csi300 改成 csi500
```

**建议的对照序列**：

| 实验 | 改动 | 预期观察 |
|---|---|---|
| 1 | 基线 | IC ≈ 0.048 |
| 2 | `csi300` → `csi500` | IC 下降（池子大更难选） |
| 3 | `n_drop: 5` → `2` | 成本下降，但信号捕获变差 |
| 4 | `Alpha158` → `Alpha360` | 因子更多但更原始 |
| 5 | `learning_rate: 0.2` → `0.05` | 通常 IC 略升，训练变慢 |

每次跑完对比 `mlruns/` 里的 metrics，这是量化研究的基本工作流。
