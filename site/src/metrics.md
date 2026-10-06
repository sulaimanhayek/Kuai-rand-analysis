---
title: The tail gap
---

```js
import {pct, pctCI, ci, share, secs, int, num, pval, value, tone, METRIC, day} from "./components/format.js";
const metrics = FileAttachment("data/metrics.json").json();
const experiment = FileAttachment("data/experiment.json").json();
const overview = FileAttachment("data/overview.json").json();
```

```js
const main = metrics.layer1.main;
const mwt = main.find((d) => d.metric === "mwt");
const defs = new Map(metrics.definitions.map((d) => [d.key, d]));
const T = overview.tier_thresholds;
```

# How much worse does a tail video do?

<p class="lede">Random delivery puts tail and head videos in front of the same users at the same moments, so any difference comes from the videos. Tail means at most ${T.tail_max_pre_impressions} recommended impressions in the ${overview.periods.pre[1] - overview.periods.pre[0] + 1}-day pre-period; head means ${T.head_min_pre_impressions} or more. Every estimate is per impression with a 95% interval clustered by user and by video.</p>

## Every metric

```js
display(html`<div class="card scroll"><table class="data">
  <thead><tr><th>Metric</th><th class="r">Head</th><th class="r">Tail</th><th class="r">Tail vs head [95% CI]</th><th class="r">Impressions</th></tr></thead>
  <tbody>${main.map((d) => {
    const def = defs.get(d.metric);
    return html`<tr>
      <td>${def.label}</td>
      <td class="r">${value(d.control, def.unit, def.scale)}</td>
      <td class="r">${value(d.treatment, def.unit, def.scale)}</td>
      <td class="r num ${tone(d.metric, d.rel_lo, d.rel_hi)}">${pctCI(d.rel, d.rel_lo, d.rel_hi)}</td>
      <td class="r muted">${int(d.n)}</td>
    </tr>`;
  })}</tbody>
</table>
<p class="small">Meaningful watch time counts play seconds when a play lasts at least 3 seconds, capped at ${secs(overview.mwt_cap_s, 1)}. Long-view rate excludes videos with unknown duration. Green and red mark changes whose interval excludes zero.</p>
</div>`);
```

Tail videos lose on every engagement metric except follows, where the interval is too wide to call. Early skips rise ${pct(main.find((d) => d.metric === "early_skip").rel)} and hates ${pct(main.find((d) => d.metric === "hated").rel)}.

## The gap shrinks with past distribution

Videos split into fifths by pre-period recommended impressions. Each point is the fifth's change relative to the head fifth.

```js
const doseMetric = view(Inputs.select(metrics.definitions.map((d) => d.key), {label: "Metric", value: "mwt", format: (k) => defs.get(k).label}));
```

```js
const fifthLabel = (f) => (f === 1 ? "Tail" : f === 5 ? "Head" : `Fifth ${f}`);
const dose = metrics.layer1.dose.filter((d) => d.metric === doseMetric);
display(resize((width) => Plot.plot({
  width,
  height: 300,
  marginLeft: 56,
  x: {domain: [1, 2, 3, 4, 5], tickFormat: fifthLabel, label: "Past distribution", padding: 0.4, type: "point"},
  y: {tickFormat: (d) => pct(d, 0), label: `${defs.get(doseMetric).label}, vs head`, grid: true, nice: true},
  marks: [
    Plot.ruleY([0], {stroke: "var(--fg-faint)"}),
    Plot.line(dose, {x: "fifth", y: "rel_vs_head", stroke: "var(--accent)", strokeWidth: 2}),
    Plot.ruleX(dose.filter((d) => d.fifth < 5), {x: "fifth", y1: "rel_lo", y2: "rel_hi", stroke: "var(--accent)", strokeWidth: 2}),
    Plot.dot(dose, {x: "fifth", y: "rel_vs_head", fill: "var(--accent)", stroke: "var(--card)", r: 5,
      tip: true, title: (d) => `${fifthLabel(d.fifth)}: ${d.fifth === 5 ? "reference" : pctCI(d.rel_vs_head, d.rel_lo, d.rel_hi)}\n${int(d.n_videos)} videos, ${int(d.n)} impressions`})
  ]
})));
```

## The recommender hides most of the gap

```js
const sb = metrics.selection_bias;
const recTail = sb.find((d) => d.delivery === "recommended" && d.fifth === 1);
const randTail = sb.find((d) => d.delivery === "random" && d.fifth === 1);
```

