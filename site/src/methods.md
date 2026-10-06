---
title: Methods
---

```js
import {pct, pctCI, ci, share, secs, int, num, pp, pval, METRIC} from "./components/format.js";
const overview = FileAttachment("data/overview.json").json();
const metrics = FileAttachment("data/metrics.json").json();
const experiment = FileAttachment("data/experiment.json").json();
const decision = FileAttachment("data/decision.json").json();
const llm = FileAttachment("data/llm.json").json();
```

```js
const day = (d) => {
  const s = String(d);
  return new Date(`${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6)}T00:00:00Z`)
    .toLocaleDateString("en-GB", {day: "numeric", month: "short", timeZone: "UTC"});
};
const [preStart, preEnd] = overview.periods.pre.map(day);
const [expStart, expEnd] = overview.periods.experiment.map(day);
const th = overview.tier_thresholds;
const T = decision.thresholds;
const checks = experiment.checks;
const headSrm = checks.srm.find((d) => d.fifth === 5);
const seenHead = checks.seen_filter.find((d) => d.fifth === 5);
const draws = checks.draws_by_pre_volume;
const dropout = (f) => checks.dropout.find((d) => d.fifth === f);
const maxSmd = d3.max(checks.balance, (d) => Math.abs(d.smd));
const sens = experiment.sensitivities.filter((d) => d.variant !== "primary");
const [sensLo, sensHi] = d3.extent(sens, (d) => d.rel);
const aa = (design, se) => experiment.aa.summary.find((d) => d.design === design && d.se === se);
const aaTwoWayMax = d3.max(experiment.aa.summary.filter((d) => d.se === "two-way"), (d) => d.false_positive_rate);
const cl = (name) => metrics.layer1.clustering.find((d) => d.clustering.startsWith(name)).se_abs;
const vr = experiment.layer2.variance_reduction.find((d) => d.metric === "mwt");
const input = (metric, prefix) => decision.inputs.find((d) => d.metric === metric && d.quantity.startsWith(prefix));
const A = decision.assumptions;
const gems = decision.hidden_gems.summary;
const cSummary = decision.variant_c_summary;
const auditStat = (comparison, quantity) => llm.audit.stats.find((d) => d.comparison === comparison && d.quantity === quantity);
const mixShare = (labels) => llm.propagation.decomposition.find((d) => d.labels === labels && d.quantity === "mix_share_of_gap");
const pa = llm.platform_agreement;
const weakest = d3.least(pa.per_class.filter((d) => d.vertical !== "Other"), (d) => d.precision);
const fmtShare = (d) => `${share(d.estimate)} ${ci(d.lo, d.hi)}`;
```

# How it was done

<p class="lede">Every number on this site comes from one run of the pipeline over the public KuaiRand-Pure logs. This page covers the data, definitions, estimators and assumptions behind them.</p>

## Data

KuaiRand-Pure logs ${int(overview.users)} users of a short-video app watching a sample of ${int(overview.pool_videos)} videos. In some feed slots the app replaced the recommended video with one drawn at random from that sample. Those slots, ${int(overview.random_impressions)} impressions from ${int(overview.random_users)} users on ${int(overview.random_videos)} videos, carry every causal estimate here.

- **Periods.** Pre-period ${preStart} to ${preEnd} 2022, used only to define tiers and user covariates. Experiment window ${expStart} to ${expEnd} 2022.
- **Tiers.** Videos are ranked by recommended impressions in the pre-period, before any random exposure. Tail is the bottom fifth (${th.tail_max_pre_impressions} or fewer), head the top fifth (${th.head_min_pre_impressions} or more). Ties stay in the same tier.
- **Cleaning.** Feed tab only. Exact duplicate rows dropped; for same-millisecond conflicts the row with the longest play is kept. Videos with unknown duration stay in every metric except long view.

