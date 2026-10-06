-- Load raw KuaiRand-Pure CSVs into DuckDB as-is. Typing and cleaning happen in later steps.
CREATE OR REPLACE TABLE raw_log_standard_early AS
SELECT * FROM read_csv('data/raw/KuaiRand-Pure/data/log_standard_4_08_to_4_21_pure.csv', header = true);

CREATE OR REPLACE TABLE raw_log_standard_late AS
SELECT * FROM read_csv('data/raw/KuaiRand-Pure/data/log_standard_4_22_to_5_08_pure.csv', header = true);

CREATE OR REPLACE TABLE raw_log_random AS
SELECT * FROM read_csv('data/raw/KuaiRand-Pure/data/log_random_4_22_to_5_08_pure.csv', header = true);

CREATE OR REPLACE TABLE raw_user_features AS
SELECT * FROM read_csv('data/raw/KuaiRand-Pure/data/user_features_pure.csv', header = true);

CREATE OR REPLACE TABLE raw_video_basic AS
SELECT * FROM read_csv('data/raw/KuaiRand-Pure/data/video_features_basic_pure.csv', header = true);

CREATE OR REPLACE TABLE raw_video_stats AS
SELECT * FROM read_csv('data/raw/KuaiRand-Pure/data/video_features_statistic_pure.csv', header = true);

-- Supplementary content metadata (fetched by pipeline/fetch_supplementary.py).
CREATE OR REPLACE TABLE raw_captions AS
SELECT * FROM read_parquet('data/raw/supplementary/captions_pure.parquet');

CREATE OR REPLACE TABLE raw_categories AS
SELECT * FROM read_parquet('data/raw/supplementary/categories_pure.parquet');
