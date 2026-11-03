-- Adabiya (Al Adabiyah, Gulf of Suez) appears as a port of loading on the November 2026 freight sheet,
-- spelled "Adabya". UN/LOCODE EGADA.

BEGIN;

INSERT INTO locations (code, name, kind, country_code, aliases)
VALUES ('EGADA', 'Adabiya', 'seaport', 'EG', '{ADABYA,ADABIYA,"AL ADABIYAH","EL ADABIYA"}')
ON CONFLICT (code) DO NOTHING;

COMMIT;
