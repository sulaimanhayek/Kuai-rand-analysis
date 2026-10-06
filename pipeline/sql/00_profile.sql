-- Phase 1 profiling queries. Each block is a named query run by pipeline/profile.py,
-- which writes the results to notes/01_data_profile_tables.md.
-- Assumes pipeline/sql/01_ingest.sql has been run.

-- name: log_overview
WITH logs AS (
    SELECT 'standard_pre (4/08-4/21 file)' AS log, * FROM raw_log_standard_early
    UNION ALL
    SELECT 'standard_exp (4/22-5/08 file)', * FROM raw_log_standard_late
    UNION ALL
    SELECT 'random_exp (4/22-5/08 file)', * FROM raw_log_random
)
SELECT
    log,
    count(*)                                   AS n_rows,
    count(DISTINCT user_id)                    AS n_users,
    count(DISTINCT video_id)                   AS n_videos,
    min(date)                                  AS first_date,
    max(date)                                  AS last_date,
    count(DISTINCT date)                       AS n_dates,
    CAST(sum(is_rand) AS BIGINT)               AS n_is_rand,
    count(DISTINCT tab)                        AS n_tabs
FROM logs
GROUP BY log
ORDER BY log;

-- name: log_duplicates
WITH logs AS (
    SELECT 'standard_pre' AS log, * FROM raw_log_standard_early
    UNION ALL SELECT 'standard_exp', * FROM raw_log_standard_late
    UNION ALL SELECT 'random_exp', * FROM raw_log_random
),
exact AS (
    SELECT log, count(*) AS n_rows FROM logs GROUP BY log
),
distinct_rows AS (
    SELECT log, count(*) AS n_distinct FROM (SELECT DISTINCT * FROM logs) GROUP BY log
),
same_event AS (
    -- Same user, video and millisecond timestamp but a different play time: conflicting records.
    SELECT log, count(*) AS n_conflicting_events
    FROM (
        SELECT log, user_id, video_id, time_ms
        FROM logs
        GROUP BY ALL
        HAVING count(DISTINCT play_time_ms) > 1
    )
    GROUP BY log
)
SELECT
    e.log,
    e.n_rows,
    e.n_rows - d.n_distinct                    AS n_exact_duplicate_rows,
    round(100.0 * (e.n_rows - d.n_distinct) / e.n_rows, 2) AS pct_exact_duplicates,
    coalesce(s.n_conflicting_events, 0)        AS n_conflicting_events
FROM exact e
JOIN distinct_rows d USING (log)
LEFT JOIN same_event s USING (log)
ORDER BY e.log;

-- name: daily_volume
WITH pre AS (
    SELECT date, count(*) AS n, count(DISTINCT user_id) AS users FROM raw_log_standard_early GROUP BY date
),
std AS (
    SELECT date, count(*) AS n, count(DISTINCT user_id) AS users FROM raw_log_standard_late GROUP BY date
),
rnd AS (
    SELECT date, count(*) AS n, count(DISTINCT user_id) AS users FROM raw_log_random GROUP BY date
)
SELECT
    coalesce(pre.date, std.date, rnd.date)     AS date,
    pre.n                                      AS standard_pre_rows,
    std.n                                      AS standard_exp_rows,
    rnd.n                                      AS random_rows,
    rnd.users                                  AS random_users,
    round(rnd.n / (rnd.n + std.n), 3)          AS random_share_of_logged
FROM pre
FULL JOIN std USING (date)
FULL JOIN rnd USING (date)
ORDER BY date;

-- name: tab_distribution
SELECT
    tab,
    count(*) FILTER (WHERE src = 'standard_pre') AS standard_pre,
    count(*) FILTER (WHERE src = 'standard_exp') AS standard_exp,
    count(*) FILTER (WHERE src = 'random_exp')   AS random_exp
FROM (
    SELECT tab, 'standard_pre' AS src FROM raw_log_standard_early
    UNION ALL SELECT tab, 'standard_exp' FROM raw_log_standard_late
    UNION ALL SELECT tab, 'random_exp' FROM raw_log_random
)
GROUP BY tab
ORDER BY tab;

