# 02. Analysis plan

Status: **approved.** The pipeline (`python -m pipeline`) implements this plan.
Numbers below come from `notes/01_data_profile.md` / `01_data_profile_tables.md`; pipeline results are in
`site/src/data/`.

**Decisions** (section 11):
1. Lever: tail (bottom fifth) vs head (top fifth), with variants A, B and C.
2. Layer 1 is the primary estimate; layer 2 is the constructed user-split readout, labelled as such.
3. MWT cap: pre-period p99.9 (373.5s); 180s is a sensitivity.
4. Thresholds and the ship / A/B test / don't-ship rule as proposed.
5. Audit labels: about 300 videos labelled by Claude, a different model family from the Gemini tagger, read from the
   Chinese captions directly (no translation step). Reported as a second model's labels, not human ground truth.
6. Licence: code MIT; derived data CC BY-SA 4.0.
7. KuaiRand-1K not used.

**Where the data refined the plan:**
- **Tier cut-offs.** Explicit thresholds put the tail at 0 to 12 pre-period impressions and the head at 177+ (the
  profile's `ntile` gave 13 and 179).
- **A second cause of the SRM.** Random draws skip videos the user has already seen: 0.20% of head draws are repeats,
  against 2.86% expected without a filter. Users have mostly seen head videos, so heavier users get fewer head draws
  per tail draw (0.952 for the lightest fifth of users by pre-period volume, 0.897 for the heaviest). Together with
  head videos leaving the pool early, this explains the shortfall (head at 0.954 of expected impressions overall,
  0.972 among videos still drawn on or after 05-05). The contrast is still random within user, over videos the user
  has not seen, which is also what a boost would serve.
- **CUPED shifts the layer-2 estimate slightly.** Over 100 random splits the mean shift in MWT is +0.019s
  (Monte Carlo SE 0.007), against +0.013s expected from the draw imbalance above, which CUPED corrects. The
  "no shift" property holds when impression counts do not depend on the arm (unit test on synthetic data).
- **CUPED pilot numbers corrected.** With the impression-level adjustment, variance falls 26% with user-clustered
  SEs and 12% with two-way SEs (not 28%).
- **Tagging model.** `gemini-3.1-flash-lite`. The free tier allows 20 requests a day on the Flash model, too few
  for about 380 calls. On 40 videos tagged by both, Flash-Lite matched `gemini-3.8-flash` on 85% of verticals, 82.5%
  of formats and all commercial flags. The taxonomy has 18 verticals plus Unclear.
- **Audit sample.** Stratified by tier (tail 120, head 120, middle 60) and by whether the LLM and the platform agree
  (half each), with weights N_h / n_h. Videos 0 to 80 were read while writing the prompt, so they are excluded.
  The audit labels are made blind to the Gemini and platform labels. The audit model reads the same caption text as
  the tagger, while the platform's categories may also use the video itself, so agreement with the audit can favour
  the LLM over the platform.
- **Correction method.** The audit estimates M = P(LLM label | audit label). Within each tier, class totals of
  outcome and impressions are unmixed through M, giving corrected per-class means and class mix. M is drawn from a
  Dirichlet posterior, combined with the two-way bootstrap. A draw of M that implies negative impressions, or a
  class mean outside [0, cap], in either tier cannot have produced the observed data, so it is rejected and redrawn;
  the interval comes from 400 feasible draws, and the rejection count is reported. With 300 audit videos and 18
  classes, the corrected per-vertical effects are wide; the decomposition is not. Assumes the same tagging error in
  both tiers and no dependence on the outcome given the true class. Verticals with fewer than 20 tail or head videos
  are pooled.

## 1. Product question and lever

**Question.** Should the feed give a distribution boost to under-distributed videos? If yes, in what form?

**Why this lever.**
- The recommender concentrates exposure heavily: in the pre-period, the top 20% of pool videos got 75.6% of impressions.
- Cold-start distribution is a standing trade-off in any feed. Exploring new videos can surface good content and keep
  creators posting, but each exploratory slot costs short-term engagement.
- The random-exposure log is exactly the data needed to price that trade-off.

**Levers ruled out by the data.**
- Freshness: all videos are the same age.
- Creator size by followers: no author follower counts.
- Session-length effects: logs are truncated to pool videos.

**Tiers.**
- Videos are ranked by recommended impressions in the pre-period (04-09 to 04-21, before any random exposure).
- **Tail** = the bottom fifth (0 to 13 impressions). **Head** = the top fifth (179+).
- The pipeline will use explicit thresholds so tied videos stay in the same tier. The profile's `ntile` broke ties by
  `video_id`.
- Head is the right comparison because 73.0% of recommended pool impressions in the experiment period went to
  head videos. It is roughly what a boosted slot would displace.
- Q2 to Q4 give a dose-response curve.
- Since 87% of authors have one video in the pool, "under-distributed video" is a close proxy for "under-distributed creator".

**Variants to evaluate.**

| Variant | Policy | How it's evaluated |
|---|---|---|
| A. Blanket boost | reallocate *s*% of tab-1 slots to tail videos, served untargeted | causal per-impression effect, plus a labelled projection |
| B. Gated boost | give each tail video *k* test impressions; keep boosting only the videos whose early results clear a bar | simulation on random-exposure data (first *k* impressions select, later impressions evaluate) |
| C. Content-aware boost | boost the tail only in content categories where the tail penalty is small | segment estimates by content category |

**Recommendation rule (fixed before Phase 2 runs).**
- **Ship** a variant if it meets all criteria in section 2 under the *pessimistic* delivery assumption
  (tail videos served untargeted, as in the random log).
- **Run a real A/B test** if it meets them only under the *optimistic* assumption (tail videos perform as when the
  recommender chose to show them).
- **Don't ship** if it meets neither.

## 2. Metrics

| Role | Metric | Definition | Why |
|---|---|---|---|
| **North-star** | Meaningful watch time per impression (MWT) | play seconds if the play lasted at least 3s, capped (see below), else 0; averaged over impressions | Watch time is the core user value in a short-video feed. The 3s floor drops scroll-pasts. The cap stops a few very long plays from driving the mean and the variance. It's per impression because the slot is the unit of randomisation and per-user totals aren't observable in Pure. |
| Guardrail 1 | Early-skip rate | share of impressions with play under 3s | The most direct "this slot was a miss" signal; fast and well powered |
| Guardrail 2 | Negative feedback | `is_hate` per 1K impressions | Explicit dissatisfaction. Rare, so the MDE will be reported and may be wide. |
| Guardrail 3 | Next-impression carryover | MWT of the user's next logged impression after a tail vs head slot, within the same short window | The best available stand-in for session health, since abandonment can't be measured. **Feasibility check first:** if gaps between logged impressions are too long, it is reported as exploratory only. |
| Lever goal | Distribution breadth | tail share of impressions; effective number of videos (exp of entropy); top-20% share | The reason to boost at all. Mechanical under the policy, so it is **projected**, not estimated. |
| Secondary | Long-view rate, likes / 1K, follows / 1K, profile enters / 1K | | Diagnostic. Follows matter for creators. |

**Cap.** Two candidates, both fixed from pre-period data only:
- the pre-period p99.9 of 373.5s, recommended because it trims only true outliers;
- 180s, which truncates 1.93% of recommended plays.

The other one will be reported as a sensitivity. The choice barely matters for the random log (0.19% of plays over 180s).

**Decision thresholds** (defaults; part of the approval):
- MWT change ≥ -1.0%
- early-skip increase ≤ +1.0 pp
- hates per 1K ≤ +5% relative

All three are measured at the tab-1 feed level at the evaluated reallocation share.

**Dropped:** session abandonment and time per session. Pure has no complete sessions, and this is stated on the site.

## 3. Causal design

The randomisation is at the slot level: a recommended item is replaced by a uniformly drawn pool video.
That makes the item's attributes random with respect to the user, the context and the time. It does **not** make users
random, because every user is in both "arms". The analysis is built around that.

| Layer | What | Unit and inference | Label on site |
|---|---|---|---|
| 1. Primary estimate | Tail vs head effect per impression on every metric, in tab-1 random slots; Q1 to Q5 dose-response | Impressions. OLS with **two-way cluster-robust SEs (user × video)**, analytic. Item variance dominates (pilot SE 0.143 by item vs 0.062 by user), so the effective sample size is closer to the number of videos than the number of impressions. | Causal |
| 2. Experiment readout | The same contrast as a user-level A/B: users are randomly split (seeded), and each user contributes only their arm's impressions | Users. Ratio metrics with delta-method SEs; CUPED | **"Constructed from slot-level randomisation"** |
| 3. Selection-bias view | Tail-vs-head gap when the recommender chose to show the video (-21%) vs random delivery (-47%) | Descriptive | Descriptive |
| 4. Feed-level impact | Variants A to C at *s* = 1%, 2%, 5% of tab-1 slots: MWT, guardrails, distribution breadth | Causal inputs plus stated assumptions; pessimistic and optimistic bounds | **"Projection"** |

**Rough check on Variant A.** Using the profile means, a 5% blanket boost under the pessimistic assumption costs about
5% × (4.44 − 22.69) / 22.69 ≈ **-4.0% MWT**. That would fail the threshold, so the real questions are B and C. Phase 2
will recompute this with proper inference rather than rely on the rough number.

**Randomisation checks:**
- covariate balance of drawn items by user segment and date (done in Phase 1; repeated in the pipeline);
- an SRM chi-square test of random impressions by tier against each tier's share of pool videos.
- **Known imbalance to report, not hide:** head videos leave the random pool earlier and get 149.4 impressions on
  average, against about 158 for other tiers. The SRM test will flag this. Two sensitivities:
  - add date fixed effects;
  - restrict to videos still drawn on or after 05-05.

**Content-mix decomposition.** Tail videos skew toward content types that do worse for everyone (dance, anime) and are
longer. The pipeline will split the raw tail gap into:
- a content-mix part (reweight head videos to the tail's category and duration mix);
- a within-content part.

The within-content part is the one a content-aware boost (Variant C) cares about.

**Hidden gems (for Variant B).**
- **Shrinkage:** shrink each video's random-exposure MWT and skip rate towards its tier mean (empirical Bayes,
  normal-normal for MWT, beta-binomial for skip). Report the share of tail videos whose posterior beats the head median.
- **Gate simulation:** use each video's first *k* random impressions (k = 5, 10, 20, 50) to decide whether to keep
  boosting it. Score the decision on its later impressions. Report precision, recall, and the projected cost and
  benefit per gate. Labelled **simulation**.

## 4. CUPED

- **Covariates:** each user's pre-period tab-1 behaviour (04-09 to 04-21):
  - MWT, skip rate, long-view rate, like rate, log impressions;
  - a has-pre-period flag. Users with no pre-period data get the mean plus the flag; 92.1% have tab-1 history.
- **Where it applies:** the user-level readout (layer 2). Ratio metrics are linearised first; the pre-period
  covariates are then regressed out of that linearised metric.
- **Where it doesn't help, and the site will say so:** in the within-user impression analysis (layer 1), CUPED
  changed the pilot two-way SE from 0.1461 to 0.1458. Each user is in both arms, so stable user differences
  already cancel.
- **Pilot evidence:** in the user split, variance falls 22.5% with one covariate and 28.2% with five.
  The estimate doesn't move.
- **How it's shown:**
  - a CUPED on/off toggle on the experiment page, with CIs narrowing in place;
  - a "variance reduction" stat with its equivalent in extra users;
  - a short note on why layer 1 doesn't benefit.
- **Test:** across many random splits, the mean of (CUPED estimate − raw estimate) is about 0 (within Monte Carlo error)
  and variance is lower. There is also a synthetic-data unit test with a known effect.

## 5. Segments (heterogeneous effects)

Pre-registered list. Each segment gets a tail-vs-head estimate with two-way clustered SEs and an interaction test.
Benjamini–Hochberg is applied across all interaction tests at q = 0.10. Results are shown as a forest plot.

| Side | Segments |
|---|---|
| User | Activity tier (full / high / middle / other). Tenure (≤180, 181 to 365, 366 to 730, 730+ days). Pre-period MWT tercile. Pre-period skip-rate tercile. Pre-period volume tercile. Is a creator. Following-count range. |
| Content | Platform top-level category (top 10 plus other). LLM content tags (section 8). Duration bucket. |

Excluded:
- `onehot_feat*`: encrypted and undocumented.
- `is_live_streamer`: unreliable encoding.
- Anything from `video_features_statistic`: leakage.

## 6. Novelty and time

- **By day:** the tail gap for each of the 17 days, plus a weighted trend test.
  Phase 1 already shows a flat range of -0.41 to -0.51.
- **Fatigue:** the tail gap by a user's nth random impression (1 to 5, 6 to 20, 21 to 50, 51 to 100, 101+).
- **Halves:** first-half vs second-half estimates.
- **Note:** random volume rises over the window, so raw daily totals mix in composition changes.
  The tail-vs-head contrast is within-day and not affected.

## 7. Power and MDE

- **Formula:** MDE at 80% power, α = 0.05 two-sided, is about 2.8 × SE.

| Design | Pilot SE (MWT) | MDE | Relative to head MWT (8.31s) |
|---|---:|---:|---:|
| Impression level, two-way clustered | 0.146s | 0.41s | 4.9% |
| User split, no CUPED | 0.129s | 0.36s | 4.3% |
| User split, CUPED (5 covariates) | 0.109s | 0.31s | 3.7% |

- The pipeline will export variance components (user, video, residual). The site's calculator lets the reader change
  the number of videos, impressions per video, number of users, α, power and CUPED variance reduction, and shows MDE
  and the sample size needed.
- Because the video component dominates, adding videos shrinks the MDE much faster than adding impressions.
- The hate-rate MDE will be computed and stated plainly if it's underpowered.

## 8. AI component: LLM content tags from captions

The profile found captions for 98.9% of pool videos, plus the platform's own model-predicted categories. That makes
LLM tagging possible, and the platform categories give a reference label for every video.

**What Gemini tags.** Each caption plus cover text gets three tags:
1. **Content vertical**, from a fixed English taxonomy of 18 verticals plus Unclear. The taxonomy is designed so the
   platform's 38 top-level categories map onto it.
2. **Format**: original creation / re-upload or clip of third-party media (film, TV, anime, compilations) / unclear.
3. **Commercial intent**: promotes a product, shop or link (yes / no).

**Why each tag matters.**
- *Vertical* gives readable English segments and feeds the content-mix decomposition.
- *Format* is a natural targeting rule. A boost for under-distributed *original* creators is a different policy from one
  for re-uploaded clips.
- *Commercial* is a guardrail segment.
- None of the three is invented. Each is read from text the creator wrote.

**Validation.**
- **Against platform labels (all videos):** compare the vertical to the mapped platform category. Report agreement,
  Cohen's κ, per-class precision and recall, and a confusion matrix. Caveat: the platform labels are model outputs too.
- **Audit labels** (decision 5):
  - A stratified sample of 300 videos (by tier × LLM-platform agreement), exported to `notes/hand_labels/sample.csv`.
  - Labelled by a second model, blind to the Gemini and platform labels; the pipeline reads them back.
  - Reported: κ for all three tags, and an error breakdown by class, caption length, hashtag-only captions and
    repaired rows.
- **Propagation.** Recompute the within-content tail penalty and the per-vertical effects three ways:
  - with LLM tags;
  - with platform labels;
  - with a misclassification correction based on the audit confusion matrix (see the correction method above).
  Report how far the headline moves.

**Engineering.**
- Key from `GEMINI_API_KEY`, never committed (`.env` is gitignored).
- A pinned model (`gemini-3.1-flash-lite`) at temperature 0, with JSON-schema output (enums), 20 captions per call
  (376 calls).
- Every call is cached on disk, keyed by a hash of model, prompt version and input.
- The resulting tag table (7,583 rows) is committed as derived data, so the pipeline runs without a key.

## 9. Cleaning

- Drop exact duplicate rows. For same-millisecond conflicts, keep the row with the max play time.
- Analyse tab 1 only; it holds 99.3% of random impressions.
- Videos with missing duration: keep for MWT, skip and feedback; exclude from long-view and completion.
- The pre-period starts on 04-09, whatever the file name says.
- Never use `video_features_statistic` for tiers or segments.
- Supplementary captions: parsed line by line, with 35 repaired rows flagged.

## 10. Pipeline and tests

```
python -m pipeline            # runs every step in order
  01_ingest   raw CSV/Parquet -> DuckDB              (sql/01_ingest.sql)
  02_clean    dedupe, tab filter, tiers, covariates  (sql/02_clean.sql)
  03_metrics  per-impression metrics, daily series  (sql/03_metrics.sql)
  04_causal   layer 1 to 4 estimates, SRM, CUPED, power
  05_segments forest-plot data, BH
  06_llm      tags (cached), validation, propagation
  07_export   JSON/CSV -> site/src/data/
tests/
  test_aa.py     user-split A/A and video-split A/A (calibrates the two-way SEs): false-positive rate within
                 binomial bounds of 5%, p-values uniform
  test_cuped.py  no mean shift across splits; variance reduced; synthetic known-effect case
  test_tags.py   kappa against statsmodels; the correction recovers known class effects
```

- Report every estimate with an absolute and a relative delta, a 95% CI (analytic, delta method or cluster-robust;
  bootstrap only where stated), and n.

## 11. Open decisions

1. **Lever and tiers:** tail (bottom fifth) vs head (top fifth) by pre-period distribution, with variants A, B and C.
2. **Design:** layer 1 impression-level as the primary estimate; layer 2 constructed user split as the readout, clearly labelled.
3. **MWT cap:** pre-period p99.9 (373.5s, recommended) or 180s.
4. **Thresholds:** -1.0% MWT, +1.0 pp early skip, +5% hates, and the ship / A/B test / don't-ship rule.
5. **AI component:** Gemini tags (vertical, format, commercial) validated against platform labels and audit labels.
   Resolved by decision 5: a second model labels from the Chinese text directly, with no translation step.
6. **Licence:** code MIT; derived data CC BY-SA 4.0 (inherited).
7. **KuaiRand-1K:** not needed now. Only worth it if a full-session view of the random-vs-recommended comparison becomes essential.
