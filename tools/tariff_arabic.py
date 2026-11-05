"""Arabic descriptions from the Customs Encyclopedia's tariff tables (pages 30-495), read with PyMuPDF.

The PDF stores Arabic as visual-order pieces: a printed row is rebuilt from the pieces on the same baseline, right
to left, joined as stored (each piece carries its own word spaces). Brackets come mirrored and are swapped back.
An entry's text is its heading (NN.NN row, with any start printed just above it), the dash groups it sits under
("- زبد:") and its own line. The result is a reading aid: a few lines keep the PDF's split words or spacing, and
the Encyclopedia page is the reference. Needs pymupdf (dev only: pip install pymupdf).
"""
import re
import unicodedata

import pymupdf

CODE = re.compile(r"(?<![\d.])(\d{4}\.\d{2}(?:\d{2})?)(?![\d.])")
HEAD = re.compile(r"^\s*(\d{2}\.\d{2})(?![\d.])")
RATE = re.compile(r"^(\d{1,3}(?:\.\d+)?|معفاه|معفاة)$")
NOISE = ("الجريدة الرسمية", "رمز النظام", "الفئة", "المنسق", "الصنف", "(%)", "%)(")
FIX = str.maketrans({"ی": "ي", "ک": "ك", "ѧ": None, "ـ": None, "(": ")", ")": "("})


def norm(t):
    t = unicodedata.normalize("NFKC", t).translate(FIX)
    return "".join(c for c in t if unicodedata.category(c) not in ("Cc", "Co", "Cs", "Cn"))   # symbol-font and stray codes


CODE_X0 = 464      # tariff code column: pieces starting right of this
RATE_X1 = 110      # rate column: pieces ending left of this


def visual_lines(page):
    """Printed lines rebuilt from text pieces: same baseline, right to left, joined exactly as stored (each piece
    carries its own word spaces; the PDF splits some words into pieces with no space between them)."""
    spans = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for sp in line["spans"]:
                if sp["text"].strip():
                    spans.append((sp["bbox"], sp["text"]))
    spans.sort(key=lambda x: ((x[0][1] + x[0][3]) / 2, -x[0][2]))
    rows = []
    for bbox, text in spans:
        mid = (bbox[1] + bbox[3]) / 2
        if rows and abs(rows[-1][0] - mid) <= 2.5:
            rows[-1][1].append((bbox, text))
        else:
            rows.append([mid, [(bbox, text)]])
    out = []
    for _, pieces in rows:
        code, rate, desc = "", "", ""
        for bbox, text in sorted(pieces, key=lambda x: -x[0][2]):
            text = norm(text)
            text = re.sub(r"^(\s*)([.،:؛])\s+(\S[^.،:؛]*)$", r"\1\3\2", text)   # ". حية" printed RTL = "حية."
            if bbox[0] >= CODE_X0:
                code += text
            elif bbox[2] <= RATE_X1:
                rate += text
            else:
                desc += text
        out.append((code.strip(), rate.strip(), desc))
    return out


def tidy(t):
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"(^|\s)\u064b+(\S+?)\u064b*(?=\s|$)", lambda m: m.group(1) + m.group(2) + "\u064b", t)         # tanween printed before its word: "ًمعا" -> "معاً"
    t = re.sub(r'"\s*([^"]*?)\s*"', r'"\1"', t)
    t = re.sub(r"\)([A-Za-z0-9][A-Za-z0-9 ,.+/-]*)\(", r"(\1)", t)      # Latin inside mirrored brackets: ")HFC-125(" -> "(HFC-125)"
    t = re.sub(r"\(\s+", "(", t); t = re.sub(r"\s+\)", ")", t)
    t = re.sub(r"\s+([،,.؛;:])", r"\1", t)
    return t.strip(" -").lstrip("،؛:. ")


