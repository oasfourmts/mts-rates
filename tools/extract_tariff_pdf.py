"""Extract general import duty rates from the Egyptian Customs Encyclopedia (الموسوعة الجمركية, January 2025):
Presidential Decree 218 of 2022 with its amendments, Arabic tariff tables on pages 30-495 and the English HS
nomenclature on pages 496 onwards.

    python tools/extract_tariff_pdf.py <encyclopedia.pdf> data/tariff_2025.csv

Output columns: code (6 or 8 digits, no dot), duty_pct, description_en (6-digit HS text from the English part;
8-digit national lines inherit their 6-digit text), page (where the rate is printed), note.

Rules (checked against customs.gov.eg on 5 Nov 2026: 13 of 13 lines found there matched):
- A tariff line is a line whose first code is NNNN.NN or NNNN.NNNN; its rate is the number (or معفاة, exempt =
  0) at the start of that line, or of one of the next two lines of a long entry.
- A line whose text after the code contains ":" is a heading; its sub-lines carry the rates.
- Rates outside the tariff's usual set are dropped as layout slips (they are page numbers or headings) and
  listed on stderr for a person to check. Chapter 24 (tobacco) is laid out differently and is not extracted.
"""
from __future__ import annotations

import csv
import re
import subprocess
import sys
import unicodedata

ARABIC_PAGES = range(30, 496)
ENGLISH_FROM = 496
USUAL_RATES = {0, 2, 5, 10, 20, 30, 35, 40, 45, 50, 60, 100, 135}
BIDI = dict.fromkeys(map(ord, "‪‫‬‭‮‎‏"), None)
CODE = re.compile(r"(?<![\d.])(\d{4}\.\d{2}(?:\d{2})?)(?![\d.])")
RATE = re.compile(r"^\s*(\d{1,3}(?:\.\d+)?|معفاه|معفاة)(?=\s{2,}|\s*$)")
EN_LINE = re.compile(r"^\s{4,}(\d{4}\.\d{2})\s{2,}(.*)$")
EN_CONT = re.compile(r"^\s{12,}(\S.*)$")
EN_HEADING = re.compile(r"^\s{0,3}(\d{2}\.\d{2})\s{2,}(\S.*)$")


def pdf_pages(path: str) -> list[str]:
    text = subprocess.run(["pdftotext", "-layout", path, "-"], check=True, capture_output=True).stdout.decode("utf-8")
    return text.split("\f")


def rate_of(text: str):
    m = RATE.match(text)
    if not m:
        return None
    return 0.0 if m.group(1).startswith("معف") else float(m.group(1))


def arabic_rates(pages: list[str]) -> tuple[dict[str, tuple[float, int]], list[tuple[str, float, int]]]:
    lines = [(p, unicodedata.normalize("NFKC", line.translate(BIDI)))
             for p in ARABIC_PAGES for line in pages[p - 1].splitlines()]
    rates, dropped = {}, []
    for k, (page, text) in enumerate(lines):
        codes = CODE.findall(text)
        if not codes:
            continue
        code = codes[0]
        if ":" in text[text.index(code) + len(code):]:
            continue
        rate = rate_of(text)
        for step in (1, 2):
            if rate is not None or k + step >= len(lines) or CODE.search(lines[k + step][1]):
                break
            rate = rate_of(lines[k + step][1])
        if rate is None or code in rates:
            continue
        if rate not in USUAL_RATES:
            dropped.append((code, rate, page))
            continue
        rates[code] = (rate, page)
    return rates, dropped


def english_descriptions(pages: list[str]) -> dict[str, str]:
    """6-digit code -> "heading text — subheading text", from the English HS nomenclature."""
    heads, subs, current = {}, {}, None
    for page in pages[ENGLISH_FROM - 1:]:
        for line in page.splitlines():
            h, m = EN_HEADING.match(line), EN_LINE.match(line)
            if h:
                current = ("h", h.group(1))
                heads[h.group(1)] = h.group(2).strip()
                continue
            if m:
                current = ("s", m.group(1))
                subs[m.group(1)] = m.group(2).strip()
                continue
            c = EN_CONT.match(line)
            if current and c and not re.match(r"^-+\s", c.group(1)) and not CODE.match(c.group(1)):
                (heads if current[0] == "h" else subs)[current[1]] += " " + c.group(1).strip()
            else:
                current = None
    tidy = lambda v: re.sub(r"^[-\s]+|^\d{4}\.\d{2}\s+", "", re.sub(r"\s+", " ", v)).strip().rstrip(".")  # noqa: E731
    out = {}
    for code, text in subs.items():
        head = heads.get(f"{code[:2]}.{code[2:4]}")
        out[code] = f"{tidy(head)} — {tidy(text)}" if head and tidy(text) != tidy(head) else tidy(text)
    for heading, text in heads.items():          # NNNN.00 lines: the heading itself
        out.setdefault(f"{heading[:2]}{heading[3:]}.00", tidy(text))
    return out


def main(pdf: str, out_csv: str) -> int:
    pages = pdf_pages(pdf)
    rates, dropped = arabic_rates(pages)
    english = english_descriptions(pages)
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["code", "duty_pct", "description_en", "page", "note"])
        for code in sorted(rates, key=lambda c: c.replace(".", "")):
            rate, page = rates[code]
            six = code[:7]
            note = "" if len(code) == 7 else "national sub-line"
            w.writerow([code.replace(".", ""), f"{rate:g}", english.get(six, ""), page, note])
    print(f"{len(rates)} tariff lines written to {out_csv}; {sum(1 for c in rates if c[:7] in english)} with an "
          f"English description", file=sys.stderr)
    for code, rate, page in dropped:
        print(f"check by hand: {code} read as {rate:g} on page {page}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:3]))
