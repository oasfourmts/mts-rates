-- MTS Rate Management Platform: initial schema (PostgreSQL 16)
-- Rates are never overwritten: a new offer is a new rate_card; the old one is expired.

BEGIN;

-- ---------- Reference data ----------

CREATE TABLE currencies (
    code        char(3) PRIMARY KEY,                 -- ISO 4217
    name        text NOT NULL
);

CREATE TABLE fx_rates (
    currency    char(3) NOT NULL REFERENCES currencies,
    on_date     date NOT NULL,
    per_usd     numeric(14,6) NOT NULL CHECK (per_usd > 0),   -- units of currency per 1 USD
    source      text NOT NULL DEFAULT 'manual',
    PRIMARY KEY (currency, on_date)
);

CREATE TYPE location_kind AS ENUM ('seaport', 'airport', 'rail_terminal', 'inland_zone', 'free_zone', 'city');

CREATE TABLE locations (
    id           bigserial PRIMARY KEY,
    code         text NOT NULL UNIQUE,              -- UN/LOCODE, IATA code, or a zone slug such as EG-Z-SADAT
    name         text NOT NULL,
    kind         location_kind NOT NULL,
    country_code char(2),
    aliases      text[] NOT NULL DEFAULT '{}'        -- spellings seen in sheets and emails
);
CREATE INDEX locations_aliases_idx ON locations USING gin (aliases);

CREATE TYPE partner_kind AS ENUM ('shipping_line', 'airline', 'trucker', 'rail_operator',
                                  'customs_broker', 'overseas_agent', 'co_loader', 'other');

CREATE TABLE partners (
    id            bigserial PRIMARY KEY,
    name          text NOT NULL,
    kind          partner_kind NOT NULL,
    match_key     text NOT NULL,                      -- upper-cased, spaces collapsed: used to match sheet spellings
    scac_or_iata  text,
    rate_emails   text[] NOT NULL DEFAULT '{}',       -- addresses that send rates to rates@ (Phase 2)
    active        boolean NOT NULL DEFAULT true,
    UNIQUE (kind, match_key)
);

CREATE TABLE clients (
    id            bigserial PRIMARY KEY,
    name          text NOT NULL UNIQUE,
    sales_owner   text,
    default_margin_pct numeric(5,2),
    notes         text
);

CREATE TABLE equipment (
    code         text PRIMARY KEY,                    -- 20DV, 40HC, 40RF, 2X20DV, LCL, AIR ...
    description  text NOT NULL,
    teu          numeric(3,1)
);

CREATE TYPE charge_basis AS ENUM ('per_container', 'per_trip', 'per_kg', 'per_cbm', 'per_wm',
                                  'per_bl', 'per_shipment', 'percent');

CREATE TABLE charge_codes (
    code         text PRIMARY KEY,                    -- OF, BAF, EBS, LSS, FUE, ETS, EFS, WAR, OTHC, BL ...
    name         text NOT NULL,
    default_basis charge_basis NOT NULL
);

CREATE TABLE hs_codes (
    code         text PRIMARY KEY,                    -- 2 to 10 digits
    description  text NOT NULL,
    duty_pct     numeric(6,2),
    vat_pct      numeric(6,2),
    permits      text[] NOT NULL DEFAULT '{}'
);

-- ---------- Imports and review ----------

CREATE TABLE import_runs (
    id            bigserial PRIMARY KEY,
    source_kind   text NOT NULL CHECK (source_kind IN ('sheet', 'email', 'file', 'manual')),
    source_ref    text NOT NULL,                      -- e.g. sheet id + tab name, or Gmail message id
    started_at    timestamptz NOT NULL DEFAULT now(),
    rows_read     integer NOT NULL DEFAULT 0,
    rows_imported integer NOT NULL DEFAULT 0,
    created_by    text NOT NULL DEFAULT current_user
);

CREATE TABLE import_issues (
    id            bigserial PRIMARY KEY,
    import_run_id bigint NOT NULL REFERENCES import_runs ON DELETE CASCADE,
    row_ref       text NOT NULL,                      -- where in the source, e.g. "row 14, col 'BAF'"
    severity      text NOT NULL CHECK (severity IN ('skipped', 'warning')),
    message       text NOT NULL,
    raw           text,
    resolved      boolean NOT NULL DEFAULT false
);

-- ---------- Rates ----------

