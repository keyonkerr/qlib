/* Experiment dashboard front end.
 *
 * Two data sources, both server-side:
 *   /api/runs          -> the rail   (every run + its summary numbers)
 *   /api/runs/{id}     -> the detail (verdict, notes, render list)
 *
 * No client-side metric maths: anything derived (deltas, annualised ICIR,
 * cost drag) is computed in dashboard/metrics.py so the table and the prose
 * cannot disagree.
 */

const pct = v => (v == null ? "—" : (v * 100).toFixed(2) + "%");
const pp = v => (v == null ? "—" : (v >= 0 ? "+" : "") + (v * 100).toFixed(1) + "pp");
const f4 = v => (v == null ? "—" : v.toFixed(4));
const f2 = v => (v == null ? "—" : v.toFixed(2));
const ddmm = ms => {
  if (!ms) return "—";
  const d = new Date(ms);
  return String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
};

const CHART_TABS = [
  { key: "report", label: "净值与回撤", stem: "report" },
  { key: "score_ic", label: "IC 累积", stem: "score_ic" },
  { key: "model_performance", label: "模型表现", stem: "model_performance", n: 7 },
  { key: "risk_analysis", label: "风险拆解", stem: "risk_analysis", n: 5 },
];

const SUB_LABEL = {
  model_performance: ["分组收益", "预测 IC", "预测自相关", "预测换手", "分月稳定性", "分层收益", "尾部对比"],
  risk_analysis: ["超额曲线", "回撤", "日度风险", "换手率", "成本"],
};

const state = {
  runs: [],
  current: null,
  detail: null,
  chart: "report",
  sub: 0,
  query: "",
  closed: new Set(),
};

const el = id => document.getElementById(id);

/* ───────────── rail ───────────── */

function renderRail() {
  const body = el("railBody");
  const q = state.query.trim().toLowerCase();
  const pool = state.runs.filter(r =>
    !q || r.name.toLowerCase().includes(q) || r.run_id.toLowerCase().includes(q)
  );

  body.innerHTML = "";

  if (!pool.length) {
    const p = document.createElement("p");
    p.className = "rail-empty";
    p.textContent = state.runs.length
      ? "没有匹配的 run。试着清空搜索条件。"
      : "mlruns 里还没有完成的 run。先跑一次 qlib.cli.run。";
    body.appendChild(p);
    return;
  }

  const exps = [...new Set(state.runs.map(r => r.experiment))];

  for (const exp of exps) {
    const items = pool.filter(r => r.experiment === exp);
    if (!items.length) continue;

    const section = document.createElement("section");
    section.className = "group";
    section.dataset.open = String(!state.closed.has(exp));

    const head = document.createElement("button");
    head.className = "group-head";
    head.type = "button";
    head.innerHTML =
      `<span class="chev"></span><span class="group-name">${exp}</span>` +
      `<span class="group-count">${items.length}</span>`;
    head.addEventListener("click", () => {
      const open = section.dataset.open === "true";
      section.dataset.open = String(!open);
      open ? state.closed.add(exp) : state.closed.delete(exp);
    });

    const ul = document.createElement("ul");
    ul.className = "run-list";

    for (const r of items) {
      const s = r.summary;
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.className = "run-item";
      btn.type = "button";
      btn.setAttribute("aria-current", String(state.current && r.run_id === state.current.run_id));
      btn.innerHTML =
        `<div class="run-top">` +
          `<span class="run-name" title="${r.name}">${r.name}</span>` +
          `<span class="run-figs${r.has_renders ? "" : " no-figs"}" ` +
            `title="${r.has_renders ? "图表已生成" : "尚未生成图表"}" ` +
            `aria-label="${r.has_renders ? "有图表" : "无图表"}"></span>` +
          `<span class="run-date">${ddmm(r.start_time)}</span>` +
        `</div>` +
        `<div class="run-metrics">` +
          `<span><em>IC</em> ${f4(s.ic)}</span>` +
          `<span><em>年化</em> ${pct(s.ann_cost)}</span>` +
          `<span><em>IR</em> ${f2(s.ir_cost)}</span>` +
        `</div>`;
      btn.addEventListener("click", () => selectRun(r.run_id));
      li.appendChild(btn);
      ul.appendChild(li);
    }

    section.appendChild(head);
    section.appendChild(ul);
    body.appendChild(section);
  }
}

/* ───────────── detail ───────────── */

async function selectRun(runId) {
  state.current = state.runs.find(r => r.run_id === runId) || null;
  state.chart = "report";
  state.sub = 0;
  renderRail();

  const res = await fetch(`/api/runs/${encodeURIComponent(runId)}`);
  if (!res.ok) {
    el("loadState").hidden = false;
    el("loadState").textContent = `读取 run 失败：${res.status}`;
    return;
  }
  state.detail = await res.json();

  el("loadState").hidden = true;
  el("verdictBlock").hidden = false;
  el("plotBlock").hidden = false;
  el("readingBlock").hidden = false;

  renderHeader();
  renderTabs();
  renderStrip();
  renderFrame();
  renderNotes();
}

