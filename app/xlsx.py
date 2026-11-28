"""Small helper for Excel (.xlsx) downloads: bold header, filters, frozen first row, sane widths."""
import io
from datetime import date, datetime

from flask import Response
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

MONEY = '#,##0.00 "€"'
HEADER_FILL = PatternFill("solid", fgColor="EDE9FE")


def _cell_value(value):
    if isinstance(value, str) and len(value) == 10 and value[4] == "-" and value[7] == "-":
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return value
    return value


def sheet(wb, title, headers, rows, money=(), wrap=(), first=False, notes=None):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title[:31]
    start = 1
    if notes:
        for line in notes:
            ws.cell(row=start, column=1, value=line)
            start += 1
        start += 1
    for col, h in enumerate(headers, 1):
        c = ws.cell(row=start, column=col, value=h)
        c.font = Font(bold=True)
        c.fill = HEADER_FILL
    widths = [len(str(h)) + 2 for h in headers]
    for r, row in enumerate(rows, start + 1):
        for col, value in enumerate(row, 1):
            c = ws.cell(row=r, column=col, value=_cell_value(value))
            if isinstance(c.value, str) and c.value.startswith("="):
                c.data_type = "s"  # text typed by people is never run as a formula
            if isinstance(c.value, (date, datetime)):
                c.number_format = "DD.MM.YYYY" if isinstance(c.value, date) and not isinstance(
                    c.value, datetime) else "DD.MM.YYYY HH:MM:SS"
            if headers[col - 1] in money:
                c.number_format = MONEY
            if headers[col - 1] in wrap:
                c.alignment = Alignment(wrap_text=True, vertical="top")
            widths[col - 1] = min(60, max(widths[col - 1], len(str(value or "")) + 2))
    for col, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = max(8, w)
    ws.freeze_panes = ws.cell(row=start + 1, column=1)
    if rows:
        ws.auto_filter.ref = f"A{start}:{get_column_letter(len(headers))}{start + len(rows)}"
    return ws


def new_workbook():
    return Workbook()


def response(wb, filename):
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(),
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})
