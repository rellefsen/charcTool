"""Generate printable HTML status boards for browser print or download."""

from __future__ import annotations

from datetime import datetime, timezone

from config import STATUS_BG, STATUS_CODES, STATUS_COLORS, STATUS_SORT_CAPTION


def format_board_timestamp(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%m/%d %H:%M")
    except ValueError:
        return ts or "—"


def _status_pill(code: str) -> str:
    bg = STATUS_BG.get(code, "#F3F4F6")
    fg = STATUS_COLORS.get(code, "#111827")
    return (
        f'<span class="status-pill" style="background:{bg};color:{fg};">'
        f"{code}</span>"
    )


def status_counts(rows: list[dict]) -> dict[str, int]:
    return {code: sum(1 for row in rows if row["status_code"] == code) for code in STATUS_CODES}


def build_printable_html(
    rows: list[dict],
    *,
    title: str,
    subtitle: str = "",
    show_precinct: bool = False,
    change_labels: dict[str, str] | None = None,
) -> str:
    """Build a self-contained HTML document for printing."""
    change_labels = change_labels or {}
    counts = status_counts(rows)
    printed_at = datetime.now(timezone.utc).strftime("%m/%d/%Y %H:%M UTC")

    if show_precinct:
        headers = ["Precinct", "House", "Address", "Status", "Updated", "Was → Now"]
    else:
        headers = ["House", "Address", "Status", "Updated"]

    body_rows: list[str] = []
    for row in rows:
        house_id = row["house_id"]
        precinct_id = row.get("precinct_id", "")
        change_key = (
            f"{precinct_id}:{house_id.upper()}" if show_precinct else house_id.upper()
        )
        change_label = change_labels.get(change_key, "—")
        cells = []
        if show_precinct:
            cells.append(f"<td>{precinct_id}</td>")
        cells.extend(
            [
                f"<td><strong>{house_id}</strong></td>",
                f"<td>{row.get('address', '—')}</td>",
                f"<td>{_status_pill(row['status_code'])}</td>",
                f"<td>{format_board_timestamp(row.get('timestamp', ''))}</td>",
            ]
        )
        if show_precinct:
            cells.append(f"<td>{change_label}</td>")
        body_rows.append(f"<tr>{''.join(cells)}</tr>")

    header_html = "".join(f"<th>{label}</th>" for label in headers)
    summary = " · ".join(
        f"{code}: {counts.get(code, 0)}" for code in STATUS_CODES
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{
    font-family: Arial, Helvetica, sans-serif;
    color: #111827;
    margin: 0.75in;
    font-size: 12pt;
  }}
  h1 {{
    font-size: 20pt;
    margin: 0 0 0.25rem;
  }}
  .meta {{
    color: #4b5563;
    margin-bottom: 1rem;
    line-height: 1.4;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
  }}
  th, td {{
    border: 1px solid #d1d5db;
    padding: 0.35rem 0.5rem;
    text-align: left;
    vertical-align: top;
  }}
  th {{
    background: #f3f4f6;
    font-size: 10pt;
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }}
  tr {{
    page-break-inside: avoid;
  }}
  .status-pill {{
    display: inline-block;
    padding: 0.15rem 0.5rem;
    border-radius: 0.35rem;
    font-weight: 700;
    font-size: 10pt;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }}
  .summary {{
    margin-top: 1rem;
    font-weight: 700;
  }}
  @media print {{
    body {{ margin: 0.5in; }}
  }}
</style>
</head>
<body>
  <h1>{title}</h1>
  <div class="meta">
    {f"<div>{subtitle}</div>" if subtitle else ""}
    <div>Printed {printed_at}</div>
    <div>{STATUS_SORT_CAPTION}</div>
  </div>
  <table>
    <thead><tr>{header_html}</tr></thead>
    <tbody>
      {"".join(body_rows)}
    </tbody>
  </table>
  <div class="summary">{summary}</div>
</body>
</html>"""