```js
const tiers = [["Tail", "tail"], ["Middle three fifths", "mid"], ["Head", "head"]];
display(html`<div class="card scroll" style="max-width:640px"><table class="data">
  <thead><tr><th>Tier</th><th class="r">Videos</th><th class="r">Random impressions</th><th class="r">Per video</th></tr></thead>
  <tbody>${tiers.map(([label, key]) => html`<tr>
    <td>${label}</td>
    <td class="r">${int(overview.videos_by_tier[key])}</td>
    <td class="r">${int(overview.impressions_by_tier[key])}</td>
    <td class="r">${num(overview.impressions_by_tier[key] / overview.videos_by_tier[key], 1)}</td>
  </tr>`)}</tbody>
</table></div>`);
```

## Metrics

Every metric is a mean per impression, because the slot is what was randomised. The logs only cover the sampled videos, so per-user totals and sessions are not observable.

<div class="card scroll"><table class="data">
  <thead><tr><th>Metric</th><th>Definition</th><th>Role</th></tr></thead>
  <tbody>
    <tr><td>Meaningful watch time</td><td>Play seconds if the play lasted at least 3s, else 0. Capped at ${secs(overview.mwt_cap_s, 1)}, the pre-period 99.9th percentile of recommended play time.</td><td>North star</td></tr>
    <tr><td>Early-skip rate</td><td>Share of impressions played for under 3s.</td><td>Guardrail</td></tr>
    <tr><td>Hates per 1K</td><td>Explicit "hate" feedback per 1,000 impressions.</td><td>Guardrail</td></tr>
    <tr><td>Next-video watch time</td><td>Meaningful watch time of the user's next logged impression, if it is a random slot within 60s or 5 minutes.</td><td>Guardrail, exploratory</td></tr>
    <tr><td>Long-view rate</td><td>The log's long-view flag. Videos with unknown duration excluded.</td><td>Diagnostic</td></tr>
    <tr><td>Likes, follows, profile enters</td><td>Per 1,000 impressions.</td><td>Diagnostic</td></tr>
  </tbody>
</table></div>

The guardrails are judged at the feed level, at the share of slots a boost would take: meaningful watch time may fall by at most ${share(-T.mwt_rel_min)}, the early-skip rate may rise by at most ${num(100 * T.skip_pp_max, 0)} percentage point, and hates may rise by at most ${share(T.hate_rel_max, 0)}. All thresholds and the cap were fixed before the analysis ran.

## Four layers

```js
display(html`<div class="card scroll"><table class="data">
  <thead><tr><th>Layer</th><th>Question</th><th>Estimator</th><th>Label</th></tr></thead>
  <tbody>
    <tr><td>1. Impression level</td><td>How much less does a tail video earn per random slot than a head video?</td><td>Difference in means over random slots, standard errors clustered by user and by video.</td><td>Causal</td></tr>
    <tr><td>2. Experiment readout</td><td>What would a user-level A/B test have shown?</td><td>Users split at random into a tail arm and a head arm; each keeps only its arm's impressions. Per-user ratio metric, delta-method standard errors, CUPED.</td><td>Constructed from slot-level randomisation</td></tr>
    <tr><td>3. Delivery</td><td>How large is the gap when the recommender chooses the video?</td><td>The same contrast on recommended slots.</td><td>Descriptive</td></tr>
    <tr><td>4. Feed projection</td><td>What happens to the whole feed if a share of slots goes to tail videos?</td><td>Layer 1 inputs plus stated delivery assumptions, with a bootstrap over users and videos.</td><td>Projection</td></tr>
  </tbody>
</table></div>`);
```

The content-mix split on the <a href="./metrics">tail gap page</a> reweights head impressions to the tail's mix of categories (or categories by duration) and splits the gap into a mix part and a within-content part. Segment effects use the same layer 1 estimator within each level, one heterogeneity test per segment, and Benjamini-Hochberg correction across all tests.

## Why intervals cluster by video

All impressions of a video share its quality, so they are not independent draws. With about ${int(overview.random_impressions / overview.random_videos)} random impressions per video, the variation between videos sets the precision. For the watch-time gap, the standard error is ${secs(cl("none"), 3)} treating impressions as independent, ${secs(cl("user") , 3)} clustered by user, ${secs(cl("video"), 3)} by video and ${secs(cl("user x video"), 3)} by both.

