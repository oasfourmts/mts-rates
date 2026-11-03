-- Monthly exchange rates entered by hand (temporary until an automatic feed exists).
-- A rate entered for a month is stored on the 1st of that month; to_usd() uses the latest rate on or before
-- the search date, so it applies until the next month's rate is entered.

BEGIN;

ALTER TABLE fx_rates
    ADD COLUMN entered_by text,
    ADD COLUMN entered_at timestamptz NOT NULL DEFAULT now();

COMMIT;
