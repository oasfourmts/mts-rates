-- Trucking prices can differ by direction. A legacy-sheet cell like "15500 IMP-16000 EXP" holds an import
-- delivery price (port -> zone) and an export pickup price (zone -> port); a plain number applies both ways
-- (MTS, 3 Nov 2026). Prices stay stored port -> zone; applies_to says which moves may use them.

BEGIN;

ALTER TABLE transport_rates
    ADD COLUMN applies_to text NOT NULL DEFAULT 'both' CHECK (applies_to IN ('both', 'import', 'export'));

COMMIT;
