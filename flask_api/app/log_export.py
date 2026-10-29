"""Turn moderation log rows into a file an operator can open: text, Word or Excel.

Three formats because three people ask for this file and they do different things with it.
Text is for grepping, mailing and diffing, and it is the one that always works. Word is for
pasting into an incident write-up. Excel is for sorting and pivoting, which is what anybody
auditing a month of refusals actually does, so it is a real workbook with typed cells rather
than a CSV wearing an .xlsx name - a CSV would hand them timestamps as strings and risk scores
that do not sort numerically.

Nothing here touches the database or Flask: it takes rows and gives back bytes, so the
formatting can be tested without a request and the endpoint stays about permissions and
filters.
"""
from __future__ import annotations

import datetime as _dt
import html
import io

# Word opens an HTML document saved as .doc and has since Word 2000; Google Docs and
# LibreOffice do too. That is why there is no python-docx here: a real .docx would add a
# dependency to produce a worse table than this one, and the alternative people reach for -
# renaming a .csv to .doc - opens as a wall of commas.
_DOC_HEAD = """<html xmlns:o="urn:schemas-microsoft-com:office:office"
      xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40">
<head><meta charset="utf-8">
<title>{title}</title>
<!--[if gte mso 9]><xml><w:WordDocument><w:View>Print</w:View></w:WordDocument></xml><![endif]-->
<style>
  @page {{ size: A4 landscape; margin: 1.2cm; }}
  body {{ font-family: Calibri, Arial, sans-serif; font-size: 9pt; }}
  h1 {{ font-size: 15pt; margin: 0 0 2pt; }}
  p.sub {{ color: #555; font-size: 8.5pt; margin: 0 0 10pt; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 0.5pt solid #999; padding: 3pt 4pt; vertical-align: top;
            text-align: left; word-break: break-word; }}
  th {{ background: #eef2f7; font-weight: bold; }}
  tr.reject td {{ background: #fdf0f0; }}
  tr.revise td {{ background: #fffaf0; }}
</style></head><body>
"""


def _stamp(value) -> str:
    """The stored timestamp as something a reader can compare, in UTC and said so.

    Rows carry a float epoch. Left raw it exports as 1793018846.530235, which is the single
    most useless thing a log export can contain.
    """
    if value in (None, ""):
        return ""
    try:
        return (_dt.datetime.fromtimestamp(float(value), _dt.timezone.utc)
                .strftime("%Y-%m-%d %H:%M:%S UTC"))
    except (TypeError, ValueError, OSError):
        return str(value)


def _cell(row: dict, key: str):
    v = row.get(key)
    if key in ("created_at", "decided_at"):
        return _stamp(v)
    if v is None:
        return ""
    return v


def _flat(value) -> str:
    """One line, because a newline inside a text column breaks the alignment it sits in."""
    return " ".join(str(value).split())


def filename(fmt: str, now: _dt.datetime | None = None) -> str:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    ext = {"txt": "txt", "doc": "doc", "xlsx": "xlsx"}[fmt]
    return f"moderation-logs-{now.strftime('%Y%m%d-%H%M%S')}.{ext}"


def _header_lines(columns, rows, total, truncated, filters) -> list[str]:
    """What the file says about itself before the first row.

    An export that does not record its own filter is a file nobody can act on later: 3,415
    refusals and a filtered view of 12 look identical once the screen that made them is gone.
    """
    applied = ", ".join(f"{k}={v}" for k, v in sorted(filters.items()) if v) or "none"
    out = [
        "BONC - Moderation Logs & Audit Trail",
        f"Exported:  {_dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Filters:   {applied}",
        f"Rows:      {len(rows)} of {total} matching",
    ]
    if truncated:
        out.append(f"NOTE:      truncated to the first {len(rows)} rows, newest first. "
                   "Narrow the filter to export the rest.")
    return out


def to_txt(rows, columns, total, truncated=False, filters=None) -> bytes:
    """A fixed-width table, sized to its own contents so the columns actually line up."""
    filters = filters or {}
    heads = [label for _, label in columns]
    body = [[_flat(_cell(r, key)) for key, _ in columns] for r in rows]

    # Wide free-text columns are capped so one 2 KB preview cannot push every other column off
    # the screen; the full text is in the Excel and Word versions, which wrap instead.
    caps = {"Content": 80, "Feedback message": 80, "Feedback title": 40}
    for line in body:
        for i, label in enumerate(heads):
            cap = caps.get(label)
            if cap and len(line[i]) > cap:
                line[i] = line[i][:cap - 1] + "…"

    widths = [max(len(heads[i]), *(len(l[i]) for l in body)) if body else len(heads[i])
              for i in range(len(heads))]
    sep = "  "
    out = _header_lines(columns, rows, total, truncated, filters)
    out.append("")
    out.append(sep.join(h.ljust(w) for h, w in zip(heads, widths)).rstrip())
    out.append(sep.join("-" * w for w in widths).rstrip())
    out.extend(sep.join(c.ljust(w) for c, w in zip(line, widths)).rstrip() for line in body)
    if not body:
        out.append("(no moderation events match this filter)")
    return ("\n".join(out) + "\n").encode("utf-8")


