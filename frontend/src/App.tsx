import { Fragment, useEffect, useState, type FormEvent } from "react";
import {
  api,
  ApiError,
  type FreightOption,
  type DoorOption,
  type DoorResult,
  type FreightResult,
  type TruckingResult,
} from "./api";
import { basis, cairoToday, day, days, money, moneyIn, validity } from "./format";
import { FxPage } from "./FxPage";
import { LocationInput } from "./LocationInput";

type Mode = "freight" | "trucking";
const NOT_BOXES = new Set(["LCL", "AIR", "FTL", "LTL"]);

interface Form {
  mode: Mode;
  from: string;
  to: string;
  equipment: string;
  count: string;
  weight: string;
  on: string;
  direction: "import" | "export";
}

function readUrl(): Form {
  const p = new URLSearchParams(location.search);
  return {
    mode: p.get("mode") === "trucking" ? "trucking" : "freight",
    from: p.get("from") ?? "",
    to: p.get("to") ?? "",
    equipment: p.get("equipment") ?? "20DV",
    count: p.get("count") ?? "1",
    weight: p.get("weight") ?? "",
    on: p.get("on") ?? cairoToday(),
    direction: p.get("direction") === "export" ? "export" : "import",
  };
}

function writeUrl(f: Form) {
  const p = new URLSearchParams({ mode: f.mode, from: f.from, to: f.to, equipment: f.equipment, count: f.count, on: f.on });
  if (f.equipment === "20DV" && f.weight) p.set("weight", f.weight);
  if (f.mode === "trucking") p.set("direction", f.direction);
  history.replaceState(null, "", `?${p}`);
}

type Page = "search" | "fx";
const pageFromPath = (): Page => (location.pathname.startsWith("/exchange-rates") ? "fx" : "search");

export function App() {
  const [page, setPage] = useState<Page>(pageFromPath);
  const [me, setMe] = useState<{ email: string; is_admin: boolean } | null>(null);

  useEffect(() => {
    api.me().then(setMe).catch(() => setMe(null));
    const onPop = () => setPage(pageFromPath());
    addEventListener("popstate", onPop);
    return () => removeEventListener("popstate", onPop);
  }, []);

  function go(e: React.MouseEvent<HTMLAnchorElement>, to: Page) {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return; // let the browser open a new tab
    e.preventDefault();
    history.pushState(null, "", to === "fx" ? "/exchange-rates" : "/");
    setPage(to);
  }

  return (
    <div className="page">
      <header className="top">
        <span className="brand">MTS Rates</span>
        <nav className="nav" aria-label="Screens">
          <a href="/" aria-current={page === "search" ? "page" : undefined} onClick={(e) => go(e, "search")}>
            Rate Search
          </a>
          <a href="/exchange-rates" aria-current={page === "fx" ? "page" : undefined} onClick={(e) => go(e, "fx")}>
            Exchange rates
          </a>
        </nav>
        {me && <span className="who">{me.email}</span>}
      </header>
      <main>{page === "fx" ? <FxPage isAdmin={!!me?.is_admin} /> : <RateSearch />}</main>
    </div>
  );
}

