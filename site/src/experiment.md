---
title: Experiment readout
---

```js
import {pct, pctCI, ci, share, secs, int, num, pval, value, tone} from "./components/format.js";
import {modelSE, videosFor, usersFor, zSum} from "./components/power.js";
const experiment = FileAttachment("data/experiment.json").json();
const metrics = FileAttachment("data/metrics.json").json();
```

```js
const l2 = experiment.layer2;
const pw = experiment.power;
const defs = metrics.definitions;
const bounds = (r, se) => (se === "user" ? [r.rel_lo, r.rel_hi] : [r.rel_lo_two_way, r.rel_hi_two_way]);
const vr = new Map(l2.variance_reduction.map((d) => [d.metric, d]));
const extraUsers = (cut) => 1 / (1 - cut) - 1;
```

# Reading the gap as an A/B test

<p class="lede">The random slots randomise which video a user sees, not which users see tail videos. To read the gap the way a user-randomised test would, each user is assigned at random to one arm and keeps only that arm's impressions: tail videos in one, head videos in the other. This is constructed from the slot-level randomisation, not a test anyone ran.</p>

## Is the split clean?

```js
const srm = l2.srm;
const ch = experiment.checks;
const maxSMD = d3.max(ch.balance, (d) => Math.abs(d.smd));
display(html`<div class="grid grid-cols-4">
  <div class="card kpi">
    <div class="label">Users per arm, tail / head</div>
    <div class="value">${int(srm.users_assigned_t)} / ${int(srm.users_assigned_c)}</div>
    <div class="ci">Sample-ratio test ${pval(srm.p)}</div>
    <div class="foot">${int(srm.users_with_data_t)} and ${int(srm.users_with_data_c)} have impressions in their arm.</div>
  </div>
  <div class="card kpi">
    <div class="label">Users with pre-period history</div>
    <div class="value">${share(l2.pre_coverage)}</div>
    <div class="foot">CUPED uses each user's pre-period behaviour. The rest get the average and a flag.</div>
  </div>
  <div class="card kpi">
    <div class="label">Largest covariate imbalance</div>
    <div class="value">${num(maxSMD, 3)}</div>
    <div class="ci">standardised mean difference</div>
    <div class="foot">Across ${ch.balance.length} user covariates, tail vs head impressions. Below 0.1 is the usual bar.</div>
  </div>
  <div class="card kpi">
    <div class="label">Tier independent of activity and date</div>
    <div class="value">${pval(ch.tier_by_activity.p).replace("p = ", "")} / ${pval(ch.tier_by_date.p).replace("p = ", "")}</div>
    <div class="ci">chi-squared p-values</div>
    <div class="foot">Heavy and light users, and early and late days, draw tail and head videos at the same rates.</div>
  </div>
</div>`);
```

```js
const headFifth = ch.srm.find((d) => d.fifth === 5);
```

One check fails at the slot level: head videos get ${share(1 - headFifth.ratio)} fewer random impressions than an even draw would give (chi-squared ${int(ch.srm_test.chi2)}, ${pval(ch.srm_test.p)}). Random draws skip videos the user has already seen, and users have seen head videos far more often. That removes a few impressions from heavy users' head draws but does not change who sees what otherwise, which the balance checks above confirm. The estimate barely moves with user fixed effects (see <a href="./metrics#robustness">robustness</a>). Details are in <a href="./methods">methods</a>.

## The readout

```js
const useCuped = view(Inputs.toggle({label: "CUPED", value: false}));
```

```js
const seType = view(Inputs.radio(new Map([["Clustered by user and video", "two_way"], ["Clustered by user only", "user"]]), {label: "Intervals", value: "two_way"}));
```

