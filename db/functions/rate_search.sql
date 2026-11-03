-- Rate search. Safe to re-run: every object is CREATE OR REPLACE.
-- Only rates that are active and valid on the requested date are returned.

-- Convert an amount to USD with the latest FX rate on or before a date. NULL when no rate is known,
-- so a quote can never be built on a missing exchange rate.
CREATE OR REPLACE FUNCTION to_usd(amount numeric, currency char(3), on_date date)
RETURNS numeric LANGUAGE sql STABLE AS $$
    SELECT CASE WHEN currency = 'USD' THEN amount
                ELSE round(amount / (SELECT per_usd FROM fx_rates f
                                     WHERE f.currency = to_usd.currency AND f.on_date <= to_usd.on_date
                                     ORDER BY f.on_date DESC LIMIT 1), 2) END
$$;

-- Find a location by code, name or any alias (case-insensitive).
CREATE OR REPLACE FUNCTION find_location(term text)
RETURNS bigint LANGUAGE sql STABLE AS $$
    SELECT id FROM locations
    WHERE code = upper(trim(term)) OR upper(name) = upper(trim(term))
       OR upper(regexp_replace(trim(term), '\s+', ' ', 'g')) = ANY (aliases)
    ORDER BY (code = upper(trim(term))) DESC
    LIMIT 1
$$;

-- Ocean freight options for one lane, cheapest first. total_usd is the cost of ONE container:
-- every charge on the rate, per-B/L fees included (so it overstates per-container cost on multi-box bookings).
-- total_usd is NULL when a charge's currency has no FX rate; such options sort last and say why.
CREATE OR REPLACE FUNCTION search_freight(p_origin text, p_destination text, p_equipment text,
                                          p_on date DEFAULT current_date)
RETURNS TABLE (
    rate_card_id bigint, freight_rate_id bigint, carrier text, rate_type text,
    routing text, via text, transit_days text, free_days integer, free_time_terms text,
    valid_to date, total_usd numeric, missing_fx text[], charges jsonb, remarks text
) LANGUAGE sql STABLE AS $$
    SELECT rc.id, fr.id, p.name, rc.rate_type, fr.routing, fr.via,
           CASE WHEN fr.transit_days_min IS NULL THEN NULL
                WHEN fr.transit_days_min = fr.transit_days_max THEN fr.transit_days_min::text
                ELSE fr.transit_days_min || '-' || fr.transit_days_max END,
           fr.free_days, fr.free_time_terms, rc.valid_to,
           CASE WHEN bool_and(to_usd(fc.amount, fc.currency, p_on) IS NOT NULL)
                THEN sum(to_usd(fc.amount, fc.currency, p_on)) END,
           array_remove(array_agg(DISTINCT CASE WHEN to_usd(fc.amount, fc.currency, p_on) IS NULL
                                                THEN fc.currency END), NULL),
           jsonb_agg(jsonb_build_object('code', fc.charge_code, 'amount', fc.amount, 'currency', fc.currency,
                                        'basis', fc.basis) ORDER BY fc.id),
           fr.remarks
    FROM freight_rates fr
    JOIN rate_cards rc ON rc.id = fr.rate_card_id
    JOIN partners p ON p.id = rc.partner_id
    JOIN freight_charges fc ON fc.freight_rate_id = fr.id
    WHERE fr.origin_id = find_location(p_origin)
      AND fr.destination_id = find_location(p_destination)
      AND fr.equipment_code = upper(p_equipment)
      AND rc.status = 'active'
      AND p_on BETWEEN rc.valid_from AND rc.valid_to
    GROUP BY rc.id, fr.id, p.name
    ORDER BY 11 NULLS LAST, 7
$$;

-- Inland trucking options between a port and an inland zone, cheapest first.
-- For 20DV, pass the weight class ('light' under 8 t, 'heavy' otherwise); other equipment ignores it.
-- p_direction 'import' (delivery port -> zone) or 'export' (pickup zone -> port) uses that direction's price
-- where the sheet gave one per direction, and the single price otherwise; NULL returns every price.
DROP FUNCTION IF EXISTS search_trucking(text, text, text, text, date);
DROP FUNCTION IF EXISTS search_trucking(text, text, text, text, date, text);
CREATE FUNCTION search_trucking(p_port text, p_zone text, p_equipment text,
                                p_weight_class text DEFAULT NULL, p_on date DEFAULT current_date,
                                p_direction text DEFAULT NULL)
RETURNS TABLE (rate_card_id bigint, transport_rate_id bigint, trucker text, price numeric,
               currency char(3), weight_class text, applies_to text, valid_to date)
LANGUAGE sql STABLE AS $$
    SELECT rc.id, tr.id, p.name, tr.price, rc.currency, tr.weight_class, tr.applies_to, rc.valid_to
    FROM transport_rates tr
    JOIN rate_cards rc ON rc.id = tr.rate_card_id
    JOIN partners p ON p.id = rc.partner_id
    WHERE tr.origin_id = find_location(p_port)
      AND tr.destination_id = find_location(p_zone)
      AND tr.equipment_code = upper(p_equipment)
      AND (tr.weight_class IS NULL OR p_weight_class IS NULL OR tr.weight_class = p_weight_class)
      AND (p_direction IS NULL OR tr.applies_to IN ('both', p_direction))
      AND rc.status = 'active'
      AND p_on BETWEEN rc.valid_from AND rc.valid_to
    ORDER BY tr.price, p.name, tr.applies_to
$$;

-- Lanes on the expiry dashboard: active rate cards ending within p_days.
CREATE OR REPLACE FUNCTION expiring_rate_cards(p_days integer DEFAULT 7, p_on date DEFAULT current_date)
RETURNS TABLE (rate_card_id bigint, partner text, mode transport_mode, valid_to date, days_left integer, lines bigint)
LANGUAGE sql STABLE AS $$
    SELECT rc.id, p.name, rc.mode, rc.valid_to, rc.valid_to - p_on,
           (SELECT count(*) FROM freight_rates WHERE rate_card_id = rc.id)
         + (SELECT count(*) FROM transport_rates WHERE rate_card_id = rc.id)
    FROM rate_cards rc JOIN partners p ON p.id = rc.partner_id
    WHERE rc.status = 'active' AND rc.valid_to BETWEEN p_on AND p_on + p_days
    ORDER BY rc.valid_to, p.name
$$;