The A/A tests check this. They make ${aa("video split", "user").reps} random null splits inside the head tier, where the true effect is zero. With user clustering alone, ${share(aa("user x video split", "user").false_positive_rate, 0)} of splits by user and video and ${share(aa("video split", "user").false_positive_rate, 0)} of splits by video come out significant at 5%. With two-way clustering no design exceeds ${share(aaTwoWayMax, 1)}. Every interval on this site uses two-way clustering unless it says otherwise.

## Randomisation checks

- **Who gets which tier.** The tier of a random draw is unrelated to user activity (${pval(checks.tier_by_activity.p)}) and date (${pval(checks.tier_by_date.p)}). In the readout, users split ${int(experiment.layer2.srm.users_assigned_t)} to ${int(experiment.layer2.srm.users_assigned_c)} (${pval(experiment.layer2.srm.p)}), and the largest standardised difference in pre-period covariates is ${num(maxSmd, 3)}.
- **Head videos get fewer random impressions.** Head received ${share(headSrm.ratio)} of the impressions its share of videos implies (χ² = ${int(checks.srm_test.chi2)}). Two causes:
  - The random draw skips videos the user has already seen. Only ${share(seenHead.observed, 2)} of head draws are repeats, against ${share(seenHead.expected_if_unfiltered, 2)} expected without a filter. Heavier users have seen more head videos, so they get fewer head draws per tail draw (${num(draws[0].head_per_tail, 3)} for the lightest fifth of users, ${num(draws[draws.length - 1].head_per_tail, 3)} for the heaviest).
  - Head videos leave the random pool earlier: ${num(dropout(5).mean_random_impressions, 1)} random impressions per video against ${num(dropout(1).mean_random_impressions, 1)} for tail.
- **What it means.** Within a user, the draw is still random over videos the user has not seen, which is what a boost would serve. Date fixed effects, user fixed effects, restricting to videos still drawn late in the window, a 180s cap and dropping unknown durations all keep the gap between ${pct(sensHi)} and ${pct(sensLo)}. See <a href="./metrics#robustness">robustness</a>.

## CUPED

The readout adjusts each user's metric for their pre-period behaviour: the same metric, meaningful watch time, early-skip rate, long-view rate, like rate and log impression count, plus a flag for users with no pre-period data (${share(1 - experiment.layer2.pre_coverage)} of users, who get the mean). For meaningful watch time this cuts the variance by ${share(vr.variance_reduction_user)} with user-clustered errors and ${share(vr.variance_reduction_two_way)} with two-way errors. The smaller cut is the video-to-video part, which a user's history cannot predict. Across 100 random user splits, the adjustment moves the estimate by about as much as the head-draw imbalance above implies. See the <a href="./experiment">experiment readout</a>.

## Power model

The calculator on the experiment page uses variance components estimated from the random slots: video ${num(experiment.power.components.video, 1)} s², user ${num(experiment.power.components.user, 1)} s², residual ${num(experiment.power.components.residual, 1)} s². With V videos and n impressions per arm, U users and a CUPED cut c:

```tex
\begin{aligned}
\text{Every user sees both arms:}\quad & \mathrm{SE}^2 = 2\left(\frac{\sigma_v^2}{V} + \frac{\sigma_u^2 + \sigma_r^2}{n}\right) \\[4pt]
\text{Users split into arms:}\quad & \mathrm{SE}^2 = 2\left(\frac{\sigma_v^2}{V} + (1 - c)\left(\frac{\sigma_u^2\, d}{U/2} + \frac{\sigma_r^2}{n}\right)\right) \\[4pt]
& \mathrm{MDE} = (z_{1-\alpha/2} + z_{\text{power}})\,\mathrm{SE}
\end{aligned}
```