def extract(pdf: str, pages=range(30, 496)) -> dict[str, str]:
    """{code without dot: 'heading — group(s) — line'} for every line printed with a code."""
    doc = pymupdf.open(pdf)
    items, head_text, head_code, groups, block, mode = {}, "", "", {}, None, None

    def close():
        nonlocal block
        if block:
            if block["code"]:
                level = block["level"]
                if block["code"].replace(".", "")[:4] == head_code:
                    ctx = [tidy(v) for k, v in sorted(groups.items()) if k < level]
                    parts = [tidy(head_text)] + ctx + [tidy(block["text"])]
                else:                       # a heading with a single line (e.g. 0205.00) prints no NN.NN row
                    parts = [tidy(block["text"])]
                parts = [p.rstrip(" :") for p in parts if p.rstrip(" :")]
                items.setdefault(block["code"].replace(".", ""), " — ".join(parts))
            elif block["text"].rstrip().endswith(":"):
                for k in [k for k in groups if k >= block["level"]]:
                    del groups[k]
                groups[block["level"]] = block["text"]
        block = None

    since_code = []   # text lines since the last row with a code or rate
    pending = []      # lines after a complete entry (code and rate seen): next heading's start, or the entry's tail

    def flush_to_block():
        if pending and block is not None:
            block["text"] += " " + " ".join(pending)
        pending.clear()

    head_item = None

    def emit_head_item():
        """A NNNN.00 line read as a heading whose rate sits on a later row and that has no lines under it."""
        if head_item and not any(c.startswith(head_item[:4]) for c in items):
            items[head_item] = tidy(head_text).rstrip(" :")

    def take_start():
        """Lines just above a code row with no text of its own: its start, not the previous entry's tail."""
        tail = [t for t in since_code[-2:] if not re.match(r"^(\d+\.\s|_+$)", t)]   # "2. أحماض ..." is a sub-chapter title
        if not pending and block is not None:
            for t in reversed(since_code[-2:]):
                if block["text"].endswith(" " + t):
                    block["text"] = block["text"][: -len(t) - 1]
        pending.clear(); since_code.clear()
        return " ".join(tail)

    for pno in pages:
        since_code.clear()            # a heading starts on its own page, just above its NN.NN row
        for code_cell, rate_cell, desc in visual_lines(doc[pno - 1]):
            whole = f"{code_cell} {desc} {rate_cell}"
            if any(n in whole for n in NOISE):
                since_code.clear(); continue          # the table's column header: notes above it are not a heading
            if re.fullmatch(r"[\d\s٠-٩]+", whole):
                continue
            if re.fullmatch(r"\s*_{5,}\s*", desc) and not code_cell:     # a rule under the last line of a chapter
                flush_to_block(); close(); mode = "notes"; continue
            if re.match(r"^\s*(الفصل|القسم|ملاحظ)", desc) and not code_cell:
                pending.clear(); close(); mode = "notes"; continue
            hm = re.fullmatch(r"(\d{2}\.\d{2})", code_cell)
            cm = CODE.fullmatch(code_cell)
            if not cm and not hm:
                inline = CODE.match(desc.strip())          # a code glued to its text, e.g. "3004.9010ـ ـ ـ أدوية"
                if inline:
                    cm, desc = inline, desc.strip()[inline.end():]
                elif not code_cell and not rate_cell and mode != "notes":
                    glued = re.match(r"\s*(\d{2}\.\d{2})(?=[\u0600-\u06ff])", desc)   # "74.19أصناف أخر من نحاس."
                    if glued:
                        hm, desc = glued, desc[glued.end():]
            code = cm.group(1) if cm else None
            text = desc.strip()
            if mode == "notes" and code and not hm and rate_cell and not RATE.match(rate_cell):
                code, rate_cell = None, ""                # a code quoted in a note: prose runs into the rate column
            if not hm and code and code.endswith(".00") and not rate_cell and not text.startswith("-"):
                hm = re.match(r"(\d{4})", code)          # "1004.00 شوفان." with lines 1004.10 ... under it
            if hm and text:                               # heading text on its own row: lines above end the last entry
                flush_to_block(); since_code.clear()
            if hm or (code and not text):
                start = take_start()
            if hm:
                close(); emit_head_item()
                head_item = code.replace(".", "") if code else None
                mode = "head"; head_text = (start + " " + text).strip(); groups = {}
                head_code = hm.group(1).replace(".", ""); continue
            if code and not text:                         # a one-line heading printed with its text above the code
                text = start
            if code or rate_cell:
                since_code.clear()
            elif text and not re.fullmatch(r"[_\s]+", text):
                since_code.append(text)
            if mode == "head" and code is None and not text.startswith("-"):
                head_text += " " + text; continue
            if text.startswith("-"):
                flush_to_block(); close(); mode = "item"
                level = len(re.match(r"^[-\s]+", text).group(0).replace(" ", ""))
                block = {"code": code, "text": text, "level": level, "rated": bool(rate_cell)}
            elif code and (block is None or block["code"]):
                flush_to_block(); close(); mode = "item"
                block = {"code": code, "text": text, "level": 1, "rated": bool(rate_cell)}
            elif block is not None and mode == "item":
                if block.get("rated") and block["code"] and not code:
                    if text:
                        pending.append(text)
                    continue
                if text:
                    block["text"] += " " + text
                if code and not block["code"]:      # the code can sit on a line of its own, mid-entry
                    block["code"] = code
                if rate_cell:
                    block["rated"] = True
    flush_to_block()
    close()
    emit_head_item()
    return items