-- name: random_exposures_per_user
WITH per_user AS (
    SELECT user_id, count(*) AS n, count(DISTINCT date) AS active_days
    FROM raw_log_random
    GROUP BY user_id
)
SELECT
    count(*)                                   AS n_users,
    round(avg(n), 1)                           AS mean_impressions,
    quantile_cont(n, 0.10)                     AS p10,
    quantile_cont(n, 0.50)                     AS p50,
    quantile_cont(n, 0.90)                     AS p90,
    max(n)                                     AS max_impressions,
    round(avg(active_days), 1)                 AS mean_days_with_random,
    quantile_cont(active_days, 0.50)           AS p50_days_with_random
FROM per_user;

-- name: random_exposures_per_video
WITH per_video AS (
    SELECT v.video_id, count(r.video_id) AS n
    FROM raw_video_basic v
    LEFT JOIN raw_log_random r USING (video_id)
    GROUP BY v.video_id
)
SELECT
    count(*)                                   AS n_videos,
    round(avg(n), 1)                           AS mean_impressions,
    round(stddev(n), 1)                        AS sd_impressions,
    round(sqrt(avg(n)), 1)                     AS poisson_sd_if_uniform,
    quantile_cont(n, 0.05)                     AS p05,
    quantile_cont(n, 0.50)                     AS p50,
    quantile_cont(n, 0.95)                     AS p95,
    min(n)                                     AS min_impressions,
    max(n)                                     AS max_impressions
FROM per_video;

-- name: random_pool_size_by_date
SELECT date, count(DISTINCT video_id) AS distinct_videos_drawn
FROM raw_log_random
GROUP BY date
ORDER BY date;

-- name: video_basic_overview
SELECT
    count(*)                                   AS n_videos,
    count(DISTINCT author_id)                  AS n_authors,
    min(upload_dt)                             AS first_upload,
    max(upload_dt)                             AS last_upload,
    count(*) FILTER (WHERE tag IS NULL)        AS null_tag,
    count(*) FILTER (WHERE video_duration IS NULL) AS null_duration,
    count(*) FILTER (WHERE music_type IS NULL) AS null_music_type,
    count(*) FILTER (WHERE video_type = 'AD')  AS n_ads,
    count(DISTINCT visible_status)             AS n_visible_status_values
FROM raw_video_basic;

-- name: video_upload_dates
SELECT upload_dt, count(*) AS n_videos
FROM raw_video_basic
GROUP BY upload_dt
ORDER BY upload_dt;

-- name: videos_per_author
WITH per_author AS (
    SELECT author_id, count(*) AS n FROM raw_video_basic GROUP BY author_id
)
SELECT
    count(*)                                   AS n_authors,
    round(avg(n), 2)                           AS mean_videos,
    max(n)                                     AS max_videos,
    count(*) FILTER (WHERE n = 1)              AS single_video_authors,
    round(100.0 * count(*) FILTER (WHERE n = 1) / count(*), 1) AS pct_single_video
FROM per_author;

-- name: video_duration_seconds
SELECT
    round(quantile_cont(video_duration, 0.05) / 1000, 1) AS p05,
    round(quantile_cont(video_duration, 0.25) / 1000, 1) AS p25,
    round(quantile_cont(video_duration, 0.50) / 1000, 1) AS p50,
    round(quantile_cont(video_duration, 0.75) / 1000, 1) AS p75,
    round(quantile_cont(video_duration, 0.95) / 1000, 1) AS p95
FROM raw_video_basic;

-- name: missing_duration_in_logs
SELECT
    'random_exp'                               AS log,
    count(*) FILTER (WHERE duration_ms = 0)    AS rows_zero_duration,
    count(DISTINCT video_id) FILTER (WHERE duration_ms = 0) AS videos_zero_duration
FROM raw_log_random;

-- name: tags
WITH exploded AS (
    SELECT video_id, unnest(string_split(tag, ',')) AS tag_id
    FROM raw_video_basic
    WHERE tag IS NOT NULL
)
SELECT
    count(DISTINCT tag_id)                     AS n_distinct_tags,
    (SELECT count(*) FROM raw_video_basic WHERE len(string_split(tag, ',')) = 1) AS videos_with_1_tag,
    (SELECT count(*) FROM raw_video_basic WHERE len(string_split(tag, ',')) >= 2) AS videos_with_2plus_tags,
    (SELECT max(cnt) FROM (SELECT count(*) AS cnt FROM exploded GROUP BY tag_id)) AS videos_in_largest_tag
FROM exploded;

