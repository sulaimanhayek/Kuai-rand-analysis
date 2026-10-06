---
toc: false
---

```js
import {pct, pctCI, ci, share, secs, int, pp, num, pval, VARIANT, VERDICT} from "./components/format.js";
const overview = FileAttachment("data/overview.json").json();
const decision = FileAttachment("data/decision.json").json();
const metrics = FileAttachment("data/metrics.json").json();
const experiment = FileAttachment("data/experiment.json").json();
```

```js
const mwt = overview.headline.find((d) => d.metric === "mwt");
const decomp = (q) => metrics.decomposition.find((d) => d.cells === "category" && d.quantity === q);
const within = decomp("within_rel");
const mixShare = decomp("mix_share_of_gap");
const maxShare = (scenario) => d3.extent(decision.max_share.filter((d) => d.scenario === scenario), (d) => d.max_share);
const [pessLo, pessHi] = maxShare("pessimistic");
const [optLo, optHi] = maxShare("optimistic");
const gems = decision.hidden_gems.summary;
const shipShare = d3.min(decision.decision_summary, (d) => d.max_share_ship);
const testShare = d3.max(decision.decision_summary, (d) => d.max_share_ab_test);
const input = (metric, prefix) => decision.inputs.find((d) => d.metric === metric && d.quantity.startsWith(prefix));
const lift = input("mwt", "Targeting lift");
const baseline = input("mwt", "Average recommended slot");
const tailRec = input("mwt", "Tail, recommended");
const gate = decision.gate.find((d) => d.default);
const chosenC = decision.variant_c.filter((d) => d.chosen).map((d) => d.category);
const cSummary = decision.variant_c_summary;
const breadth = (variant, s) => decision.breadth.find((d) => d.variant === variant && d.share === s);
const largest = d3.max(decision.shares);
const carry60 = metrics.carryover.estimates.find((d) => d.metric === "next_mwt" && d.window_s === 60);
const recTail = metrics.selection_bias.find((d) => d.delivery === "recommended" && d.fifth === 1);
```

# Should the feed boost under-distributed videos?

<p class="lede">KuaiRand-Pure is a public log from a short-video app in which ${int(overview.random_impressions)} feed slots showed a uniformly random video instead of the recommended one. Random delivery prices what a video is worth when the recommender is not choosing who sees it, which is what a distribution boost buys.</p>

<div class="card callout">
  <div class="eyebrow">Recommendation</div>
  <div class="headline">Ship a boost at ${share(shipShare, 0)} of feed slots. Test larger shares in a real experiment first.</div>
  <p>A tail video (bottom fifth by past distribution) earns ${secs(mwt.treatment)} of meaningful watch time per impression under random delivery, against ${secs(mwt.control)} for a head video (top fifth): <span class="num">${pctCI(mwt.rel, mwt.rel_lo, mwt.rel_hi)}</span>.</p>
  <p>At ${share(shipShare, 0)} of slots, every variant stays inside all three guardrails even if boosted videos get no help from targeting. Up to ${share(testShare, 0)} they pass only if the recommender's targeting carries over to boosted videos (a ${num(lift.estimate, 2)}× lift when it chose to show tail videos). This log cannot show that, so it needs an A/B test.</p>
</div>

<div class="grid grid-cols-4">
  <div class="card kpi">
    <div class="label">Tail vs head, meaningful watch time</div>
    <div class="value">${pct(mwt.rel)}</div>
    <div class="ci">${ci(mwt.rel_lo, mwt.rel_hi)}</div>
    <div class="foot">Per impression, random delivery, ${int(mwt.n)} impressions.</div>
  </div>
  <div class="card kpi">
    <div class="label">Gap within the same category</div>
    <div class="value">${pct(within.estimate)}</div>
    <div class="ci">${ci(within.lo, within.hi)}</div>
    <div class="foot">Category mix explains ${share(mixShare.estimate, 0)} ${ci(mixShare.lo, mixShare.hi, 0)} of the gap.</div>
  </div>
  <div class="card kpi">
    <div class="label">Largest share that passes every guardrail</div>
    <div class="value">${share(pessLo)} to ${share(pessHi)}</div>
    <div class="ci">across variants, no targeting help</div>
    <div class="foot">${share(optLo, 0)} to ${share(optHi, 0)} if targeting carries over.</div>
  </div>
  <div class="card kpi">
    <div class="label">Tail videos above the head median</div>
    <div class="value">${share(gems.tail.share_posterior_above_bar)}</div>
    <div class="ci">head videos: ${share(gems.head.share_posterior_above_bar)}</div>
    <div class="foot">Per-video MWT after shrinkage, against a bar of ${secs(decision.hidden_gems.bar)}.</div>
  </div>