def to_doc(rows, columns, total, truncated=False, filters=None) -> bytes:
    filters = filters or {}
    head, *meta = _header_lines(columns, rows, total, truncated, filters)
    parts = [_DOC_HEAD.format(title=html.escape(head)),
             f"<h1>{html.escape(head)}</h1>",
             '<p class="sub">' + "<br>".join(html.escape(m) for m in meta) + "</p>",
             "<table><thead><tr>",
             "".join(f"<th>{html.escape(label)}</th>" for _, label in columns),
             "</tr></thead><tbody>"]
    for r in rows:
        # Shading by decision, so a reader scanning a printed page finds the refusals without
        # reading the column - the same thing the badge colours do on screen.
        cls = str(r.get("decision") or "").strip().lower()
        parts.append(f'<tr class="{html.escape(cls)}">')
        parts.append("".join(f"<td>{html.escape(str(_cell(r, key)))}</td>" for key, _ in columns))
        parts.append("</tr>")
    if not rows:
        parts.append(f'<tr><td colspan="{len(columns)}">'
                     "(no moderation events match this filter)</td></tr>")
    parts.append("</tbody></table></body></html>")
    # UTF-8 with a BOM: without it Word guesses the codepage and non-Latin previews - which on
    # this corpus means most of the Devanagari ones - open as mojibake.
    return b"\xef\xbb\xbf" + "".join(parts).encode("utf-8")


def to_xlsx(rows, columns, total, truncated=False, filters=None) -> bytes:
    """A real workbook: typed cells, a frozen header, and a filter row already switched on."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    filters = filters or {}
    wb = Workbook()
    ws = wb.active
    ws.title = "Moderation Logs"

    head, *meta = _header_lines(columns, rows, total, truncated, filters)
    ws.append([head])
    ws["A1"].font = Font(bold=True, size=13)
    for line in meta:
        ws.append([line])
    ws.append([])

    # Taken AFTER the append, never predicted. `append([])` moves openpyxl's write cursor but
    # leaves max_row where it was, because the row it opened holds no cells - so `max_row + 1`
    # named the blank spacer and the filter row was attached one row above the headings.
    ws.append([label for _, label in columns])
    first = ws.max_row
    fill = PatternFill("solid", fgColor="EEF2F7")
    for c in ws[first]:
        c.font = Font(bold=True)
        c.fill = fill

    numeric = {"risk_score", "latency_ms"}
    for r in rows:
        line = []
        for key, _ in columns:
            v = _cell(r, key)
            if key in numeric and v not in ("", None):
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    pass
            line.append(v)
        ws.append(line)

    # The two things that make a sheet usable the moment it opens, and that nobody sets by hand
    # on a file they were handed.
    ws.freeze_panes = ws.cell(row=first + 1, column=1)
    ws.auto_filter.ref = f"A{first}:{get_column_letter(len(columns))}{ws.max_row}"

    wide = {"Content", "Feedback message", "Feedback title", "Request ID"}
    for i, (key, label) in enumerate(columns, start=1):
        letter = get_column_letter(i)
        seen = [len(str(_cell(r, key))) for r in rows[:400]] or [0]
        width = max(len(label) + 2, min(max(seen) + 2, 60 if label in wide else 24))
        ws.column_dimensions[letter].width = width
        if label in wide:
            for c in ws[letter][first:]:
                c.alignment = Alignment(wrap_text=True, vertical="top")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


RENDERERS = {"txt": to_txt, "doc": to_doc, "xlsx": to_xlsx}
MEDIA_TYPES = {
    "txt": "text/plain; charset=utf-8",
    "doc": "application/msword",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def render(fmt: str, rows, columns, total, truncated=False, filters=None) -> tuple[bytes, str, str]:
    """(bytes, media type, filename) for one of the supported formats."""
    if fmt not in RENDERERS:
        raise ValueError(f"unsupported export format {fmt!r}")
    data = RENDERERS[fmt](rows, columns, total, truncated=truncated, filters=filters)
    return data, MEDIA_TYPES[fmt], filename(fmt)
