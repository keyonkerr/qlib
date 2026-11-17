/* Experiment dashboard — 「实验解剖」 (anatomy of a run).
 *
 * Idea: a run is not a row in a table, it is a specimen. The page reads like
 * a lab dissection sheet: what went in (setting), what came out (signal,
 * execution, outcome), and what each number means.
 *
 * Two devices carry the design:
 *   1. A metric glossary keyed by real qlib metric names. Every number on the
 *      page can be opened to reveal what it is, how it is computed and which
 *      band counts as usable. This is the reason for a self-hosted platform.
 *   2. One way to show figures. Everything (qlib Plotly renders AND the
 *      series we compute) goes into the same "plate" component: a numbered
 *      figure with a caption and a reading note. No tabs-versus-flat split.
 *
 * Data comes from /api/runs and /api/runs/{id}; this page only formats and
 * explains, it never recomputes metrics.
 */

const pct = (v, d = 2) => (v == null ? "—" : (v * 100).toFixed(d) + "%");
const num = (v, d = 4) => {
  if (v == null) return "—";
  if (typeof v !== "number") return String(v);
  if (v === 0) return "0";
  if (Math.abs(v) >= 1000) return v.toFixed(0);
  if (Math.abs(v) < 1e-4) return v.toExponential(2);
  return v.toFixed(d);
};
const signed = (v, d = 4) => (v == null ? "—" : (v > 0 ? "+" : "") + v.toFixed(d));
const signPct = (v, d = 1) => (v == null ? "—" : (v >= 0 ? "+" : "") + (v * 100).toFixed(d) + "pp");
const day = ms => {
  if (!ms) return "—";
  const d = new Date(ms);
  return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, "0")}.${String(d.getDate()).padStart(2, "0")}`;
};
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ═══════════════════════════════════════════════════════════════════
   1. GLOSSARY — the core of this page.
   Every entry: what it is (一句话), how qlib computes it (公式/口径),
   how to read it (判读), and where the number comes from in the run.
   ═══════════════════════════════════════════════════════════════════ */

const GLOSSARY = {
  /* --- 信号：模型预测得准不准 --- */
  IC: {
    group: "信号",
    name: "IC · 信息系数",
    what: "每天把全部股票的「模型打分」和「真实涨跌」做一次相关性，再把整段回测期逐日的相关系数取平均。",
    how: "Pearson 相关，逐日计算后取均值。源码 qlib/contrib/eva/alpha.py::calc_ic，按交易日 groupby，不是整段算一个数。",
    read: [
      [0.03, "低于 0.03 通常不足以支撑选股，先怀疑特征或标签。"],
      [0.05, "0.03–0.05 可用，A 股日频的常见区间。"],
      [1, "0.05 以上算强信号，先确认没有未来函数泄漏。"],
    ],
    caveat: "相关性不等于赚钱：高 IC 也可能被换手和成本吃掉。",
  },
  ICIR: {
    group: "信号",
    name: "ICIR · 信息系数稳定性",
    what: "把逐日 IC 当作一个序列，用它自己的波动去衡量平均 IC 有多可靠。",
    how: "IC 均值 ÷ IC 标准差。qlib 记录的是日均值，年化约 ×√238。",
    read: [
      [0.2, "低于 0.2：IC 时正时负，信号不稳定。"],
      [0.35, "0.2–0.35 一般，需要靠组合分散。"],
      [1, "0.35 以上说明 IC 长期同号，可信度高。"],
    ],
    caveat: "样本越短 ICIR 越容易虚高，和回测期长度一起看。",
  },
  "Rank IC": {
    group: "信号",
    name: "Rank IC · 排名相关系数",
    what: "先只保留每只股票的排名，再算相关。对极端值不敏感。",
    how: "Spearman 相关，逐日计算后取均值。",
    read: [
      [0.03, "同 IC 的量级门槛。"],
      [1, "稳定高于普通 IC 时，说明预测里没有极端值主导。"],
    ],
    caveat: "与 IC 差距突然放大，往往是出现了少量极端预测值。",
  },
  "Rank ICIR": {
    group: "信号",
    name: "Rank ICIR · 排名稳定性",
    what: "Rank IC 版本的 ICIR。",
    how: "Rank IC 均值 ÷ Rank IC 标准差。",
    read: [
      [0.35, "低于 0.35 说明排名关系不稳定。"],
      [1, "越高越好，思路同 ICIR。"],
    ],
  },
  predicted_score: {
    group: "信号",
    name: "预测分数",
    what: "模型对「这只股票未来会涨多少」给出的相对分数，只用于排序，绝对值没有含义。",
    how: "LightGBM 对 label（默认 Ref($close,-2)/Ref($close,-1)-1，即次日收益）的回归输出。",
    read: [[0, "同一交易日内横向比较才有意义，跨日不可比。"]],
  },
  label: {
    group: "信号",
    name: "标签",
    what: "模型要学的目标。Alpha158 默认是「下一交易日的收盘价相对今天的变化」。",
    how: "Ref($close, -2) / Ref($close, -1) - 1：明天相对今天，负号表示向未来取数。",
    read: [[0, "换标签是最有效的改进路径之一：5 日标签通常降换手、抬 IC。"]],

  },

  /* --- 执行：下单到成交发生了什么 --- */
  ffr: {
    group: "执行",
    name: "ffr · 成交率",
    what: "下单的量最终成交了多大比例。",
    how: "实际成交额 ÷ 目标下单额，再按订单取平均（可配 amount/value 加权）。qlib/backtest/report.py。",
    read: [[0.99, "接近 1 说明限制条件宽松、基本都能成交。"], [1, "明显低于 1 要么被涨跌停卡住，要么成交额假设太乐观。"]],
  },
  pa: {
    group: "执行",
    name: "pa · 价格优势",
    what: "成交价比基准价「占了多少便宜」。正数=买得比基准便宜（或卖得比基准贵）。",
    how: "sign × (成交价 ÷ 基准价 − 1)，sign 区分买卖方向。",
    read: [[0, "接近 0 说明按基准价成交。"], [1, "明显不为 0 时，先查基准价数据是否齐全。"]],
    caveat: "qlib 在缺少复权因子时会退化，pa 恒为 0，这时它不代表「成交完美」。",
  },
  pos: {
    group: "执行",
    name: "pos · 胜率",
    what: "在所有成交里，拿到正价格优势的比例。",
    how: "(pa > 0 的订单数) ÷ 订单总数。",
    read: [[0.5, "低于 0.5 说明多数成交不占优。"], [1, "越高越好，0.5 以上算正常。"]],
    caveat: "同 pa：pa 数据失效时 pos 也会归零。",
  },
  turnover: {
    group: "执行",
    name: "换手率",
    what: "每次调仓动用了多大比例的仓位。",
    how: "成交额 ÷ 持仓市值，逐日记录在 report_normal_1day.turnover。",
    read: [[0, "高换手直接推高成本和税。"], [1, "看它和成本序列是否同步冲高，那是改进的第一刀。"]],
  },
  cost: {
    group: "执行",
    name: "交易成本",
    what: "当天买卖付出的手续费 + 印花税。",
    how: "按 exchange_kwargs 计：open_cost、close_cost、min_cost。",
    read: [[0, "和换手率一起看：换手翻倍，成本通常也跟着翻。"]],
  },

  /* --- 结果：赚没赚到、赚得稳不稳 --- */
  annualized_return: {
    group: "结果",
    name: "年化超额收益",
    what: "扣成本后，相对基准（csi300）每年多赚多少。",
    how: "日均超额 × 238。qlib 用算术累加，不是复利，见 qlib/contrib/evaluate.py::risk_analysis。",
    read: [
      [0, "负值意味着不如直接买基准。"],
      [0.1, "10% 以内偏弱。"],
      [1, "越高越好，但要和最大回撤一起看。"],
    ],
  },
  information_ratio: {
    group: "结果",
    name: "IR · 信息比率",
    what: "每承担一份波动，换来多少超额收益。是「性价比」而不是「收益」。",
    how: "日均超额 ÷ 超额标准差 × √238。已经年化，不要再乘。",
    read: [
      [0.5, "低于 0.5 性价比不佳。"],
      [1, "0.5–1 一般。"],
      [2, "1 以上可用，2 以上少见，先怀疑回测过拟合。"],
    ],
    caveat: "IR 高而年化低，说明胜在稳，不是胜在猛。",
  },
  max_drawdown: {
    group: "结果",
    name: "最大回撤",
    what: "从任一高点跌下来最深的那一段，衡量最难受的时候有多难受。",
    how: "累计收益曲线减历史最高点后的最小值（算术累计口径）。",
    read: [
      [-0.3, "越接近 0 越好。"],
      [0, "超过 −30% 需要确认资金能否扛住。"],
    ],
    caveat: "qlib 记录的是算术累计回撤，和净值曲线画出来的回撤略有差别。",
  },
  std: {
    group: "结果",
    name: "标准差 · 波动",
    what: "日度超额收益的波动幅度。",
    how: "样本标准差（ddof=1）。",
    read: [[0, "越小越稳。它是 IR 的分母。"]],
  },
  mean: {
    group: "结果",
    name: "日均超额",
    what: "每天的原始超额收益，未年化。",
    how: "超额收益序列的算术平均。",
    read: [[0, "正数是前提，绝对值小不代表没用，要看稳定性。"]],
  },
  bench: {
    group: "结果",
    name: "基准收益",
    what: "同一天 csi300 的涨跌，用来做对照。",
    how: "report_normal_1day.bench 列。",
    read: [[0, "超额 = 持仓收益 − 基准收益，两者要同时看。"]],
  },
  return: {
    group: "结果",
    name: "持仓收益",
    what: "策略账户当天的实际收益。",
    how: "report_normal_1day.return 列。",
    read: [[0, "把它和 bench 画在一起，才能看出超额来自哪里。"]],
  },

  /* --- 训练 --- */
  "l2.train": {
    group: "训练",
    name: "训练损失 l2",
    what: "模型在训练集上的均方误差，每提升一轮记录一次。",
    how: "LightGBM 每轮 eval，qlib 以 step=轮次写入 mlflow。",
    read: [[0, "持续下降是正常的。"]],
    caveat: "只看训练损失没有意义，它必然下降。",
  },
  "l2.valid": {
    group: "训练",
    name: "验证损失 l2",
    what: "模型在验证集上的均方误差。",
    how: "同 l2.train，验证集固定不参与拟合。",
    read: [[0, "它停止下降而训练损失继续降，就是过拟合的信号。"]],
    caveat: "early_stopping_rounds 会在它连续若干轮不改善时停止训练。",
  },

  /* --- 参数（讲解型，不是词典型） --- */
  num_leaves: {
    group: "参数",
    name: "num_leaves · 叶子数",
    what: "每棵树的叶子数量，直接决定单棵树的容量。",
    read: [[0, "越大越能拟合细节，也越容易过拟合。常见 31–255。"]],
  },
  learning_rate: {
    group: "参数",
    name: "learning_rate · 学习率",
    what: "每棵新树对最终结果的影响权重。",
    read: [[0, "越小越稳但需要更多轮；越大越快也越容易震荡。常见 0.05–0.2。"]],
  },
  max_depth: {
    group: "参数",
    name: "max_depth · 树深",
    what: "单棵树的最大层数。",
    read: [[0, "越深容量越大。和 num_leaves 一起控制复杂度。"]],
  },
  lambda_l1: {
    group: "参数",
    name: "lambda_l1 · L1 正则",
    what: "对叶子权重的绝对值惩罚，能把部分权重压到 0。",
    read: [[0, "起特征筛选作用，越大越稀疏。"]],
  },
  lambda_l2: {
    group: "参数",
    name: "lambda_l2 · L2 正则",
    what: "对叶子权重的平方惩罚，把权重整体压小。",
    read: [[0, "抗过拟合的主要旋钮，越大越保守。"]],
  },
  colsample_bytree: {
    group: "参数",
    name: "colsample_bytree · 列采样率",
    what: "每棵树随机使用多少比例的特征。",
    read: [[0, "小于 1 相当于每次换一批特征，降低对个别特征的依赖。"]],
  },
  subsample: {
    group: "参数",
    name: "subsample · 行采样率",
    what: "每棵树随机使用多少比例的样本。",
    read: [[0, "小于 1 引入随机性，通常配合更多轮数。"]],
  },
  instruments: {
    group: "参数",
    name: "instruments · 股票池",
    what: "参与训练和回测的股票范围。",
    read: [[0, "csi300 流动性好、噪声小；csi500/csi1000 更难，也更有超额空间。"]],
  },
  segments: {
    group: "参数",
    name: "segments · 数据切分",
    what: "训练 / 验证 / 测试三段时间区间。",
    read: [[0, "训练段用来拟合，验证段用来定早停，测试段只用来评估，绝不能泄漏。"]],
  },
  topk: {
    group: "参数",
    name: "topk / n_drop · 组合构建",
    what: "每天持有预测分数最高的 topk 只，每次换掉其中表现最好的 n_drop 只。",
    read: [[0, "topk 越小越集中、波动越大；n_drop 影响换手。"]],
  },
  benchmark: {
    group: "参数",
    name: "benchmark · 基准",
    what: "计算超额收益时对照的指数。",
    read: [[0, "所有「超额」都以它为 0 点，换基准等于换分母。"]],
  },
};

/* Metric name -> glossary key. qlib names are long and period-prefixed. */
function glossaryFor(key) {
  if (GLOSSARY[key]) return GLOSSARY[key];
  const last = key.split(".").pop();
  if (GLOSSARY[last]) return GLOSSARY[last];
  if (GLOSSARY[key.replace(/^1day\./, "")]) return GLOSSARY[key.replace(/^1day\./, "")];
  return null;
}

/* ═══════════════════════════════════════════════════════════════════
   2. PLATE — one figure component for everything.
   ═══════════════════════════════════════════════════════════════════ */

const W = 720;
const H = 150;

/* One series. Keeps the zero rule and the soft fill; the axis labels are the
   only scale cue, so the series must always be judged against them. */
function lineSVG(values, opts) {
  const { color = "var(--indigo)", baseline = null, labels = null, area = false } = opts || {};
  const pts = values.map((v, i) => [i, v]).filter(p => p[1] != null && Number.isFinite(p[1]));
  if (pts.length < 2) return `<p class="plate-empty">没有可画的数据</p>`;

  let lo = Math.min(...pts.map(p => p[1]));
  let hi = Math.max(...pts.map(p => p[1]));
  if (baseline != null) { lo = Math.min(lo, baseline); hi = Math.max(hi, baseline); }
  if (hi === lo) { hi = lo + 1e-12; lo -= 1e-12; }

  const padX = 4, padY = 8;
  const w = W - padX * 2, h = H - padY * 2;
  const X = i => padX + (values.length <= 1 ? 0 : (i / (values.length - 1)) * w);
  const Y = v => padY + h - ((v - lo) / (hi - lo)) * h;

  const path = pts.map((p, k) => `${k ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");
  const fill = area && pts.length
    ? `<path d="${path} L${X(pts[pts.length - 1][0]).toFixed(1)},${(padY + h).toFixed(1)} ` +
      `L${X(pts[0][0]).toFixed(1)},${(padY + h).toFixed(1)} Z" fill="${color}" opacity=".07"/>`
    : "";
  const zero = baseline == null ? ""
    : `<line x1="${padX}" x2="${padX + w}" y1="${Y(baseline).toFixed(1)}" y2="${Y(baseline).toFixed(1)}" class="zero"/>`;

  const hits = pts.map(p => {
    const lbl = labels ? `<b>${esc(labels[p[0]])}</b> ` : "";
    return `<circle cx="${X(p[0]).toFixed(1)}" cy="${Y(p[1]).toFixed(1)}" r="5" fill="transparent">` +
      `<title>${lbl}${num(p[1], 6)}</title></circle>`;
  }).join("");

  return `<div class="line">
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
      ${zero}${fill}<path d="${path}" fill="none" stroke="${color}" stroke-width="1.5" vector-effect="non-scaling-stroke"/>
      ${hits}
    </svg>
    <div class="line-scale"><span>${num(lo, 5)}</span><span>${num(hi, 5)}</span></div>
  </div>`;
}