```js
const readout = defs.map((def) => {
  const off = l2.readout.find((d) => d.metric === def.key && !d.cuped);
  const on = l2.readout.find((d) => d.metric === def.key && d.cuped);
  let [lo, hi] = d3.extent([off, on].flatMap((r) => [...bounds(r, "user"), ...bounds(r, "two_way")]));
  const pad = (hi - lo) * 0.12;
  [lo, hi] = [lo - pad, hi + pad];
  const x = (v) => `${(100 * (v - lo)) / (hi - lo)}%`;
  const el = {
    t: html`<td class="r"></td>`, c: html`<td class="r"></td>`, est: html`<td class="r num"></td>`, w: html`<td class="r num muted"></td>`,
    ghost: html`<div class="ghost"></div>`, ci: html`<div class="ci"></div>`, dot: html`<div class="dot"></div>`
  };
  const zero = lo < 0 && hi > 0 ? html`<div class="zero" style="left:${x(0)}"></div>` : "";
  el.row = html`<tr><td>${def.label}</td>${el.t}${el.c}${el.est}${el.w}<td><div class="bar-track">${zero}${el.ghost}${el.ci}${el.dot}</div></td></tr>`;
  return {def, off, on, x, el};
});

function paint(cuped, se) {
  for (const r of readout) {
    const cur = cuped ? r.on : r.off;
    const [lo, hi] = bounds(cur, se);
    const [glo, ghi] = bounds(r.off, se);
    const {el, x, def} = r;
    el.t.textContent = value(cur.treatment, def.unit, def.scale);
    el.c.textContent = value(cur.control, def.unit, def.scale);
    el.est.textContent = pctCI(cur.rel, lo, hi);
    el.est.className = `r num ${tone(def.key, lo, hi)}`;
    el.w.textContent = `${num(100 * (hi - lo), 1)} pp${cuped ? ` (${pct((hi - lo) / (ghi - glo) - 1, 0)})` : ""}`;
    Object.assign(el.ghost.style, {left: x(glo), width: `calc(${x(ghi)} - ${x(glo)})`, opacity: cuped ? 1 : 0});
    Object.assign(el.ci.style, {left: x(lo), width: `calc(${x(hi)} - ${x(lo)})`});
    el.dot.style.left = x(cur.rel);
  }
}
paint(false, "two_way");

display(html`<div class="card scroll"><table class="data">
  <thead><tr><th>Metric</th><th class="r">Tail arm</th><th class="r">Head arm</th><th class="r">Tail vs head [95% CI]</th><th class="r">Interval width</th><th style="min-width:180px">Interval, each row on its own scale</th></tr></thead>
  <tbody>${readout.map((r) => r.el.row)}</tbody>
</table>
<p class="small">${int(srm.users_with_data_t)} users and ${int(l2.readout[0].n_imps_t)} impressions in the tail arm, ${int(srm.users_with_data_c)} and ${int(l2.readout[0].n_imps_c)} in the head arm. With CUPED on, grey shows the interval without it. The thin line marks zero.</p>
</div>`);
```

```js
paint(useCuped, seType);
```

```js
const cutMwt = seType === "user" ? vr.get("mwt").variance_reduction_user : vr.get("mwt").variance_reduction_two_way;
const cutSkip = seType === "user" ? vr.get("early_skip").variance_reduction_user : vr.get("early_skip").variance_reduction_two_way;
display(html`<div class="grid grid-cols-3">
  <div class="card kpi">
    <div class="label">CUPED cuts the variance of meaningful watch time by</div>
    <div class="value">${share(cutMwt)}</div>
    <div class="foot">${seType === "user" ? "User-clustered intervals." : "Intervals clustered by user and video."}</div>
  </div>
  <div class="card kpi">
    <div class="label">Same precision as running with</div>
    <div class="value">${share(extraUsers(cutMwt))}</div>
    <div class="ci">more users</div>
    <div class="foot">Variance falls in proportion to 1 / users, so a ${share(cutMwt)} cut is worth 1 / (1 − ${num(cutMwt, 3)}) − 1 more users.</div>
  </div>
  <div class="card kpi">
    <div class="label">Early-skip rate, variance cut</div>
    <div class="value">${share(cutSkip)}</div>
    <div class="foot">Equivalent to ${share(extraUsers(cutSkip))} more users. Skipping is a stable habit, so past behaviour predicts it well.</div>
  </div>
</div>`);
```

