"""Excel report: RTL, print-ready (A4, fit to width, repeated headers, page numbers), live formulas."""

from __future__ import annotations

import io
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.modules.reports.data import ReportData
from app.modules.reports.labels import (
    CATEGORY_AR,
    PERIOD_AR,
    SOURCE_AR,
    describe_action,
    entity_ar,
    key_ar,
)

FONT = "Arial"
INK = "0E2733"
BREEZE = "1F7A8C"
HEADER_FILL = PatternFill("solid", fgColor=BREEZE)
ZEBRA_FILL = PatternFill("solid", fgColor="EEF4F6")
THIN = Side(style="thin", color="C9D6DB")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
DATE_FMT = "yyyy-mm-dd hh:mm"

SHEET_SUMMARY = "الملخص"
SHEET_COMMITMENTS = "الالتزامات"
SHEET_MEMORIES = "الذاكرة"
SHEET_APPROVALS = "الموافقات"

ST_UPCOMING, ST_OVERDUE, ST_DONE = "جاية", "متأخرة", "خلصت"


def _naive(data: ReportData, dt: datetime | None) -> datetime | None:
    """Excel has no timezones: write the user's local wall-clock time."""
    local = data.local(dt)
    return local.replace(tzinfo=None) if local is not None else None


def _setup_print(ws: Worksheet, *, landscape: bool) -> None:
    ws.sheet_view.rightToLeft = True
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "landscape" if landscape else "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = ws.page_margins.right = 0.5
    ws.page_margins.top = ws.page_margins.bottom = 0.6
    ws.print_options.horizontalCentered = True
    ws.oddFooter.center.text = "صفحة &P من &N"
    ws.oddFooter.center.font = FONT
    ws.oddHeader.right.text = "نسيم — &A"
    ws.oddHeader.right.font = FONT


def _table(
    ws: Worksheet,
    headers: list[str],
    rows: list[list[object]],
    widths: list[int],
    *,
    date_cols: frozenset[int] = frozenset(),
    pct_cols: frozenset[int] = frozenset(),
    wrap_cols: frozenset[int] = frozenset(),
) -> None:
    ws.append(headers)
    for col, _ in enumerate(headers, start=1):
        c = ws.cell(row=1, column=col)
        c.font = Font(name=FONT, bold=True, color="FFFFFF", size=11)
        c.fill = HEADER_FILL
        c.alignment = Alignment(horizontal="right", vertical="center", wrap_text=True)
        c.border = BORDER
    for r, row in enumerate(rows, start=2):
        ws.append(row)
        for col in range(1, len(headers) + 1):
            c = ws.cell(row=r, column=col)
            c.font = Font(name=FONT, size=10, color=INK)
            c.border = BORDER
            c.alignment = Alignment(horizontal="right", vertical="top", wrap_text=col in wrap_cols)
            if r % 2 == 0:
                c.fill = ZEBRA_FILL
            if col in date_cols:
                c.number_format = DATE_FMT
            if col in pct_cols:
                c.number_format = "0%"
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[1].height = 22
    ws.freeze_panes = "A2"
    last = get_column_letter(len(headers))
    ws.auto_filter.ref = f"A1:{last}{max(1, len(rows) + 1)}"
    ws.print_title_rows = "1:1"