CREATE TYPE transport_mode AS ENUM ('sea_fcl', 'sea_lcl', 'air', 'road', 'rail', 'inland', 'customs');
CREATE TYPE trade_direction AS ENUM ('import', 'export', 'domestic');
CREATE TYPE rate_status AS ENUM ('draft', 'active', 'expired', 'rejected');

CREATE TABLE rate_cards (
    id            bigserial PRIMARY KEY,
    mode          transport_mode NOT NULL,
    direction     trade_direction NOT NULL,
    partner_id    bigint NOT NULL REFERENCES partners,
    rate_type     text NOT NULL DEFAULT 'contract' CHECK (rate_type IN ('contract', 'spot', 'tariff')),
    valid_from    date NOT NULL,
    valid_to      date NOT NULL,
    currency      char(3) NOT NULL REFERENCES currencies,   -- default for lines that do not name one
    status        rate_status NOT NULL DEFAULT 'draft',
    import_run_id bigint REFERENCES import_runs,
    source_ref    text,                                     -- sheet tab, email id, carrier quote number
    approved_by   text,
    approved_at   timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    notes         text,
    CHECK (valid_to >= valid_from)
);
CREATE INDEX rate_cards_validity_idx ON rate_cards (mode, status, valid_from, valid_to);

-- Ocean, air, road and rail main legs
CREATE TABLE freight_rates (
    id               bigserial PRIMARY KEY,
    rate_card_id     bigint NOT NULL REFERENCES rate_cards ON DELETE CASCADE,
    origin_id        bigint NOT NULL REFERENCES locations,
    destination_id   bigint NOT NULL REFERENCES locations,
    equipment_code   text NOT NULL REFERENCES equipment,
    routing          text CHECK (routing IN ('direct', 'indirect')),
    via              text,                                  -- transshipment port(s) as written
    transit_days_min integer CHECK (transit_days_min >= 0),
    transit_days_max integer CHECK (transit_days_max >= transit_days_min),
    free_days        integer CHECK (free_days >= 0),
    free_time_terms  text,                                  -- e.g. "7 DET", "14 merged"
    quote_ref        text,                                  -- carrier's quote number
    reported_total   numeric(12,2),                         -- the sheet's own all-in figure, kept for checking
    remarks          text,
    source_row       text
);
CREATE INDEX freight_rates_lane_idx ON freight_rates (origin_id, destination_id, equipment_code);

-- Air weight breaks (Phase 1 screens; filled from air tariffs)
CREATE TABLE air_weight_breaks (
    freight_rate_id  bigint NOT NULL REFERENCES freight_rates ON DELETE CASCADE,
    min_kg           numeric(10,2) NOT NULL CHECK (min_kg >= 0),
    price_per_kg     numeric(12,4) NOT NULL CHECK (price_per_kg >= 0),
    minimum_charge   numeric(12,2),
    PRIMARY KEY (freight_rate_id, min_kg)
);

-- Inland trucking and rail: port <-> inland zone
CREATE TABLE transport_rates (
    id               bigserial PRIMARY KEY,
    rate_card_id     bigint NOT NULL REFERENCES rate_cards ON DELETE CASCADE,
    origin_id        bigint NOT NULL REFERENCES locations,
    destination_id   bigint NOT NULL REFERENCES locations,
    equipment_code   text NOT NULL REFERENCES equipment,
    weight_class     text CHECK (weight_class IN ('light', 'heavy')),   -- 20DV: light < 8 t
    price            numeric(12,2) NOT NULL CHECK (price > 0),
    basis            charge_basis NOT NULL DEFAULT 'per_trip',
    source_row       text
);
CREATE INDEX transport_rates_lane_idx ON transport_rates (origin_id, destination_id, equipment_code);

-- Every charge on a freight rate, ocean freight included, each with its own currency
CREATE TABLE freight_charges (
    id               bigserial PRIMARY KEY,
    freight_rate_id  bigint NOT NULL REFERENCES freight_rates ON DELETE CASCADE,
    charge_code      text NOT NULL REFERENCES charge_codes,
    amount           numeric(12,2) NOT NULL CHECK (amount >= 0),
    currency         char(3) NOT NULL REFERENCES currencies,
    basis            charge_basis NOT NULL,
    raw_text         text                                   -- the cell as written, for review
);
CREATE INDEX freight_charges_rate_idx ON freight_charges (freight_rate_id);

-- Customs clearance: depends on commodity (HS chapter), shipment type and purpose of the goods
CREATE TYPE customs_purpose AS ENUM ('commercial', 'temporary_admission', 're_export', 'project_cargo',
                                     'personal_effects', 'samples', 'free_zone');