/* Two series on one canvas: one scale, one zero rule, both curves. */
function linePair(aVals, bVals, opts) {
  const { aColor = "var(--indigo)", bColor = "var(--ochre)", labels = null, aName = "", bName = "" } = opts || {};
  const all = [...aVals, ...bVals].filter(v => Number.isFinite(v));
  if (all.length < 2) return `<p class="plate-empty">没有可画的数据</p>`;

  let lo = Math.min(...all), hi = Math.max(...all);
  if (hi === lo) { hi = lo + 1e-12; lo -= 1e-12; }

  const padX = 4, padY = 8;
  const w = W - padX * 2, h = H - padY * 2;
  const n = Math.max(aVals.length, bVals.length);
  const X = i => padX + (n <= 1 ? 0 : (i / (n - 1)) * w);
  const Y = v => padY + h - ((v - lo) / (hi - lo)) * h;

  const pathOf = vals => vals
    .map((v, i) => [i, v])
    .filter(p => Number.isFinite(p[1]))
    .map((p, k) => `${k ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`)
    .join(" ");

  const hits = (vals, name, color) => vals
    .map((v, i) => Number.isFinite(v)
      ? `<circle cx="${X(i).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="5" fill="transparent">` +
        `<title>${name} 第 ${i + 1} 轮  ${num(v, 6)}</title></circle>`
      : "").join("");

  return `<div class="line">
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-label="${esc(aName)} 与 ${esc(bName)} 对比">
      <path d="${pathOf(aVals)}" fill="none" stroke="${aColor}" stroke-width="1.6" vector-effect="non-scaling-stroke"/>
      <path d="${pathOf(bVals)}" fill="none" stroke="${bColor}" stroke-width="1.6" vector-effect="non-scaling-stroke"/>
      ${hits(aVals, aName, aColor)}${hits(bVals, bName, bColor)}
    </svg>
    <div class="line-scale"><span>${num(lo, 5)}</span><span>${num(hi, 5)}</span></div>
  </div>`;
}

let plateNo = 0;
function plate({ title, note, body, meta, wide = false, id }) {
  plateNo += 1;
  const anchor = id || `plate-${plateNo}`;
  return `<figure class="plate${wide ? " wide" : ""}" id="${anchor}">
    <figcaption class="plate-cap">
      <span class="plate-no">图 ${plateNo}</span>
      <h3 class="plate-title">${esc(title)}</h3>
      ${meta ? `<span class="plate-meta">${esc(meta)}</span>` : ""}
      ${note ? `<p class="plate-note">${esc(note)}</p>` : ""}
    </figcaption>
    <div class="plate-body">${body}</div>
  </figure>`;
}

function statBand(items) {
  return `<dl class="band">${items.map(it =>
    `<div class="${it.strong ? "strong" : ""}"><dt>${esc(it.label)}</dt><dd>${esc(it.value)}</dd></div>`
  ).join("")}</dl>`;
}

/* ═══════════════════════════════════════════════════════════════════
   3. Annotations: every number rendered with a glossary lookup.
   ═══════════════════════════════════════════════════════════════════ */

function anno(value, term, opts) {
  const g = glossaryFor(term);
  if (!g) return `<span class="v">${esc(value)}</span>`;
  return `<button class="v has-term" type="button" data-term="${esc(term)}"
    aria-expanded="false" aria-label="${esc(g.name)}，展开解释">${esc(value)}</button>`;
}

function termSheet(term) {
  const g = glossaryFor(term);
  if (!g) return "";
  const bands = (g.read || []).map(([edge, text]) => {
    const bound = typeof edge === "number" && edge !== 0
      ? `≥ ${num(edge, edge < 1 && edge > -1 ? 2 : 2)}`
      : "提示";
    return `<li><span class="band-edge">${esc(bound)}</span><span>${esc(text)}</span></li>`;
  }).join("");
  return `<div class="sheet-doc" role="dialog" aria-label="${esc(g.name)}">
    <div class="doc-head">
      <span class="doc-group">${esc(g.group)}</span>
      <h4>${esc(g.name)}</h4>
      <button class="doc-close" type="button" aria-label="收起解释">收起</button>
    </div>
    <p class="doc-what">${esc(g.what)}</p>
    ${g.how ? `<p class="doc-how"><span>怎么算</span>${esc(g.how)}</p>` : ""}
    ${bands ? `<ul class="doc-bands">${bands}</ul>` : ""}
    ${g.caveat ? `<p class="doc-caveat">${esc(g.caveat)}</p>` : ""}
  </div>`;
}

/* ═══════════════════════════════════════════════════════════════════
   4. State + boot
   ═══════════════════════════════════════════════════════════════════ */

const state = {
  configs: [],
  current: null,        // selected run
  currentConfig: null,  // the YAML that run belongs to
  configPath: null,     // the YAML open in the config sheet, e.g. "path_exps/00_baseline.yaml"
  detail: null,
  query: "",
  closed: new Set(),
  selected: [],         // run ids ticked for comparison, in tick order
  compare: null,        // last /api/compare payload
  job: null,            // { job_id, path } for the drawer's stream
  eventSource: null,
};

const MAX_COMPARE = 4;
const SLOT_COLORS = ["indigo", "ochre"];   // A/B solid, C/D dashed in the same two
const el = id => document.getElementById(id);

async function boot() {
  let configs;
  try {
    const res = await fetch("/api/configs");
    if (!res.ok) throw new Error(res.status);
    configs = await res.json();
  } catch (e) {
    el("loading").textContent = `连不上后端：${e.message}`;
    return;
  }
  state.configs = configs;
  const exps = [...new Set(configs.map(c => c.experiment))];
  const totalRuns = configs.reduce((n, c) => n + c.run_count, 0);
  el("meta").textContent = `${configs.length} 个配置 · ${totalRuns} 个 run · ${exps.length} 个实验`;

  if (!configs.length) {
    el("loading").textContent = "mlruns 里还没有完成的 run。";
    return;
  }
  const first = configs.find(c => c.has_renders) || configs[0];
  renderConfigs();
  renderPickBar();
  await selectRun(first.newest_run_id, first.key);
}

el("pickgo").addEventListener("click", openCompare);
el("pickclear").addEventListener("click", () => {
  state.selected = [];
  exitCompare();
  el("specimen").hidden = !state.detail || !!state.configPath;
  renderConfigs();
  renderPickBar();
  if (state.detail && !state.configPath) renderOutline();
});

el("q").addEventListener("input", e => {
  state.query = e.target.value.trim().toLowerCase();
  renderConfigs();
});

/* ═══════════════════════════════════════════════════════════════════
   Comparison selection.

   The tick lives on the run row, but the row itself stays a plain button:
   a checkbox cannot nest inside <button>, so each row becomes
   `div.run-row > [input.pick-box] + button.run-entry`. Only the box
   changes state; clicking the rest of the row opens that run alone, which
   keeps "tick to compare" and "click to read" two different gestures.
   ═══════════════════════════════════════════════════════════════════ */

function slotOf(runId) {
  const i = state.selected.indexOf(runId);
  return i < 0 ? null : "ABCD"[i];
}

function togglePick(runId) {
  const i = state.selected.indexOf(runId);
  if (i >= 0) {
    state.selected.splice(i, 1);
  } else if (state.selected.length < MAX_COMPARE) {
    state.selected.push(runId);
  } else {
    return;   // full: the bar already says so
  }
  // 少于 2 个就没有可对照的东西：必须真的退出对照页（清 DOM、恢复解剖页），
  // 而不是只把 state.compare 置空——那样 Esc 和返回按钮都会失效。
  if (state.selected.length < 2) exitCompare();
  // 选择变了，已渲染的对照页不再对应当前选择；但不销毁它，
  // 否则用户改选之后会看到一个空白的对照容器。
  syncPickBoxes();
  renderPickBar();
}

/* Update the ticks in place instead of re-rendering the index. Re-rendering
   would destroy the very checkbox mid-click and the browser would revert it. */
function syncPickBoxes() {
  document.querySelectorAll(".pick-box").forEach(box => {
    const picked = state.selected.includes(box.dataset.run);
    box.checked = picked;
    box.disabled = !picked && state.selected.length >= MAX_COMPARE;
    box.title = picked ? "取消对比" : "勾选后参与对比";
  });
  document.querySelectorAll(".run-row").forEach(row => {
    const id = row.dataset.run;
    const tag = row.querySelector(".slot-tag");
    if (!tag) return;
    const slot = slotOf(id);
    const i = state.selected.indexOf(id);
    tag.textContent = slot || "";
    tag.hidden = !slot;
    tag.className = "slot-tag" + (slot
      ? ` ${SLOT_COLORS[i % 2]}${i >= 2 ? " dash" : ""}` : "");
  });
}

function findRun(runId) {
  for (const c of state.configs) {
    const hit = c.runs.find(r => r.run_id === runId);
    if (hit) return { run: hit, config: c };
  }
  return null;
}

function renderPickBar() {
  const bar = el("pickbar");
  const n = state.selected.length;
  bar.hidden = n === 0;
  if (!n) return;

  el("pickn").textContent = n >= MAX_COMPARE
    ? `已选 ${n} / ${MAX_COMPARE} · 已满`
    : `已选 ${n} / ${MAX_COMPARE}`;
  el("pickslots").innerHTML = state.selected.map((id, i) => {
    const hit = findRun(id);
    const label = hit ? (hit.run.seed ? `种子 ${hit.run.seed}` : hit.config.label) : id.slice(0, 8);
    return `<li class="pick-slot">
      <button type="button" class="slot-tag ${SLOT_COLORS[i % 2]}${i >= 2 ? " dash" : ""}"
        data-drop="${esc(id)}" title="取消勾选 ${esc(label)}">${"ABCD"[i]}</button>
      <span class="slot-name" title="${esc(label)}">${esc(label)}</span>
    </li>`;
  }).join("");
  el("pickgo").disabled = n < 2;
  el("pickgo").textContent = n < 2 ? "再选 1 个" : `开始对照（${n}）`;
}