-- name: user_activity_degree
SELECT user_active_degree, count(*) AS n_users, round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
FROM raw_user_features
GROUP BY user_active_degree
ORDER BY n_users DESC;

-- name: user_tenure
SELECT register_days_range, count(*) AS n_users, round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
FROM raw_user_features
GROUP BY register_days_range
ORDER BY min(register_days);

-- name: user_flag_values
SELECT 'is_live_streamer' AS col, is_live_streamer AS value, count(*) AS n_users FROM raw_user_features GROUP BY ALL
UNION ALL SELECT 'is_video_author', is_video_author, count(*) FROM raw_user_features GROUP BY ALL
UNION ALL SELECT 'is_lowactive_period', is_lowactive_period, count(*) FROM raw_user_features GROUP BY ALL
ORDER BY col, value;

-- name: join_coverage
WITH logs AS (
    SELECT 'standard_pre' AS log, user_id, video_id FROM raw_log_standard_early
    UNION ALL SELECT 'standard_exp', user_id, video_id FROM raw_log_standard_late
    UNION ALL SELECT 'random_exp', user_id, video_id FROM raw_log_random
)
SELECT
    l.log,
    count(*)                                   AS n_rows,
    round(avg(CASE WHEN u.user_id IS NOT NULL THEN 1 ELSE 0 END), 4) AS user_features_coverage,
    round(avg(CASE WHEN v.video_id IS NOT NULL THEN 1 ELSE 0 END), 4) AS video_basic_coverage,
    round(avg(CASE WHEN s.video_id IS NOT NULL THEN 1 ELSE 0 END), 4) AS video_stats_coverage
FROM logs l
LEFT JOIN raw_user_features u USING (user_id)
LEFT JOIN raw_video_basic v USING (video_id)
LEFT JOIN raw_video_stats s USING (video_id)
GROUP BY l.log
ORDER BY l.log;

-- name: random_users_with_pre_period
SELECT
    count(*)                                   AS random_users,
    count(*) FILTER (WHERE user_id IN (SELECT user_id FROM raw_log_standard_early)) AS with_pre_period_logs,
    count(*) FILTER (WHERE user_id IN (SELECT user_id FROM raw_log_standard_early WHERE tab = 1)) AS with_pre_period_tab1
FROM (SELECT DISTINCT user_id FROM raw_log_random);

-- name: balance_by_activity
-- If items are drawn at random, item attributes should not vary with user attributes.
SELECT
    u.user_active_degree,
    count(*)                                   AS random_impressions,
    round(avg(v.video_duration) / 1000, 1)     AS mean_item_duration_s,
    round(avg(CASE WHEN v.video_type = 'AD' THEN 1 ELSE 0 END), 4) AS ad_share,
    round(avg(CASE WHEN list_contains(string_split(v.tag, ','), '39') THEN 1 ELSE 0 END), 4) AS tag39_share,
    round(avg(CASE WHEN v.upload_type = 'LongImport' THEN 1 ELSE 0 END), 4) AS longimport_share
FROM raw_log_random r
JOIN raw_user_features u USING (user_id)
JOIN raw_video_basic v USING (video_id)
GROUP BY u.user_active_degree
HAVING count(*) > 10000
ORDER BY random_impressions DESC;

-- name: balance_by_date
SELECT
    CAST(r.date AS VARCHAR)                    AS date,
    round(avg(v.video_duration) / 1000, 1)     AS mean_item_duration_s,
    round(avg(CASE WHEN list_contains(string_split(v.tag, ','), '39') THEN 1 ELSE 0 END), 4) AS tag39_share
FROM raw_log_random r
JOIN raw_video_basic v USING (video_id)
GROUP BY 1
ORDER BY 1;

-- name: engagement_by_source_tab1
-- Meaningful watch time (draft definition): play time in seconds if the play lasted >= 3s, capped at 180s, else 0.
SELECT
    src,
    count(*)                                                   AS impressions,
    round(avg(CASE WHEN play_time_ms >= 3000 THEN least(play_time_ms, 180000) ELSE 0 END) / 1000, 2) AS meaningful_watch_s,
    round(avg(play_time_ms) / 1000, 2)                         AS mean_play_s,
    round(median(play_time_ms) / 1000, 2)                      AS median_play_s,
    round(avg(CASE WHEN play_time_ms < 3000 THEN 1 ELSE 0 END), 3) AS early_skip_rate,
    round(avg(is_click), 3)                                    AS valid_play_rate,
    round(avg(long_view), 3)                                   AS long_view_rate,
    round(1000 * avg(is_like), 2)                              AS likes_per_1k,
    round(1000 * avg(is_follow), 3)                            AS follows_per_1k,
    round(1000 * avg(is_hate), 3)                              AS hates_per_1k
