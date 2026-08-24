-- Initial schema. frames stores metadata only, never raw traces/image arrays.
-- "what"/"where"/"why"/"how" are quoted throughout: "where" is a SQL keyword,
-- quoted consistently for symmetry with the other three rather than renaming
-- just that one column.
--
-- PROJECT RULE for every migration file (including this one): every
-- statement must be idempotent (IF NOT EXISTS, etc). duckdb_store.py wraps
-- each file in an explicit transaction so a mid-file failure rolls back
-- cleanly, but a *successfully*-applied-twice migration (e.g. a retried
-- deploy after a crash right after COMMIT but before the version row's
-- durability is confirmed) must still be a safe no-op the second time.
-- Filenames must start with a numeric prefix (any width) — sorted
-- numerically, not lexicographically, by duckdb_store.py's _migration_sort_key.

CREATE SEQUENCE IF NOT EXISTS frame_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS finding_id_seq START 1;

CREATE TABLE IF NOT EXISTS frames (
    frame_id BIGINT PRIMARY KEY,
    survey_id VARCHAR NOT NULL,
    line_id VARCHAR NOT NULL,
    source_type VARCHAR NOT NULL,
    position DOUBLE,
    position_source VARCHAR,
    antenna_freq_mhz DOUBLE,
    sample_interval_ns DOUBLE,
    dielectric_assumed DOUBLE,
    provenance VARCHAR,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS findings (
    finding_id BIGINT PRIMARY KEY,
    survey_id VARCHAR NOT NULL,
    line_id VARCHAR NOT NULL,
    frame_id BIGINT NOT NULL,
    detection_class VARCHAR NOT NULL,
    detection_confidence DOUBLE NOT NULL,
    depth_m DOUBLE,
    depth_confidence VARCHAR NOT NULL,
    position_m DOUBLE,
    position_confidence VARCHAR NOT NULL,
    amplitude DOUBLE,
    amplitude_confidence VARCHAR NOT NULL,
    hyperbola_width_px DOUBLE NOT NULL,
    neighbours VARCHAR,
    risk_level VARCHAR NOT NULL,
    risk_score DOUBLE NOT NULL,
    risk_rules_fired VARCHAR,
    "what" VARCHAR,
    "where" VARCHAR,
    "why" VARCHAR,
    "how" VARCHAR,
    recommended_action VARCHAR,
    reasoning_latency_ms DOUBLE,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_findings_survey_line ON findings (survey_id, line_id);
CREATE INDEX IF NOT EXISTS idx_findings_line_position ON findings (line_id, position_m);