function RateSearch() {
  const [form, setForm] = useState<Form>(readUrl);
  const [equipment, setEquipment] = useState<string[]>(["20DV", "40DV", "40HC", "40RF", "2X20DV"]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>("");
  const [result, setResult] = useState<AnyResult | null>(null);

  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => ({ ...f, [k]: v }));

  useEffect(() => {
    api.equipment().then((e) => setEquipment(e.map((x) => x.code).filter((c) => !NOT_BOXES.has(c)))).catch(() => {});
    if (form.from && form.to) void run(form);
    // Run once on load when the link carries a search.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function run(f: Form) {
    setBusy(true);
    setError("");
    writeUrl(f);
    try {
      const count = Math.max(1, parseInt(f.count, 10) || 1);
      setResult(
        f.mode === "freight"
          ? await api.freight({
              origin: f.from, destination: f.to, equipment: f.equipment, on: f.on, containers: count,
              weight_t: f.equipment === "20DV" ? f.weight : undefined,
            })
          : await api.trucking({
              port: f.from, zone: f.to, equipment: f.equipment, on: f.on, trips: count,
              weight_t: f.equipment === "20DV" ? f.weight : undefined, direction: f.direction,
            }),
      );
    } catch (e) {
      setResult(null);
      setError(
        e instanceof ApiError && e.status === 401
          ? "Your sign-in has ended. Reload the page to sign in again."
          : e instanceof Error
            ? e.message
            : "The search did not complete. Check your connection and search again.",
      );
    } finally {
      setBusy(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    void run(form);
  }

  function switchMode(mode: Mode) {
    if (mode === form.mode) return;
    setForm((f) => ({ ...f, mode, from: "", to: "" }));
    setResult(null);
    setError("");
  }

  const trucking = form.mode === "trucking";

  return (
    <>
        <div className="modes" role="radiogroup" aria-label="What to price">
          {(["freight", "trucking"] as Mode[]).map((m) => (
            <button
              key={m}
              type="button"
              role="radio"
              aria-checked={form.mode === m}
              className="mode"
              onClick={() => switchMode(m)}
            >
              {m === "freight" ? "Ocean freight" : "Inland trucking"}
            </button>
          ))}
        </div>

        <form className="lane" onSubmit={submit}>
          <div className="route">
            <LocationInput
              label={trucking ? "Port" : "From port or factory zone"}
              value={form.from}
              onChange={(v) => set("from", v)}
              kind={trucking ? "seaport" : undefined}
              placeholder={trucking ? "Alexandria" : "Alexandria or EL Sadat City"}
            />
            <span className="arrow" aria-hidden="true">
              <svg viewBox="0 0 120 16" preserveAspectRatio="none">
                <line x1="0" y1="8" x2="110" y2="8" />
                <path d="M106 2 L118 8 L106 14 Z" />
              </svg>
            </span>
            <LocationInput
              label={trucking ? "Inland destination" : "Destination port"}
              value={form.to}
              onChange={(v) => set("to", v)}
              kind={trucking ? "inland_zone" : undefined}
              placeholder={trucking ? "EL Sadat City" : "Genoa or ITGOA"}
            />
          </div>

          <div className="cargo">
            {trucking && (
              <label>
                <span>Move</span>
                <select value={form.direction} onChange={(e) => set("direction", e.target.value as Form["direction"])}>
                  <option value="import">Import delivery</option>
                  <option value="export">Export pickup</option>
                </select>
              </label>
            )}
            <label>
              <span>Equipment</span>
              <select value={form.equipment} onChange={(e) => set("equipment", e.target.value)}>
                {equipment.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </label>
            <label className="narrow">
              <span>{trucking ? "Trips" : "Boxes"}</span>
              <input type="number" min={1} max={500} value={form.count} onChange={(e) => set("count", e.target.value)} />
            </label>
            {form.equipment === "20DV" && (
              <label className="narrow">
                <span>Cargo weight, t</span>
                <input
                  type="number" min={0.1} max={60} step={0.1} value={form.weight}
                  placeholder={trucking ? "both" : "if trucked"}
                  onChange={(e) => set("weight", e.target.value)}
                />
              </label>
            )}
            <label>
              <span>Valid on</span>
              <input type="date" value={form.on} onChange={(e) => set("on", e.target.value)} required />
            </label>
            <button className="go" type="submit" disabled={busy}>
              {busy ? "Searching" : "Find rates"}
            </button>
          </div>
        </form>

        <section aria-live="polite" className="results">
          {error && <p className="error">{error}</p>}
          {result?.mode === "freight" && <FreightTable r={result} />}
          {result?.mode === "door" && <DoorTable r={result} />}
          {result?.mode === "trucking" && <TruckingTable r={result} />}
          {!result && !error && (
            <p className="hint">
              Enter a lane to see every valid buy rate, cheapest first, with all charges added up. Start from a factory
              zone to add pickup trucking and compare ports.
            </p>
          )}
        </section>
    </>
  );
}

type AnyResult = FreightResult | DoorResult | TruckingResult;

function Summary({ r, count, unit }: { r: AnyResult; count: number; unit: string }) {
  const n = r.options.length;
  return (
    <div className="summary">
      <h1>
        {r.origin.name} to {r.destination.name}
      </h1>
      <p>
        {n === 0 ? "No valid rates" : n === 1 ? "1 rate" : `${n} rates`} for {count} {unit}
        {count === 1 ? "" : r.mode === "trucking" ? "s" : "es"} of {r.equipment}, valid on {day(r.on)}
        {r.mode !== "freight" && r.weight_class && `, ${r.weight_class} (cargo ${r.weight_t} t)`}
      </p>
    </div>
  );
}

function FxNote({ r }: { r: AnyResult }) {
  const searchMonth = r.on.slice(0, 7);
  const stale = r.fx.filter((f) => f.on_date.slice(0, 7) < searchMonth);
  const missing = new Set<string>();
  for (const o of r.options as { missing_fx?: string[] }[]) for (const c of o.missing_fx ?? []) missing.add(c);
  const monthOf = (iso: string) =>
    new Date(Number(iso.slice(0, 4)), Number(iso.slice(5, 7)) - 1, 1).toLocaleDateString("en-GB", { month: "long" });
  return (
    <>
      {r.fx.length > 0 && (
        <p className="fx">
          Converted at{" "}
          {r.fx.map((f, i) => (
            <Fragment key={f.currency}>
              {i > 0 && ", "}
              {Number(f.per_usd)} {f.currency} per USD ({monthOf(f.on_date)} rate)
            </Fragment>
          ))}
          .
        </p>
      )}
      {stale.length > 0 && (
        <p className="warn-note">
          No {monthOf(r.on)} rate yet for {stale.map((f) => f.currency).join(", ")}, so the latest earlier one was used.
          Enter it on <a href="/exchange-rates">Exchange rates</a>.
        </p>
      )}
      {missing.size > 0 && (
        <p className="warn-note">
          Totals need a {[...missing].join(", ")} rate. Enter it on <a href="/exchange-rates">Exchange rates</a>.
        </p>
      )}
    </>
  );
}

function Empty({ r }: { r: AnyResult }) {
  return (
    <p className="hint">
      Nothing on file for {r.origin.code} to {r.destination.code} in {r.equipment} on {day(r.on)}. Try another date or
      equipment, or ask Pricing to source this lane.
    </p>
  );
}

function Validity({ to, daysLeft, soon }: { to: string; daysLeft: number; soon: boolean }) {
  return (
    <td className={soon ? "valid soon" : "valid"}>
      {day(to)}
      <small>{validity(daysLeft)}</small>
    </td>
  );
}

function FreightTable({ r }: { r: FreightResult }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!r.options.length)
    return (
      <>
        <Summary r={r} count={r.containers} unit="box" />
        <Empty r={r} />
      </>
    );
  const multi = r.containers > 1;
  return (
    <>
      <Summary r={r} count={r.containers} unit="box" />
      <div className="scroll">
        <table className="rates">
          <thead>
            <tr>
              <th scope="col">Carrier</th>
              <th scope="col" className="num">Total, USD</th>
              {multi && <th scope="col" className="num">Per box</th>}
              <th scope="col">Transit</th>
              <th scope="col">Free time</th>
              <th scope="col">Valid until</th>
            </tr>
          </thead>
          <tbody>
            {r.options.map((o, i) => (
              <FreightRow
                key={o.freight_rate_id}
                o={o}
                best={i === 0 && o.total_usd !== null}
                multi={multi}
                open={open === o.freight_rate_id}
                toggle={() => setOpen(open === o.freight_rate_id ? null : o.freight_rate_id)}
              />
            ))}
          </tbody>
        </table>
      </div>
      <FxNote r={r} />
    </>
  );
}

function FreightRow({ o, best, multi, open, toggle }: {
  o: FreightOption; best: boolean; multi: boolean; open: boolean; toggle: () => void;
}) {
  const cols = multi ? 6 : 5;
  const transit = [o.transit_days && days(o.transit_days), o.routing, o.via && `via ${o.via}`]
    .filter(Boolean).join(", ");
  return (
    <>
      <tr className={best ? "best" : undefined}>
        <th scope="row">
          <button type="button" className="expand" aria-expanded={open} onClick={toggle}>
            {o.carrier}
          </button>
          {o.rate_type !== "contract" && <small>{o.rate_type === "spot" ? "Spot rate" : "Tariff rate"}</small>}
          {best && <small className="tag">Cheapest</small>}
        </th>
        <td className="num total">
          {o.total_usd !== null ? money(o.total_usd) : <span className="warn">Needs {o.missing_fx.join(", ")} rate</span>}
          <NotesFlag notes={o.sheet_notes} onOpen={toggle} open={open} />
        </td>
        {multi && <td className="num">{money(o.per_container_usd)}</td>}
        <td>{transit || <span className="muted">not given</span>}</td>
        <td>{o.free_time_terms ?? (o.free_days !== null ? days(o.free_days) : <span className="muted">not given</span>)}</td>
        <Validity to={o.valid_to} daysLeft={o.days_left} soon={o.expiring_soon} />
      </tr>
      {open && (
        <tr className="detail">
          <td colSpan={cols}>
            <Charges o={o} />
            <SheetNotes notes={o.sheet_notes} />
            {o.remarks && <p className="remarks">{o.remarks}</p>}
          </td>
        </tr>
      )}
    </>
  );
}

function TruckingTable({ r }: { r: TruckingResult }) {
  if (!r.options.length)
    return (
      <>
        <Summary r={r} count={r.trips} unit="trip" />
        <Empty r={r} />
      </>
    );
  const showClass = r.equipment === "20DV" && !r.weight_class;
  const multi = r.trips > 1;
  const anyUsd = r.options.some((o) => o.total_usd !== null);
  return (
    <>
      <Summary r={r} count={r.trips} unit="trip" />
      {showClass && <p className="hint">No cargo weight given, so both 20DV classes are listed: light is under 8 t.</p>}
      <div className="scroll">
        <table className="rates">
          <thead>
            <tr>
              <th scope="col">Trucker</th>
              {showClass && <th scope="col">Weight</th>}
              <th scope="col" className="num">Per trip</th>
              {multi && <th scope="col" className="num">Total</th>}
              {anyUsd && <th scope="col" className="num">USD</th>}
              <th scope="col">Valid until</th>
            </tr>
          </thead>
          <tbody>
            {r.options.map((o, i) => (
              <tr key={o.transport_rate_id} className={i === 0 && !showClass ? "best" : undefined}>
                <th scope="row">
                  {o.trucker}
                  {o.applies_to !== "both" && <small>{o.applies_to === "import" ? "Import price" : "Export price"}</small>}
                  {i === 0 && !showClass && <small className="tag">Cheapest</small>}
                </th>
                {showClass && <td>{o.weight_class}</td>}
                <td className="num total">{moneyIn(o.price, o.currency)} {o.currency}</td>
                {multi && <td className="num">{moneyIn(o.total, o.currency)} {o.currency}</td>}
                {anyUsd && <td className="num">{money(o.total_usd)}</td>}
                <Validity to={o.valid_to} daysLeft={o.days_left} soon={o.expiring_soon} />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="fx">
        Buy prices, one-way trip{" "}
        {r.direction === "export" ? `from ${r.destination.name} to the port` : `from the port to ${r.destination.name}`}.
        Where the sheet gives separate import and export prices, only the {r.direction ?? "matching"} price is shown.
      </p>
      <FxNote r={r} />
    </>
  );
}

function Charges({ o }: { o: FreightOption }) {
  return (
    <table className="charges">
      <caption>Charges on this rate</caption>
      <thead>
        <tr>
          <th scope="col">Charge</th>
          <th scope="col" className="num">Rate</th>
          <th scope="col">Basis</th>
          <th scope="col" className="num">Qty</th>
          <th scope="col" className="num">Amount</th>
          <th scope="col" className="num">USD</th>
        </tr>
      </thead>
      <tbody>
        {o.charges.map((c, i) => (
          <tr key={i}>
            <th scope="row">{c.name}</th>
            <td className="num">{moneyIn(c.amount, c.currency)} {c.currency}</td>
            <td>{basis(c.basis)}</td>
            <td className="num">{c.quantity}</td>
            <td className="num">{moneyIn(c.line_total, c.currency)} {c.currency}</td>
            <td className="num">{c.line_usd !== null ? money(c.line_usd) : <span className="warn">no rate</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function DoorTable({ r }: { r: DoorResult }) {
  const [open, setOpen] = useState<number | null>(null);
  const tried = r.ports_checked.map((p) => p.name).join(", ");
  if (!r.options.length)
    return (
      <>
        <Summary r={r} count={r.containers} unit="box" />
        <p className="hint">
          {r.ports_checked.length
            ? `Trucking from ${r.origin.name} is priced to ${tried}, but none of these ports has a valid ${r.equipment} freight rate to ${r.destination.name} on ${day(r.on)}. Ask Pricing to source this lane.`
            : `No trucking price from ${r.origin.name} in ${r.equipment} on ${day(r.on)}. Search from a port instead, or ask Pricing to add the trucking rate.`}
        </p>
      </>
    );
  const multi = r.containers > 1;
  return (
    <>
      <Summary r={r} count={r.containers} unit="box" />
      <p className="fx">
        Pickup from {r.origin.name} to the port, then ocean freight. Ports compared: {tried}.
      </p>
      <div className="scroll">
        <table className="rates">
          <thead>
            <tr>
              <th scope="col">Via port and carrier</th>
              <th scope="col" className="num">Total, USD</th>
              {multi && <th scope="col" className="num">Per box</th>}
              <th scope="col" className="num">Trucking</th>
              <th scope="col" className="num">Freight, USD</th>
              <th scope="col">Transit</th>
              <th scope="col">Valid until</th>
            </tr>
          </thead>
          <tbody>
            {r.options.map((o, i) => (
              <DoorRow
                key={o.freight.freight_rate_id}
                o={o}
                best={i === 0 && o.total_usd !== null}
                multi={multi}
                open={open === o.freight.freight_rate_id}
                toggle={() => setOpen(open === o.freight.freight_rate_id ? null : o.freight.freight_rate_id)}
              />
            ))}
          </tbody>
        </table>
      </div>
      <FxNote r={r} />
    </>
  );
}

function DoorRow({ o, best, multi, open, toggle }: {
  o: DoorOption; best: boolean; multi: boolean; open: boolean; toggle: () => void;
}) {
  const f = o.freight;
  const t = o.trucking;
  // The option is only as valid as its shorter-lived leg.
  const ends = f.days_left <= t.days_left ? f : t;
  return (
    <>
      <tr className={best ? "best" : undefined}>
        <th scope="row">
          <button type="button" className="expand" aria-expanded={open} onClick={toggle}>
            {o.port.name}, {f.carrier}
          </button>
          {f.rate_type !== "contract" && <small>{f.rate_type === "spot" ? "Spot rate" : "Tariff rate"}</small>}
          {best && <small className="tag">Cheapest</small>}
        </th>
        <td className="num total">
          {o.total_usd !== null ? money(o.total_usd) : <span className="warn">Needs {o.missing_fx.join(", ")} rate</span>}
          <NotesFlag notes={f.sheet_notes} onOpen={toggle} open={open} />
        </td>
        {multi && <td className="num">{money(o.per_container_usd)}</td>}
        <td className="num">
          {moneyIn(t.total, t.currency)} {t.currency}
          <small>
            {t.trucker}
            {t.applies_to === "export" && ", export price"}
            {o.other_truckers > 0 && `, cheapest of ${o.other_truckers + 1}`}
          </small>
        </td>
        <td className="num">
          {f.total_usd !== null ? money(f.total_usd) : <span className="warn">Needs {f.missing_fx.join(", ")} rate</span>}
        </td>
        <td>{f.transit_days ? days(f.transit_days) : <span className="muted">not given</span>}</td>
        <Validity to={ends.valid_to} daysLeft={ends.days_left} soon={o.expiring_soon} />
      </tr>
      {open && (
        <tr className="detail">
          <td colSpan={multi ? 7 : 6}>
            <p className="leg">
              Pickup: {t.trucker}, {moneyIn(t.price, t.currency)} {t.currency} per trip × {t.trips}
              {t.weight_class && ` (${t.weight_class})`}, valid until {day(t.valid_to)}
            </p>
            <Charges o={f} />
            <SheetNotes notes={f.sheet_notes} />
            {f.remarks && <p className="remarks">{f.remarks}</p>}
          </td>
        </tr>
      )}
    </>
  );
}

type Note = FreightOption["sheet_notes"][number];

/** Under the total: the sheet row had something the import could not take, so the total may be short. */
function NotesFlag({ notes, onOpen, open }: { notes: Note[]; onOpen: () => void; open: boolean }) {
  if (!notes.length) return null;
  return (
    <small>
      <button type="button" className="note-flag" onClick={onOpen} aria-expanded={open}>
        {notes.length === 1 ? "1 sheet note" : `${notes.length} sheet notes`}: check before quoting
      </button>
    </small>
  );
}

function SheetNotes({ notes }: { notes: Note[] }) {
  if (!notes.length) return null;
  return (
    <div className="sheet-notes">
      <p>Not included above, as written on the sheet:</p>
      <ul>
        {notes.map((n, i) => (
          <li key={i}>
            {n.message}
            {n.raw && <q>{n.raw}</q>}
          </li>
        ))}
      </ul>
    </div>
  );
}
