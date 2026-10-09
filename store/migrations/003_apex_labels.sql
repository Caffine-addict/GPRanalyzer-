-- People verifying the detector's targets, to train a learned detector on real ground
-- (docs/TRAINING_PLAN.md). Append-only: a changed mind is a new row, and the latest row for a
-- target (job, trace, time) is its decision. "no" is as useful as "yes": a hard negative.

CREATE TABLE apex_labels (
    label_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job TEXT NOT NULL,
    trace INTEGER NOT NULL CHECK (trace >= 0),
    time_ns DOUBLE PRECISION NOT NULL,  -- apex two-way time after time zero, as the merged target gives it
    decision TEXT NOT NULL CHECK (decision IN ('yes', 'no', 'unsure')),
    user_id BIGINT NOT NULL REFERENCES users (user_id),
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX apex_labels_target ON apex_labels (job, trace, time_ns, decided_at DESC);
