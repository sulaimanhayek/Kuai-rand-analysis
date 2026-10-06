---
title: Segments
---

```js
import {pct, pctCI, pp, ppCI, share, int, qval} from "./components/format.js";
const segments = FileAttachment("data/segments.json").json();
```

# Who the gap hits hardest

<p class="lede">The tail gap by segment, each level estimated on random delivery with intervals clustered by user and video. Each segment gets one test of whether the gap is the same at every level, and the ${segments.tests.length} tests are corrected together (Benjamini-Hochberg, q = ${segments.bh_q}).</p>

```js
const metric = view(Inputs.radio(new Map([["Meaningful watch time, relative change", "mwt"], ["Early-skip rate, percentage points", "early_skip"]]), {label: "Metric", value: "mwt"}));
```

```js
const isMwt = metric === "mwt";
const fmt = (d) => (isMwt ? pct(d.rel) : pp(d.abs));
const fmtCI = (d) => (isMwt ? pctCI(d.rel, d.rel_lo, d.rel_hi) : ppCI(d.abs, d.abs_lo, d.abs_hi));
const est = segments.estimates.filter((d) => d.metric === metric).map((d) => ({
  ...d,
  y: isMwt ? d.rel : d.abs, lo: isMwt ? d.rel_lo : d.abs_lo, hi: isMwt ? d.rel_hi : d.abs_hi, overall: isMwt ? d.overall_rel : d.overall_abs
}));
const tests = segments.tests.filter((d) => d.metric === metric);
const bySeg = d3.group(est, (d) => d.segment);
const domain = d3.extent(est.flatMap((d) => [d.lo, d.hi]));
const overall = est[0].overall;
const name = (s) => s.replace(" (LLM)", "");
const varies = tests.filter((t) => t.heterogeneous).sort((a, b) => a.q - b.q);
const steady = tests.filter((t) => !t.heterogeneous);
const level = (seg, lvl) => est.find((d) => d.segment === seg && d.level === lvl);
```

## Where it varies

```js
const worst = (rows) => (isMwt ? d3.least(rows, (d) => d.y) : d3.greatest(rows, (d) => d.y));
const best = (rows) => (isMwt ? d3.greatest(rows, (d) => d.y) : d3.least(rows, (d) => d.y));
display(html`<div class="card scroll"><table class="data">
  <thead><tr><th>Segment</th><th>Hit hardest</th><th>Hit least</th><th class="r">Test</th></tr></thead>
  <tbody>${varies.map((t) => {
    const rows = bySeg.get(t.segment);
    const w = worst(rows), b = best(rows);
    return html`<tr>
      <td>${name(t.segment)}</td>
      <td>${w.level} <span class="num bad">${fmt(w)}</span></td>
      <td>${b.level} <span class="num">${fmt(b)}</span></td>
      <td class="r muted">${qval(t.q)}</td>
    </tr>`;
  })}</tbody>
</table>
<p class="small">No evidence it varies by ${steady.map((t) => name(t.segment).toLowerCase()).join(", ")}. Overall gap ${fmt(est[0])}.</p>
</div>`);
```

```js
display(isMwt ? html`<p>The gap widens with video length, from ${pct(level("Duration", "Under 15s").rel)} under 15 seconds to ${pct(level("Duration", "Over 3 min").rel)} over 3 minutes. Users with more pre-period viewing lose more (${pct(level("Pre-period volume", "High").rel)} for the heaviest third vs ${pct(level("Pre-period volume", "Low").rel)} for the lightest), and users with no pre-period history lose least (${pct(level("Pre-period volume", "No pre-period history").rel)}). Content segments compare different tail and head videos within each group, so they describe where the gap sits, not what causes it.</p>`
  : html`<p>Users who rarely skipped before see the largest rise in skips (${pp(level("Pre-period skip rate", "Low").abs)} for the lowest third vs ${pp(level("Pre-period skip rate", "High").abs)} for the highest). Frequent skippers already skip ${share(level("Pre-period skip rate", "High").control)} of head videos, so there is less room to rise. By content, the rise ranges from ${pp(level("Category", "Celebrity").abs)} for celebrity videos to ${pp(level("Category", "Sports").abs)} for sports. Content segments compare different tail and head videos within each group, so they describe where the gap sits, not what causes it.</p>`);
```

```js
function forest(seg) {
  const rows = bySeg.get(seg).toSorted((a, b) => a.order - b.order);
  const t = tests.find((d) => d.segment === seg);
  const labelWidth = Math.max(90, 7 * d3.max(rows, (d) => d.level.length) + 14);
  return html`<div class="card">
    <h2 style="margin-bottom:0.15rem">${name(seg)} ${seg.includes("(LLM)") ? html`<span class="tag">Model-tagged</span>` : ""}</h2>
    <div class="small" style="margin-bottom:0.5rem">${t.heterogeneous ? html`<strong style="color:var(--fg)">Varies</strong>` : "No evidence it varies"}, ${qval(t.q)}</div>
    ${resize((width) => Plot.plot({
      width,
      height: 28 * rows.length + 40,
      style: {fontSize: "12px"},
      marginTop: 8,
      marginLeft: labelWidth,
      marginRight: 12,
      x: {domain, tickFormat: (d) => (isMwt ? pct(d, 0) : pp(d, 0).replace(" pp", "")), label: null, ticks: 5, grid: true},
      y: {domain: rows.map((d) => d.level), label: null},
      marks: [
        Plot.ruleX([overall], {stroke: "var(--fg-faint)", strokeDasharray: "4,3"}),
        Plot.ruleY(rows, {y: "level", x1: "lo", x2: "hi", stroke: "var(--accent)", strokeWidth: 2}),
        Plot.dot(rows, {y: "level", x: "y", fill: "var(--accent)", stroke: "var(--card)", r: 4.5,
          tip: true, title: (d) => `${d.level}: ${fmtCI(d)}\n${int(d.n)} impressions, ${int(d.n_users)} users, ${int(d.n_videos)} videos`})
      ]
    }))}
  </div>`;
}
const userSegs = tests.filter((t) => t.side === "user").map((t) => t.segment);
const contentSegs = tests.filter((t) => t.side === "content").map((t) => t.segment);
```

## Who is watching

<p class="small">${metric === "mwt" ? "Tail vs head, relative change" : "Tail vs head, percentage points"}, with 95% intervals. Dashed line: the overall gap. All charts share one scale.</p>

```js
display(html`<div class="grid grid-cols-2 forest">${userSegs.map(forest)}</div>`);
```

## What is shown

```js
display(html`<div class="grid grid-cols-2 forest">${contentSegs.map(forest)}</div>`);
```

<p class="small">Model-tagged segments come from video captions labelled by a language model and checked against the platform's categories and a blind audit. See <a href="./methods#content-tags">methods</a>.</p>

<p class="small"><a href="./methods">How it was done →</a></p>