function renderHeader() {
  const d = state.detail;
  const run = d.run;
  const s = d.summary;

  el("verdict").textContent = d.verdict;
  el("verdictSub").textContent = d.sub || "";
  el("srcName").textContent = `${run.experiment} / ${run.name}`;
  el("srcRun").textContent = run.run_id;

  const cells = [
    { label: "IC", value: f4(s.ic), delta: s.ic_delta, fmt: v => (v > 0 ? "+" : "") + v.toFixed(4),
      tip: "预测值与未来收益的截面相关系数，逐日计算后取均值" },
    { label: "ICIR", value: f4(s.icir), delta: s.icir_delta, fmt: v => (v > 0 ? "+" : "") + v.toFixed(4),
      tip: "IC 均值 ÷ IC 标准差；qlib 记录的是日均值，年化约 ×√238" },
    { label: "年化超额", value: pct(s.ann_cost), delta: s.ann_delta, fmt: pp,
      tip: "扣除手续费和印花税后的年化超额收益；qlib 用算术累加，不是复利" },
    { label: "IR", value: f2(s.ir_cost), delta: s.ir_delta, fmt: v => (v > 0 ? "+" : "") + v.toFixed(2),
      tip: "信息比率 = 日均超额 ÷ 超额标准差 × √238" },
    { label: "最大回撤", value: pct(s.dd_cost), delta: s.dd_delta, fmt: pp,
      tip: "算术累计收益的回撤，不是净值曲线回撤" },
  ];

  el("stats").innerHTML = cells.map(c => {
    const dcls = c.delta == null ? "" : (c.delta > 0 ? "pos" : "neg");
    const dtxt = c.delta == null ? (s.has_baseline ? "—" : "本组基线") : c.fmt(c.delta);
    return `<div class="stat">
      <dt title="${c.tip}">${c.label}</dt>
      <dd>${c.value}</dd>
      <span class="delta ${dcls}">${dtxt}</span>
    </div>`;
  }).join("");
}

function renderTabs() {
  const has = state.detail && state.detail.run.has_renders;
  el("tabs").innerHTML = "";

  for (const t of CHART_TABS) {
    const b = document.createElement("button");
    b.className = "tab";
    b.type = "button";
    b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", String(state.chart === t.key));
    b.innerHTML = `${t.label}<span class="n">${t.n || 1}</span>`;
    b.addEventListener("click", () => {
      state.chart = t.key;
      state.sub = 0;
      renderTabs();
      renderStrip();
      renderFrame();
    });
    el("tabs").appendChild(b);
  }
  el("tabs").hidden = !has;
}

function renderStrip() {
  const t = CHART_TABS.find(x => x.key === state.chart);
  const n = t.n || 1;
  el("strip").hidden = n <= 1;
  el("strip").innerHTML = "";
  if (n <= 1) return;

  const labels = SUB_LABEL[state.chart] || [];
  for (let i = 0; i < n; i++) {
    const c = document.createElement("button");
    c.className = "chip";
    c.type = "button";
    c.setAttribute("aria-pressed", String(i === state.sub));
    c.textContent = labels[i] || String(i + 1);
    c.addEventListener("click", () => {
      state.sub = i;
      renderStrip();
      renderFrame();
    });
    el("strip").appendChild(c);
  }
}

function renderFrame() {
  const frame = el("frame");
  const run = state.detail && state.detail.run;

  if (!run || !run.has_renders) {
    frame.innerHTML = `<div class="empty">
      <h3>这个 run 还没有图表</h3>
      <p>图由 <code>report_graphs.py</code> 从 pred.pkl 与回测报告生成，写入 run 自己的
         artifacts 目录。指标和回测数字不受影响，仍然可读。</p>
      <p>生成方式：<br><code>python report_graphs.py --run-id ${run ? run.run_id : ""}</code></p>
    </div>`;
    return;
  }

  const t = CHART_TABS.find(x => x.key === state.chart);
  const file = t.stem + (t.n > 1 ? "_" + state.sub : "") + ".html";

  frame.innerHTML = "";
  const f = document.createElement("iframe");
  f.title = `${run.name} 的${t.label}图`;
  f.loading = "lazy";
  f.src = `/renders/${run.run_id}/${file}`;
  f.addEventListener("load", () => {
    try {
      const doc = f.contentDocument;
      const plot = doc.querySelector(".plotly-graph-div");
      const h = plot ? plot.getBoundingClientRect().height : doc.body.scrollHeight;
      if (h > 200) {
        f.style.height = Math.ceil(h + 22) + "px";
        frame.style.height = f.style.height;
      }
    } catch (e) {
      /* cross-origin: keep the CSS height */
    }
  });
  frame.appendChild(f);
}

function renderNotes() {
  const notes = (state.detail && state.detail.notes) || [];
  el("notes").innerHTML = notes.map(n =>
    `<div class="note${n.warn ? " warn" : ""}">
      <h3>${n.title}</h3>
      ${n.paras.map(p => `<p>${p}</p>`).join("")}
    </div>`
  ).join("") || `<div class="note"><h3>待补充</h3><p>这个 run 没有可用的解读规则命中。</p></div>`;
}

/* ───────────── boot ───────────── */

async function boot() {
  let runs;
  try {
    const res = await fetch("/api/runs");
    if (!res.ok) throw new Error(res.status);
    runs = await res.json();
  } catch (e) {
    el("loadState").textContent = `无法连接后端：${e.message}`;
    return;
  }

  state.runs = runs;
  const exps = [...new Set(runs.map(r => r.experiment))];
  el("railMeta").textContent = `${runs.length} runs · ${exps.length} 实验`;

  if (!runs.length) {
    el("loadState").textContent = "mlruns 里还没有完成的 run。";
    renderRail();
    return;
  }

  // Prefer a run that already has charts: that is the one worth looking at.
  const first = runs.find(r => r.has_renders) || runs[0];
  renderRail();
  await selectRun(first.run_id);
}

el("search").addEventListener("input", e => {
  state.query = e.target.value;
  renderRail();
});

boot();