FROM (
    SELECT *, 'standard_pre' AS src FROM raw_log_standard_early
    UNION ALL SELECT *, 'standard_exp' FROM raw_log_standard_late
    UNION ALL SELECT *, 'random_exp' FROM raw_log_random
)
WHERE tab = 1
GROUP BY src
ORDER BY src;

-- name: pre_period_exposure_concentration
WITH per_video AS (
    SELECT v.video_id, count(l.video_id) AS pre_impressions
    FROM raw_video_basic v
    LEFT JOIN raw_log_standard_early l USING (video_id)
    GROUP BY v.video_id
),
ranked AS (
    SELECT
        pre_impressions,
        row_number() OVER (ORDER BY pre_impressions DESC) AS rk,
        count(*) OVER ()                                  AS n_videos,
        sum(pre_impressions) OVER ()                      AS total,
        sum(pre_impressions) OVER (ORDER BY pre_impressions DESC ROWS UNBOUNDED PRECEDING) AS cum
    FROM per_video
)
SELECT
    max(n_videos)                                              AS n_videos,
    count(*) FILTER (WHERE pre_impressions = 0)                AS videos_with_zero,
    quantile_cont(pre_impressions, 0.5)                        AS median_pre_impressions,
    round(max(cum) FILTER (WHERE rk <= 0.01 * n_videos) / max(total), 3) AS top1pct_share,
    round(max(cum) FILTER (WHERE rk <= 0.10 * n_videos) / max(total), 3) AS top10pct_share,
    round(max(cum) FILTER (WHERE rk <= 0.20 * n_videos) / max(total), 3) AS top20pct_share,
    round(max(cum) FILTER (WHERE rk <= 0.50 * n_videos) / max(total), 3) AS top50pct_share
FROM ranked;

-- name: engagement_by_pre_exposure_quintile
-- Quintile 1 = least distributed in the pre-period (tail), 5 = most distributed (head).
WITH per_video AS (
    SELECT v.video_id, count(l.video_id) AS pre_impressions
    FROM raw_video_basic v
    LEFT JOIN raw_log_standard_early l USING (video_id)
    GROUP BY v.video_id
),
quintiles AS (
    SELECT video_id, pre_impressions, ntile(5) OVER (ORDER BY pre_impressions, video_id) AS quintile
    FROM per_video
),
exp_logs AS (
    SELECT *, 'random' AS src FROM raw_log_random WHERE tab = 1
    UNION ALL
    SELECT *, 'recommended' AS src FROM raw_log_standard_late WHERE tab = 1
)
SELECT
    e.src,
    q.quintile,
    min(q.pre_impressions)                                     AS pre_imps_min,
    max(q.pre_impressions)                                     AS pre_imps_max,
    count(*)                                                   AS impressions,
    round(avg(CASE WHEN e.play_time_ms >= 3000 THEN least(e.play_time_ms, 180000) ELSE 0 END) / 1000, 2) AS meaningful_watch_s,
    round(avg(CASE WHEN e.play_time_ms < 3000 THEN 1 ELSE 0 END), 3) AS early_skip_rate,
    round(avg(e.long_view), 3)                                 AS long_view_rate,
    round(1000 * avg(e.is_like), 2)                            AS likes_per_1k,
    round(1000 * avg(e.is_follow), 3)                          AS follows_per_1k,
    round(1000 * avg(e.is_hate), 3)                            AS hates_per_1k,
    round(avg(e.duration_ms) FILTER (WHERE e.duration_ms > 0) / 1000, 1) AS mean_duration_s
FROM exp_logs e
JOIN quintiles q USING (video_id)
GROUP BY e.src, q.quintile
ORDER BY e.src, q.quintile;

