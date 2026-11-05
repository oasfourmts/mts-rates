"""Builds templates/MTS_Customs_Clearance_Rates.xlsx (python tools/build_customs_sheet.py): MTS customs clearance rate sheet: the layout the rate app imports (app/importers/customs.py)."""
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

OUT = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "templates" / "MTS_Customs_Clearance_Rates.xlsx")
F = "Arial"
TEAL = "1F5F5B"
H = Font(name=F, bold=True, color="FFFFFF", size=10)
HF = PatternFill("solid", fgColor=TEAL)
CALC_H = PatternFill("solid", fgColor="7A8A8E")
B = Font(name=F, size=10)
BB = Font(name=F, size=10, bold=True)
BLUE = Font(name=F, size=10, color="0000FF")
TITLE = Font(name=F, size=14, bold=True, color=TEAL)
NOTE = Font(name=F, size=9, italic=True, color="52636A")
EX = PatternFill("solid", fgColor="F1F3F2")          # example rows
INPUT = PatternFill("solid", fgColor="FFF2B3")       # calculator inputs
CALC = PatternFill("solid", fgColor="E9EDEC")        # calculator helper columns
thin = Side(style="thin", color="D5DCDA")
GRID = Border(left=thin, right=thin, top=thin, bottom=thin)
WRAP = Alignment(wrap_text=True, vertical="top")
CEN = Alignment(horizontal="center", vertical="top", wrap_text=True)
MAXR = 300   # rows prepared with dropdowns and calculator formulas

wb = Workbook()


def header(ws, row, names, widths, calc_from=None):
    for c, (n, w) in enumerate(zip(names, widths), 1):
        cell = ws.cell(row=row, column=c, value=n)
        cell.font, cell.alignment, cell.border = H, CEN, GRID
        cell.fill = CALC_H if calc_from and c >= calc_from else HF
        ws.column_dimensions[cell.column_letter].width = w
    ws.row_dimensions[row].height = 42
    ws.freeze_panes = ws.cell(row=row + 1, column=1)