The design effect d = ${num(experiment.power.user_split.user_deff, 2)} accounts for users contributing unequal numbers of impressions. At this dataset's size the model gives ${secs(experiment.power.user_split.se_model, 3)} for the user split against ${secs(experiment.power.user_split.se_observed, 3)} measured, and ${secs(experiment.power.se_model, 3)} against ${secs(experiment.power.se_observed, 3)} when every user sees both arms.

## Feed projection

Each variant moves a share s of feed slots (${decision.shares.map((d) => share(d, 0)).join(", ")}) from the average recommended slot to tail videos. The feed-level change is

```tex
\Delta = s \cdot \frac{\bar y_{\text{boost}} - \bar y_{\text{base}}}{\bar y_{\text{base}}}
```

for watch time and hates, and s times the difference in points for the early-skip rate.

- **Baseline.** The average recommended slot among the sampled videos: ${secs(input("mwt", "Average").estimate)} of meaningful watch time, ${share(input("early_skip", "Average").estimate)} early skips, ${num(1000 * input("hated", "Average").estimate, 2)} hates per 1K. The logs only record recommended impressions of sampled videos, so this stands in for the whole feed.
- **No targeting help.** Boosted videos perform as they do in random slots.
- **Targeting carries over.** Boosted videos get the lift the recommender achieves for tail videos: ${num(input("mwt", "Targeting").estimate, 2)}× watch time [${num(input("mwt", "Targeting").lo, 2)}, ${num(input("mwt", "Targeting").hi, 2)}] and ${num(input("early_skip", "Targeting").estimate, 2)}× early skips. Hates keep the random-slot rate, because recommended tail slots hold too few hates to estimate a lift.
- **Verdict.** Ship if every guardrail holds with no targeting help. A/B test if they hold only when targeting carries over. Don't ship otherwise. A verdict is marked robust when the 95% interval of every deciding guardrail sits on the same side of its threshold as the estimate.
- **Intervals.** A Poisson bootstrap over users and videos (${A.n_boot} replicates) re-runs the whole procedure, including the gate decisions and the category choice.

