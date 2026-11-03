import { useEffect, useMemo, useState, type FormEvent } from "react";
import { api, ApiError, type FxRow } from "./api";
import { cairoToday, money } from "./format";

const monthName = (ym: string) => {
  const [y, m] = ym.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("en-GB", { month: "long", year: "numeric" });
};

const plain = (v: string) => String(Number(v)); // "50.250000" -> "50.25"

/** Monthly exchange rates, entered by hand. A month's rate applies from the 1st until the next month is entered. */
export function FxPage({ isAdmin }: { isAdmin: boolean }) {
  const [rows, setRows] = useState<FxRow[]>([]);
  const [currencies, setCurrencies] = useState<{ code: string; name: string }[]>([]);
  const [loadError, setLoadError] = useState("");
  const [currency, setCurrency] = useState("EGP");
  const [month, setMonth] = useState(cairoToday().slice(0, 7));
  const [rate, setRate] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmNeeded, setConfirmNeeded] = useState("");
  const [saved, setSaved] = useState("");
  const [loaded, setLoaded] = useState(false);

  async function load() {
    try {
      const [r, c] = await Promise.all([api.fxRates(13), api.currencies()]);
      setRows(r);
      setCurrencies(c);
      setLoadError("");
      setLoaded(true);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "The rates did not load. Reload the page.");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  // Months down the side (newest first), currencies across: those that have a rate, plus EGP and EUR.
  const { months, codes, cell } = useMemo(() => {
    const cell = new Map<string, FxRow>();
    for (const r of rows) cell.set(`${r.on_date.slice(0, 7)}|${r.currency}`, r);
    const months = [...new Set([cairoToday().slice(0, 7), ...rows.map((r) => r.on_date.slice(0, 7))])].sort().reverse();
    const codes = [...new Set(["EGP", "EUR", ...rows.map((r) => r.currency)])].sort();
    return { months, codes, cell };
  }, [rows]);

  const n = Number(rate.replace(/,/g, ""));
  const valid = rate.trim() !== "" && Number.isFinite(n) && n > 0;

  // Prefill the form with what is already stored for that month, so a correction starts from it. Runs when the
  // month or currency changes (and once the list arrives), not after every save.
  useEffect(() => {
    const existing = cell.get(`${month}|${currency}`);
    setRate(existing ? plain(existing.per_usd) : "");
    setError("");
    setConfirmNeeded("");
    setSaved("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [month, currency, loaded]);

  async function save(confirm: boolean) {
    setBusy(true);
    setError("");
    setSaved("");
    try {
      const row = await api.saveFx(currency, month, rate.replace(/,/g, "").trim(), confirm);
      setConfirmNeeded("");
      await load();
      setSaved(`Saved: ${monthName(month)}, 1 USD = ${plain(row.per_usd)} ${row.currency}.`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setConfirmNeeded(e.message);
      else setError(e instanceof Error ? e.message : "The rate was not saved. Try again.");
    } finally {
      setBusy(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    void save(false);
  }

  return (
    <>
      <div className="summary">
        <h1>Exchange rates</h1>
        <p>
          One rate per currency per month. Rate Search uses a month's rate from the 1st until the next month is
          entered.
        </p>
      </div>

      {isAdmin ? (
        <form className="lane fx-form" onSubmit={submit}>
          <div className="cargo">
            <label>
              <span>Month</span>
              <input type="month" value={month} onChange={(e) => setMonth(e.target.value)} required />
            </label>
            <label>
              <span>Currency</span>
              <select value={currency} onChange={(e) => setCurrency(e.target.value)}>
                {(currencies.length ? currencies : [{ code: "EGP", name: "Egyptian pound" }]).map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.code}, {c.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="rate-in">
              <span>1 USD buys</span>
              <span className="with-unit">
                <input inputMode="decimal" value={rate} onChange={(e) => {
                    setRate(e.target.value);
                    setConfirmNeeded("");
                  }}
                  required
                />
                <b>{currency}</b>
              </span>
            </label>
            <button className="go" type="submit" disabled={busy || !valid}>
              {busy ? "Saving" : "Save rate"}
            </button>
          </div>
          {valid && (
            <p className="check">
              Check: 1,000 {currency} = USD {money(String(1000 / n))}, and 1 {currency} = USD {money(String(1 / n), 4)}.
            </p>
          )}
          {confirmNeeded && (
            <p className="error">
              {confirmNeeded}{" "}
              <button type="button" className="inline" onClick={() => void save(true)} disabled={busy}>
                Save anyway
              </button>
            </p>
          )}
          {error && <p className="error">{error}</p>}
          {saved && <p className="ok">{saved}</p>}
        </form>
      ) : (
        <p className="hint">Only admins can enter exchange rates. Ask an admin if a month is missing.</p>
      )}

      {loadError && <p className="error">{loadError}</p>}
      <div className="scroll">
        <table className="rates fx-grid">
          <caption className="fx">Units of each currency per 1 USD</caption>
          <thead>
            <tr>
              <th scope="col">Month</th>
              {codes.map((c) => (
                <th key={c} scope="col" className="num">{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {months.map((m) => (
              <tr key={m}>
                <th scope="row">{monthName(m)}</th>
                {codes.map((c) => {
                  const r = cell.get(`${m}|${c}`);
                  return (
                    <td key={c} className="num">
                      {r ? (
                        <>
                          {plain(r.per_usd)}
                          {r.entered_by && <small>{r.entered_by.split("@")[0]}</small>}
                        </>
                      ) : (
                        <span className="muted">not entered</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