-- name: tail_gap_by_date
WITH per_video AS (
    SELECT v.video_id, count(l.video_id) AS pre_impressions
    FROM raw_video_basic v
    LEFT JOIN raw_log_standard_early l USING (video_id)
    GROUP BY v.video_id
),
tiers AS (
    SELECT video_id, ntile(5) OVER (ORDER BY pre_impressions, video_id) AS quintile FROM per_video
),
mw AS (
    SELECT r.date, t.quintile,
           CASE WHEN r.play_time_ms >= 3000 THEN least(r.play_time_ms, 180000) ELSE 0 END / 1000.0 AS mwt
    FROM raw_log_random r
    JOIN tiers t USING (video_id)
    WHERE r.tab = 1
)
SELECT
    CAST(date AS VARCHAR)                                      AS date,
    count(*)                                                   AS impressions,
    round(avg(mwt) FILTER (WHERE quintile = 5), 2)             AS head_mwt_s,
    round(avg(mwt) FILTER (WHERE quintile = 1), 2)             AS tail_mwt_s,
    round(avg(mwt) FILTER (WHERE quintile = 1) / avg(mwt) FILTER (WHERE quintile = 5) - 1, 3) AS tail_vs_head
FROM mw
GROUP BY 1
ORDER BY 1;

-- name: tail_gap_by_exposure_count
-- Does the tail penalty grow as a user sees more random items? (exploration fatigue / novelty check)
WITH per_video AS (
    SELECT v.video_id, count(l.video_id) AS pre_impressions
    FROM raw_video_basic v
    LEFT JOIN raw_log_standard_early l USING (video_id)
    GROUP BY v.video_id
),
tiers AS (
    SELECT video_id, ntile(5) OVER (ORDER BY pre_impressions, video_id) AS quintile FROM per_video
),
numbered AS (
    SELECT r.*, t.quintile,
           row_number() OVER (PARTITION BY r.user_id ORDER BY r.time_ms) AS k,
           CASE WHEN r.play_time_ms >= 3000 THEN least(r.play_time_ms, 180000) ELSE 0 END / 1000.0 AS mwt
    FROM raw_log_random r
    JOIN tiers t USING (video_id)
    WHERE r.tab = 1
)
SELECT
    CASE WHEN k <= 5 THEN '1. 1-5' WHEN k <= 20 THEN '2. 6-20' WHEN k <= 50 THEN '3. 21-50'
         WHEN k <= 100 THEN '4. 51-100' ELSE '5. 101+' END        AS nth_random_exposure,
    count(*)                                                   AS impressions,
    round(avg(mwt), 2)                                         AS all_mwt_s,
    round(avg(mwt) FILTER (WHERE quintile = 1) / avg(mwt) FILTER (WHERE quintile = 5) - 1, 3) AS tail_vs_head
FROM numbered
GROUP BY 1
ORDER BY 1;

-- name: tier_composition_checks
-- Would excluding missing-duration videos, or videos leaving the random pool early, unbalance the tiers?
WITH per_video AS (
    SELECT v.video_id, v.video_duration, count(l.video_id) AS pre_impressions
    FROM raw_video_basic v
    LEFT JOIN raw_log_standard_early l USING (video_id)
    GROUP BY v.video_id, v.video_duration
),
tiers AS (
    SELECT *, ntile(5) OVER (ORDER BY pre_impressions, video_id) AS quintile FROM per_video
),
last_draw AS (
    SELECT video_id, max(date) AS last_random_date, count(*) AS random_impressions
    FROM raw_log_random
    GROUP BY video_id
)
SELECT
    t.quintile,
    count(*)                                                   AS n_videos,
    count(*) FILTER (WHERE t.video_duration IS NULL)           AS missing_duration,
    count(*) FILTER (WHERE d.last_random_date < 20220505)      AS last_drawn_before_may5,
    round(avg(d.random_impressions), 1)                        AS mean_random_impressions
FROM tiers t
LEFT JOIN last_draw d USING (video_id)
GROUP BY t.quintile
ORDER BY t.quintile;

