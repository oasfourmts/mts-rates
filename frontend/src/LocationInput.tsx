import { useEffect, useId, useState } from "react";
import { api, type Place } from "./api";

interface Props {
  label: string;
  value: string;
  onChange: (v: string) => void;
  kind?: string; // e.g. "seaport" or "inland_zone"
  placeholder?: string;
}

/** Free text with suggestions from the location list. The server accepts a code, a name or any alias,
 *  so a typed "PSD East" works as well as picking EGPSE. */
export function LocationInput({ label, value, onChange, kind, placeholder }: Props) {
  const id = useId();
  const [places, setPlaces] = useState<Place[]>([]);

  useEffect(() => {
    const q = value.trim();
    if (q.length < 2) {
      setPlaces([]);
      return;
    }
    let live = true;
    const t = setTimeout(() => {
      api.locations(q, kind).then((p) => live && setPlaces(p)).catch(() => live && setPlaces([]));
    }, 180);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [value, kind]);

  return (
    <label className="place">
      <span className="place-label">{label}</span>
      <input
        list={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete="off"
        spellCheck={false}
        required
      />
      <datalist id={id}>
        {places.map((p) => (
          <option key={p.code} value={p.code}>
            {p.name}
            {p.country_code ? `, ${p.country_code}` : ""}
          </option>
        ))}
      </datalist>
    </label>
  );
}