```js
const flipped = readout.filter((r) => {
  const [a, b] = bounds(r.off, "two_way");
  const [c, d] = bounds(r.on, "two_way");
  return tone(r.def.key, a, b) === "" && tone(r.def.key, c, d) !== "";
});
const twoWayCut = vr.get("mwt").variance_reduction_two_way;
const userCut = vr.get("mwt").variance_reduction_user;
```

CUPED does not change the call on meaningful watch time: the gap is large enough that any interval excludes zero. It matters for the small effects the guardrails test. ${flipped.length ? `With CUPED, ${flipped.map((r) => r.def.label[0].toLowerCase() + r.def.label.slice(1)).join(" and ")} ${flipped.length === 1 ? "moves" : "move"} from inconclusive to ${pctCI(flipped[0].on.rel, ...bounds(flipped[0].on, "two_way"))}.` : ""} The cut is smaller with video clustering (${share(twoWayCut)} vs ${share(userCut)}) because part of that interval is video-to-video variation, which a user's history cannot predict.

## Which intervals to trust

```js
const aaLabel = {"user split": "Users split, same videos", "video split": "Videos split, same users", "user x video split": "Users and videos split, as in this readout"};
const aa = experiment.aa.summary.map((d) => ({...d, design: aaLabel[d.design]}));
const aaUser = experiment.aa.summary.find((d) => d.design === "user x video split" && d.se === "user");
```

To check the intervals, the same readout was run ${int(aa[0].reps)} times inside the head tier, where the true effect is zero by construction. A 95% interval should flag about 5% of those runs.

```js
display(html`<div class="small" style="display:flex;gap:1.25rem;flex-wrap:wrap">
  <span><span style="color:var(--accent)">●</span> Clustered by user and video</span>
  <span><span style="color:var(--fg-muted)">●</span> Clustered by user only</span>
</div>`);
display(resize((width) => Plot.plot({
  width,
  height: 210,
  marginLeft: width < 640 ? 150 : 270,
  x: {domain: [0, 0.5], tickFormat: (d) => share(d, 0), label: "Share of null runs flagged as significant", grid: true},
  y: {domain: Object.values(aaLabel), label: null, tickFormat: (d) => (width < 640 ? d.replace(", as in this readout", "") : d)},
  marks: [
    Plot.ruleX([0.05], {stroke: "var(--fg-faint)", strokeDasharray: "4,3"}),
    ...[["two-way", "var(--accent)", -7], ["user", "var(--fg-muted)", 7]].flatMap(([se, color, dy]) => {
      const rows = aa.filter((d) => d.se === se);
      return [
        Plot.ruleY(rows, {y: "design", x1: "fpr_lo", x2: "fpr_hi", stroke: color, strokeWidth: 2, dy}),
        Plot.dot(rows, {y: "design", x: "false_positive_rate", fill: color, stroke: "var(--card)", r: 5, dy,
          tip: true, title: (d) => `${d.design}\n${se === "user" ? "User only" : "User and video"}: ${share(d.false_positive_rate)} ${ci(d.fpr_lo, d.fpr_hi)}`})
      ];
    })
  ]
})));
```

User-clustered intervals are right when both arms see the same videos. Once the arms see different videos, as here and in any test of a change to which videos get shown, they flag ${share(aaUser.false_positive_rate, 0)} of null runs: they treat the particular videos in each arm as fixed. Clustering by user and video stays at or below 5%, so it is the default above.

## Does CUPED move the estimate?

```js
const cs = experiment.cuped_splits;
const splitRows = cs.splits.flatMap((d) => [{method: "Without CUPED", est: d.raw}, {method: "With CUPED", est: d.cuped}]);
display(resize((width) => Plot.plot({
  width,
  height: 150,
  marginLeft: 110,
  x: {label: "Tail vs head, meaningful watch time (s)", grid: true, tickFormat: (d) => num(d, 1)},
  y: {domain: ["Without CUPED", "With CUPED"], label: null},
  marks: [
    Plot.tickX(splitRows, {x: "est", y: "method", stroke: (d) => d.method, strokeOpacity: 0.55}),
    Plot.tickX([{method: "Without CUPED", est: cs.summary.mean_raw}, {method: "With CUPED", est: cs.summary.mean_cuped}],
      {x: "est", y: "method", stroke: "var(--fg)", strokeWidth: 2.5})
  ],
  color: {domain: ["Without CUPED", "With CUPED"], range: ["var(--fg-muted)", "var(--accent)"]}
})));
```