CREATE TABLE customs_rates (
    id               bigserial PRIMARY KEY,
    rate_card_id     bigint NOT NULL REFERENCES rate_cards ON DELETE CASCADE,
    clearance_point_id bigint REFERENCES locations,         -- null = any port
    shipment_type    text NOT NULL CHECK (shipment_type IN ('fcl', 'lcl', 'air', 'courier', 'road', 'any')),
    hs_chapter       text CHECK (hs_chapter ~ '^[0-9]{2}$'),  -- null = any commodity
    purpose          customs_purpose NOT NULL DEFAULT 'commercial',
    fee              numeric(12,2) NOT NULL CHECK (fee >= 0),
    basis            charge_basis NOT NULL,
    documents        text[] NOT NULL DEFAULT '{}',
    notes            text
);

-- ---------- Sales workflow (filled from Phase 2) ----------

CREATE TABLE rfqs (
    id               bigserial PRIMARY KEY,
    quote_no         text UNIQUE,                            -- the RFQ app's id, e.g. R261496
    client_id        bigint REFERENCES clients,
    received_at      timestamptz,
    gmail_thread_id  text,
    mode             text,
    direction        trade_direction,
    services         text[] NOT NULL DEFAULT '{}',
    details          jsonb NOT NULL DEFAULT '{}',
    status           text NOT NULL DEFAULT 'open'
);

CREATE TABLE audit_log (
    id               bigserial PRIMARY KEY,
    at               timestamptz NOT NULL DEFAULT now(),
    actor            text NOT NULL,                          -- a person's email, or 'agent'
    action           text NOT NULL,
    table_name       text NOT NULL,
    row_id           bigint,
    detail           jsonb
);

-- ---------- Seed reference data ----------

INSERT INTO currencies (code, name) VALUES
    ('USD', 'US dollar'), ('EUR', 'Euro'), ('EGP', 'Egyptian pound'), ('GBP', 'Pound sterling'),
    ('CNY', 'Chinese yuan'), ('SAR', 'Saudi riyal'), ('AED', 'UAE dirham'), ('TRY', 'Turkish lira');

INSERT INTO equipment (code, description, teu) VALUES
    ('20DV', '20'' dry van', 1), ('40DV', '40'' dry van', 2), ('40HC', '40'' high cube', 2),
    ('40RF', '40'' reefer', 2), ('20RF', '20'' reefer', 1), ('20OT', '20'' open top', 1),
    ('40OT', '40'' open top', 2), ('20FR', '20'' flat rack', 1), ('40FR', '40'' flat rack', 2),
    ('2X20DV', 'Two 20'' dry vans on one truck', 2), ('LCL', 'Less than container load', NULL),
    ('AIR', 'Air cargo', NULL), ('FTL', 'Full truck load', NULL), ('LTL', 'Part truck load', NULL);

INSERT INTO charge_codes (code, name, default_basis) VALUES
    ('OF',   'Ocean freight', 'per_container'),
    ('BAF',  'Bunker adjustment factor', 'per_container'),
    ('EBS',  'Emergency bunker surcharge', 'per_container'),
    ('LSS',  'Low sulphur / ECA surcharge', 'per_container'),
    ('FUE',  'FuelEU surcharge', 'per_container'),
    ('ETS',  'EU emissions trading surcharge', 'per_container'),
    ('EFS',  'Emergency fuel surcharge', 'per_container'),
    ('WAR',  'War risk surcharge', 'per_container'),
    ('OTHER','Other surcharge (see remarks)', 'per_container'),
    ('OTHC', 'Origin terminal handling', 'per_container'),
    ('BL',   'Bill of lading fee', 'per_bl'),
    ('TRUCK','Inland trucking', 'per_trip'),
    ('CUSTOMS', 'Customs clearance fee', 'per_shipment');

INSERT INTO locations (code, name, kind, country_code, aliases) VALUES
    ('EGALY', 'Alexandria', 'seaport', 'EG', '{ALEXANDRIA}'),
    ('EGEDK', 'El Dekheila', 'seaport', 'EG', '{"EL DEKHEILA",DEKHEILA}'),
    ('EGDAM', 'Damietta', 'seaport', 'EG', '{DAMIETTA}'),
    ('EGPSE', 'Port Said East', 'seaport', 'EG', '{"PORT SAID EAST","PSD EAST",PSE}'),
    ('EGSOK', 'Sokhna', 'seaport', 'EG', '{SOKHNA,"AIN SOKHNA"}');

COMMIT;
