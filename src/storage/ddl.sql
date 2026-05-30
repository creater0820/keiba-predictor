-- keiba_predictor のドメインテーブル DDL（生 SQL）。
-- SQLAlchemy の models.py と列を一致させること。Turso(libSQL) でも SQLite でも使える。
-- マイグレーションは行わない（CREATE TABLE IF NOT EXISTS）。スキーマ変更時は作り直し。

CREATE TABLE IF NOT EXISTS races (
    race_id          TEXT PRIMARY KEY,
    date             TEXT,
    venue            TEXT,
    race_no          INTEGER,
    race_name        TEXT DEFAULT '',
    distance         INTEGER DEFAULT 0,
    surface          TEXT DEFAULT '',
    course_condition TEXT DEFAULT '',
    fetched_at       TEXT
);
CREATE INDEX IF NOT EXISTS ix_races_date ON races (date);

CREATE TABLE IF NOT EXISTS horses (
    horse_id                 TEXT PRIMARY KEY,
    name                     TEXT DEFAULT '',
    sex                      TEXT DEFAULT '',
    age                      INTEGER DEFAULT 0,
    sire_id                  TEXT DEFAULT '',
    dam_sire_id              TEXT DEFAULT '',
    running_style            TEXT DEFAULT '',
    running_style_confidence INTEGER DEFAULT 0,
    fetched_at               TEXT
);

CREATE TABLE IF NOT EXISTS race_entries (
    race_id       TEXT,
    horse_id      TEXT,
    post_position INTEGER DEFAULT 0,
    horse_number  INTEGER DEFAULT 0,
    jockey        TEXT DEFAULT '',
    weight        REAL DEFAULT 0.0,
    fetched_at    TEXT,
    PRIMARY KEY (race_id, horse_id)
);

CREATE TABLE IF NOT EXISTS track_bias_daily (
    date                TEXT,
    venue               TEXT,
    surface             TEXT,
    inside_outside_bias REAL DEFAULT 0.0,
    pace_bias           REAL DEFAULT 0.0,
    raw_json            TEXT DEFAULT '',
    fetched_at          TEXT,
    PRIMARY KEY (date, venue, surface)
);

CREATE TABLE IF NOT EXISTS pedigree_stats (
    sire_id         TEXT,
    distance_bucket TEXT,
    surface         TEXT,
    win_rate        REAL DEFAULT 0.0,
    sample_size     INTEGER DEFAULT 0,
    fetched_at      TEXT,
    PRIMARY KEY (sire_id, distance_bucket, surface)
);

CREATE TABLE IF NOT EXISTS scrape_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT,
    fetched_at  TEXT,
    status_code INTEGER DEFAULT 0,
    etag        TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_scrape_log_url ON scrape_log (url);
CREATE INDEX IF NOT EXISTS ix_scrape_log_fetched_at ON scrape_log (fetched_at);
