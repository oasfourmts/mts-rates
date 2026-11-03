import type { Money } from "./api";

const cache = new Map<string, Intl.NumberFormat>();

function nf(digits: number) {
  const key = String(digits);
  if (!cache.has(key)) {
    cache.set(key, new Intl.NumberFormat("en-GB", { minimumFractionDigits: digits, maximumFractionDigits: digits }));
  }
  return cache.get(key)!;
}

/** "1595.22" -> "1,595.22". Display only: the exact string from the server stays the value of record. */
export function money(value: Money | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "";
  const n = Number(value);
  return Number.isFinite(n) ? nf(digits).format(n) : value;
}

/** EGP prices are whole pounds on the sheet; show them without cents. */
export function moneyIn(value: Money, currency: string): string {
  return money(value, currency === "EGP" ? 0 : 2);
}

export function day(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function cairoToday(): string {
  // en-CA formats as YYYY-MM-DD.
  return new Date().toLocaleDateString("en-CA", { timeZone: "Africa/Cairo" });
}

const BASIS: Record<string, string> = {
  per_container: "per box",
  per_trip: "per trip",
  per_bl: "per B/L",
  per_shipment: "per shipment",
  per_kg: "per kg",
  per_cbm: "per CBM",
  per_wm: "per W/M",
  percent: "%",
};

export function basis(b: string): string {
  return BASIS[b] ?? b;
}

export function validity(daysLeft: number): string {
  if (daysLeft === 0) return "ends today";
  if (daysLeft === 1) return "ends tomorrow";
  return `${daysLeft} days left`;
}

export function days(t: string | number): string {
  return `${t} ${String(t) === "1" ? "day" : "days"}`;
}
