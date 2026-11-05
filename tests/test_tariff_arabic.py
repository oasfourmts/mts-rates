"""Text clean-up rules of tools/tariff_arabic.py (the PDF reading itself needs the Encyclopedia and pymupdf)."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("pymupdf")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import tariff_arabic  # noqa: E402


@pytest.mark.parametrize("raw, tidy", [
    ("ًًمصل اللبن", "مصلً اللبن"),                 # tanween printed before its word
    ("أسماك )HFC-125( مجمدة", "أسماك (HFC-125) مجمدة"),          # Latin inside mirrored brackets
    ("،  حبوب كاكاو ، خام .", "حبوب كاكاو، خام."),              # stray leading comma, spaces before punctuation
    ("قمصان من نوع\" تى شيرت \"", "قمصان من نوع\"تى شيرت\""),
])
def test_tidy(raw, tidy):
    assert tariff_arabic.tidy(raw) == tidy


def test_norm_drops_symbol_font_codes_and_mirrors_brackets():
    assert tariff_arabic.norm("ی)INN(") == "ي(INN)"