</div>

## Three ways to boost

Each variant moves a share of feed slots from the average recommended video (${secs(baseline.estimate)} of meaningful watch time) to tail videos.

<div class="grid grid-cols-3">
  <div class="card">
    <h2>A. Blanket boost</h2>
    <p>Serve any tail video in the boosted slots.</p>
  </div>
  <div class="card">
    <h2>B. Gated boost</h2>
    <p>Give every tail video <i>k</i> test impressions and keep boosting only those that clear the ${secs(decision.hidden_gems.bar)} bar. At <i>k</i> = ${gate.k}, ${share(gate.pass_rate)} pass. ${share(gate.precision, 0)} of those stay above the bar on later impressions, and they include ${share(gate.recall, 0)} of the tail videos that do.</p>
  </div>
  <div class="card">
    <h2>C. Content-aware boost</h2>
    <p>Boost only categories where tail videos matched the average random slot (${secs(cSummary.threshold_first_half_avg_random_mwt)}) in the first half of the window: ${chosenC.join(" and ")}, ${share(cSummary.tail_video_share)} of tail videos.</p>
  </div>
</div>

## How large can the boost be?

The largest share of feed slots each variant can take while meaningful watch time falls by at most ${share(-decision.thresholds.mwt_rel_min, 0)}, the early-skip rate rises by at most ${num(100 * decision.thresholds.skip_pp_max, 0)} point and hates rise by at most ${share(decision.thresholds.hate_rel_max, 0)}. Grey assumes boosted videos perform as they do under random delivery; blue assumes they keep the targeting lift the recommender gets for tail videos.

```js
const order = decision.max_share.map((d) => d.variant).filter((v, i, a) => a.indexOf(v) === i);
const scenarioStyle = {pessimistic: {color: "var(--fg-muted)", dy: -7}, optimistic: {color: "var(--accent)", dy: 7}};
display(html`<div class="small" style="display:flex;gap:1.25rem;flex-wrap:wrap;margin-bottom:.25rem">
  <span><span style="color:var(--fg-muted)">●</span> No targeting help</span>
  <span><span style="color:var(--accent)">●</span> Targeting carries over</span>
  <span>Lines: 95% bootstrap interval. Dashed: shares evaluated below.</span>
</div>`);
display(resize((width) => Plot.plot({
  width,
  height: 300,
  marginLeft: width < 560 ? 110 : 130,
  x: {type: "log", domain: [0.008, 0.8], ticks: [0.01, 0.02, 0.05, 0.1, 0.2, 0.5], tickFormat: (d) => share(d, 0), label: "Share of feed slots", grid: true},
  y: {domain: order, tickFormat: (v) => VARIANT[v], label: null},
  marks: [
    Plot.ruleX(decision.shares, {stroke: "var(--fg-faint)", strokeDasharray: "3,3"}),
    ...Object.entries(scenarioStyle).flatMap(([scenario, {color, dy}]) => {
      const rows = decision.max_share.filter((d) => d.scenario === scenario);
      return [
        Plot.ruleY(rows, {y: "variant", x1: "lo", x2: "hi", stroke: color, strokeWidth: 2, dy}),
        Plot.dot(rows, {y: "variant", x: "max_share", fill: color, r: 4.5, dy,
          tip: true, title: (d) => `${VARIANT[d.variant]}, ${scenario}\n${share(d.max_share)} ${ci(d.lo, d.hi)}`})
      ];
    })
  ]
})));
```

## Guardrails at each share

```js
const pickShare = view(Inputs.radio(decision.shares, {label: "Share of slots", value: largest, format: (d) => share(d, 0)}));
const pickScenario = view(Inputs.radio(["pessimistic", "optimistic"], {label: "Boosted videos", value: "pessimistic",
  format: (d) => (d === "pessimistic" ? "No targeting help" : "Targeting carries over")}));
```

