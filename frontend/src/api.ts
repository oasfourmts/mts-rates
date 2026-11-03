// Typed calls to the FastAPI backend. Money arrives as strings ("1595.22") and stays a string until display.

export type Money = string;

export interface Place {
  code: string;
  name: string;
  kind: string;
  country_code?: string | null;
}

export interface Charge {
  code: string;
  name: string;
  amount: Money;
  currency: string;
  basis: string;
  quantity: number;
  line_total: Money;
  line_usd: Money | null;
}

export interface FreightOption {
  rate_card_id: number;
  freight_rate_id: number;
  carrier: string;
  rate_type: string;
  routing: string | null;
  via: string | null;
  transit_days: string | null;
  free_days: number | null;
  free_time_terms: string | null;
  valid_to: string;
  days_left: number;
  expiring_soon: boolean;
  total_usd: Money | null;
  per_container_usd: Money | null;
  missing_fx: string[];
  charges: Charge[];
  remarks: string | null;
  sheet_notes: { severity: string; message: string; raw: string | null }[];
}

export interface TruckingOption {
  rate_card_id: number;
  transport_rate_id: number;
  trucker: string;
  price: Money;
  currency: string;
  weight_class: string | null;
  applies_to: "both" | "import" | "export";
  trips: number;
  total: Money;
  total_usd: Money | null;
  valid_to: string;
  days_left: number;
  expiring_soon: boolean;
}

export interface Fx {
  currency: string;
  per_usd: string;
  on_date: string;
}

interface SearchBase {
  on: string;
  origin: Place;
  destination: Place;
  equipment: string;
  fx: Fx[];
}

export interface FreightResult extends SearchBase {
  mode: "freight";
  containers: number;
  options: FreightOption[];
}

export interface TruckingResult extends SearchBase {
  mode: "trucking";
  weight_t: string | null;
  weight_class: string | null;
  direction: "import" | "export" | null;
  trips: number;
  options: TruckingOption[];
}

export interface DoorOption {
  port: Place;
  freight: FreightOption;
  trucking: TruckingOption;
  other_truckers: number;
  total_usd: Money | null;
  per_container_usd: Money | null;
  missing_fx: string[];
  expiring_soon: boolean;
}

export interface DoorResult extends SearchBase {
  mode: "door";
  containers: number;
  weight_t: string | null;
  weight_class: string | null;
  ports_checked: Place[];
  ports_without_rates: Place[];
  options: DoorOption[];
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export interface FxRow {
  currency: string;
  on_date: string;
  per_usd: string;
  source: string;
  entered_by: string | null;
  entered_at: string;
}

async function get<T>(path: string, params: Record<string, string | number | undefined> = {}): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") qs.set(k, String(v));
  return send<T>(`/api/${path}${qs.size ? "?" + qs : ""}`, { headers: { Accept: "application/json" } });
}

async function send<T>(url: string, init: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let message = `The server answered ${res.status}.`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") message = body.detail;
      else if (Array.isArray(body.detail)) message = body.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      /* keep the status message */
    }
    throw new ApiError(res.status, message);
  }
  return res.json() as Promise<T>;
}

export const api = {
  me: () => get<{ email: string; is_admin: boolean }>("me"),
  currencies: () => get<{ code: string; name: string }[]>("currencies"),
  fxRates: (months = 13) => get<FxRow[]>("fx", { months }),
  saveFx: (currency: string, month: string, per_usd: string, confirm = false) =>
    send<FxRow>(`/api/fx/${encodeURIComponent(currency)}/${encodeURIComponent(month)}`, {
      method: "PUT",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ per_usd, confirm }),
    }),
  equipment: () => get<{ code: string; description: string }[]>("equipment"),
  locations: (q: string, kind?: string) => get<Place[]>("locations", { q, kind }),
  freight: (p: {
    origin: string; destination: string; equipment: string; on?: string; containers?: number; weight_t?: string;
  }) => get<FreightResult | DoorResult>("search/freight", p),
  trucking: (p: {
    port: string; zone: string; equipment: string; on?: string; weight_t?: string; trips?: number; direction?: string;
  }) =>
    get<TruckingResult>("search/trucking", p),
};