When the recommender chose to show a tail video, it earned ${secs(recTail.mwt)} of meaningful watch time, ${pct(recTail.rel_vs_head)} against a recommended head video. Under random delivery the gap is ${pct(randTail.rel_vs_head)}. Comparing logged recommendations would understate what a boost costs, because the recommender only shows tail videos to users who are likely to watch them. That targeting is also why a boost might do better than random delivery suggests, which is the optimistic case in the memo.

```js
display(html`<div class="small" style="display:flex;gap:1.25rem;flex-wrap:wrap">
  <span><span style="color:var(--fg-muted)">●</span> Recommended delivery</span>
  <span><span style="color:var(--accent)">●</span> Random delivery</span>
</div>`);
display(resize((width) => Plot.plot({
  width,
  height: 300,
  marginLeft: 48,
  x: {domain: [1, 2, 3, 4, 5], tickFormat: fifthLabel, label: "Past distribution", padding: 0.4, type: "point"},
  y: {label: "Meaningful watch time (s)", grid: true, domain: [0, 26]},
  marks: [
    ...[["recommended", "var(--fg-muted)"], ["random", "var(--accent)"]].flatMap(([delivery, color]) => {
      const rows = sb.filter((d) => d.delivery === delivery);
      return [
        Plot.line(rows, {x: "fifth", y: "mwt", stroke: color, strokeWidth: 2}),
        Plot.ruleX(rows, {x: "fifth", y1: "lo", y2: "hi", stroke: color, strokeWidth: 2}),
        Plot.dot(rows, {x: "fifth", y: "mwt", fill: color, stroke: "var(--card)", r: 5,
          tip: true, title: (d) => `${delivery}, ${fifthLabel(d.fifth)}: ${secs(d.mwt)} [${num(d.lo, 2)}, ${num(d.hi, 2)}]\n${int(d.n)} impressions`})
      ];
    })
  ]
})));
```

<p class="small">Recommended tail impressions are rare: ${int(recTail.n)} in the experiment window, so that point is the least precise.</p>

## How much is content mix?

```js
const dec = (cells, q) => metrics.decomposition.find((d) => d.cells === cells && d.quantity === q);
const cellsLabel = {"category": "Category", "category x duration": "Category and duration"};
```

Tail videos lean toward content that does worse in every tier, such as anime and dance, and away from short drama and comedy. Reweighting head videos to the tail's mix splits the gap into a content-mix part and a within-content part. The within-content part is what a content-aware boost cannot avoid.

```js
display(html`<div class="card scroll"><table class="data">
  <thead><tr><th>Matched on</th><th class="r">Total gap</th><th class="r">Content mix</th><th class="r">Within content</th><th class="r">Mix share of the gap</th></tr></thead>
  <tbody>${Object.entries(cellsLabel).map(([cells, label]) => {
    const [raw, mix, within, sh] = ["raw_rel", "mix_rel", "within_rel", "mix_share_of_gap"].map((q) => dec(cells, q));
    return html`<tr>
      <td>${label}</td>
      <td class="r num">${pctCI(raw.estimate, raw.lo, raw.hi)}</td>
      <td class="r num">${pctCI(mix.estimate, mix.lo, mix.hi)}</td>
      <td class="r num">${pctCI(within.estimate, within.lo, within.hi)}</td>
      <td class="r num">${share(sh.estimate)} ${ci(sh.lo, sh.hi)}</td>
    </tr>`;
  })}</tbody>
</table>
<p class="small">Bootstrap intervals, resampling users and videos. Mix and within parts add up to the total gap.</p>
</div>`);
```

```js
const isOther = (d) => d.category === "All other categories";
const mix = metrics.category_mix.toSorted((a, b) => isOther(a) - isOther(b) || b.tail_video_share - b.head_video_share - (a.tail_video_share - a.head_video_share));
display(html`<div class="small" style="display:flex;gap:1.25rem;flex-wrap:wrap">
  <span><span style="color:var(--fg-muted)">●</span> Head videos</span>
  <span><span style="color:var(--accent)">●</span> Tail videos</span>
</div>`);
display(resize((width) => Plot.plot({
  width,
  height: 34 * mix.length + 40,
  marginLeft: width < 560 ? 130 : 160,
  x: {tickFormat: (d) => share(d, 0), label: "Share of videos in the tier", grid: true},
  y: {domain: mix.map((d) => d.category), label: null},
  marks: [
    Plot.link(mix, {y1: "category", y2: "category", x1: "head_video_share", x2: "tail_video_share", stroke: "var(--ghost)", strokeWidth: 3}),
    Plot.dot(mix, {y: "category", x: "head_video_share", fill: "var(--fg-muted)", r: 5}),
    Plot.dot(mix, {y: "category", x: "tail_video_share", fill: "var(--accent)", r: 5,
      tip: true, title: (d) => `${d.category}\nTail: ${share(d.tail_video_share)} of videos, MWT ${secs(d.tail_mwt)}\nHead: ${share(d.head_video_share)} of videos, MWT ${secs(d.head_mwt)}`})
  ]
})));
```

