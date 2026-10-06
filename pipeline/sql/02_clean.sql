-- Step 02: deduplicated logs, the MWT cap, video tiers and user covariates.
-- Assumes 01_ingest.sql has run and the table l1_names (Chinese -> English category) exists.

-- Logs. One row per (user, video, millisecond). Exact duplicates collapse; for conflicting records
-- the longest play wins, with the remaining columns as a deterministic tiebreak.
CREATE OR REPLACE TABLE log_pre AS
SELECT * EXCLUDE (rn) FROM (
    SELECT *, row_number() OVER (
        PARTITION BY user_id, video_id, time_ms
        ORDER BY play_time_ms DESC, long_view DESC, is_like DESC, is_follow DESC, is_hate DESC,
                 is_click DESC, is_comment DESC, is_forward DESC, is_profile_enter DESC, tab
    ) AS rn
    FROM raw_log_standard_early
    WHERE date BETWEEN 20220409 AND 20220421
)
WHERE rn = 1;

CREATE OR REPLACE TABLE log_rec AS
SELECT * EXCLUDE (rn) FROM (
    SELECT *, row_number() OVER (
        PARTITION BY user_id, video_id, time_ms
        ORDER BY play_time_ms DESC, long_view DESC, is_like DESC, is_follow DESC, is_hate DESC,
                 is_click DESC, is_comment DESC, is_forward DESC, is_profile_enter DESC, tab
    ) AS rn
    FROM raw_log_standard_late
)
WHERE rn = 1;

CREATE OR REPLACE TABLE log_rand AS
SELECT * EXCLUDE (rn) FROM (
    SELECT *, row_number() OVER (
        PARTITION BY user_id, video_id, time_ms
        ORDER BY play_time_ms DESC, long_view DESC, is_like DESC, is_follow DESC, is_hate DESC,
                 is_click DESC, is_comment DESC, is_forward DESC, is_profile_enter DESC, tab
    ) AS rn
    FROM raw_log_random
)
WHERE rn = 1;

-- MWT cap: pre-period p99.9 of tab-1 recommended play time. Fixed before any outcome is looked at.
CREATE OR REPLACE TABLE params AS
SELECT
    quantile_cont(play_time_ms, 0.999)                          AS cap_ms,
    avg(CASE WHEN play_time_ms > 180000 THEN 1.0 ELSE 0 END)     AS pre_share_over_180s,
    count(*)                                                    AS pre_tab1_impressions
FROM log_pre
WHERE tab = 1;

-- Video tiers: fifths of pre-period recommended impressions (all tabs). Explicit thresholds from
-- quantile_disc, so videos with the same count always land in the same fifth.
CREATE OR REPLACE TABLE tier_thresholds AS
WITH pre_counts AS (
    SELECT b.video_id, count(p.video_id) AS pre_impressions
    FROM raw_video_basic b
    LEFT JOIN log_pre p USING (video_id)
    GROUP BY b.video_id
)
SELECT
    quantile_disc(pre_impressions, 0.2) AS q20,
    quantile_disc(pre_impressions, 0.4) AS q40,
    quantile_disc(pre_impressions, 0.6) AS q60,
    quantile_disc(pre_impressions, 0.8) AS q80
FROM pre_counts;

CREATE OR REPLACE TABLE videos AS
WITH pre_counts AS (
    SELECT b.video_id, count(p.video_id) AS pre_impressions
    FROM raw_video_basic b
    LEFT JOIN log_pre p USING (video_id)
    GROUP BY b.video_id
),
random_draws AS (
    SELECT video_id, count(*) AS random_impressions, min(date) AS first_random_date,
           max(date) AS last_random_date
    FROM log_rand
    WHERE tab = 1
    GROUP BY video_id
)
SELECT
    b.video_id,
    b.author_id,
    b.video_duration / 1000.0                                   AS duration_s,
    pc.pre_impressions,
    CASE WHEN pc.pre_impressions <= t.q20 THEN 1
         WHEN pc.pre_impressions <= t.q40 THEN 2
         WHEN pc.pre_impressions <= t.q60 THEN 3
         WHEN pc.pre_impressions <= t.q80 THEN 4
         ELSE 5 END                                             AS fifth,
    CASE WHEN pc.pre_impressions <= t.q20 THEN 'tail'
         WHEN pc.pre_impressions > t.q80 THEN 'head'
         ELSE 'mid' END                                         AS tier,
    CASE WHEN b.video_duration IS NULL THEN 'missing'
         WHEN b.video_duration < 15000 THEN '1. under 15s'
         WHEN b.video_duration < 30000 THEN '2. 15-30s'
         WHEN b.video_duration < 60000 THEN '3. 30-60s'
         WHEN b.video_duration < 180000 THEN '4. 1-3 min'
         ELSE '5. 3 min+' END                                   AS duration_bucket,
    c.first_level_category_name                                 AS l1_zh,
    coalesce(n.l1_en, 'Unknown')                                AS l1_en,
    c.first_level_category_prob                                 AS l1_prob,
    c.second_level_category_name                                AS l2_zh,
    cap.caption,
    cap.show_cover_text,
    coalesce(cap.parse_repaired, false)                         AS caption_repaired,
    coalesce(d.random_impressions, 0)                           AS random_impressions,
    d.first_random_date,
    d.last_random_date
FROM raw_video_basic b
JOIN pre_counts pc USING (video_id)
CROSS JOIN tier_thresholds t
LEFT JOIN raw_categories c USING (video_id)
LEFT JOIN l1_names n ON n.l1_zh = c.first_level_category_name
LEFT JOIN raw_captions cap USING (video_id)
LEFT JOIN random_draws d USING (video_id);

-- User covariates from the pre-period (tab 1). Users with no pre-period tab-1 data get NULLs here;
-- the analysis fills them with the mean and sets has_pre = 0.
CREATE OR REPLACE TABLE users AS
WITH pre AS (
    SELECT
        user_id,
        count(*)                                                AS pre_n,
        avg(CASE WHEN play_time_ms >= 3000 THEN least(play_time_ms, (SELECT cap_ms FROM params)) ELSE 0 END)
            / 1000.0                                            AS pre_mwt,
        avg(CASE WHEN play_time_ms < 3000 THEN 1.0 ELSE 0 END)  AS pre_skip,
        avg(long_view)                                          AS pre_long_view,
        avg(is_like)                                            AS pre_like,
        avg(is_hate)                                            AS pre_hate,
        avg(is_follow)                                          AS pre_follow,
        avg(is_profile_enter)                                   AS pre_profile
    FROM log_pre
    WHERE tab = 1
    GROUP BY user_id
)
SELECT
    u.user_id,
    u.user_active_degree,
    u.is_video_author,
    u.follow_user_num_range,
    u.register_days,
    p.* EXCLUDE (user_id),
    CASE WHEN p.user_id IS NULL THEN 0 ELSE 1 END               AS has_pre
FROM raw_user_features u
LEFT JOIN pre p USING (user_id);