/* Left index: experiments → YAML → its runs.
   A YAML is the unit that gets edited, so it is the line you scan; its runs
   hang underneath and switch the right pane. */
function renderConfigs() {
  const box = el("runs");
  const q = state.query;
  const pool = state.configs.filter(c =>
    !q ||
    c.key.toLowerCase().includes(q) ||
    c.experiment.toLowerCase().includes(q) ||
    c.runs.some(r =>
      r.run_id.toLowerCase().includes(q) ||
      r.name.toLowerCase().includes(q) ||
      (r.seed && String(r.seed).includes(q))
    )
  );
  box.innerHTML = "";
  const exps = [...new Set(state.configs.map(c => c.experiment))];
  const never = pool.filter(c => c.run_count === 0);
  const ran = pool.filter(c => c.run_count > 0);

  if (!pool.length) {
    box.innerHTML = `<p class="index-empty">没有匹配的配置。</p>`;
    return;
  }

  for (const expName of exps) {
    const items = ran.filter(c => c.experiment === expName);
    if (!items.length) continue;
    const open = !state.closed.has(expName);
    const group = document.createElement("section");
    group.className = "index-group";
    group.innerHTML = `
      <button class="group-toggle" type="button" aria-expanded="${open}">
        <span class="tri" aria-hidden="true"></span>
        <span class="group-label">${esc(expName)}</span>
        <span class="group-n">${items.length}</span>
      </button>
      <div class="cfg-list" ${open ? "" : "hidden"}></div>`;
    group.querySelector(".group-toggle").addEventListener("click", () => {
      const nowOpen = state.closed.has(expName);
      if (nowOpen) state.closed.delete(expName); else state.closed.add(expName);
      renderConfigs();
    });

    const list = group.querySelector(".cfg-list");
    for (const c of items) list.appendChild(configEntry(c));
    box.appendChild(group);
  }

  // 磁盘上有、但从没跑完过的配置。单列一组，否则它们会混在实验分组里
  // 让「这个实验跑出了什么」变得难读。
  if (never.length) {
    const group = document.createElement("section");
    group.className = "index-group index-never";
    group.innerHTML = `
      <div class="group-toggle group-static">
        <span class="group-label">未运行</span>
        <span class="group-n">${never.length}</span>
      </div>
      <div class="cfg-list"></div>`;
    const list = group.querySelector(".cfg-list");
    for (const c of never) list.appendChild(configEntry(c));
    box.appendChild(group);
  }
}

function configEntry(c) {
  const wrap = document.createElement("section");
  const active = state.configPath === c.path;
  wrap.className = "cfg-entry" + (active ? " active" : "");
  if (!c.runnable) wrap.classList.add("cfg-gone");

  const agg = c.aggregate || {};
  // 多次运行时显示波动范围，这是判断「配置差异 vs 随机噪声」的依据
  let spread = "";
  if (agg.ic) {
    spread = agg.ic.spread === 0
      ? `<span class="cfg-spread same" title="多次运行结果完全一致：seed 固定时训练是确定性的">结果一致</span>`
      : `<span class="cfg-spread" title="同一配置多次运行的 IC 极差">±${num(agg.ic.spread, 4)}</span>`;
  }

  const head = document.createElement("button");
  head.type = "button";
  head.className = "cfg-head-btn";
  head.innerHTML = `
    <span class="cfg-name">${esc(c.label)}</span>
    ${c.run_count ? `<span class="cfg-runs-n">${c.run_count}${c.run_count > 1 ? " 次" : ""}</span>` : ""}
    <span class="cfg-metrics">
      ${c.run_count
        ? `<span><i>IC</i>${num(c.runs[0].summary.ic)}</span>${spread}`
        : `<span class="cfg-never">从未运行</span>`}
    </span>
    <span class="cfg-date">${c.run_count ? day(c.runs[0].start_time) : day(c.mtime * 1000)}</span>`;
  head.title = c.runnable ? "查看配置文件并运行" : "源文件已不在磁盘上，只能看历史记录";
  head.addEventListener("click", () => openConfig(c.path));

  // 只跑过一次的配置：勾选框挂在配置行上，否则这类配置进不了对照。
  // 多次运行的配置，勾选框在各 run 行上——配置行现在打开 YAML，不再展开。
  if (c.run_count === 1) {
    const only = c.runs[0];
    const picked = state.selected.includes(only.run_id);
    const slot = slotOf(only.run_id);
    const slotIdx = state.selected.indexOf(only.run_id);
    const box = document.createElement("input");
    box.type = "checkbox";
    box.className = "pick-box";
    box.dataset.run = only.run_id;
    box.checked = picked;
    box.disabled = !picked && state.selected.length >= MAX_COMPARE;
    box.title = picked ? "取消对比" : "勾选后参与对比";
    box.setAttribute("aria-label", `勾选 ${c.label} 参与对比`);
    box.addEventListener("change", () => togglePick(only.run_id));
    const tag = document.createElement("span");
    tag.className = "slot-tag cfg-slot" + (slot
      ? ` ${SLOT_COLORS[slotIdx % 2]}${slotIdx >= 2 ? " dash" : ""}` : "");
    tag.textContent = slot || "";
    tag.hidden = !slot;
    const row = document.createElement("div");
    row.className = "run-row cfg-row";
    row.dataset.run = only.run_id;
    row.append(box, head, tag);
    wrap.appendChild(row);
    return wrap;
  }

  wrap.appendChild(head);

  if (c.run_count > 1) {
    const runs = document.createElement("ul");
    runs.className = "cfg-runs";
    for (const r of c.runs) runs.appendChild(runLine(c, r));
    wrap.appendChild(runs);
  }
  return wrap;
}

function runLine(c, r) {
  const li = document.createElement("li");
  const row = document.createElement("div");
  row.className = "run-row";
  row.dataset.run = r.run_id;

  const picked = state.selected.includes(r.run_id);
  const slot = slotOf(r.run_id);
  const slotIdx = state.selected.indexOf(r.run_id);
  const box = document.createElement("input");
  box.type = "checkbox";
  box.className = "pick-box";
  box.dataset.run = r.run_id;
  box.checked = picked;
  box.disabled = !picked && state.selected.length >= MAX_COMPARE;
  box.title = picked ? "取消对比" : "勾选后参与对比";
  box.setAttribute("aria-label", `勾选 ${r.name} 参与对比`);
  box.addEventListener("change", () => togglePick(r.run_id));

  const b = document.createElement("button");
  b.type = "button";
  b.className = "run-entry";
  b.setAttribute("aria-current", String(state.current && r.run_id === state.current.run_id));
  const s = r.summary || {};
  const seedTag = r.seed ? `<span class="run-seed">seed ${esc(r.seed)}</span>` : "";
  // 同一配置的多次运行常常同名（就是同一个文件名），用时间 + id 短码区分
  const runLabel = r.seed ? `种子 ${esc(r.seed)}` : `${day(r.start_time)} · ${esc(r.run_id.slice(0, 6))}`;
  b.innerHTML = `
    <span class="run-top">
      <span class="run-name">${runLabel}</span>
      ${seedTag}
      <span class="run-date">${day(r.start_time)}</span>
    </span>
    <span class="run-line">
      <span><i>IC</i>${num(s.ic)}</span>
      <span><i>年化</i>${pct(s.ann_cost)}</span>
      <span><i>IR</i>${num(s.ir_cost, 2)}</span>
      <span class="dot ${r.has_renders ? "on" : ""}" title="${r.has_renders ? "有图表" : "无图表"}"></span>
      <span class="slot-tag${slot ? ` ${SLOT_COLORS[slotIdx % 2]}${slotIdx >= 2 ? " dash" : ""}` : ""}"${
        slot ? "" : " hidden"}>${slot || ""}</span>
    </span>`;
  b.addEventListener("click", () => selectRun(r.run_id, c.key));
  row.append(box, b);
  li.appendChild(row);
  return li;
}

async function selectRun(runId, configKey) {
  exitCompare();   // clicking a run row always means "read this one"
  closeConfigSheet();
  state.current = null;
  state.currentConfig = configKey || (state.configs.find(c => c.runs.some(r => r.run_id === runId)) || {}).key || null;
  renderConfigs();
  el("loading").hidden = false;
  el("loading").textContent = "读取 run…";
  el("specimen").hidden = true;

  const res = await fetch(`/api/runs/${encodeURIComponent(runId)}`);
  if (!res.ok) {
    el("loading").textContent = `读取失败：${res.status}`;
    return;
  }
  state.detail = await res.json();
  state.current = state.detail.run;
  el("loading").hidden = true;
  el("specimen").hidden = false;
  plateNo = 0;
  renderConfigs();   // state.current 这时才有值，左栏才能标出选中的 run
  renderSpecimen();
  renderOutline();
  window.scrollTo({ top: 0, behavior: "instant" });
}

/* ═══════════════════════════════════════════════════════════════════
   Config sheet: the YAML itself, and the button that runs it.

   Third main view next to the specimen and the comparison. The YAML is the
   source of truth on disk (not a run's snapshot), so the page answers "what
   does this file say right now" — and the same text is what a launch will
   use. Highlighting reuses yamlHTML(), the same function the specimen's
   「配置原文」 section uses, so the two views read identically.
   ═══════════════════════════════════════════════════════════════════ */

function closeConfigSheet() {
  if (!state.configPath) return;
  state.configPath = null;
  el("config").hidden = true;
  el("config").innerHTML = "";
}

function showOnly(pane) {
  el("specimen").hidden = pane !== "specimen";
  el("comparison").hidden = pane !== "comparison";
  el("config").hidden = pane !== "config";
  el("loading").hidden = true;
  if (pane !== "specimen" && state.detail) {
    el("specimen").innerHTML = "";   // 三个页面同时在 DOM 里会出现重复 id 和重复 Plotly iframe
  }
  if (pane !== "comparison") el("comparison").innerHTML = "";
  if (pane !== "config") el("config").innerHTML = "";
}

async function openConfig(path) {
  if (!path) return;
  exitCompare();
  state.configPath = path;
  showOnly("config");
  renderConfigs();
  el("config").hidden = false;
  el("config").innerHTML = `<p class="loading">读取配置…</p>`;

  let d;
  try {
    const res = await fetch(`/api/configs/${path.split("/").map(encodeURIComponent).join("/")}`);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || res.status);
    }
    d = await res.json();
  } catch (e) {
    el("config").innerHTML = `<p class="loading">读取失败：${esc(e.message)}</p>`;
    return;
  }
  state.configDetail = d;
  el("config").innerHTML = renderConfigSheet(d);
  wireConfigSheet(d);
  renderOutline();
  window.scrollTo({ top: 0, behavior: "instant" });
}

function renderConfigSheet(d) {
  const job = d.job;
  const runRows = d.runs.length
    ? d.runs.map(r => `
        <tr>
          <td class="cfg-run-when">${day(r.start_time)}</td>
          <td>${r.seed ? `种子 ${esc(r.seed)}` : `<code>${esc(r.run_id.slice(0, 6))}</code>`}</td>
          <td class="cfg-run-num">${num(r.summary && r.summary.ic)}</td>
          <td class="cfg-run-num">${pct(r.summary && r.summary.ann_cost)}</td>
          <td class="cfg-run-num">${num(r.summary && r.summary.ir_cost, 2)}</td>
          <td class="cfg-run-go"><button class="linkish" type="button" data-run="${esc(r.run_id)}">打开</button></td>
        </tr>`).join("")
    : `<tr><td colspan="6" class="cfg-empty">这个配置还没有跑出过 run。</td></tr>`;

  const status = job ? jobBanner(job) : "";

  return `
    <header class="cfg-sheet-id">
      <p class="cfg-sheet-crumb">${esc(d.dir)} / <span>${esc(d.label)}</span></p>
      <h1 class="cfg-sheet-title">${esc(d.label)}</h1>
      <div class="cfg-sheet-actions">
        ${status}
        ${d.runnable === false ? "" : `
        <div class="run-box" id="runbox">
          <button class="run-go" id="runtoggle" type="button" aria-expanded="false">
            <span>运行</span><span class="run-caret" aria-hidden="true">▾</span>
          </button>
          <div class="run-panel" id="runpanel" hidden>
            ${d.repeatable ? `
            <label class="run-row-in">
              <span>重复次数</span>
              <select id="runrepeat">
                <option value="1" selected>1 次</option>
                <option value="3">3 次（seed 42–44）</option>
                <option value="5">5 次（seed 42–46）</option>
              </select>
            </label>` : ""}
            <p class="run-hint">${d.repeatable
              ? "重复运行会改写 seed 并各自入库；不重复则直接用文件里的 seed。"
              : "这份配置没有实验 seed 变量，只跑一次。"}</p>
            <p class="run-error" id="runerror" hidden></p>
            <button class="run-start" id="runstart" type="button">开始运行</button>
          </div>
        </div>`}
      </div>
    </header>

    <section class="act" data-act="yaml">
      <h2 class="act-head"><span class="act-no">一</span>配置原文</h2>
      <p class="act-lede">页面展示的就是磁盘上这份文件，运行按钮用它启动训练。改动文件后刷新即可看到新内容。</p>
      <div class="cfg-head">
        <code class="cfg-path">examples/${esc(d.path)}</code>
        <span class="cfg-stat">${d.lines} 行 · 最近修改 ${day(d.mtime * 1000)}</span>
      </div>
      <div class="cfg-origin-row">
        <span class="cfg-badge disk">读自磁盘</span>
        <span class="cfg-origin-note">这是配置源文件当前的内容；每个 run 的「配置原文」是它跑时冻结的快照。</span>
      </div>
      <pre class="yaml cfg-yaml" tabindex="0">${yamlHTML(d.yaml)}</pre>
    </section>

    <section class="act" data-act="runs">
      <h2 class="act-head"><span class="act-no">二</span>该配置的运行</h2>
      <p class="act-lede">同一份 YAML 的每次运行。点「打开」进入那一次实验的解剖页。</p>
      <table class="cfg-runs-table">
        <thead><tr><th>时间</th><th>run</th><th>IC</th><th>年化</th><th>IR</th><th></th></tr></thead>
        <tbody>${runRows}</tbody>
      </table>
    </section>`;
}