## Stable over the window

```js
const byDay = metrics.time.by_day.map((d) => ({...d, date: day(d.date)}));
display(resize((width) => Plot.plot({
  width,
  height: 260,
  marginLeft: 56,
  x: {type: "utc", label: null, tickFormat: "%b %d"},
  y: {tickFormat: (d) => pct(d, 0), label: "Meaningful watch time, tail vs head", grid: true, domain: [-0.65, -0.25]},
  marks: [
    Plot.ruleY([mwt.rel], {stroke: "var(--fg-faint)", strokeDasharray: "4,3"}),
    Plot.ruleX(byDay, {x: "date", y1: "rel_lo", y2: "rel_hi", stroke: "var(--accent)", strokeOpacity: 0.5, strokeWidth: 2}),
    Plot.dot(byDay, {x: "date", y: "rel", fill: "var(--accent)", r: 4,
      tip: true, title: (d) => `${d3.utcFormat("%b %d")(d.date)}: ${pctCI(d.rel, d.rel_lo, d.rel_hi)}\n${int(d.n)} impressions`})
  ]
})));
```

```js
const fatigue = metrics.time.fatigue;
const halves = metrics.time.halves;
```

The daily gap moves within its noise around the overall ${pct(mwt.rel)} (dashed), with no trend (${num(metrics.time.trend.gap_change_per_day_s, 3)}s per day, ${pval(metrics.time.trend.p)}). It does not grow with exposure either: from a user's first 5 random impressions to beyond their 100th it stays between ${pct(d3.max(fatigue, (d) => d.rel))} and ${pct(d3.min(fatigue, (d) => d.rel))} (${pval(metrics.time.fatigue_test.p)}), and the two halves of the window agree (${halves.map((d) => pct(d.rel)).join(" and ")}, ${pval(metrics.time.halves_test.p)}).

## Spillover to the next video

```js
const feas = metrics.carryover.feasibility;
const carry = metrics.carryover.estimates;
const carryLabel = {next_mwt: "Next video's meaningful watch time", next_skip: "Next video's early-skip rate"};
```

A bad slot could cost more than itself if users disengage from what follows. The logs only cover the ${int(overview.pool_videos)} sampled videos, so the next logged impression is often far away: the median gap is ${int(feas.median_gap_s / 60)} minutes and only ${share(feas.share_next_within_60s)} of next impressions come within 60 seconds. Treat this as exploratory.

```js
display(html`<div class="card scroll"><table class="data">
  <thead><tr><th>After a tail vs head slot</th><th class="r">Next impression within</th><th class="r">Change [95% CI]</th><th class="r">Impressions</th></tr></thead>
  <tbody>${carry.map((d) => html`<tr>
    <td>${carryLabel[d.metric]}</td>
    <td class="r">${d.window_s}s</td>
    <td class="r num ${tone(d.metric, d.rel_lo, d.rel_hi)}">${pctCI(d.rel, d.rel_lo, d.rel_hi)}</td>
    <td class="r muted">${int(d.n)}</td>
  </tr>`)}</tbody>
</table></div>`);
```

No spillover is detectable in either window.

## Robustness

```js
const sens = experiment.sensitivities;
const sensLabel = {
  "primary": "Primary estimate",
  "cap at 180s": "Cap meaningful watch time at 180s",
  "date fixed effects": "Add date fixed effects",
  "user fixed effects": "Add user fixed effects",
  "videos still drawn on or after 20220505": "Only videos still drawn in the last 4 days",
  "videos with known duration": "Only videos with known duration"
};
display(html`<div class="card scroll"><table class="data">
  <thead><tr><th>Specification</th><th class="r">Meaningful watch time, tail vs head</th><th class="r">Impressions</th></tr></thead>
  <tbody>${sens.map((d) => html`<tr>
    <td>${sensLabel[d.variant] ?? d.variant}</td>
    <td class="r num">${pctCI(d.rel, d.rel_lo, d.rel_hi)}</td>
    <td class="r muted">${int(d.n)}</td>
  </tr>`)}</tbody>
</table></div>`);
```

<p class="small"><a href="./experiment">Reading it as an A/B test →</a></p>
