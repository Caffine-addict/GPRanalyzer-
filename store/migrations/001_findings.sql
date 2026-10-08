-- The live pipeline's frames and findings. frames stores metadata only, never raw traces.
-- "what"/"where"/"why"/"how" are quoted throughout: "where" is a SQL keyword, and the other three
-- are quoted for symmetry rather than renaming one column.
-- Migrations run in one transaction (store/database.py); filenames start with a number.

CREATE TABLE frames (
    frame_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    survey_id TEXT NOT NULL,
    line_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    position DOUBLE PRECISION,
    position_source TEXT,
    antenna_freq_mhz DOUBLE PRECISION,
    sample_interval_ns DOUBLE PRECISION,
    dielectric_assumed DOUBLE PRECISION,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX frames_survey ON frames (survey_id);

CREATE TABLE findings (
    finding_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    survey_id TEXT NOT NULL,
    line_id TEXT NOT NULL,
    frame_id BIGINT NOT NULL REFERENCES frames (frame_id),
    detection_class TEXT NOT NULL,
    detection_confidence DOUBLE PRECISION NOT NULL,
    depth_m DOUBLE PRECISION,
    depth_confidence TEXT NOT NULL,
    position_m DOUBLE PRECISION,
    position_confidence TEXT NOT NULL,
    amplitude DOUBLE PRECISION,
    amplitude_confidence TEXT NOT NULL,
    hyperbola_width_px DOUBLE PRECISION NOT NULL,
    neighbours JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- how many independent receivers recorded the target; without it a round trip through the
    -- store silently regrades a finding from QL-B1 to QL-B2 (evidence/quality.py)
    corroborating_channels INTEGER NOT NULL DEFAULT 1,
    risk_level TEXT NOT NULL,
    risk_score DOUBLE PRECISION NOT NULL,
    risk_rules_fired JSONB NOT NULL DEFAULT '[]'::jsonb,
    "what" TEXT,
    "where" TEXT,
    "why" TEXT,
    "how" TEXT,
    recommended_action TEXT,
    reasoning_latency_ms DOUBLE PRECISION,
    -- where on its line the finding sits ([start, end) traces and samples); all four or none
    location_trace_start INTEGER,
    location_trace_end INTEGER,
    location_sample_start INTEGER,
    location_sample_end INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((location_trace_start IS NULL) = (location_sample_end IS NULL))
);
CREATE INDEX findings_survey_line ON findings (survey_id, line_id);
CREATE INDEX findings_line_position ON findings (line_id, position_m);