function jobBanner(job) {
  const text = job.status === "running" ? "运行中"
    : job.status === "queued" ? "排队中"
    : job.status === "done" ? "已完成" : "失败";
  return `<span class="run-badge ${esc(job.status)}" data-job="${esc(job.id)}">
    <span class="run-badge-dot" aria-hidden="true"></span>${text}
    <button class="linkish run-badge-log" type="button" data-log="${esc(job.id)}">日志</button>
  </span>`;
}

function wireConfigSheet(d) {
  const toggle = el("runtoggle");
  if (toggle) {
    toggle.addEventListener("click", () => {
      const panel = el("runpanel");
      const open = panel.hidden;
      panel.hidden = !open;
      toggle.setAttribute("aria-expanded", String(open));
    });
  }
  const start = el("runstart");
  if (start) start.addEventListener("click", () => {
    const sel = el("runrepeat");
    startRun(d.path, sel ? Number(sel.value) : 1);
  });

  el("config").querySelectorAll("button[data-run]").forEach(b => {
    b.addEventListener("click", () => selectRun(b.dataset.run, d.label));
  });
  el("config").querySelectorAll("button[data-log]").forEach(b => {
    b.addEventListener("click", () => openDrawer(b.dataset.log, d.path));
  });
}

/* ═══════════════════════════════════════════════════════════════════
   Launching a run, and the log drawer.

   The queue lives in the uvicorn process, not the browser: closing the page
   or the drawer does not stop training. Reloading the server does — the
   queue is in memory on purpose (see runner.py).
   ═══════════════════════════════════════════════════════════════════ */

async function startRun(path, repeat) {
  let jobId;
  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, repeat }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || res.status);
    }
    jobId = (await res.json()).job_id;
  } catch (e) {
    const box = el("runerror");
    if (box) {
      box.textContent = `无法启动：${e.message}`;
      box.hidden = false;
    }
    return;
  }
  const box = el("runerror");
  if (box) box.hidden = true;
  state.job = { job_id: jobId, path };
  openDrawer(jobId, path);
  refreshConfigsSoon();
}

function openDrawer(jobId, path) {
  state.job = { job_id: jobId, path };
  const root = el("rundrawer");
  root.hidden = false;
  root.classList.remove("done", "failed");
  el("drawertitle").textContent = path;
  el("drawermeta").textContent = "连接中";
  el("drawerlog").textContent = "";
  logLines = 0;
  el("drawerfoot").hidden = true;
  el("drawerfoot").innerHTML = "";
  el("drawercancel").hidden = false;

  if (state.eventSource) state.eventSource.close();
  const es = new EventSource(`/api/run/${encodeURIComponent(jobId)}/logs`);
  state.eventSource = es;

  es.onmessage = ev => {
    appendLog(ev.data);
    el("drawermeta").textContent = "运行中";
  };
  es.addEventListener("end", () => {
    es.close();
    state.eventSource = null;
    finishDrawer(jobId, path);
  });
  es.onerror = () => {
    // EventSource 会自己重连；连接真的断了就别再空转
    if (es.readyState === EventSource.CLOSED) finishDrawer(jobId, path);
  };
}

const MAX_LOG_LINES = 4000;
let logLines = 0;

function appendLog(line) {
  const box = el("drawerlog");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  box.textContent += (box.textContent ? "\n" : "") + line;
  logLines += 1;
  // 一次训练能刷出上千行；DOM 里留太多会让滚动变卡。超限时从头砍掉一半，
  // 代价是看不了最早的那段——服务端环形缓冲仍保留最近 2000 行。
  if (logLines > MAX_LOG_LINES) {
    const lines = box.textContent.split("\n");
    const keep = lines.slice(Math.floor(lines.length / 2));
    box.textContent = "…（较早的日志已省略）\n" + keep.join("\n");
    logLines = keep.length + 1;
  }
  if (atBottom) box.scrollTop = box.scrollHeight;
}

async function finishDrawer(jobId, path) {
  let state_;
  try {
    const r = await fetch(`/api/run/${encodeURIComponent(jobId)}`);
    state_ = await r.json();
  } catch (e) {
    state_ = null;
  }
  const root = el("rundrawer");
  const ok = state_ && state_.status === "done";
  root.classList.add(ok ? "done" : "failed");
  el("drawermeta").textContent = state_
    ? (ok ? "已完成" : `失败${state_.exit_code != null ? ` · 退出码 ${state_.exit_code}` : ""}`)
    : "已结束";
  el("drawercancel").hidden = true;

  const foot = el("drawerfoot");
  foot.hidden = false;
  foot.innerHTML = ok
    ? `运行结束。<button class="linkish" type="button" id="draweropens">刷新并查看新 run</button>`
    : `运行失败，日志末尾是原因。<button class="linkish" type="button" id="draweropens">重试</button>`;
  const btn = el("draweropens");
  if (btn) btn.addEventListener("click", async () => {
    await reloadConfigs();
    if (ok) {
      const c = state.configs.find(x => x.path === path);
      if (c && c.newest_run_id) selectRun(c.newest_run_id, c.key);
    } else if (state.configPath === path) {
      openConfig(path);
    }
    el("rundrawer").hidden = true;
  });

  refreshConfigsSoon();
}

async function reloadConfigs() {
  try {
    const res = await fetch("/api/configs");
    if (!res.ok) return;
    state.configs = await res.json();
  } catch (e) {
    return;
  }
  const exps = [...new Set(state.configs.map(c => c.experiment))];
  const totalRuns = state.configs.reduce((n, c) => n + c.run_count, 0);
  el("meta").textContent = `${state.configs.length} 个配置 · ${totalRuns} 个 run · ${exps.length} 个实验`;
  renderConfigs();
  renderPickBar();
}

let refreshTimer = null;
function refreshConfigsSoon() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(reloadConfigs, 1500);
}

el("drawerclose").addEventListener("click", () => { el("rundrawer").hidden = true; });
el("drawercancel").addEventListener("click", async () => {
  if (!state.job) return;
  try {
    await fetch(`/api/run/${encodeURIComponent(state.job.job_id)}/cancel`, { method: "POST" });
  } catch (e) { /* 取消失败就让训练自然结束 */ }
});

/* ═══════════════════════════════════════════════════════════════════
   Compare view: N runs, one row per field, colour only where they differ.
   ═══════════════════════════════════════════════════════════════════ */

function exitCompare() {
  if (!state.compare) return;
  state.compare = null;
  el("comparison").hidden = true;
  el("comparison").innerHTML = "";
  el("specimen").hidden = !state.detail || !!state.configPath;
  // 打开对照时解剖页的 DOM 被清掉了，回来必须重绘
  if (state.detail && !state.configPath) {
    plateNo = 0;
    renderSpecimen();
    renderOutline();
  }
}

async function openCompare() {
  const ids = [...state.selected];
  if (ids.length < 2) return;
  closeConfigSheet();
  state.compareIds = ids;
  el("specimen").hidden = true;
  el("specimen").innerHTML = "";   // 两页同时留在 DOM 里会出现重复 id 和重复的 Plotly iframe
  el("config").hidden = true;
  el("config").innerHTML = "";
  el("comparison").hidden = false;
  el("loading").hidden = false;
  el("loading").textContent = `读取 ${ids.length} 个 run 并对照…`;

  let payload;
  try {
    const res = await fetch(`/api/compare?runs=${ids.map(encodeURIComponent).join(",")}`);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || res.status);
    }
    payload = await res.json();
  } catch (e) {
    el("loading").textContent = `对照失败：${e.message}`;
    return;
  }

  el("loading").hidden = true;
  state.compare = payload;
  state.compareIds = ids;
  plateNo = 0;
  renderCompare();
  window.scrollTo({ top: 0, behavior: "instant" });
}

