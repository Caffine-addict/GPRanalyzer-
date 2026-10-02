-- Finding gained `location`: where on its line a finding sits ([start, end) traces and samples,
-- or pixel columns and rows for an image), so a viewer can draw it over the radargram. Without
-- persisting it, a survey reloaded from the store, or rendered into a report, has findings it can
-- no longer place — the live feed showed a box that a reload silently loses.
--
-- Nullable, no default: rows written before this migration genuinely have no recorded location,
-- and an image finding from any source may have none. All four are set together or not at all.
ALTER TABLE findings ADD COLUMN location_trace_start INTEGER;
ALTER TABLE findings ADD COLUMN location_trace_end INTEGER;
ALTER TABLE findings ADD COLUMN location_sample_start INTEGER;
ALTER TABLE findings ADD COLUMN location_sample_end INTEGER;
