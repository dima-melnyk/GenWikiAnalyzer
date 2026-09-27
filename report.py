"""
report.py — renders a trend chart (PNG) and a shareable one-page PDF
report from the output of trend_stats.analyze_series() for one or more
series (e.g. the same topic across several language Wikipedias).
"""
from __future__ import annotations

import datetime as dt
import os
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, ListFlowable, ListItem,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Article titles arrive in whatever script the source wiki uses (Polish/Czech
# diacritics, Ukrainian Cyrillic, etc.). Reportlab's built-in Helvetica only
# covers Latin-1, so register DejaVu Sans (bundled with matplotlib, already a
# dependency of this skill) which covers Latin Extended + Cyrillic + Greek.
_FONT_REGULAR = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"
try:
    _dejavu_dir = os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data", "fonts", "ttf")
    pdfmetrics.registerFont(TTFont("DejaVuSans", os.path.join(_dejavu_dir, "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", os.path.join(_dejavu_dir, "DejaVuSans-Bold.ttf")))
    _FONT_REGULAR = "DejaVuSans"
    _FONT_BOLD = "DejaVuSans-Bold"
except Exception:
    pass  # falls back to Helvetica (Latin-1 only) if the font file isn't found


def _ts_to_date(ts: str) -> dt.date:
    # Wikimedia timestamps: YYYYMMDDHH
    return dt.date(int(ts[0:4]), int(ts[4:6]), int(ts[6:8]))


def make_chart(series_by_label: dict[str, list[dict]], title: str, out_path: str,
               normalized_share: Optional[dict[str, list[dict]]] = None) -> str:
    """series_by_label: {"pl.wikipedia — Post przerywany": [{"timestamp":..,"views":..}, ...], ...}
    normalized_share: optional parallel dict of {"views": share_pct} series to plot on a
    secondary axis (article views as a % of the whole wiki's traffic)."""
    fig, ax = plt.subplots(figsize=(7.5, 3.4), dpi=150)
    colors_cycle = plt.rcParams["axes.prop_cycle"].by_key()["color"]

    for i, (label, items) in enumerate(series_by_label.items()):
        if not items:
            continue
        items_sorted = sorted(items, key=lambda x: x["timestamp"])
        dates = [_ts_to_date(it["timestamp"]) for it in items_sorted]
        views = [it["views"] for it in items_sorted]
        ax.plot(dates, views, label=label, color=colors_cycle[i % len(colors_cycle)], linewidth=1.8)

    ax.set_ylabel("Monthly pageviews")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.grid(True, alpha=0.25)
    ax.set_title(title, fontsize=11)
    if len(series_by_label) > 1:
        ax.legend(fontsize=7, loc="upper left")
    fig.autofmt_xdate(rotation=30)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


def _verdict_color(confidence: str):
    return {"high": colors.HexColor("#1a7f37"), "medium": colors.HexColor("#9a6700"),
            "low": colors.HexColor("#b3261e")}.get(confidence, colors.black)


def build_pdf_report(out_path: str, title: str, subtitle: str, chart_path: str,
                      rows: list[dict], global_caveats: list[str], recommendation: str = "") -> str:
    """rows: one dict per series/language, each with keys:
        label, n_points, period, total_change_pct, annualized_growth_pct,
        trend_direction, confidence, confidence_reasons (list), notable (str, optional)
    """
    styles = getSampleStyleSheet()
    # Rebind every style used below onto the Unicode-capable font so that
    # non-Latin-1 article titles (ř, ů, і, ї, ...) render instead of boxes.
    for name in ("Normal", "Heading1", "Heading3", "Title"):
        styles[name].fontName = _FONT_BOLD if name.startswith("Heading") or name == "Title" else _FONT_REGULAR
    styles.add(ParagraphStyle(name="TinyGrey", parent=styles["Normal"], fontSize=7.5,
                               textColor=colors.HexColor("#555555"), fontName=_FONT_REGULAR))
    styles.add(ParagraphStyle(name="H1", parent=styles["Heading1"], fontSize=15, spaceAfter=2, fontName=_FONT_BOLD))
    styles.add(ParagraphStyle(name="Sub", parent=styles["Normal"], fontSize=9.5,
                               textColor=colors.HexColor("#444444"), spaceAfter=8, fontName=_FONT_REGULAR))
    styles.add(ParagraphStyle(name="CellSmall", parent=styles["Normal"], fontSize=8, leading=10, fontName=_FONT_REGULAR))
    styles.add(ParagraphStyle(name="CellHeader", parent=styles["Normal"], fontSize=7.5, leading=9,
                               fontName=_FONT_BOLD))

    doc = SimpleDocTemplate(out_path, pagesize=letter,
                             topMargin=0.55 * inch, bottomMargin=0.5 * inch,
                             leftMargin=0.6 * inch, rightMargin=0.6 * inch)
    story = []
    story.append(Paragraph(title, styles["H1"]))
    story.append(Paragraph(subtitle, styles["Sub"]))

    if chart_path and os.path.exists(chart_path):
        story.append(Image(chart_path, width=6.8 * inch, height=3.08 * inch))
        story.append(Spacer(1, 8))

    header_labels = ["Wiki / article", "Period", "Total change", "Annualized growth", "Trend", "Confidence"]
    table_data = [[Paragraph(h, styles["CellHeader"]) for h in header_labels]]
    for r in rows:
        table_data.append([
            Paragraph(r["label"], styles["CellSmall"]),
            Paragraph(r.get("period", ""), styles["CellSmall"]),
            Paragraph(f'{r["total_change_pct"]:.0f}%' if r.get("total_change_pct") is not None else "n/a", styles["CellSmall"]),
            Paragraph(f'{r["annualized_growth_pct"]:.0f}%/yr' if r.get("annualized_growth_pct") is not None else "n/a", styles["CellSmall"]),
            Paragraph(r.get("trend_direction", "n/a"), styles["CellSmall"]),
            Paragraph(f'<font color="#{_verdict_color(r.get("confidence", "")).hexval()[2:]}">'
                      f'<b>{r.get("confidence", "n/a").upper()}</b></font>', styles["CellSmall"]),
        ])
    tbl = Table(table_data, colWidths=[1.55 * inch, 1.05 * inch, 0.85 * inch, 1.15 * inch, 0.65 * inch, 0.95 * inch])
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 0), (-1, -1), _FONT_REGULAR),
        ("FONTNAME", (0, 0), (-1, 0), _FONT_BOLD),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
    ]
    tbl.setStyle(TableStyle(style_cmds))
    story.append(tbl)
    story.append(Spacer(1, 10))

    story.append(Paragraph("Why this confidence rating", styles["Heading3"]))
    for r in rows:
        reasons = r.get("confidence_reasons") or []
        if not reasons:
            continue
        story.append(Paragraph(f"<b>{r['label']}</b>", styles["CellSmall"]))
        story.append(ListFlowable(
            [ListItem(Paragraph(reason, styles["CellSmall"])) for reason in reasons],
            bulletType="bullet", leftIndent=14,
        ))
    story.append(Spacer(1, 8))

    if recommendation:
        story.append(Paragraph("What to check next", styles["Heading3"]))
        story.append(Paragraph(recommendation, styles["CellSmall"]))
        story.append(Spacer(1, 8))

    story.append(Paragraph("Caveats & methodology", styles["Heading3"]))
    story.append(ListFlowable(
        [ListItem(Paragraph(c, styles["TinyGrey"])) for c in global_caveats],
        bulletType="bullet", leftIndent=14,
    ))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"Source: Wikimedia Pageviews API (wikimedia.org/api/rest_v1/metrics/pageviews), "
        f"human traffic only (agent=user). Generated {dt.date.today().isoformat()}.",
        styles["TinyGrey"],
    ))

    doc.build(story)
    return out_path
