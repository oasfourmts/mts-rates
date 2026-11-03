-- Carriers missing from the importer's known list were re-cased on 3 Nov 2026 ("NVOCC (Crew)" became
-- "Nvocc (crew)", "EGL" became "Egl"). New imports keep the sheet's spelling; this fixes the stored names.

BEGIN;

UPDATE partners SET name = 'NVOCC (Crew)' WHERE kind = 'shipping_line' AND match_key = 'NVOCC (CREW)';
UPDATE partners SET name = 'EGL' WHERE kind = 'shipping_line' AND match_key = 'EGL';

COMMIT;