-- name: content_coverage
-- Supplementary captions and platform categories (Zenodo 18159199), restricted to the Pure pool.
SELECT
    count(*)                                                   AS n_videos,
    count(*) FILTER (WHERE length(trim(c.caption)) > 0)        AS with_caption,
    count(*) FILTER (WHERE length(trim(c.show_cover_text)) > 0) AS with_cover_text,
    count(*) FILTER (WHERE c.parse_repaired)                   AS caption_rows_repaired,
    quantile_disc(length(c.caption), 0.1)                      AS caption_chars_p10,
    quantile_disc(length(c.caption), 0.5)                      AS caption_chars_p50,
    quantile_disc(length(c.caption), 0.9)                      AS caption_chars_p90,
    count(k.first_level_category_name)                         AS with_l1_category,
    count(DISTINCT k.first_level_category_name)                AS n_l1,
    count(DISTINCT k.second_level_category_name)               AS n_l2,
    count(DISTINCT k.third_level_category_name) FILTER (WHERE k.third_level_category_name <> 'UNKNOWN') AS n_l3,
    round(quantile_cont(k.first_level_category_prob, 0.1), 3)  AS l1_prob_p10,
    round(quantile_cont(k.first_level_category_prob, 0.5), 3)  AS l1_prob_p50,
    -- Join check: caption-file duration should equal video_features_basic duration.
    count(*) FILTER (WHERE c.duration = v.video_duration)      AS duration_matches,
    count(*) FILTER (WHERE c.duration IS NOT NULL AND v.video_duration IS NOT NULL) AS duration_comparable
FROM raw_video_basic v
LEFT JOIN raw_captions c USING (video_id)
LEFT JOIN raw_categories k USING (video_id);

-- name: category_l1_profile
-- Platform top-level category (model-predicted, Chinese names as shipped): pool share, tier mix,
-- and engagement under random exposure (tab 1).
WITH per_video AS (
    SELECT v.video_id, count(l.video_id) AS pre_impressions
    FROM raw_video_basic v
    LEFT JOIN raw_log_standard_early l USING (video_id)
    GROUP BY v.video_id
),
tiers AS (
    SELECT video_id, ntile(5) OVER (ORDER BY pre_impressions, video_id) AS quintile FROM per_video
),
vids AS (
    SELECT t.video_id, t.quintile, coalesce(k.first_level_category_name, '(none)') AS l1
    FROM tiers t LEFT JOIN raw_categories k USING (video_id)
),
rnd AS (
    SELECT r.video_id,
           count(*) AS n,
           sum(CASE WHEN r.play_time_ms >= 3000 THEN least(r.play_time_ms, 180000) ELSE 0 END) / 1000.0 AS mwt_sum,
           sum(CASE WHEN r.play_time_ms < 3000 THEN 1 ELSE 0 END) AS skips
    FROM raw_log_random r WHERE r.tab = 1 GROUP BY r.video_id
)
SELECT
    v.l1                                                       AS l1_category,
    count(*)                                                   AS n_videos,
    round(100.0 * count(*) / sum(count(*)) OVER (), 1)         AS pct_of_pool,
    round(100.0 * avg(CASE WHEN v.quintile = 1 THEN 1 ELSE 0 END), 1) AS pct_in_tail_q1,
    round(100.0 * avg(CASE WHEN v.quintile = 5 THEN 1 ELSE 0 END), 1) AS pct_in_head_q5,
    sum(r.n)                                                   AS random_impressions,
    round(sum(r.mwt_sum) / sum(r.n), 2)                        AS random_mwt_s,
    round(sum(r.skips) / sum(r.n), 3)                          AS random_skip_rate
FROM vids v
LEFT JOIN rnd r USING (video_id)
GROUP BY v.l1
ORDER BY n_videos DESC;

-- name: play_time_tail
-- Inputs for the meaningful-watch cap: how much of each log would a cap truncate?
WITH logs AS (
    SELECT 'standard_pre' AS log, play_time_ms FROM raw_log_standard_early WHERE tab = 1
    UNION ALL SELECT 'standard_exp', play_time_ms FROM raw_log_standard_late WHERE tab = 1
    UNION ALL SELECT 'random_exp', play_time_ms FROM raw_log_random WHERE tab = 1
)
SELECT
    log,
    count(*)                                                   AS impressions,
    round(quantile_cont(play_time_ms, 0.99) / 1000, 1)         AS p99_s,
    round(quantile_cont(play_time_ms, 0.999) / 1000, 1)        AS p999_s,
    round(max(play_time_ms) / 1000, 1)                         AS max_s,
    round(avg(CASE WHEN play_time_ms > 180000 THEN 1.0 ELSE 0 END), 4) AS share_over_180s
FROM logs
GROUP BY log
ORDER BY log;