def build_report_xlsx(data: ReportData) -> bytes:
    wb = Workbook()

    # ---------------- commitments ----------------
    wc = wb.active
    assert wc is not None
    wc.title = SHEET_COMMITMENTS
    rows: list[list[object]] = []
    for status, items in ((ST_OVERDUE, data.overdue), (ST_UPCOMING, data.upcoming), (ST_DONE, data.completed)):
        for c in items:
            rows.append([
                c.title, status, _naive(data, c.due_date), c.description or "",
                "نعم" if c.is_proactive_generated else "لا", _naive(data, c.approved_at),
            ])
    _table(wc, ["الالتزام", "الحالة", "المعاد", "التفاصيل", "اقتراح من نسيم", "اتوافق عليه"], rows,
           [38, 12, 18, 44, 14, 18], date_cols=frozenset({3, 6}), wrap_cols=frozenset({1, 4}))
    for r in range(2, len(rows) + 2):
        if wc.cell(row=r, column=2).value == ST_OVERDUE:
            wc.cell(row=r, column=2).font = Font(name=FONT, size=10, bold=True, color="B3443A")
    _setup_print(wc, landscape=True)

    # ---------------- memories ----------------
    wm = wb.create_sheet(SHEET_MEMORIES)
    mrows: list[list[object]] = [
        [CATEGORY_AR[m.category], entity_ar(m.entity_name), key_ar(m.attribute_key), m.attribute_value,
         m.confidence_score, _naive(data, m.last_confirmed_at), SOURCE_AR.get(m.source_type, m.source_type.value)]
        for m in data.memories
    ]
    _table(wm, ["التصنيف", "عن مين", "المعلومة", "القيمة", "الثقة", "آخر تأكيد", "المصدر"], mrows,
           [12, 16, 22, 40, 9, 18, 10], date_cols=frozenset({6}), pct_cols=frozenset({5}), wrap_cols=frozenset({4}))
    _setup_print(wm, landscape=True)

    # ---------------- approvals ----------------
    wa = wb.create_sheet(SHEET_APPROVALS)
    arows: list[list[object]] = []
    for a in data.awaiting:
        kind, details = describe_action(a)
        arows.append([kind, details, _naive(data, a.created_at)])
    _table(wa, ["النوع", "التفاصيل", "اتطلب"], arows, [16, 60, 18], date_cols=frozenset({3}), wrap_cols=frozenset({2}))
    _setup_print(wa, landscape=False)

    # ---------------- summary (first tab, live formulas) ----------------
    ws = wb.create_sheet(SHEET_SUMMARY, 0)
    ws.sheet_view.rightToLeft = True
    ws["A1"] = f"تقرير نسيم {PERIOD_AR[data.period]}"
    ws["A1"].font = Font(name=FONT, size=18, bold=True, color=INK)
    ws["A2"] = f"{data.user.display_name or data.user.email}"
    ws["A2"].font = Font(name=FONT, size=11, color="5B7280")
    ws["A3"] = "اتعمل في"
    ws["B3"] = _naive(data, data.now)
    ws["B3"].number_format = DATE_FMT
    for cell in ("A3", "B3"):
        ws[cell].font = Font(name=FONT, size=10, color="5B7280")
        ws[cell].alignment = Alignment(horizontal="right")

    sc, sm, sa = f"'{SHEET_COMMITMENTS}'", f"'{SHEET_MEMORIES}'", f"'{SHEET_APPROVALS}'"
    kpis = [
        ("التزامات جاية", f'=COUNTIF({sc}!B:B,"{ST_UPCOMING}")'),
        ("متأخرة", f'=COUNTIF({sc}!B:B,"{ST_OVERDUE}")'),
        ("خلصت في الفترة", f'=COUNTIF({sc}!B:B,"{ST_DONE}")'),
        ("مستنية موافقتك", f"=MAX(COUNTA({sa}!A:A)-1,0)"),
        ("معلومات محفوظة", f"=MAX(COUNTA({sm}!A:A)-1,0)"),
        ("نسبة الإنجاز", "=IFERROR(B8/(B6+B7+B8),0)"),
    ]
    ws["A5"], ws["B5"] = "المؤشر", "القيمة"
    for cell in ("A5", "B5"):
        ws[cell].font = Font(name=FONT, bold=True, color="FFFFFF")
        ws[cell].fill = HEADER_FILL
        ws[cell].border = BORDER
        ws[cell].alignment = Alignment(horizontal="right")
    for i, (label, formula) in enumerate(kpis, start=6):
        ws.cell(row=i, column=1, value=label)
        ws.cell(row=i, column=2, value=formula)
        for col in (1, 2):
            cell = ws.cell(row=i, column=col)
            cell.font = Font(name=FONT, size=11, color=INK, bold=col == 2)
            cell.border = BORDER
            cell.alignment = Alignment(horizontal="right")
    ws["B11"].number_format = "0%"
    ws["A13"] = "الأرقام بتتحسب تلقائياً من باقي الشيتات."
    ws["A13"].font = Font(name=FONT, size=9, italic=True, color="5B7280")
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 20
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "portrait"
    ws.oddFooter.center.text = "صفحة &P من &N"

    wb.calculation.fullCalcOnLoad = True
    wb.properties.title = f"تقرير نسيم {PERIOD_AR[data.period]}"
    wb.properties.creator = "NASEM AI"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