/* Formatting follows the kind the backend marked; nothing is computed here. */
function fmtCell(v, kind) {
  if (v == null || v === "") return null;
  switch (kind) {
    case "pct":  return pct(v);
    case "pct1": return pct(v, 1);
    case "float": return num(v);
    case "count": return typeof v === "number" ? v.toLocaleString("en-US") : String(v);
    case "date": {
      const d = new Date(v * 1000);
      return Number.isNaN(d.valueOf()) ? String(v)
        : `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, "0")}.${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    }
    default: return String(v);
  }
}

function compareHead(cmp) {
  const legend = cmp.runs.map(r => {
    const i = "ABCD".indexOf(r.slot);
    return `<span class="lg-item">
      <i class="lg-line ${SLOT_COLORS[i % 2]}${i >= 2 ? " dash" : ""}"></i>
      <span class="lg-slot ${SLOT_COLORS[i % 2]}">${r.slot}</span>
      <span class="lg-name">${esc(r.config || r.name)}</span>
      ${r.seed ? `<span class="lg-seed">seed ${esc(r.seed)}</span>` : ""}
    </span>`;
  }).join("");

  const warn = cmp.warnings.length
    ? `<div class="flag cm-warn">
        <b>这些 run 的指标不可直接比较：</b>
        ${cmp.warnings.map(w => `${esc(w.label)} 分别为 ${w.values.map(v => `<code>${esc(v)}</code>`).join(" / ")}`).join("；")}。
        测试段或股票池不同，IC、收益衡量的是不同的东西——数字仍然真实，但横向比较没有意义。
      </div>`
    : `<p class="cm-clean">测试段、股票池、基准、数据源一致，指标可以直接横向比较。</p>`;

  return `<header class="act act-id cm-id">
    <div class="id-label">
      <span class="id-no">对照</span>
      <span class="id-id">${cmp.runs.length} 个 run</span>
      <span class="id-exp">${esc([...new Set(cmp.runs.map(r => r.experiment))].join(" / "))}
        <button class="linkish cm-back" id="pickback" type="button">返回单 run 视图 · Esc</button>
      </span>
    </div>
    <h2 class="id-title">${esc(cmp.verdict)}</h2>
    <div class="cm-legend">${legend}
      <span class="lg-item lg-key"><span class="same-chip">相同</span>不加标记</span>
      <span class="lg-item lg-key"><span class="diff-chip">不同</span>赭色</span>
      <span class="lg-item lg-key"><span class="best-chip">最优</span>靛蓝下划线</span>
      <span class="lg-item lg-key"><span class="miss-chip">缺</span>该 run 没有此项</span>
    </div>
    ${warn}
  </header>`;
}

/* One comparison table. Rows keep the order the backend sent (spec order);
   a differing row is marked on the row, and only the differing cells get
   the ochre — identical cells stay plain ink so the eye stops on the delta. */
function cmTable(rows, cmp, opts) {
  const o = opts || {};
  const anyDiff = rows.some(r => !r.same && !r.always_diff);
  const head = cmp.runs.map((r, i) => {
    const slot = `<span class="slot-tag ${SLOT_COLORS[i % 2]}${i >= 2 ? " dash" : ""}">${r.slot}</span>`;
    return `<th>${slot}<span class="cm-col-name">${esc(r.config || r.name)}${r.seed ? ` · <span class="cm-col-seed">seed ${esc(r.seed)}</span>` : ""}</span></th>`;
  }).join("");

  const body = rows.map(row => {
    const cells = row.values.map((v, i) => {
      const text = fmtCell(v, row.kind);
      if (text == null) return `<td class="cm-miss">缺</td>`;
      const isBest = row.best === i;
      // 身份字段天然不同（run id、时间），不上赭色，否则真信号被淹没
      const diff = !row.same && !row.always_diff;
      return `<td class="${diff ? "cm-diff" : ""}${isBest ? " cm-best" : ""}">${esc(text)}</td>`;
    }).join("");
    const term = row.term
      ? `<button class="has-term cm-term" type="button" data-term="${esc(row.term)}" aria-expanded="false"
          aria-label="展开解释">?</button>`
      : "";
    const cls = row.same || row.always_diff ? "cm-same" : "cm-differ";
    return `<tr class="${cls}">
      <th><span class="cm-row-label">${esc(row.label || row.key)}</span>
        ${o.showKey && row.key !== row.label ? `<code class="cm-row-key">${esc(row.key)}</code>` : ""}
        ${term}</th>${cells}</tr>`;
  }).join("");

  const foot = anyDiff
    ? `<tfoot><tr><th>不同</th>${cmp.runs.map(() => `<td>${rows.filter(r => !r.same && !r.always_diff).length}</td>`).join("")}</tr></tfoot>`
    : "";

  // 值全是自由文本时用固定布局，避免长路径/JSON 把表撑出容器
  const texty = rows.every(r => r.kind === "text" || r.kind === "count");
  return `<div class="cm-wrap"><table class="cm-table${texty ? " cm-text" : ""}">
    <thead><tr><th class="cm-corner">字段</th>${head}</tr></thead>
    <tbody>${body}</tbody>${foot}</table></div>`;
}

function cmSection(no, key, title, lede, body) {
  return `<section class="act cm-act" data-act="${key}" id="act-${key}">
    <h2 class="act-head"><span class="act-no">${no}</span>${esc(title)}</h2>
    ${lede ? `<p class="act-lede">${esc(lede)}</p>` : ""}
    ${body}
  </section>`;
}

/* Overlay chart: one slot-coloured line per run, shared y axis. */
function lineMany(series, opts) {
  const o = opts || {};
  const usable = series.filter(s => (s.values || []).some(v => Number.isFinite(v)));
  if (!usable.length) return `<p class="plate-empty">没有可画的数据</p>`;

  const all = usable.flatMap(s => s.values.filter(v => Number.isFinite(v)));
  let lo = Math.min(...all), hi = Math.max(...all);
  if (o.baseline != null) { lo = Math.min(lo, o.baseline); hi = Math.max(hi, o.baseline); }
  if (hi === lo) { hi = lo + 1e-12; lo -= 1e-12; }

  const padX = 4, padY = 8;
  const w = W - padX * 2, h = H - padY * 2;
  const n = Math.max(...series.map(s => (s.values || []).length)) || 1;
  const X = i => padX + (n <= 1 ? 0 : (i / (n - 1)) * w);
  const Y = v => padY + h - ((v - lo) / (hi - lo)) * h;

  const paths = series.map((s, si) => {
    if (!s.values) return "";
    const i = "ABCD".indexOf(s.slot);
    const color = `var(--${SLOT_COLORS[i % 2]})`;
    const dash = i >= 2 ? ` stroke-dasharray="5 3"` : "";
    const d = s.values.map((v, k) => [k, v])
      .filter(p => Number.isFinite(p[1]))
      .map((p, kk) => `${kk ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`)
      .join(" ");
    const hits = s.values.map((v, k) => Number.isFinite(v)
      ? `<circle cx="${X(k).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="5" fill="transparent" class="cm-hit"
          data-slot="${si}" data-i="${k}"><title>${esc(s.slot)} ${num(v, 6)}</title></circle>`
      : "").join("");
    return `<path d="${d}" fill="none" stroke="${color}" stroke-width="1.5"${dash} vector-effect="non-scaling-stroke"/>${hits}`;
  }).join("");

  const zero = o.baseline == null ? ""
    : `<line x1="${padX}" x2="${padX + w}" y1="${Y(o.baseline).toFixed(1)}" y2="${Y(o.baseline).toFixed(1)}" class="zero"/>`;

  const labels = o.labels || null;
  const dates = labels && labels.length
    ? `<div class="cm-axis"><span>${esc(labels[0])}</span><span>${esc(labels[labels.length - 1])}</span></div>`
    : "";

  return `<div class="line"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      ${zero}${paths}
    </svg>
    <div class="line-scale"><span>${num(lo, 5)}</span><span>${num(hi, 5)}</span></div>
    ${dates}</div>`;
}

function renderCompare() {
  const cmp = state.compare;
  const secs = Object.fromEntries(cmp.sections.map(s => [s.key, s]));
  cmChartCache = {};

  const diffRows = cmp.param_diff;
  const diffBody = diffRows.length
    ? `<p class="act-lede">只列出不同的参数。相同的 ${cmp.param_same.length} 项收起在下面——
      如果清单只有一两行，那这次差异就来自这一两行。</p>
      ${cmTable(diffRows, cmp, { showKey: true })}
      <details class="fold cm-fold"><summary>相同的参数（${cmp.param_same.length}）</summary>
        ${cmTable(cmp.param_same, cmp, { showKey: true })}
      </details>`
    : `<p class="repeat-single">所有 run 的参数完全相同。结果上的任何差异都不是配置改动带来的——
      它来自训练随机性、运行时间或数据窗口的细微不同。</p>`;

  const yamlDiff = cmp.yaml ? `<details class="fold cm-fold cm-yaml">
      <summary>配置原文并列（${esc(cmp.yaml.a.path)}）</summary>
      ${yamlPair(cmp.yaml.a, cmp.yaml.b)}
    </details>` : "";

  const risk = cmp.risk.length ? (() => {
    const groups = [...new Set(cmp.risk.map(r => r.group))];
    const stats = [...new Set(cmp.risk.map(r => r.stat))];
    const GL = { mean: "日均超额", std: "标准差", annualized_return: "年化超额",
      information_ratio: "IR", max_drawdown: "最大回撤" };
    const cellOf = (g, s) => cmp.risk.find(r => r.group === g && r.stat === s);
    const head = cmp.runs.map((r, i) =>
      `<th><span class="slot-tag ${SLOT_COLORS[i % 2]}${i >= 2 ? " dash" : ""}">${r.slot}</span></th>`).join("");
    const body = groups.map(g => {
      const cells = [...new Set(cmp.risk.filter(r => r.group === g).map(r => r.stat))];
      return cells.map((s, si) => {
        const lookup = cellOf(g, s);
        const vals = lookup ? lookup.values : [];
        return `<tr><th>${si === 0 ? esc(g === "excess_return_with_cost" ? "含成本" : "无成本") : ""}</th>
          <th class="cm-stat">${esc(GL[s] || s)}</th>
          ${vals.map((v, i) => `<td class="${lookup && !lookup.same ? "cm-diff" : ""}${
            lookup && lookup.best === i ? " cm-best" : ""}">${v == null ? '<span class="cm-miss">缺</span>' : esc(num(v))}</td>`).join("")}
        </tr>`;
      }).join("");
    }).join("");
    return `<div class="cm-wrap"><table class="cm-table cm-risk">
      <thead><tr><th class="cm-corner">口径</th><th class="cm-stat">统计量</th>${head}</tr></thead>
      <tbody>${body}</tbody></table></div>`;
  })() : `<p class="repeat-single">这些 run 没有风险拆解表（缺 port_analysis_1day.pkl）。</p>`;

  const tagRows = cmp.tags;
  const tagBody = tagRows.length
    ? cmTable(tagRows.map(t => ({ ...t, label: t.key, kind: "text", best: null, term: null })), cmp, { showKey: false })
    : `<p class="repeat-single">没有可对比的标签。</p>`;

  const artBody = cmp.artifacts.length
    ? `<div class="cm-wrap"><table class="cm-table cm-art">
        <thead><tr><th class="cm-corner">产物</th>${cmp.runs.map((r, i) =>
          `<th><span class="slot-tag ${SLOT_COLORS[i % 2]}${i >= 2 ? " dash" : ""}">${r.slot}</span></th>`).join("")}</tr></thead>
        <tbody>${cmp.artifacts.map(a => `<tr><th><code class="cm-row-key">${esc(a.path)}</code></th>
          ${a.present.map(p => `<td>${p ? '<span class="cm-yes">有</span>' : '<span class="cm-miss">缺</span>'}</td>`).join("")}</tr>`).join("")}
        </tbody></table></div>`
    : `<p class="repeat-single">产物的文件构成完全一致——没有哪个 run 多出或少掉文件。</p>`;

  el("comparison").innerHTML = [
    compareHead(cmp),
    cmSection("一", "setting", "实验设置", "喂进去的东西。这里不同，后面的指标就不可比。",
      cmTable(secs.setting.rows, cmp)),
    cmSection("二", "params", "配置差异", "改了哪些参数。只有这一节不同、指标也变，才算配置的作用。",
      yamlDiff + diffBody),
    cmSection("三", "signal", "信号：预测得准不准", "同一段测试期、同一批股票上，谁的打分更贴近真实涨跌。",
      cmTable(secs.signal.rows, cmp) + chartsFor(cmp, ["ic", "rank_ic"])),
    cmSection("四", "execution", "执行：下单到成交", "换手低、成本低，是持续赚钱的前提。",
      cmTable(secs.execution.rows, cmp) + chartsFor(cmp, ["turnover", "cost"])),
    cmSection("五", "outcome", "结果：赚没赚到，稳不稳", "账本。含成本年化、IR、回撤三项一起读。",
      cmTable(secs.outcome.rows, cmp) + risk + chartsFor(cmp, ["excess"])),
    cmSection("六", "training", "训练：有没有学过头", "验证损失回升就是过拟合的信号。",
      cmTable(secs.training.rows, cmp) + chartsFor(cmp, ["l2_valid"])),
    cmSection("七", "record", "原始记录", "标签与产物的差异——谁的图表生成了、谁没有。",
      `<div class="sub-head"><h3>标签</h3></div>${tagBody}
       <div class="sub-head"><h3>产物差异</h3><p>${cmp.artifacts.length
         ? "只在部分 run 里存在的文件。渲染图表不在对照页里挂载，点单 run 查看。"
         : "各 run 的产物目录逐文件比对后的结果。"}</p></div>${artBody}`),
  ].join("");

  // 图版编号必须按文档顺序递增，所以图在这里按调用顺序惰性生成
  renderOutline();
}

/* Overlay plates are emitted lazily in document order so plateNo stays
   sequential: section three asks for ic/rank_ic, then execution, etc. */
let cmChartCache = {};
function chartsFor(cmp, ids) {
  return ids.map(id => {
    if (!(id in cmChartCache)) cmChartCache[id] = cmChartPlate(cmp, id);
    return cmChartCache[id];
  }).join("");
}

function cmChartPlate(cmp, id) {
  const c = cmp.charts.find(x => x.id === id);
  if (!c) return "";
  const legend = cmp.runs.map((r, i) => {
    const has = c.series[i] && c.series[i].values;
    return `<span class="${has ? "" : "is-missing"}"><i class="lg-line ${SLOT_COLORS[i % 2]}${i >= 2 ? " dash" : ""}"></i>${r.slot} ${esc(r.config || r.name)}</span>`;
  }).join("");
  const missingRuns = cmp.runs.filter((r, i) => !c.series[i] || !c.series[i].values);
  const missing = missingRuns.length
    ? " · " + missingRuns.map(r => `${r.slot} ${esc(r.config || r.name)} 没有这条数据`).join("，")
    : "";
  return plate({
    id: `cm-${c.id}`,
    title: c.title,
    note: c.note + missing,
    meta: c.series.some(s => s.values) ? null : "无数据",
    body: `<div class="curve-key">${legend}</div>` +
      lineMany(c.series.map((s, i) => ({ ...s, slot: "ABCD"[i] })),
               { baseline: ["ic", "rank_ic", "excess"].includes(c.id) ? 0 : null,
                 labels: c.labels }),
  });
}

/* ═══════════════════════════════════════════════════════════════════
   5. The specimen: five acts.
   ═══════════════════════════════════════════════════════════════════ */

function renderSpecimen() {
  const d = state.detail;
  const run = d.run;
  const s = d.summary || {};
  const x = run.extras || {};
  const figures = renderPlates(run);

  el("specimen").innerHTML = [
    actIdentity(run, s),
    actSameConfig(),
    actSetting(run, x),
    actConfigSource(run, x),
    actSignal(run, x),
    actExecution(run, x),
    actOutcome(run, x),
    actTraining(run, x),
    figures ? `<section class="act" data-act="figures">
      <h2 class="act-head"><span class="act-no">七</span>官方图表</h2>
      <p class="act-lede">qlib 用自家 report 接口生成的图。和上面那些序列图是同一份数据，
        只是换了更专业的视角——分组收益、分层收益、月度稳定性都在这里。</p>
      ${figures}
    </section>` : "",
    actRecord(run),
    actProvenance(run, x),
  ].join("");

  observePlots();
}

/* --- 0. identity: the run as a specimen label --- */
function actIdentity(run, s) {
  const notes = (state.detail.notes || []);
  const verdict = state.detail.verdict || run.name;
  const sub = state.detail.sub || "";
  return `<header class="act act-id" data-act="id">
    <div class="id-label">
      <span class="id-no">RUN</span>
      <span class="id-id">${esc(run.run_id.slice(0, 12))}</span>
      <span class="id-exp">${esc(run.experiment)} / ${esc(run.name)}</span>
    </div>
    <h2 class="id-title">${esc(verdict)}</h2>
    ${sub ? `<p class="id-sub">${esc(sub)}</p>` : ""}
    <dl class="id-facts">
      <div><dt>开始</dt><dd>${esc(day(run.start_time))}</dd></div>
      <div><dt>耗时</dt><dd>${esc(duration(run.duration_ms))}</dd></div>
      <div><dt>状态</dt><dd>${esc(run.status)}</dd></div>
      <div><dt>用户</dt><dd>${esc(run.user || "—")}</dd></div>
    </dl>
    ${notes.length ? `<div class="id-notes">${notes.map(n =>
      `<p class="${n.warn ? "warn" : ""}"><b>${esc(n.title)}</b>${n.paras.map(p => esc(p)).join("")}</p>`
    ).join("")}</div>` : ""}
  </header>`;
}

const duration = ms => {
  if (ms == null) return "—";
  const sec = Math.round(ms / 1000);
  if (sec < 60) return `${sec} 秒`;
  return `${Math.floor(sec / 60)} 分 ${sec % 60} 秒`;
};

/* --- 0.5 same config, other runs ------------------------------------
   Only rendered when this YAML was run more than once. This is the whole
   point of grouping by config: it separates "the config change did this"
   from "the seed did this". Everything below is the same config. */

function actSameConfig() {
  const cfg = state.configs.find(c => c.key === state.currentConfig);
  if (!cfg || cfg.run_count < 2) {
    // 只有一次运行：说明为什么看不到波动，比留空更有用
    return cfg ? `<section class="act act-repeat" data-act="repeat">
      <p class="repeat-single">这个配置只运行过 <b>1</b> 次，
        所以下面的指标里包含多少随机波动无法判断。用 <code>run_all.py --repeat N</code>
        跑多次才能把配置差异和种子噪声分开。</p>
    </section>` : "";
  }

  const agg = cfg.aggregate || {};
  const cur = state.current && state.current.run_id;

  const rows = cfg.runs.map(r => {
    const s = r.summary || {};
    const isCur = r.run_id === cur;
    return `<tr class="${isCur ? "cur" : ""}">
      <th>${r.seed ? `种子 ${esc(r.seed)}` : esc(r.name)}
        ${isCur ? '<span class="cur-tag">当前</span>' : ""}</th>
      <td>${num(s.ic)}</td>
      <td>${pct(s.ann_cost)}</td>
      <td>${num(s.ir_cost, 2)}</td>
      <td>${pct(s.dd_cost)}</td>
      <td class="row-when">${day(r.start_time)}</td>
      <td><button class="linkish" type="button" data-run="${esc(r.run_id)}">切换</button></td>
    </tr>`;
  }).join("");

  const spreadRow = ["ic", "ann_cost", "ir_cost", "dd_cost"].map(k => {
    const a = agg[k];
    if (!a) return `<td>—</td>`;
    if (a.spread === 0) return `<td class="spread-cell same">一致</td>`;
    const fmt = k === "ic" || k === "ir_cost" ? v => num(v, 4) : v => pct(v);
    return `<td class="spread-cell">${fmt(a.spread)}</td>`;
  }).join("");

  const icSpread = agg.ic;
  const deterministic = icSpread && icSpread.spread === 0;
  const guide = deterministic
    ? `同一份 YAML 跑了 ${cfg.run_count} 次，IC 完全相同（${num(icSpread.min, 4)}）。
       seed 固定时 qlib 的训练与回测是确定性的，重复运行不会产生波动——
       「重跑一次看看」在这里没有意义，任何差异都只可能来自配置或代码改动。`
    : icSpread
      ? `同一份 YAML 的 ${cfg.run_count} 次运行，IC 极差 ${num(icSpread.spread, 4)}
         （${num(icSpread.min, 4)} ~ ${num(icSpread.max, 4)}）。`
      : "";

  // 当前 run 相对同配置其他运行的偏离，用它判断这次是运气还是真的不同
  const curRun = cfg.runs.find(r => r.run_id === cur);
  let outlier = "";
  if (curRun && icSpread && icSpread.n >= 2 && curRun.summary.ic != null && !deterministic) {
    const dev = curRun.summary.ic - icSpread.mean;
    const ratio = icSpread.spread > 0 ? Math.abs(dev) / icSpread.spread : 0;
    outlier = ratio > 0.8
      ? `本次 IC 偏离同配置均值 ${num(dev, 4)}，已接近或超出这组运行的极差——值得看看这次运行有没有异常。`
      : `本次 IC 在正常波动范围内（偏离均值 ${num(dev, 4)}）。`;
  }

  return `<section class="act act-repeat" data-act="repeat">
    <h2 class="act-head"><span class="act-no">同配置</span>这个 YAML 跑过 ${cfg.run_count} 次</h2>
    <p class="act-lede">${esc(guide)}${deterministic ? "" :
      " 比较两份不同 YAML 时，指标差异只有超过这个极差才算真的有差别；否则可能只是种子不同。"}</p>
    ${outlier ? `<p class="repeat-judge">${esc(outlier)}</p>` : ""}
    <div class="risk-wrap">
      <table class="repeat">
        <thead><tr>
          <th>运行</th><th>IC</th><th>年化</th><th>IR</th><th>最大回撤</th><th>时间</th><th></th>
        </tr></thead>
        <tbody>${rows}</tbody>
        <tfoot><tr>
          <th>极差</th>${spreadRow}<td></td><td></td>
        </tr></tfoot>
      </table>
    </div>
  </section>`;
}

/* --- 1. setting: what went in --- */
function actSetting(run, x) {
  const ds = x.dataset_inputs || {};
  const cfg = x.config_summary || {};
  const seg = ds.segments || {};
  const bt = (cfg.port_analysis_config || {}).backtest || {};
  const sty = (cfg.port_analysis_config || {}).strategy || {};
  const p = run.all_params || {};

  const rows = [];
  if (ds.class) rows.push(["数据集", `${ds.class} · ${ds.handler || "—"}`]);
  const segLabel = { train: "训练段", valid: "验证段", test: "测试段" };
  for (const k of ["train", "valid", "test"]) {
    if (seg[k]) rows.push([segLabel[k], seg[k].join("  →  ")]);
  }
  if (cfg.market) rows.push(["股票池", cfg.market]);
  if (cfg.benchmark) rows.push(["基准", cfg.benchmark]);
  if (cfg.qlib_init) {
    const qi = cfg.qlib_init;
    rows.push(["数据源", `${qi.provider_uri || "—"} · ${qi.region || "—"}`]);
  }
  if (sty.class) rows.push(["策略", `${sty.class} ${JSON.stringify(sty.kwargs || {})}`]);
  if (bt.start_time) rows.push(["回测区间", `${bt.start_time}  →  ${bt.end_time}`]);
  if (bt.account) rows.push(["初始资金", Number(bt.account).toLocaleString("en-US")]);
  const ek = bt.exchange_kwargs || {};
  if (ek.open_cost != null) rows.push(["交易成本", `开 ${ek.open_cost} · 平 ${ek.close_cost} · 最低 ${ek.min_cost}`]);
  if (ek.limit_threshold != null) rows.push(["撮合规则", `限价阈值 ${ek.limit_threshold} · 成交价 ${ek.deal_price}`]);

  /* 模型超参：每个都可以展开讲解 */
  const hyper = [
    ["num_leaves", p["model.kwargs.num_leaves"]],
    ["learning_rate", p["model.kwargs.learning_rate"]],
    ["max_depth", p["model.kwargs.max_depth"]],
    ["lambda_l1", p["model.kwargs.lambda_l1"]],
    ["lambda_l2", p["model.kwargs.lambda_l2"]],
    ["colsample_bytree", p["model.kwargs.colsample_bytree"]],
    ["subsample", p["model.kwargs.subsample"]],
  ].filter(([, v]) => v != null);

  const hyperHTML = hyper.map(([k, v]) => {
    const g = GLOSSARY[k];
    return `<div class="hyper">
      <dt>${anno(v, k)}<span class="hyper-name">${esc((g && g.name.split(" · ")[1]) || k)}</span></dt>
    </div>`;
  }).join("");

  return `<section class="act" data-act="setting">
    <h2 class="act-head"><span class="act-no">一</span>实验设置</h2>
    <p class="act-lede">这是一次「喂进去什么」的记录。下面每个数字都可以点开看它控制什么。</p>
    <dl class="spec">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
    ${hyper.length ? `<div class="sub-head"><h3>模型超参</h3>
      <p>LightGBM 的七个旋钮。括号里是本次取值，点数字看含义。</p></div>
      <dl class="hyper-grid">${hyperHTML}</dl>` : ""}
  </section>`;
}

/* --- 2. config source: the YAML that defined the run ----------------
   This is the experiment. qlib only logs the resolved config pickle, which
   drops every comment; those comments are why the config reads at all. So
   show the actual file, with keys and comments marked up. */

/* Minimal YAML highlighter. Works line by line and escapes text *before*
   wrapping it in spans, so the markup we emit is never re-escaped by a later
   rule. Key/value/comment decisions are made on the raw text. */
function yamlHTML(text) {
  const esc1 = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  const span = (cls, s) => `<span class="y-${cls}">${esc1(s)}</span>`;

  return text.split("\n").map(line => {
    // line that is entirely a comment
    if (/^\s*#/.test(line)) return span("comment", line);

    // split a trailing comment, but not a '#' inside a quoted string
    let body = line, comment = "";
    const q = line.search(/(["']).*?\1/);
    const hash = line.indexOf("#", q >= 0 ? line.indexOf(line.match(/(["']).*?\1/)[0]) + 1 : 0);
    if (hash >= 0) {
      body = line.slice(0, hash).replace(/\s+$/, "");
      comment = line.slice(hash);
    }

    // leading indentation, optional list dash, then key:
    const m = body.match(/^(\s*)(-(?:\s+|$))?([\w.\-*&]+)(\s*:)(.*)$/);
    if (m) {
      const [, ind, dash, key, colon, rest] = m;
      return ind
        + (dash ? span("dash", dash) : "")
        + span("key", key)
        + span("colon", colon)
        + valueHTML(rest)
        + (comment ? span("comment", " " + comment) : "");
    }
    // not a key line: still highlight strings / anchors / numbers
    const lead = body.match(/^(\s*)(-(?:\s+|$))?/);
    const ind = lead ? lead[1] : "";
    const dash = lead && lead[2] ? lead[2] : "";
    return ind + (dash ? span("dash", dash) : "")
      + valueHTML(body.slice(ind.length + dash.length))
      + (comment ? span("comment", " " + comment) : "");
  }).join("\n");

  function valueHTML(s) {
    // walk the string, emitting escaped text plus token spans
    let out = "";
    let i = 0;
    const re = /("[^"]*"|'[^']*')|(&[\w-]+|\*[\w-]+)|(^|\s)(-?\d+(?:\.\d+)?)(?=\s|$)/g;
    let m;
    while ((m = re.exec(s))) {
      out += esc1(s.slice(i, m.index));
      if (m[1]) out += span("str", m[1]);
      else if (m[2]) out += span("anchor", m[2]);
      else out += esc1(m[3]) + span("num", m[4]);
      i = re.lastIndex;
    }
    return out + esc1(s.slice(i));
  }
}

function actConfigSource(run, x) {
  const c = x.config_yaml;
  if (!c) {
    return `<section class="act" data-act="config">
      <h2 class="act-head"><span class="act-no">二</span>配置原文</h2>
      <p class="act-lede">这个 run 没有配置快照，源文件也已不在。
        qlib 只保存解析后的配置，原文件不随 run 保留。</p>
    </section>`;
  }

  const isSnap = c.source === "snapshot";
  const commentLines = c.text.split("\n").filter(l => /^\s*#/.test(l)).length;
  const keyLines = c.text.split("\n").filter(l => /^\s*[\w.\-*&]+\s*:/.test(l)).length;

  // 快照 = 当时那份文件的拷贝，可信。回读 = 现在磁盘上的文件，可能已被改动。
  let origin;
  if (isSnap) {
    origin = `<span class="cfg-badge snap">随 run 冻结</span>
      <span class="cfg-origin">这份 YAML 在运行时被拷贝进 run 的产物（<code>artifacts/config_source.yaml</code>），
        之后改原文件不会影响这里。</span>`;
  } else if (c.matches_run === false) {
    origin = `<span class="cfg-badge drift">内容已与 run 不符</span>
      <span class="cfg-origin">没有快照，读的是磁盘上的当前文件；它已经和这次 run 记录的参数对不上了。
        下面这份原文<b>不代表</b>当时实际跑的配置。</span>`;
  } else {
    origin = `<span class="cfg-badge disk">回读源文件</span>
      <span class="cfg-origin">这个 run 没有快照，读的是磁盘上的当前文件。
        ${c.matches_run === true ? "参数与 run 记录一致，可信。" : "无法校验一致性。"}
        下次运行会自动存快照。</span>`;
  }

  return `<section class="act" data-act="config">
    <h2 class="act-head"><span class="act-no">二</span>配置原文</h2>
    <p class="act-lede">YAML 才是这次实验的定义：用哪段数据、怎么切分、什么模型、
      多少超参、怎么撮合。run 里只存了解析后的值，注释全丢了——而注释恰恰解释了每个数字为什么这么设。</p>

    <div class="cfg-head">
      <code class="cfg-path">${isSnap ? "artifacts/" : "examples/"}${esc(c.path)}</code>
      <span class="cfg-stat">${c.lines} 行 · ${keyLines} 个配置项 · ${commentLines} 行注释</span>
    </div>
    <div class="cfg-origin-row">${origin}</div>

    <details class="fold cfg-fold" open>
      <summary>展开 / 收起全文</summary>
      <pre class="yaml">${yamlHTML(c.text)}${c.truncated ? "\n<span class=\"y-comment\">…（文件过长，已截断）</span>" : ""}</pre>
    </details>

    <p class="cfg-note">配置项的含义见上一节「实验设置」；两边是同一份配置的两种读法：
      这里看原文和注释，那里看结构化摘要。</p>
  </section>`;
}

/* --- 3. signal: did the model predict --- */
function actSignal(run, x) {
  const m = run.all_metrics || {};
  const ic = m["IC"], icir = m["ICIR"], ric = m["Rank IC"], ricir = m["Rank ICIR"];
  const icS = x.ic_series, ricS = x.rank_ic_series;
  const st = icS && icS.stats;

  const band = statBand([
    { label: "IC", value: num(ic), strong: true },
    { label: "ICIR", value: num(icir) },
    { label: "Rank IC", value: num(ric) },
    { label: "Rank ICIR", value: num(ricir) },
    { label: "为正占比", value: st ? pct(st.pos_ratio, 1) : "—" },
  ]);

  const plates = [];
  if (icS) {
    plates.push(plate({
      id: "plate-ic",
      title: "IC 逐日走势",
      note: "每一天单独算一次相关性。看的是「平均为正、但摆动多大」。零线以下的天数越多，实盘越难拿住。",
      meta: `${icS.total} 个交易日`,
      body: lineSVG(icS.values, { baseline: 0, labels: icS.dates, area: true }) +        (st ? statBand([
          { label: "均值", value: num(st.mean) },
          { label: "5% 分位", value: num(st.p05) },
          { label: "95% 分位", value: num(st.p95) },
          { label: "为正占比", value: pct(st.pos_ratio, 1) },
        ]) : ""),
    }));
  }
  if (ricS) {
    plates.push(plate({
      id: "plate-ric",
      title: "Rank IC 逐日走势",
      note: "只用排名算相关。它和上图贴得越近，说明预测里没有靠少数极端值撑场面。",
      meta: `${ricS.total} 个交易日`,
      body: lineSVG(ricS.values, { baseline: 0, labels: ricS.dates, color: "var(--ink-3)" }),
    }));
  }

  return `<section class="act" data-act="signal">
    <h2 class="act-head"><span class="act-no">三</span>信号：预测得准不准</h2>
    <p class="act-lede">四个数回答同一个问题——模型打分和真实涨跌有多相关。
      点开任意一个看判读标准。</p>
    <div class="band-wrap">${bandWithTerms(ic, icir, ric, ricir, st)}</div>
    ${plates.join("")}
  </section>`;
}

function bandWithTerms(ic, icir, ric, ricir, st) {
  const cell = (term, label, val, fmt) =>
    `<div class="cell"><dt>${anno(fmt(val), term)}<span>${esc(label)}</span></dt></div>`;
  return `<div class="quad">
    ${cell("IC", "IC · 预测与真实的日相关", ic, v => num(v))}
    ${cell("ICIR", "ICIR · 这个相关稳不稳", icir, v => num(v))}
    ${cell("Rank IC", "Rank IC · 只看排名的相关", ric, v => num(v))}
    ${cell("Rank ICIR", "Rank ICIR · 排名的稳定性", ricir, v => num(v))}
  </div>${st ? `<p class="quad-foot">${icS_consistent(st, ic)}</p>` : ""}`;
}

function icS_consistent(st, ic) {
  const sameSign = st.pos_ratio >= 0.6;
  return sameSign
    ? `${st.n} 个交易日里有 ${pct(st.pos_ratio, 1)} 的 IC 为正，方向基本一致，可以直接拿去做组合。`
    : `只有 ${pct(st.pos_ratio, 1)} 的交易日 IC 为正，方向不稳定，任何收益都更像运气。`;
}

/* --- 3. execution: order -> fill --- */
function actExecution(run, x) {
  const m = run.all_metrics || {};
  const ffr = m["1day.ffr"], pa = m["1day.pa"], pos = m["1day.pos"];
  const turn = x.turnover_series, cost = x.cost_series;

  const deg = ffr === 1 && pa === 0 && pos === 0;
  let flag = "";
  if (deg) {
    flag = "撮合指标失效：pa 与 pos 恰好为 0、ffr 恰为 1。这是 qlib 在缺 factor.day.bin 时退化成复权价模式的签名，" +
      "不能读成「成交完美」。收益率和回撤不受影响，它们走另一条计算路径。";
  }

  const plates = [];
  if (turn) {
    plates.push(plate({
      id: "plate-turnover",
      title: "逐日换手率",
      note: "每天换掉多少仓位。它自己不是问题，和下面那张成本图一起看才是问题。",
      meta: `${turn.total} 个交易日`,
      body: lineSVG(turn.values, { labels: turn.dates }) +
        statBand([
          { label: "均值", value: pct(turn.stats && turn.stats.mean, 2) },
          { label: "95% 分位", value: pct(turn.stats && turn.stats.p95, 2) },
        ]),
    }));
  }
  if (cost) {
    plates.push(plate({
      id: "plate-cost",
      title: "逐日交易成本",
      note: "手续费加印花税。换手冲高的那些天，成本会一起冲高——这两条线的形状往往高度相似。",
      meta: `${cost.total} 个交易日`,
      body: lineSVG(cost.values, { labels: cost.dates, color: "var(--ochre)" }),
    }));
  }

  return `<section class="act" data-act="execution">
    <h2 class="act-head"><span class="act-no">四</span>执行：下单到成交</h2>
    <p class="act-lede">信号再好，下单成交不了也只是纸面收益。这三个数描述「真实成交」的质量。</p>
    ${flag ? `<p class="flag">${esc(flag)}</p>` : ""}
    <div class="triple">
      <div><dt>${anno(num(ffr, 2), "ffr")}<span>成交率</span></dt></div>
      <div><dt>${anno(num(pa, 4), "pa")}<span>价格优势</span></dt></div>
      <div><dt>${anno(num(pos, 2), "pos")}<span>胜率</span></dt></div>
    </div>
    ${plates.join("")}
  </section>`;
}

/* --- 4. outcome: did it make money --- */
function actOutcome(run, x) {
  const m = run.all_metrics || {};
  const annC = m["1day.excess_return_with_cost.annualized_return"];
  const annN = m["1day.excess_return_without_cost.annualized_return"];
  const ir = m["1day.excess_return_with_cost.information_ratio"];
  const dd = m["1day.excess_return_with_cost.max_drawdown"];
  const ret = x.return_series, bench = x.bench_series;
  const risk = x.risk_table || [];

  const drag = (annN != null && annC != null) ? annN - annC : null;

  const plates = [];
  if (ret) {
    plates.push(plate({
      id: "plate-return",
      title: "持仓收益 vs 基准，逐日",
      note: "两条线各自是当天的涨跌，共用一根纵轴。超额不是「赚得多」，而是「比基准多」——看两条线的差。",
      meta: `${ret.total} 个交易日`,
      body: `<div class="curve-key">
          <span><i style="background:var(--indigo)"></i>持仓收益</span>
          <span><i style="background:var(--ochre)"></i>基准收益</span>
        </div>` +
        linePair(ret.values, bench ? bench.values : [], { aName: "持仓", bName: "基准" }),
    }));
  }

  /* risk table as a real table, with term buttons on the stat names */
  const riskTable = risk.length ? (() => {
    const groups = [...new Set(risk.map(r => r.group))];
    const stats = [...new Set(risk.map(r => r.stat))];
    const GL = { mean: "日均超额", std: "标准差 · 波动", annualized_return: "年化超额收益",
      information_ratio: "IR · 信息比率", max_drawdown: "最大回撤" };
    const cellOf = (g, s) => risk.find(r => r.group === g && r.stat === s);
    return `<table class="risk">
      <thead><tr><th></th>${stats.map(s => `<th>${esc(GL[s] || s)}</th>`).join("")}</tr></thead>
      <tbody>${groups.map(g =>
        `<tr><th>${esc(g === "excess_return_with_cost" ? "含成本" : "无成本")}</th>` +
        stats.map(s => {
          const c = cellOf(g, s);
          return `<td>${c ? anno(num(c.value), s) : "—"}</td>`;
        }).join("") + `</tr>`).join("")}</tbody></table>`;
  })() : "";

  return `<section class="act" data-act="outcome">
    <h2 class="act-head"><span class="act-no">五</span>结果：赚没赚到，稳不稳</h2>
    <p class="act-lede">这部分是最终账本。收益、风险、成本损耗一起看，单看任何一个都会误判。</p>
    <div class="ledger">
      <div class="ledger-main">
        <dt>含成本年化超额</dt>
        <dd>${anno(pct(annC), "annualized_return")}</dd>
        <p>${drag == null ? "" : `其中 ${signPct(-drag)} 被成本吃掉（无成本 ${pct(annN)}）。`}</p>
      </div>
      <div><dt>${anno(num(ir, 2), "information_ratio")}<span>IR</span></dt></div>
      <div><dt>${anno(pct(dd), "max_drawdown")}<span>最大回撤</span></dt></div>
    </div>
    ${riskTable ? `<div class="sub-head"><h3>风险拆解</h3>
      <p>同一组收益，在「不扣成本」和「扣成本」两种口径下的五个统计量。点数字看算法。</p></div>
      <div class="risk-wrap">${riskTable}</div>` : ""}
    ${plates.join("")}
  </section>`;
}

/* --- 5. training: overfitting check --- */
function actTraining(run, x) {
  const c = x.training_curve;
  if (!c) return "";
  const tr = (c["l2.train"] || []).map(d => d.value);
  const va = (c["l2.valid"] || []).map(d => d.value);
  const gap = (tr.length && va.length) ? va[va.length - 1] - tr[tr.length - 1] : null;
  const vaMin = va.length ? Math.min(...va) : null;
  const rising = va.length > 5 && va[va.length - 1] > vaMin;

  return `<section class="act" data-act="training">
    <h2 class="act-head"><span class="act-no">六</span>训练：有没有学过头</h2>
    <p class="act-lede">训练损失一定下降，所以它单独没有意义。要和验证损失比：
      验证损失停下而训练损失继续走，就是过拟合开始的地方。</p>
    ${plate({
      id: "plate-curve",
      title: "训练与验证损失，逐轮",
      note: `两条曲线共用同一纵轴，共训练 ${c.rounds} 轮。`,
      meta: `${c.rounds} 轮`,
      body: `<div class="curve-key">
          <span><i style="background:var(--indigo)"></i>训练集 l2 ${anno(num(tr[tr.length - 1]), "l2.train")}</span>
          <span><i style="background:var(--ochre)"></i>验证集 l2 ${anno(num(va[va.length - 1]), "l2.valid")}</span>
        </div>` +
        linePair(tr, va, { aName: "训练", bName: "验证" }) +
        statBand([
          { label: "末轮训练", value: num(tr[tr.length - 1]) },
          { label: "末轮验证", value: num(va[va.length - 1]) },
          { label: "末轮差距", value: num(gap) },
          { label: "验证最低", value: num(vaMin) },
        ]),
    })}
    <p class="verdict-line">${rising
      ? `验证损失已从最低点 ${num(vaMin)} 回升到 ${num(va[va.length - 1])}，后段的训练只是在拟合噪声——早停更早一点会更干净。`
      : `验证损失仍在最低点附近，没有明显的过拟合迹象。`}</p>
  </section>`;
}

/* 六 = 官方图表（仅当这个 run 生成过图表时才有） */
function actFigures(run, x) {
  return `<section class="act" data-act="figures">
    <h2 class="act-head"><span class="act-no">七</span>官方图表</h2>
    <p class="act-lede">qlib 用自家 report 接口生成的图。和上面那些序列图是同一份数据，
      只是换了更专业的视角——分组收益、分层收益、月度稳定性都在这里。</p>
    <div class="figures" data-run="${esc(run.run_id)}"></div>
  </section>`;
}

/* --- 7. raw record, grouped and explained --- */
function actRecord(run) {
  const m = run.all_metrics || {};
  const p = run.all_params || {};
  const t = run.tags || {};
  const a = run.artifacts || [];

  const metricRows = Object.keys(m).sort().map(k => {
    const g = glossaryFor(k);
    const label = g ? g.name : k;
    const expl = g ? `<span class="row-hint">${esc(g.what)}</span>` : `<span class="row-hint muted">未收录解释</span>`;
    return `<tr><td class="row-key"><code>${esc(k)}</code>${label !== k ? `<span class="row-label">${esc(label)}</span>` : ""}</td>
      <td class="row-val">${num(m[k], 6)}</td><td class="row-expl">${expl}</td></tr>`;
  }).join("");

  const paramRows = Object.keys(p).sort().map(k => {
    const short = k.split(".").pop();
    const g = glossaryFor(k) || glossaryFor(short);
    const label = g ? g.name : "";
    return `<tr><td class="row-key"><code>${esc(k)}</code>${label ? `<span class="row-label">${esc(label)}</span>` : ""}</td>
      <td class="row-val">${esc(String(p[k]).slice(0, 120))}${String(p[k]).length > 120 ? "…" : ""}</td>
      <td class="row-expl">${g ? `<span class="row-hint">${esc(g.what)}</span>` : ""}</td></tr>`;
  }).join("");

  const artRows = a.map(v => {
    const depth = v.path.split("/").length - 1;
    const name = v.path.split("/").pop();
    return `<li style="--d:${depth}" class="${v.is_dir ? "dir" : ""}">
      <span class="a-name">${esc(name)}</span><span class="a-size">${esc(bytes(v.size))}</span></li>`;
  }).join("");

  return `<section class="act" data-act="record">
    <h2 class="act-head"><span class="act-no">八</span>原始记录</h2>
    <p class="act-lede">平台的价值在于把 mlflow 的原始键翻译成人话。左列是原始键，中间是值，右边是它的含义。</p>

    <div class="sub-head"><h3>指标 <span class="n">${Object.keys(m).length}</span></h3></div>
    <table class="record"><tbody>${metricRows}</tbody></table>

    <div class="sub-head"><h3>参数 <span class="n">${Object.keys(p).length}</span></h3></div>
    <table class="record"><tbody>${paramRows}</tbody></table>

    <div class="sub-head"><h3>标签 <span class="n">${Object.keys(t).length}</span></h3></div>
    <dl class="spec tight">${Object.keys(t).sort().map(k =>
      `<dt>${esc(k)}</dt><dd>${esc(t[k])}</dd>`).join("")}</dl>

    <div class="sub-head"><h3>产物 <span class="n">${a.length}</span></h3>
      <p>run 目录下的文件。renders/ 是图表，pkl 是逐日数据。</p></div>
    <ul class="tree">${artRows}</ul>
  </section>`;
}

const bytes = n => {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 ** 2).toFixed(1)} MB`;
};

/* --- 8. provenance: git state at run time --- */
function actProvenance(run, x) {
  if (!x.code_status && !x.code_diff) return "";
  return `<section class="act" data-act="provenance">
    <h2 class="act-head"><span class="act-no">九</span>出处：跑的是哪一版代码</h2>
    <p class="act-lede">实验可复现的前提是知道当时的工作区状态。下面两块是运行时自动记下的。</p>
    ${x.code_status ? `<details class="fold"><summary>git status</summary><pre>${esc(x.code_status)}</pre></details>` : ""}
    ${x.code_diff ? `<details class="fold"><summary>未提交改动 diff</summary><pre>${esc(x.code_diff)}</pre></details>` : ""}
  </section>`;
}

/* Line-by-line YAML diff for the two-run case. Uses a plain LCS so the
   result is a real alignment (both sides keep their line numbers) rather
   than a naive "which lines moved" guess. */
function yamlPair(a, b) {
  const al = a.text.split("\n"), bl = b.text.split("\n");
  const N = al.length, M = bl.length;
  // cap the table: 130-line configs are fine, pathological files fall back
  if (N * M > 400000) {
    return `<pre class="yaml">${yamlHTML(a.text)}</pre><pre class="yaml">${yamlHTML(b.text)}</pre>`;
  }
  const dp = Array.from({ length: N + 1 }, () => new Uint16Array(M + 1));
  for (let i = N - 1; i >= 0; i--) {
    for (let j = M - 1; j >= 0; j--) {
      dp[i][j] = al[i] === bl[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const rows = [];
  let i = 0, j = 0;
  while (i < N && j < M) {
    if (al[i] === bl[j]) { rows.push(["same", al[i], al[i]]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { rows.push(["del", al[i], null]); i++; }
    else { rows.push(["ins", null, bl[j]]); j++; }
  }
  while (i < N) rows.push(["del", al[i++], null]);
  while (j < M) rows.push(["ins", null, bl[j++]]);

  const body = rows.map(([kind, left, right]) => {
    const mark = kind === "same" ? " " : kind === "del" ? "−" : "+";
    return `<div class="yd yd-${kind}">
      <span class="yd-mark">${mark}</span>
      <span class="yd-a">${left == null ? "" : yamlHTML(left)}</span>
      <span class="yd-b">${right == null ? "" : yamlHTML(right)}</span>
    </div>`;
  }).join("");

  const changed = rows.filter(r => r[0] !== "same").length;
  return `<div class="yd-head">
      <span class="slot-tag indigo">A</span>${esc(a.path)}
      <span class="slot-tag ochre">B</span>${esc(b.path)}
      <span class="yd-n">${changed} 行不同 · 相同 ${N - rows.filter(r => r[0] === "del").length} 行</span>
    </div>
    <div class="yd-wrap"><pre class="yd-doc">${body}</pre></div>`;
}

/* ═══════════════════════════════════════════════════════════════════
   6. qlib Plotly renders — into the SAME plate component.
   ═══════════════════════════════════════════════════════════════════ */
const RENDER_GROUPS = [
  { stem: "report", title: "净值与回撤", note: "qlib 官方回测报告：累计净值、回撤区间和持仓概况。" },
  { stem: "score_ic", title: "IC 累积", note: "逐日 IC 的累计值，走向越直、越向上越好。" },
  { stem: "model_performance", title: "模型表现", n: 7,
    note: "七张诊断图：分组收益、预测 IC、预测自相关、预测换手、分月稳定性、分层收益、尾部对比。",
    subs: ["分组收益", "预测 IC", "预测自相关", "预测换手", "分月稳定性", "分层收益", "尾部对比"] },
  { stem: "risk_analysis", title: "风险拆解图", n: 5,
    note: "超额曲线、回撤、日度风险、换手率、成本，五张连起来看。",
    subs: ["超额曲线", "回撤", "日度风险", "换手率", "成本"] },
];

/* Ask the server which renders actually exist for this run, and emit one
   plate per figure. Missing files are simply skipped.

   Plotly pages are heavy (each pulls ~3MB), so a plate starts collapsed:
   the caption and reading note are always visible, the figure mounts on
   demand. That also matches the reading rhythm — one figure examined at a
   time instead of fourteen firing at once. */
function renderPlates(run) {
  const have = new Set(run.renders || []);
  if (!have.size) return "";
  const out = [];
  for (const g of RENDER_GROUPS) {
    const n = g.n || 1;
    const files = [];
    for (let i = 0; i < n; i++) {
      const name = g.stem + (n > 1 ? `_${i}` : "") + ".html";
      if (have.has(name)) files.push({ name, sub: (g.subs || [])[i] || "" });
    }
    if (!files.length) continue;
    out.push(`<div class="plate-run-group">
      <h3 class="group-cap">${esc(g.title)}<span class="group-n">${files.length}</span></h3>
      <p class="group-note">${esc(g.note)}</p>
      ${files.map((f, i) => plate({
        id: `render-${g.stem}-${i}`,
        title: f.sub || g.title,
        note: i === 0 ? g.note : "",
        meta: f.name,
        wide: true,
        body: `<div class="plot" data-src="/renders/${encodeURIComponent(run.run_id)}/${f.name}">
          <p class="plot-wait">图表载入中…</p>
        </div>`,
      })).join("")}
    </div>`);
  }
  return out.join("");
}

/* Mount one Plotly figure, sizing it to its content. */
function mountOne(box) {
  if (box.dataset.mounted) return;
  box.dataset.mounted = "1";
  box.innerHTML = "";
  const f = document.createElement("iframe");
  f.title = "qlib 图表";
  f.src = box.dataset.src;
  f.addEventListener("load", () => {
    try {
      const doc = f.contentDocument;
      const plot = doc.querySelector(".plotly-graph-div");
      const h = plot ? plot.getBoundingClientRect().height : doc.body.scrollHeight;
      if (h > 200) f.style.height = Math.ceil(h + 24) + "px";
    } catch (e) { /* cross-origin: keep CSS height */ }
  });
  box.appendChild(f);
}

/* Figures appear on their own — no click, no "expand" button.
   They mount in document order a few at a time: the first figures are ready
   as soon as the reader reaches them, and the rest fill in while the page
   idles. Deliberately not IntersectionObserver-based: hidden or background
   tabs may never fire it, leaving every figure stuck on "loading". */
let plotQueue = [];

function mountPending(n) {
  for (let i = 0; i < n && plotQueue.length; i++) {
    const box = plotQueue.shift();
    if (box.isConnected) mountOne(box);
  }
  if (!plotQueue.length) return;
  const next = () => mountPending(4);
  if ("requestIdleCallback" in window) requestIdleCallback(next, { timeout: 400 });
  else setTimeout(next, 120);
}

function observePlots() {
  plotQueue = [...document.querySelectorAll(".plot[data-src]")];
  if (!plotQueue.length) return;
  mountPending(2);
}

/* ═══════════════════════════════════════════════════════════════════
   7. Outline + term sheet interactions
   ═══════════════════════════════════════════════════════════════════ */

const ACT_LABEL = {
  id: "身份", repeat: "同配置运行", setting: "实验设置", config: "配置原文",
  params: "配置差异", yaml: "配置原文", runs: "该配置的运行",
  signal: "信号", execution: "执行",
  outcome: "结果", training: "训练", figures: "官方图表",
  record: "原始记录", provenance: "出处",
};

function renderOutline() {
  // 只列当前可见容器的章节：隐藏页面的章节不该出现在目录里。
  // 容器不能同时持有同一个 id，否则 getElementById 会命中隐藏的那个。
  const panes = ["specimen", "comparison", "config"];
  const active = state.configPath ? "config" : state.compare ? "comparison" : "specimen";
  const scope = el(active);
  for (const p of panes) {
    if (p !== active) el(p).querySelectorAll(".act[id]").forEach(a => a.removeAttribute("id"));
  }
  const acts = [...scope.querySelectorAll(".act")]
    .map(a => a.dataset.act)
    .filter(Boolean);
  el("outline").innerHTML = acts.map(a =>
    `<li><a href="#act-${a}" data-target="${a}">${esc(ACT_LABEL[a] || a)}</a></li>`
  ).join("");
  scope.querySelectorAll(".act").forEach(a => { if (a.dataset.act) a.id = `act-${a.dataset.act}`; });
  el("outline").querySelectorAll("a").forEach(a => {
    a.addEventListener("click", e => {
      e.preventDefault();
      const t = document.getElementById(`act-${a.dataset.target}`);
      if (t) window.scrollTo({ top: t.getBoundingClientRect().top + window.scrollY - 16, behavior: "smooth" });
    });
  });
}

/* Term buttons: one open sheet at a time, anchored under the button. */
document.addEventListener("click", e => {
  const back = e.target.closest("#pickback");
  if (back) {
    exitCompare();
    renderConfigs();
    renderPickBar();   // 由选择状态决定显隐，不能硬写成可见
    return;
  }
  const drop = e.target.closest(".pick-slot [data-drop]");
  if (drop) { togglePick(drop.dataset.drop); return; }
  const sw = e.target.closest(".repeat .linkish");
  if (sw) {
    selectRun(sw.dataset.run, state.currentConfig);
    return;
  }
  const btn = e.target.closest(".has-term");
  const close = e.target.closest(".doc-close");
  if (close) {
    const doc = close.closest(".sheet-doc");
    const host = doc && doc.closest(".term-host");
    if (host) { host._btn.setAttribute("aria-expanded", "false"); host.remove(); }
    return;
  }
  document.querySelectorAll(".term-host").forEach(h => {
    if (h._btn) h._btn.setAttribute("aria-expanded", "false");
    h.remove();
  });
  if (!btn) return;
  const g = glossaryFor(btn.dataset.term);
  if (!g) return;

  const host = document.createElement("div");
  host.className = "term-host";
  host.innerHTML = termSheet(btn.dataset.term);
  document.body.appendChild(host);
  host._btn = btn;
  btn.setAttribute("aria-expanded", "true");

  const r = btn.getBoundingClientRect();
  const hw = host.offsetWidth;
  let left = r.left + window.scrollX;
  left = Math.min(left, window.scrollX + document.documentElement.clientWidth - hw - 16);
  left = Math.max(left, window.scrollX + 12);
  host.style.left = left + "px";
  host.style.top = (r.bottom + window.scrollY + 8) + "px";
});

document.addEventListener("keydown", e => {
  if (e.key === "Escape" && state.compare) {
    // 对照页里按 Esc 退回单 run 视图，勾选保留
    const back = document.getElementById("pickback");
    if (back) back.click();
    return;
  }
  if (e.key !== "Escape") return;
  document.querySelectorAll(".term-host").forEach(h => {
    if (h._btn) h._btn.setAttribute("aria-expanded", "false");
    h.remove();
  });
});

/* ═══════════════════════════════════════════════════════════════════
   8. Boot
   ═══════════════════════════════════════════════════════════════════ */

boot();