def rows(ws, start, data, example_col=None, text_cols=(), fmt=None):
    for i, vals in enumerate(data):
        r = start + i
        for c, v in enumerate(vals, 1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.font, cell.border, cell.alignment = B, GRID, WRAP
            if c in text_cols:
                cell.number_format = "@"
            if fmt and c in fmt:
                cell.number_format = fmt[c]
            if example_col and vals[example_col - 1] == "Example":
                cell.fill = EX


def dropdown(ws, rng, source, title):
    dv = DataValidation(type="list", formula1=source, allow_blank=True, showErrorMessage=True,
                        errorTitle=title, error=f"Pick a value from the list ({title}).")
    ws.add_data_validation(dv)
    dv.add(rng)


# ======================= Lists (dropdown sources) =======================
lists = {
    "Row status": ["Active", "Draft", "Example"],
    "Direction": ["Import", "Export"],
    "Clearance point": ["Any", "Alexandria", "El Dekheila", "Damietta", "Port Said East", "Sokhna", "Adabiya",
                        "Cairo Airport"],
    "Shipment type": ["Any", "FCL", "LCL", "Air", "Courier", "Road"],
    "Purpose": ["Commercial import", "Temporary admission", "Re-export", "Project cargo", "Personal effects",
                "Samples", "Free zone"],
    "Fee item": ["Clearance fee", "Extra HS line", "Inspection attendance", "Documents and registration",
                 "Other"],
    "Basis": ["Per shipment", "Per container", "Per B/L", "Per HS line", "% of invoice value"],
    "Currency": ["EGP", "USD", "EUR"],
    "Yes/No": ["Yes", "No"],
    "Duties at clearance": ["Yes", "Guarantee", "No"],
}
L = wb.active
L.title = "Lists"
ref = {}
for c, (name, vals) in enumerate(lists.items(), 1):
    L.cell(row=1, column=c, value=name).font = BB
    for r, v in enumerate(vals, 2):
        L.cell(row=r, column=c, value=v).font = B
    col = L.cell(row=1, column=c).column_letter
    ref[name] = f"=Lists!${col}$2:${col}${len(vals) + 1}"
    L.column_dimensions[col].width = 24

# ======================= Documents =======================
D = wb.create_sheet("Documents", 0)
D["A1"] = "Documents"; D["A1"].font = TITLE
D["A2"] = "Codes used in the Documents columns. Add a row for a new document; keep codes short and unique."
D["A2"].font = NOTE
header(D, 4, ["Code", "Document", "Issued by", "Notes"], [10, 44, 34, 60])
DOCS = [
    ("CI", "Commercial invoice", "Shipper", "Values must match the customs declaration and the ACI filing"),
    ("PL", "Packing list", "Shipper", ""),
    ("BL", "Bill of lading / air waybill", "Carrier", ""),
    ("ACID", "ACID number (Advance Cargo Information)", "Importer, through Nafeza", "Filed before shipment"),
    ("CO", "Certificate of origin", "Chamber of commerce in the origin country", ""),
    ("EUR1", "EUR.1 / EUR-MED movement certificate", "Origin customs or chamber", "For EU, UK, EFTA and Turkey preferences"),
    ("CO-ARAB", "Arab certificate of origin (GAFTA)", "Origin chamber of commerce", "For GAFTA preference"),
    ("CO-COMESA", "COMESA certificate of origin", "Origin authority", "For COMESA preference"),
    ("IMP", "Importers register card", "Importer", ""),
    ("TAX", "Tax card and commercial register", "Importer", ""),
    ("POA", "Power of attorney to the broker (توكيل)", "Importer", ""),
    ("GOEIC", "GOEIC inspection / factory registration", "GOEIC", "Goods subject to quality control"),
    ("NFSA", "NFSA release", "National Food Safety Authority", "Food"),
    ("EDA", "EDA approval", "Egyptian Drug Authority", "Medicines, medical devices, cosmetics"),
    ("NTRA", "NTRA approval", "National Telecom Regulatory Authority", "Telecom and wireless devices"),
    ("GUAR", "Bank guarantee or cash deposit", "Importer's bank", "Temporary admission, re-export"),
    ("FZ", "Free zone approval", "GAFI", "Free zone cargo"),
]
rows(D, 5, DOCS)

# ======================= Purposes =======================
P = wb.create_sheet("Purposes", 0)
P["A1"] = "Purposes of the goods"; P["A1"].font = TITLE
P["A2"] = ("Each purpose has its own fees, duty treatment and documents. Pricing: check the last two columns and set "
           "\"Checked by pricing\" to Yes. The calculator uses \"Duties at clearance\".")
P["A2"].font = NOTE
header(P, 4, ["Purpose", "Arabic", "When it applies", "Duties at clearance", "Documents", "Checked by pricing",
              "Notes"], [20, 18, 46, 13, 34, 11, 46])
PURPOSES = [
    ("Commercial import", "استيراد تجاري", "Goods imported for sale or use in Egypt", "Yes",
     "CI;PL;BL;ACID;CO;IMP;TAX;POA", "No", ""),
    ("Temporary admission", "إفراج مؤقت", "Goods brought in for a limited time (exhibitions, processing, projects) and taken out again",
     "Guarantee", "CI;PL;BL;ACID;CO;POA;GUAR", "No", "Duties suspended against a guarantee: confirm conditions"),
    ("Re-export", "إعادة تصدير", "Goods that pass through Egypt and leave again", "Guarantee",
     "CI;PL;BL;ACID;POA;GUAR", "No", "Confirm conditions"),
    ("Project cargo", "مشروعات", "Equipment for an approved investment project", "Yes",
     "CI;PL;BL;ACID;CO;IMP;TAX;POA", "No", "Projects may qualify for exemptions: confirm per case"),
    ("Personal effects", "أمتعة شخصية", "Household goods and personal belongings", "Yes", "CI;PL;BL;POA", "No",
     "Exemptions may apply: confirm"),
    ("Samples", "عينات", "Samples with no commercial value", "Yes", "CI;PL;BL;POA", "No",
     "May be exempt: confirm"),
    ("Free zone", "منطقة حرة", "Goods entering a free zone", "No", "CI;PL;BL;ACID;POA;FZ", "No",
     "Duty and VAT apply only if the goods later enter Egypt: confirm"),
]
rows(P, 5, PURPOSES)
dropdown(P, "D5:D20", ref["Duties at clearance"], "Duties at clearance")
dropdown(P, "F5:F20", ref["Yes/No"], "Checked by pricing")

# ======================= Agreements =======================
A = wb.create_sheet("Agreements", 0)
A["A1"] = "Trade agreements"; A["A1"].font = TITLE
A["A2"] = ("Agreements that can lower the import duty when the goods have a valid certificate of origin. The code must "
           "match a column on the Duty Rates tab. Pricing: confirm each row and set \"Checked by pricing\" to Yes.")
A["A2"].font = NOTE
header(A, 4, ["Code", "Agreement", "Partners", "Certificate needed", "Checked by pricing", "Notes"],
       [11, 34, 52, 16, 11, 44])
AGREEMENTS = [
    ("EU", "EU-Egypt Association Agreement", "The 27 EU member states", "EUR1", "No",
     "In force since 1 June 2004"),
    ("UK", "UK-Egypt Association Agreement", "United Kingdom", "EUR1", "No", ""),
    ("EFTA", "EFTA-Egypt Free Trade Agreement", "Iceland, Liechtenstein, Norway, Switzerland", "EUR1", "No", ""),
    ("Turkey", "Turkey-Egypt Free Trade Agreement", "Turkey", "EUR1", "No", ""),
    ("Agadir", "Agadir Agreement", "Jordan, Morocco, Tunisia", "EUR1", "No", "Confirm the certificate type"),
    ("GAFTA", "Greater Arab Free Trade Area", "Arab League members in GAFTA", "CO-ARAB", "No",
     "Confirm the member list on the Countries tab"),
    ("COMESA", "COMESA Free Trade Area", "COMESA members in the free trade area", "CO-COMESA", "No",
     "Not every COMESA member is in the free trade area: confirm"),
    ("Mercosur", "Egypt-Mercosur Free Trade Agreement", "Argentina, Brazil, Paraguay, Uruguay", "CO", "No",
     "Confirm the certificate type"),
    ("AfCFTA", "African Continental Free Trade Area", "AfCFTA members", "CO", "No",
     "Confirm whether Egypt applies AfCFTA rates yet"),
]
rows(A, 5, AGREEMENTS)
dropdown(A, "E5:E30", ref["Yes/No"], "Checked by pricing")

# ======================= Countries =======================
C = wb.create_sheet("Countries", 0)
C["A1"] = "Countries of origin"; C["A1"].font = TITLE
C["A2"] = ("\"Agreement to use\" is the agreement whose rate applies to goods from that country (blank = general rate). "
           "Where a country is in several agreements, pricing picks the one used in practice.")
C["A2"].font = NOTE
header(C, 4, ["Country", "ISO code", "Agreement to use", "Notes"], [24, 9, 16, 50])
EU = [("Austria", "AT"), ("Belgium", "BE"), ("Bulgaria", "BG"), ("Croatia", "HR"), ("Cyprus", "CY"),
      ("Czechia", "CZ"), ("Denmark", "DK"), ("Estonia", "EE"), ("Finland", "FI"), ("France", "FR"),
      ("Germany", "DE"), ("Greece", "GR"), ("Hungary", "HU"), ("Ireland", "IE"), ("Italy", "IT"), ("Latvia", "LV"),
      ("Lithuania", "LT"), ("Luxembourg", "LU"), ("Malta", "MT"), ("Netherlands", "NL"), ("Poland", "PL"),
      ("Portugal", "PT"), ("Romania", "RO"), ("Slovakia", "SK"), ("Slovenia", "SI"), ("Spain", "ES"),
      ("Sweden", "SE")]
COUNTRIES = [(n, c, "EU", "") for n, c in EU]
COUNTRIES += [("United Kingdom", "GB", "UK", ""), ("Iceland", "IS", "EFTA", ""), ("Liechtenstein", "LI", "EFTA", ""),
              ("Norway", "NO", "EFTA", ""), ("Switzerland", "CH", "EFTA", ""), ("Turkey", "TR", "Turkey", ""),
              ("Jordan", "JO", "Agadir", "Also GAFTA"), ("Morocco", "MA", "Agadir", "Also GAFTA"),
              ("Tunisia", "TN", "Agadir", "Also GAFTA"),
              ("Algeria", "DZ", "GAFTA", "Confirm"), ("Bahrain", "BH", "GAFTA", "Confirm"), ("Iraq", "IQ", "GAFTA", "Confirm"),
              ("Kuwait", "KW", "GAFTA", "Confirm"), ("Lebanon", "LB", "GAFTA", "Confirm"), ("Libya", "LY", "GAFTA", "Confirm"),
              ("Oman", "OM", "GAFTA", "Confirm"), ("Palestine", "PS", "GAFTA", "Confirm"), ("Qatar", "QA", "GAFTA", "Confirm"),
              ("Saudi Arabia", "SA", "GAFTA", "Confirm"), ("Sudan", "SD", "GAFTA", "Confirm"), ("Syria", "SY", "GAFTA", "Confirm"),
              ("United Arab Emirates", "AE", "GAFTA", "Confirm"), ("Yemen", "YE", "GAFTA", "Confirm"),
              ("Kenya", "KE", "", "COMESA member: confirm whether the COMESA rate applies"),
              ("Argentina", "AR", "Mercosur", ""), ("Brazil", "BR", "Mercosur", ""), ("Paraguay", "PY", "Mercosur", ""),
              ("Uruguay", "UY", "Mercosur", ""),
              ("China", "CN", "", ""), ("India", "IN", "", ""), ("United States", "US", "", ""), ("Japan", "JP", "", ""),
              ("South Korea", "KR", "", ""), ("Vietnam", "VN", "", ""), ("Indonesia", "ID", "", ""),
              ("Malaysia", "MY", "", ""), ("Thailand", "TH", "", ""), ("Russia", "RU", "", ""),
              ("Ukraine", "UA", "", "")]
rows(C, 5, COUNTRIES)
C.auto_filter.ref = f"A4:D{4 + len(COUNTRIES)}"
dropdown(C, f"C5:C{MAXR}", f"=Agreements!$A$5:$A${4 + len(AGREEMENTS)}", "Agreement")
COUNTRY_RANGE = f"Countries!$A$5:$A${MAXR}"

# ======================= Duty Rates =======================
R = wb.create_sheet("Duty Rates", 0)
R["A1"] = "Duty rates by HS code"; R["A1"].font = TITLE
R["A2"] = ("One row per HS code, from the official tariff at customs.gov.eg/Services/Tarif. Rates are percentages "
           "(40 = 40%). Agreement columns: the preferential rate with a valid certificate; leave blank if the "
           "general rate applies. Always fill in the source and the date you checked it.")
R["A2"].font = NOTE
AG_CODES = [a[0] for a in AGREEMENTS]
duty_cols = (["Row status", "HS code (10 digits)", "Description", "General duty %", "Schedule tax (ضريبة الجدول)",
              "VAT %"] + [f"{c} %" for c in AG_CODES] + ["Import controls (document codes)",
                                                        "Source and date checked", "Remarks"])
header(R, 4, duty_cols, [10, 14, 34, 9, 14, 7] + [8] * len(AG_CODES) + [18, 30, 30])
SRC = "gomruky.com, Aug 2026: confirm on customs.gov.eg"
DUTY = [
    ["Example", "6109100000", "T-shirts and vests, knitted, of cotton", 40, "", 14] + [None] * len(AG_CODES)
    + ["GOEIC", SRC, "Example only"],
    ["Example", "8418100090", "Combined refrigerator-freezers", 60, "Yes: see tariff", 14] + [None] * len(AG_CODES)
    + ["GOEIC", SRC, "Example only"],
    ["Example", "8517130000", "Smartphones", 10, "", 14] + [None] * len(AG_CODES) + ["NTRA", SRC, "Example only"],
]
rows(R, 5, DUTY, example_col=1, text_cols=(2,))
for r in range(5, MAXR + 1):
    R.cell(row=r, column=2).number_format = "@"
dropdown(R, f"A5:A{MAXR}", ref["Row status"], "Row status")
R.auto_filter.ref = f"A4:{R.cell(row=4, column=len(duty_cols)).column_letter}{MAXR}"
AG_FIRST = R.cell(row=4, column=7).column_letter
AG_LAST = R.cell(row=4, column=6 + len(AG_CODES)).column_letter
CTRL = R.cell(row=4, column=7 + len(AG_CODES)).column_letter

# ======================= Clearance Fees =======================
S = wb.create_sheet("Clearance Fees", 0)
S["A1"] = "Clearance fees"; S["A1"].font = TITLE
S["A2"] = ("One row per fee. The app imports rows with status Active. \"Any\" / \"All\" / a blank value band = applies to "
           "everything. A row for a specific port, shipment type or HS chapter replaces the general row for the same fee "
           "item. For \"% of invoice value\", Amount is the percent (0.5 = 0.5%). Grey columns are the calculator's: "
           "don't edit them.")
S["A2"].font = NOTE
fee_cols = ["Row status", "Direction", "Clearance point", "Shipment type", "Purpose", "HS chapter (2 digits or All)",
            "Invoice value from (USD)", "Invoice value to (USD)", "Fee item", "Basis", "Amount", "Currency",
            "Minimum (same currency)", "Broker / supplier", "Valid from", "Valid to", "Documents (codes, ; between)",
            "Remarks", "Calc: applies", "Calc: specificity", "Calc: used", "Calc: USD"]
header(S, 4, fee_cols, [10, 9, 14, 10, 17, 11, 11, 11, 17, 15, 9, 8, 10, 16, 11, 11, 24, 30, 8, 9, 7, 10], calc_from=19)
FEES = [
    ["Example", "Import", "Any", "FCL", "Commercial import", "All", None, None, "Clearance fee", "Per shipment",
     4500, "EGP", None, "", "2026-11-01", "2026-11-30", "CI;PL;BL;ACID;CO;IMP;TAX;POA",
     "Example only: replace with your broker's price"],
    ["Example", "Import", "Any", "FCL", "Commercial import", "84", None, None, "Clearance fee", "Per shipment",
     6000, "EGP", None, "", "2026-11-01", "2026-11-30", "CI;PL;BL;ACID;CO;IMP;TAX;POA;GOEIC",
     "Example only: machinery (chapter 84) replaces the general clearance fee"],
    ["Example", "Import", "Any", "Any", "Commercial import", "All", None, None, "Extra HS line", "Per HS line",
     150, "EGP", None, "", "2026-11-01", "2026-11-30", "", "Example only"],
    ["Example", "Import", "Any", "Air", "Commercial import", "All", 0, 5000, "Clearance fee", "% of invoice value",
     0.5, "USD", 50, "", "2026-11-01", "2026-11-30", "CI;PL;BL;ACID;CO;IMP;TAX;POA",
     "Example only: air shipments under USD 5,000 invoice value"],
]
rows(S, 5, FEES, example_col=1, text_cols=(6,), fmt={15: "yyyy-mm-dd", 16: "yyyy-mm-dd"})
for r in range(5, MAXR + 1):
    S.cell(row=r, column=6).number_format = "@"
    for c in (15, 16):
        S.cell(row=r, column=c).number_format = "yyyy-mm-dd"
for col, name in (("A", "Row status"), ("B", "Direction"), ("C", "Clearance point"), ("D", "Shipment type"),
                  ("E", "Purpose"), ("I", "Fee item"), ("J", "Basis"), ("L", "Currency")):
    dropdown(S, f"{col}5:{col}{MAXR}", ref[name], name)
dv = DataValidation(type="date", operator="greaterThan", formula1="DATE(2020,1,1)", allow_blank=True,
                    showErrorMessage=True, error="Enter a date, e.g. 2026-11-30")
S.add_data_validation(dv); dv.add(f"O5:P{MAXR}")
S.auto_filter.ref = f"A4:R{MAXR}"

# Calculator cell map (Calculator tab, column C)
CALC_IN = {"direction": 4, "point": 5, "type": 6, "purpose": 7, "hs": 8, "origin": 9, "cert": 10, "invoice": 11,
           "freight": 12, "insurance": 13, "containers": 14, "lines": 15, "egp": 16, "eur": 17, "examples": 18}
c = {k: f"Calculator!$C${v}" for k, v in CALC_IN.items()}
CH = "Calculator!$C$21"   # HS chapter (output)
for r in range(5, MAXR + 1):
    applies = (f'=IF(AND(OR($A{r}="Active",AND($A{r}="Example",{c["examples"]}="Yes")),$B{r}={c["direction"]},'
               f'OR($C{r}="Any",$C{r}={c["point"]}),OR($D{r}="Any",$D{r}={c["type"]}),$E{r}={c["purpose"]},'
               f'OR($F{r}="All",$F{r}&""={CH}),OR($G{r}="",{c["invoice"]}>=$G{r}),OR($H{r}="",{c["invoice"]}<$H{r}),'
               f'ISNUMBER($K{r})),1,0)')
    spec = f'=IF($S{r}=1,($C{r}<>"Any")+($D{r}<>"Any")+($F{r}<>"All"),"")'
    used = f'=IF($S{r}=1,IF($T{r}=_xlfn.MAXIFS($T$5:$T${MAXR},$I$5:$I${MAXR},$I{r},$S$5:$S${MAXR},1),1,0),0)'
    rate = f'IF($L{r}="USD",1,IF($L{r}="EGP",{c["egp"]},IF($L{r}="EUR",{c["eur"]},0)))'
    base = (f'IF($J{r}="Per container",$K{r}*{c["containers"]},IF($J{r}="Per HS line",$K{r}*{c["lines"]},'
            f'IF($J{r}="% of invoice value",$K{r}/100*{c["invoice"]}*{rate},$K{r})))')
    usd = f'=IF($U{r}=1,IF({rate}>0,MAX({base},N($M{r}))/{rate},NA()),0)'
    for col, f in ((19, applies), (20, spec), (21, used), (22, usd)):
        cell = S.cell(row=r, column=col, value=f)
        cell.font, cell.fill, cell.border = Font(name=F, size=9, color="52636A"), CALC, GRID
    S.cell(row=r, column=22).number_format = "#,##0.00"

# ======================= Calculator =======================
K = wb.create_sheet("Calculator", 0)
K["A1"] = "Clearance estimate"; K["A1"].font = TITLE
K["A2"] = ("Yellow cells are inputs. The estimate uses the Duty Rates, Countries, Agreements, Purposes and Clearance "
           "Fees tabs. It is a guide for quoting, not a customs assessment: customs can reassess the value or the HS code.")
K["A2"].font = NOTE
K.column_dimensions["A"].width = 3; K.column_dimensions["B"].width = 38; K.column_dimensions["C"].width = 40
K.column_dimensions["D"].width = 60
K["B3"] = "Shipment"; K["B3"].font = BB
inputs = [
    ("direction", "Direction", "Import", ref["Direction"]),
    ("point", "Clearance point", "Alexandria", ref["Clearance point"]),
    ("type", "Shipment type", "FCL", ref["Shipment type"]),
    ("purpose", "Purpose of the goods", "Commercial import", ref["Purpose"]),
    ("hs", "HS code (10 digits)", "8418100090", None),
    ("origin", "Country of origin", "Turkey", "=" + COUNTRY_RANGE),
    ("cert", "Valid certificate of origin?", "Yes", ref["Yes/No"]),
    ("invoice", "Commercial invoice value (USD)", 20000, None),
    ("freight", "Freight to Egypt (USD)", 1500, None),
    ("insurance", "Insurance (USD)", 100, None),
    ("containers", "Containers", 1, None),
    ("lines", "HS lines on the invoice", 3, None),
    ("egp", "EGP per 1 USD", 56.12, None),
    ("eur", "EUR per 1 USD", None, None),
    ("examples", "Include Example rows from the tabs?", "Yes", ref["Yes/No"]),
]
help_text = {"hs": "As on the Duty Rates tab", "egp": "November 2026 rate entered in MTS Rates",
             "eur": "Only needed if a fee is in EUR", "examples": "Set to No once real prices are filled in",
             "cert": "Needed for the agreement rate"}
for key, label, default, src in inputs:
    r = CALC_IN[key]
    K.cell(row=r, column=2, value=label).font = B
    cell = K.cell(row=r, column=3, value=default)
    cell.font, cell.fill, cell.border = BLUE, INPUT, GRID
    if key == "hs":
        cell.number_format = "@"
    elif key in ("invoice", "freight", "insurance"):
        cell.number_format = "#,##0.00"
    if src:
        dropdown(K, f"C{r}", src, label)
    if key in help_text:
        K.cell(row=r, column=4, value=help_text[key]).font = NOTE

hs_rng = f"'Duty Rates'!$B$5:$B${MAXR}"
def duty(col):
    return f"'Duty Rates'!${col}$5:${col}${MAXR}"
K["B20"] = "Customs value and duties"; K["B20"].font = BB
out = [
    (21, "HS chapter", "=LEFT(C8,2)", None, ""),
    (22, "Description", f'=IFERROR(INDEX({duty("C")},MATCH(C8,{hs_rng},0)),"HS code not on the Duty Rates tab")', None, ""),
    (23, "Agreement for this origin", f'=IFERROR(INDEX(Countries!$C$5:$C${MAXR},MATCH(C9,{COUNTRY_RANGE},0))&"","")', None,
     "From the Countries tab"),
    (24, "Agreement rate (if any) %",
     f'=IF(OR(C23="",C10<>"Yes"),"",IFERROR(INDEX(\'Duty Rates\'!${AG_FIRST}$5:${AG_LAST}${MAXR},MATCH(C8,{hs_rng},0),'
     f'MATCH(C23&" %",\'Duty Rates\'!${AG_FIRST}$4:${AG_LAST}$4,0))&"",""))', None,
     "Blank: no preferential rate on the Duty Rates tab, or no certificate"),
    (25, "Duty rate used %", f'=IF(C24<>"",VALUE(C24),IFERROR(INDEX({duty("D")},MATCH(C8,{hs_rng},0)),NA()))', "0.0",
     "Agreement rate with a certificate, otherwise the general rate"),
    (26, "CIF value (USD)", "=C11+C12+C13", "#,##0.00", "Invoice + freight + insurance"),
    (27, "Customs duty (USD)", "=C26*C25/100", "#,##0.00", ""),
    (28, "Schedule tax", f'=IFERROR(INDEX({duty("E")},MATCH(C8,{hs_rng},0))&"","")', None,
     "Not included in the estimate: confirm the amount with the broker"),
    (29, "VAT rate %", f'=IFERROR(INDEX({duty("F")},MATCH(C8,{hs_rng},0)),NA())', "0.0", ""),
    (30, "VAT (USD)", "=(C26+C27)*C29/100", "#,##0.00", "On CIF value + duty"),
    (31, "Duties at clearance for this purpose", '=IFERROR(INDEX(Purposes!$D$5:$D$20,MATCH(C7,Purposes!$A$5:$A$20,0)),"")',
     None, "From the Purposes tab"),
    (32, "Duty + VAT to pay (USD)", '=IF(C31="Yes",C27+C30,0)', "#,##0.00", ""),
    (33, "Duty + VAT under guarantee (USD)", '=IF(C31="Guarantee",C27+C30,0)', "#,##0.00",
     "Suspended, but the guarantee must cover it"),
]
for r, label, f, fmt, note in out:
    K.cell(row=r, column=2, value=label).font = B
    cell = K.cell(row=r, column=3, value=f)
    cell.font, cell.border = B, GRID
    if fmt:
        cell.number_format = fmt
    if note:
        K.cell(row=r, column=4, value=note).font = NOTE
K["B35"] = "Clearance fees and total"; K["B35"].font = BB
tot = [
    (36, "Clearance fees (USD)", f"=SUM('Clearance Fees'!$V$5:$V${MAXR})", "From the Clearance Fees rows that apply"),
    (37, "Fee rows used", f"=SUM('Clearance Fees'!$U$5:$U${MAXR})", "0 means no fee row matches this shipment"),
    (38, "Total to pay (USD)", "=C32+C36", "Duty + VAT to pay + clearance fees"),
    (39, "Total to pay (EGP)", "=C38*C16", ""),
]
for r, label, f, note in tot:
    K.cell(row=r, column=2, value=label).font = BB if r in (38, 39) else B
    cell = K.cell(row=r, column=3, value=f)
    cell.font, cell.border = (BB if r in (38, 39) else B), GRID
    cell.number_format = "0" if r == 37 else "#,##0.00"
    if note:
        K.cell(row=r, column=4, value=note).font = NOTE
K["B41"] = "Documents to ask the client for"; K["B41"].font = BB
K["C41"] = (f'=IFERROR(INDEX(Purposes!$E$5:$E$20,MATCH(C7,Purposes!$A$5:$A$20,0)),"")'
            f'&IF(C24<>"",";"&IFERROR(INDEX(Agreements!$D$5:$D$30,MATCH(C23,Agreements!$A$5:$A$30,0)),""),"")'
            f'&IFERROR(IF(INDEX({duty(CTRL)},MATCH(C8,{hs_rng},0))&""="","",";"&INDEX({duty(CTRL)},MATCH(C8,{hs_rng},0))),"")')
K["C41"].font, K["C41"].alignment = B, WRAP
K["D41"] = "Codes are explained on the Documents tab"; K["D41"].font = NOTE
K.freeze_panes = "A4"

# ======================= Read me =======================
M = wb.create_sheet("Read me", 0)
M.column_dimensions["A"].width = 3; M.column_dimensions["B"].width = 26; M.column_dimensions["C"].width = 100
M["B1"] = "MTS customs clearance rates"; M["B1"].font = TITLE
M["B2"] = "Prepared for the pricing team, 5 Nov 2026. The MTS Rates app imports this sheet, so keep its layout."
M["B2"].font = NOTE
sections = [
    ("How the price is built", None),
    ("Customs value", "Commercial invoice + freight + insurance to Egypt (CIF). Customs can reassess a value it finds too low."),
    ("Customs duty", "CIF value × the duty rate of the HS code (Duty Rates tab). With a valid certificate of origin, an "
     "agreement can lower the rate (Countries and Agreements tabs)."),
    ("VAT", "14% of (CIF value + duty), unless the tariff gives another rate. Some goods also carry a schedule tax."),
    ("Purpose of the goods", "Decides whether duty and VAT are paid, suspended under a guarantee, or not due (Purposes tab), "
     "and which documents are needed."),
    ("Clearance fees", "The broker's or MTS's own fees (Clearance Fees tab): by port, shipment type, purpose, HS chapter "
     "and invoice value."),
    ("", None),
    ("The tabs", None),
    ("Calculator", "Try a shipment: enter HS code, origin, invoice value and purpose to see duties, VAT, fees and "
     "documents."),
    ("Clearance Fees", "Fill in: your clearance prices, one fee per row. Imported by the app."),
    ("Duty Rates", "Fill in: duty, VAT and agreement rates for the HS codes MTS handles, from customs.gov.eg/Services/Tarif."),
    ("Countries", "Check: which agreement applies to each country of origin."),
    ("Agreements", "Check: agreement names, partners and certificates."),
    ("Purposes", "Check: duty treatment and documents for each purpose."),
    ("Documents", "Codes for documents, used in the other tabs."),
    ("Lists", "The values the dropdowns offer. Add a clearance point here if you need a new one."),
    ("", None),
    ("Rules (so the app can read the sheet)", None),
    ("1", "Set Row status to Active for rows the app should use. Draft rows are kept but not used. Example rows show "
     "the format: replace or delete them once you have real prices."),
    ("2", "One fee per row, one value per cell. Pick from the dropdowns wherever there is one."),
    ("3", "Every Amount has a Currency. For \"% of invoice value\", Amount is the percent: 0.5 means 0.5%."),
    ("4", "HS codes: 10 digits, no dots or spaces (8418100090). HS chapter: 2 digits (84) or All."),
    ("5", "Value bands must not overlap for the same fee item: e.g. 0-5000 and 5000-20000 (the \"to\" value is "
     "not included)."),
    ("6", "Fill in Broker / supplier on every Active fee row: the broker's name, or MTS for MTS's own fees."),
    ("7", "Fill in Valid from and Valid to on every Active fee row. When a price changes, set the old row's Valid to "
     "and add a new row: don't overwrite."),
    ("8", "Every Duty Rates row needs its source and the date you checked it."),
    ("9", "Don't rename tabs or columns, and don't merge cells. Add rows; add a column only at the far right."),
    ("10", "Conditions and notes go in Remarks, never in an amount cell."),
    ("", None),
    ("Using the sheet", None),
    ("Share", "Upload this file to Google Drive, open it with Google Sheets, and share it with "
     "rates-agent@mts-rates.iam.gserviceaccount.com as Viewer. Then send Osama the link."),
    ("Colours", "Grey rows are examples. Yellow cells on the Calculator are inputs. Grey columns on Clearance Fees are "
     "the calculator's: don't edit them."),
    ("Sources", "Cost structure: gomruky.com/customs/clearance-costs (Aug 2026), sino-shipping.com import duties and "
     "taxes guide (Mar 2026). Official tariff: customs.gov.eg/Services/Tarif. Agreements: trade.gov Egypt country "
     "guide (Nov 2025)."),
]
r = 4
for label, text in sections:
    if text is None:
        M.cell(row=r, column=2, value=label).font = Font(name=F, size=11, bold=True, color=TEAL)
    else:
        M.cell(row=r, column=2, value=label).font = BB
        cell = M.cell(row=r, column=3, value=text)
        cell.font, cell.alignment = B, WRAP
    r += 1

wb.active = 0
wb.save(OUT)
print(OUT)