**Variant B** spends k test impressions on every tail video. A video passes if its meaningful watch time over those impressions reaches ${secs(A.bar_mwt)}, the median head video in random slots. At about ${int(A.tail_imps_per_video)} boosted impressions per tail video (the random log's rate), test slots take k / ${int(A.tail_imps_per_video)} of the boost. The gate is scored on each video's later random impressions.

```js
display(html`<div class="card scroll"><table class="data">
  <thead><tr><th>k</th><th class="r">Test slots</th><th class="r">Pass</th><th class="r">Precision</th><th class="r">Recall</th><th class="r">Passers, later watch time</th><th class="r">Boosted slot, watch time</th></tr></thead>
  <tbody>${decision.gate.map((g) => html`<tr>
    <td>${g.k}${g.default ? html` <span class="tag">Default</span>` : ""}</td>
    <td class="r">${share(g.test_slot_share)}</td>
    <td class="r">${share(g.pass_rate)}</td>
    <td class="r">${share(g.precision)}</td>
    <td class="r">${share(g.recall)}</td>
    <td class="r">${secs(g.passers_later_mwt)} <span class="muted">[${num(g.passers_later_mwt_lo, 2)}, ${num(g.passers_later_mwt_hi, 2)}]</span></td>
    <td class="r">${secs(g.boost_slot_mwt)} <span class="muted">[${num(g.boost_slot_mwt_lo, 2)}, ${num(g.boost_slot_mwt_hi, 2)}]</span></td>
  </tr>`)}</tbody>
</table>
<p class="small">Precision: share of passers whose later watch time clears the bar. Recall: share of videos that clear it later which the gate passed. Boosted slot: test and later impressions combined.</p></div>`);
```

**Variant C** boosts only categories with at least ${cSummary.min_tail_videos} tail videos whose tail matched the average random slot (${secs(cSummary.threshold_first_half_avg_random_mwt)}) in the first half of the window, and is scored on the second half.

**Hidden gems.** Each video's random-slot watch time is shrunk towards its tier mean (empirical Bayes, normal prior; beta-binomial for the skip rate). Tail prior: mean ${secs(gems.tail.prior_mean)}, SD ${secs(gems.tail.prior_sd)}. Head prior: mean ${secs(gems.head.prior_mean)}, SD ${secs(gems.head.prior_sd)}. After shrinkage ${share(gems.tail.share_posterior_above_bar)} of tail videos clear the ${secs(A.bar_mwt)} bar, against ${share(gems.tail.share_raw_above_bar)} before.

## Content tags

A language model (${llm.model}, temperature 0, fixed JSON schema, 20 videos per call) read each video's caption and cover text and returned three tags: a content vertical from 18 options plus Unclear, a format (original, clip or re-upload, unclear) and whether it promotes a product. ${int(llm.distribution.tagged)} of ${int(llm.distribution.videos)} videos were tagged; ${int(llm.distribution.no_text)} have no text. Responses are cached and the tag table is committed, so the analysis reruns without an API key.

**Validation.**

- **Against the platform's categories**, mapped to the same verticals: ${share(pa.agreement)} agreement, κ = ${num(pa.kappa, 2)}. The platform's labels are model outputs too, so this measures consistency, not accuracy. The weakest class is ${weakest.vertical.toLowerCase()} (${share(weakest.precision, 0)} of the model's ${weakest.vertical.toLowerCase()} tags match the platform).
- **Against an audit sample** of ${llm.audit.n} videos, stratified by tier and by whether the model and the platform agree, and weighted back to all videos. A second model (Claude) labelled them blind to both earlier labels, from the same text. Verticals: ${fmtShare(auditStat("vertical: LLM vs audit", "agreement"))} for the tagging model and ${fmtShare(auditStat("vertical: platform vs audit", "agreement"))} for the platform. The audit reads the same text as the tagger while the platform may also see the video, so this comparison can favour the tagging model. Format: ${share(auditStat("format: LLM vs audit", "agreement").estimate)}. Commercial: ${share(auditStat("commercial: LLM vs audit", "agreement").estimate)}.

**Carrying the error into results.** The content-mix share of the gap is computed three ways: with the platform's verticals, with the model's, and with the model's corrected for misclassification. The correction unmixes each tier's class totals through the audit's confusion matrix, drawn from a Dirichlet posterior inside the bootstrap. Draws that imply negative impressions or impossible class means are rejected (${int(llm.propagation.correction_draws.rejected)} rejected for ${int(llm.propagation.correction_draws.accepted)} accepted). It assumes the same tagging error in both tiers.

```js
display(html`<div class="card scroll" style="max-width:640px"><table class="data">
  <thead><tr><th>Verticals from</th><th class="r">Share of the gap from content mix</th></tr></thead>
  <tbody>${[["The platform", "Platform verticals"], ["The tagging model", "LLM verticals"], ["The tagging model, corrected", "LLM verticals, corrected"]].map(([label, key]) => html`<tr>
    <td>${label}</td><td class="r">${fmtShare(mixShare(key))}</td>
  </tr>`)}</tbody>
</table></div>`);
```

Whichever labels are used, content mix explains a minority of the gap.

## Limits

- One app, one short window and a fixed sample of videos. Sessions, retention and creator response are out of reach.
- Random slots are untargeted. A real boost would be targeted to some degree, which is why larger shares need an A/B test.
- The projection assumes a boosted slot displaces an average recommended slot and leaves the rest of the feed unchanged. The next-video check finds no spillover, but only ${share(metrics.carryover.feasibility.share_next_within_60s)} of next impressions come within 60 seconds.
- Content segments compare different videos within each group, so they describe where the gap sits, not what causes it.
- Content tags read captions only, and were audited by another model rather than by people.

## Reproduce

The pipeline, tests and this site are on <a href="https://github.com/sulaimanhayek/Kuai-rand-analysis">GitHub</a>. Downloading the data and running `python -m pipeline` rebuilds every file this site reads, with fixed seeds.
