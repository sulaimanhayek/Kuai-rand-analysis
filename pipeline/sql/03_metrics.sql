-- Step 03: per-impression metrics for tab-1 impressions in the experiment period (04-22 to 05-08).
-- src = 'random' (slot replaced by a uniform draw from the pool) or 'rec' (recommended, pool videos only).

CREATE OR REPLACE TABLE imp AS
WITH both_logs AS (
    SELECT 'random' AS src, * FROM log_rand WHERE tab = 1
    UNION ALL
    SELECT 'rec', * FROM log_rec WHERE tab = 1
)
SELECT
    l.src,
    l.user_id,
    l.video_id,
    l.date,
    l.time_ms,
    CASE WHEN l.play_time_ms >= 3000 THEN least(l.play_time_ms, p.cap_ms) ELSE 0 END / 1000.0 AS mwt,
    CASE WHEN l.play_time_ms >= 3000 THEN least(l.play_time_ms, 180000) ELSE 0 END / 1000.0 AS mwt_cap180,
    CASE WHEN l.play_time_ms < 3000 THEN 1.0 ELSE 0.0 END                  AS early_skip,
    CASE WHEN v.duration_s IS NULL THEN NULL ELSE CAST(l.long_view AS DOUBLE) END AS long_view,
    CAST(l.is_like AS DOUBLE)                                             AS liked,
    CAST(l.is_follow AS DOUBLE)                                           AS followed,
    CAST(l.is_hate AS DOUBLE)                                             AS hated,
    CAST(l.is_profile_enter AS DOUBLE)                                    AS profile_entered,
    v.fifth,
    v.tier,
    v.l1_en,
    v.duration_bucket,
    date_diff('day', DATE '2022-04-22', strptime(CAST(l.date AS VARCHAR), '%Y%m%d')::DATE) AS day_index,
    CASE WHEN l.date <= 20220430 THEN 1 ELSE 2 END                        AS half
FROM both_logs l
JOIN videos v USING (video_id)
CROSS JOIN params p;

-- Order of each random impression within the user (1 = the user's first random slot).
CREATE OR REPLACE TABLE imp_random AS
SELECT
    *,
    row_number() OVER (PARTITION BY user_id ORDER BY time_ms, video_id) AS nth_random,
    row_number() OVER (PARTITION BY video_id ORDER BY time_ms, user_id) AS nth_for_video
FROM imp
WHERE src = 'random';

-- Next-impression carryover inputs: the user's next logged tab-1 impression (random or recommended).
CREATE OR REPLACE TABLE imp_next AS
WITH seq AS (
    SELECT
        src, user_id, video_id, time_ms, tier, mwt, early_skip,
        lead(time_ms) OVER w                                              AS next_time_ms,
        lead(mwt) OVER w                                                  AS next_mwt,
        lead(early_skip) OVER w                                           AS next_skip,
        lead(src) OVER w                                                  AS next_src
    FROM imp
    WINDOW w AS (PARTITION BY user_id ORDER BY time_ms, src, video_id)
)
SELECT *, (next_time_ms - time_ms) / 1000.0 AS gap_s
FROM seq
WHERE src = 'random';