Each tick is one of ${cs.summary.n_splits} random user splits. CUPED tightens the spread from ${secs(cs.summary.sd_raw_across_splits, 3)} to ${secs(cs.summary.sd_cuped_across_splits, 3)}, a ${share(cs.summary.empirical_variance_reduction)} cut in variance, close to the ${share(cs.summary.variance_reduction)} its standard errors predict. The average moves by ${secs(cs.summary.mean_shift, 3)} (Monte Carlo SE ${secs(cs.summary.mc_se_shift, 3)}), ${share(cs.summary.mean_shift / Math.abs(cs.summary.mean_raw))} of the gap. The seen-video filter above predicts a shift of ${secs(cs.summary.expected_shift, 3)}, which CUPED corrects.

## Why the impression-level comparison gains little

```js
const clLabel = {"none (iid)": "None", "user": "By user", "video": "By video", "user x video (two-way)": "By user and video"};
const cl = metrics.layer1.clustering;
const vc = pw.components;
const within = {videos: (pw.design.videos_t + pw.design.videos_c) / 2};
within.ipv = (pw.design.imps_t + pw.design.imps_c) / 2 / within.videos;
const videoTerm = vc.video / within.videos;
const videoShare = videoTerm / (videoTerm + (vc.user + vc.residual) / (within.videos * within.ipv));
```

In the original slots every user sees both tail and head videos, so each user's baseline lands on both sides and cancels. What is left is mostly which videos happened to be drawn. Clustering by video moves the standard error far more than clustering by user does. In the variance model, the video term makes up ${share(videoShare, 0)} of the squared standard error. CUPED adjusts for users, so it has little to remove here, and the readout above is where it pays off.

```js
display(html`<div class="card scroll"><table class="data">
  <thead><tr><th>Standard errors clustered</th><th class="r">SE (s)</th><th class="r">Tail vs head, 95% CI (s)</th></tr></thead>
  <tbody>${cl.map((d) => html`<tr>
    <td>${clLabel[d.clustering]}</td>
    <td class="r">${num(d.se_abs, 3)}</td>
    <td class="r num">[${num(d.abs_lo, 2)}, ${num(d.abs_hi, 2)}]</td>
  </tr>`)}</tbody>
</table></div>`);
```

## Power calculator

```js
const us = pw.user_split;
const designChoice = view(Inputs.radio(new Map([["Users split into arms", "split"], ["Every user sees both arms", "within"]]), {label: "Design", value: "split"}));
```

```js
const split = designChoice === "split";
const calc = view(Inputs.form({
  videos: Inputs.range([100, 50000], {label: "Videos per arm", value: Math.round(split ? us.videos : within.videos), step: 1, transform: Math.log}),
  ipv: Inputs.range([10, 2000], {label: "Impressions per video", value: +(split ? us.imps_per_video : within.ipv).toFixed(1), step: 0.1, transform: Math.log}),
  users: Inputs.range([1000, 2000000], {label: "Users, both arms", value: us.users, step: 1, transform: Math.log, disabled: !split}),
  cuped: Inputs.range([0, 0.6], {label: "CUPED variance cut", value: split ? +vr.get("mwt").variance_reduction_user.toFixed(3) : 0, step: 0.001, disabled: !split}),
  alpha: Inputs.select([0.01, 0.05, 0.1], {label: "Significance level", value: pw.alpha}),
  power: Inputs.select([0.8, 0.9], {label: "Power", value: pw.power, format: (d) => share(d, 0)}),
  target: Inputs.range([0.5, 20], {label: "Effect to detect, % of head mean", value: 5, step: 0.1})
}));
```

