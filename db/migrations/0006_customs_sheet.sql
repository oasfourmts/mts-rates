-- Customs clearance sheet (5 Nov 2026): what the pricing team's "MTS customs clearance rates" sheet holds.
-- Clearance fees are rate cards (mode 'customs', one per broker, direction, currency and validity).
-- Duty rates, agreements, countries, purposes and documents are reference data, loaded by app/importers/customs.py.

ALTER TYPE charge_basis ADD VALUE IF NOT EXISTS 'per_line';   -- per HS line on the invoice

BEGIN;

INSERT INTO locations (code, name, kind, country_code, aliases)
VALUES ('CAI', 'Cairo Airport', 'airport', 'EG', '{"CAIRO AIRPORT","CAIRO INTERNATIONAL AIRPORT"}')
ON CONFLICT (code) DO NOTHING;

-- A clearance fee: which shipments it applies to, and how it is charged.
ALTER TABLE customs_rates
    ADD COLUMN fee_item       text NOT NULL DEFAULT 'Clearance fee',
    ADD COLUMN value_from_usd numeric(14,2),               -- commercial invoice value band, USD; null = no limit
    ADD COLUMN value_to_usd   numeric(14,2),               -- excluded: [from, to)
    ADD COLUMN minimum        numeric(12,2),               -- in the rate card's currency
    ADD COLUMN source_row     text,
    ADD CONSTRAINT customs_value_band CHECK (value_to_usd IS NULL OR value_from_usd IS NULL
                                             OR value_to_usd > value_from_usd);
COMMENT ON COLUMN customs_rates.fee IS 'Amount in the rate card currency; for basis percent, the percent of the invoice value';

ALTER TABLE hs_codes
    ADD COLUMN schedule_tax text,                         -- as written in the tariff (rate or note)
    ADD COLUMN source       text,                         -- where and when the rates were checked
    ADD COLUMN updated_at   timestamptz;

CREATE TABLE trade_agreements (
    code         text PRIMARY KEY,                        -- EU, UK, EFTA, Turkey, Agadir, GAFTA, COMESA ...
    name         text NOT NULL,
    partners     text,
    certificate  text,                                    -- document code, e.g. EUR1
    checked      boolean NOT NULL DEFAULT false,
    notes        text
);

-- Preferential duty with a valid certificate of origin; no row = the general rate applies.
CREATE TABLE agreement_rates (
    hs_code      text NOT NULL REFERENCES hs_codes ON DELETE CASCADE,
    agreement    text NOT NULL REFERENCES trade_agreements,
    duty_pct     numeric(6,2) NOT NULL CHECK (duty_pct >= 0),
    PRIMARY KEY (hs_code, agreement)
);

CREATE TABLE origin_agreements (
    country_code char(2) PRIMARY KEY,
    country_name text NOT NULL,
    agreement    text REFERENCES trade_agreements,       -- null = general rate
    notes        text
);

CREATE TABLE customs_documents (
    code         text PRIMARY KEY,
    name         text NOT NULL,
    issued_by    text,
    notes        text
);

CREATE TABLE customs_purposes (
    purpose              customs_purpose PRIMARY KEY,
    label                text NOT NULL,
    label_ar             text,
    duties_at_clearance  text NOT NULL CHECK (duties_at_clearance IN ('yes', 'guarantee', 'no')),
    documents            text[] NOT NULL DEFAULT '{}',
    checked              boolean NOT NULL DEFAULT false,
    notes                text
);

COMMIT;