```js
const T = decision.thresholds;
const cols = [
  {metric: "mwt", head: "Meaningful watch time", rule: `at least ${pct(T.mwt_rel_min, 0)}`, fmt: (d) => `${pct(d.estimate)} ${ci(d.lo, d.hi)}`},
  {metric: "early_skip", head: "Early-skip rate", rule: `at most ${pp(T.skip_pp_max, 0)}`, fmt: (d) => `${pp(d.estimate)} [${num(100 * d.lo, 1)}, ${num(100 * d.hi, 1)}]`},
  {metric: "hated", head: "Hates", rule: `at most ${pct(T.hate_rel_max, 0)}`, fmt: (d) => `${pct(d.estimate)} ${ci(d.lo, d.hi)}`}
];
const proj = decision.projection.filter((d) => d.share === pickShare && d.scenario === pickScenario);
const verdicts = decision.decision.filter((d) => d.share === pickShare);
display(html`<div class="card scroll"><table class="data">
  <thead><tr><th>Variant</th>${cols.map((c) => html`<th class="r">${c.head}<br><span class="muted">${c.rule}</span></th>`)}<th>Verdict at ${share(pickShare, 0)}</th></tr></thead>
  <tbody>${order.map((v) => {
    const verdict = verdicts.find((d) => d.variant === v);
    const style = VERDICT[verdict.verdict];
    return html`<tr>
      <td>${VARIANT[v]}</td>
      ${cols.map((c) => {
        const d = proj.find((p) => p.variant === v && p.metric === c.metric);
        return html`<td class="r num ${d.passes ? "good" : "bad"}">${c.fmt(d)}${d.robust ? "" : html`<sup class="muted">*</sup>`}</td>`;
      })}
      <td><span class="chip ${style.cls}">${style.label}</span>${verdict.robust ? "" : html`<sup class="muted">*</sup>`}</td>
    </tr>`;
  })}</tbody>
</table>
<p class="small">Feed-level change at the chosen share, with 95% bootstrap intervals. Green passes the guardrail, red fails it. <sup>*</sup>The interval crosses the threshold, so the call is not settled by this data. The verdict uses both assumptions: ship if every guardrail passes without targeting help, A/B test if they pass only with it.</p>
</div>`);
```

## What the boost buys

The point of a boost is breadth. These are mechanical projections for the blanket boost, among pool videos.

```js
const b0 = breadth("baseline", 0);
const bA = breadth("A", largest);
```

<div class="grid grid-cols-3">
  <div class="card kpi">
    <div class="label">Tail share of impressions</div>
    <div class="value">${share(b0.tail_share)} → ${share(bA.tail_share)}</div>
    <div class="foot">Today vs a ${share(largest, 0)} boost.</div>
  </div>
  <div class="card kpi">
    <div class="label">Effective number of videos</div>
    <div class="value">${int(b0.effective_videos)} → ${int(bA.effective_videos)}</div>
    <div class="foot">Exponential of the entropy of impressions.</div>
  </div>
  <div class="card kpi">
    <div class="label">Impressions going to the top 20% of videos</div>
    <div class="value">${share(b0.top20_share)} → ${share(bA.top20_share)}</div>
    <div class="foot">Concentration falls, slowly.</div>
  </div>
</div>

## What would change this

- **Targeting.** The case for larger shares rests on boosted tail videos keeping the recommender's targeting lift: ${secs(tailRec.estimate)} when it chose to show a tail video, ${num(lift.estimate, 2)} times the random-delivery figure (95% interval ${num(lift.lo, 2)} to ${num(lift.hi, 2)}), from only ${int(recTail.n)} impressions. A real test of a ${share(testShare, 0)} boost settles it.
- **Hates.** The guardrail allows ${pct(decision.thresholds.hate_rel_max, 0)}, but the smallest change in hates this data can detect is ${share(experiment.power.hate.mde_rel, 0)}. The hate guardrail is effectively untested.
- **Time.** The window is ${metrics.time.by_day.length} days. The gap shows no trend (${pval(metrics.time.trend.p)}) and no fatigue across a user's random impressions (${pval(metrics.time.fatigue_test.p)}), but longer-run effects, such as boosted creators posting more, are not in the log.
- **Spillover.** The next video after a tail slot shows no detectable change in watch time (${pctCI(carry60.rel, carry60.rel_lo, carry60.rel_hi)} within 60 seconds), but only ${share(metrics.carryover.feasibility.share_next_within_60s)} of next impressions fall that close.

<p class="small"><a href="./metrics">The tail gap in detail →</a></p>