```js
const head = pw.design.head_mean;
const z = zSum(calc.alpha, calc.power);
const args = split ? [calc.users, us.user_deff, calc.cuped] : [null, 1, 0];
const se = modelSE(vc, calc.videos, calc.ipv, ...args);
const mde = z * se;
const target = calc.target / 100;
const seTarget = (target * head) / z;
const needVideos = videosFor(vc, seTarget, calc.ipv, ...args);
const needUsers = split ? usersFor(vc, seTarget, calc.videos, calc.ipv, us.user_deff, calc.cuped) : null;
const floor = z * Math.sqrt(2 * (vc.video / calc.videos + (split ? 1 - calc.cuped : 1) * vc.residual / (calc.videos * calc.ipv)));
display(html`<div class="grid grid-cols-4 calc">
  <div class="card kpi out">
    <div class="label">Smallest detectable change</div>
    <div class="value">${share(mde / head)}</div>
    <div class="ci">${secs(mde)} of the ${secs(head)} head mean</div>
  </div>
  <div class="card kpi out">
    <div class="label">Standard error</div>
    <div class="value">${secs(se, 3)}</div>
    <div class="ci">difference in means</div>
  </div>
  <div class="card kpi out">
    <div class="label">Videos per arm for a ${share(target)} change</div>
    <div class="value">${isFinite(needVideos) ? int(Math.ceil(needVideos)) : "Not reachable"}</div>
    <div class="ci">${isFinite(needVideos) ? "other inputs as set" : "user noise alone exceeds the target; add users"}</div>
  </div>
  <div class="card kpi out">
    <div class="label">Users for a ${share(target)} change</div>
    <div class="value">${!split ? "n/a" : isFinite(needUsers) ? int(Math.ceil(needUsers)) : "Not reachable"}</div>
    <div class="ci">${!split ? "every user is in both arms" : isFinite(needUsers) ? "other inputs as set" : `these videos cap it at ${share(floor / head)}; add videos`}</div>
  </div>
</div>`);
```

<p class="small">Model: SE² = 2 × (video variance / videos + (1 − CUPED cut) × (user variance × ${num(us.user_deff, 2)} / users per arm + residual variance / impressions)), with variance components estimated from the random slots (video ${num(vc.video, 1)}, user ${num(vc.user, 1)}, residual ${num(vc.residual, 1)} s²). ${num(us.user_deff, 2)} is the design effect from users contributing unequal numbers of impressions. When every user sees both arms, user effects cancel into the residual and CUPED does not apply. At this dataset's size the model gives ${secs(us.se_model, 3)} for the user split against ${secs(us.se_observed, 3)} measured, and ${secs(pw.se_model, 3)} against ${secs(pw.se_observed, 3)} when every user sees both.</p>

```js
const hate = pw.hate;
```

${split ? `Video count sets a floor. With ${int(calc.videos)} videos per arm, no number of users gets below ${share(floor / head)}, because each arm's mean still rests on the videos it happened to draw. ` : ""}Hates are the hard guardrail. At this size the smallest detectable change in hates is ${share(hate.mde_rel, 0)} of the head rate of ${num(hate.head_rate_per_1k, 2)} per 1K, against a +5% threshold. Detecting +5% would take about ${int((hate.mde_rel / metrics.thresholds.hate_rel_max) ** 2)} times the data.

```js
display(html`<div class="card scroll"><table class="data">
  <thead><tr><th>Meaningful watch time at this dataset's size</th><th class="r">SE (s)</th><th class="r">Smallest detectable change</th></tr></thead>
  <tbody>${pw.table.map((d) => html`<tr>
    <td>${d.design}</td>
    <td class="r">${num(d.se, 3)}</td>
    <td class="r">${share(d.mde_rel_to_head)} <span class="muted">(${secs(d.mde_abs)})</span></td>
  </tr>`)}</tbody>
</table>
<p class="small">Two-sided test at α = ${pw.alpha}, ${share(pw.power, 0)} power. User-clustered rows understate the error once arms see different videos (see above).</p>
</div>`);
```

<p class="small"><a href="./segments">Who the gap hits hardest →</a></p>
