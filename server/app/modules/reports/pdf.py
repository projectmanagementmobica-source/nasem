"""HTML -> PDF with WeasyPrint (real Arabic shaping + RTL via HarfBuzz/Pango). Bundled OFL font."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.modules.guides.schemas import GuideOut
from app.modules.reports.data import ReportData
from app.modules.reports.labels import (
    CATEGORY_AR,
    PERIOD_AR,
    describe_action,
    entity_ar,
    fmt_date,
    fmt_datetime,
    key_ar,
)
from app.modules.reports.summary import ReportSummary

_ASSETS = Path(__file__).parent / "assets"
_FONTS_URL = (_ASSETS / "fonts").resolve().as_uri()
_env = Environment(
    loader=FileSystemLoader(_ASSETS / "templates"),
    autoescape=select_autoescape(["html"]),  # everything user/model-provided is escaped
)
_css_env = Environment(loader=FileSystemLoader(_ASSETS / "templates"), autoescape=False)


def _css_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _render_pdf(template: str, *, generated: str, **ctx: Any) -> bytes:
    try:
        from weasyprint import HTML  # heavy import; keep module import cheap
    except (ImportError, OSError) as e:   # serverless hosts lack pango
        from app.shared.api import AppError
        raise AppError(503, "pdf_unavailable", "PDF export is not available on this server") from e

    css = _css_env.get_template("base.css").render(fonts=_FONTS_URL, generated=_css_string(generated))
    html = _env.get_template(template).render(css=css, **ctx)
    pdf: bytes = HTML(string=html, base_url=str(_ASSETS)).write_pdf()
    return pdf


def build_report_pdf_sync(data: ReportData, summary: ReportSummary | None) -> bytes:
    def row(c: Any, when: Any) -> dict[str, str]:
        return {"title": c.title, "when": fmt_datetime(data.local(when)), "details": c.description or ""}

    awaiting = []
    for a in data.awaiting:
        kind, details = describe_action(a)
        awaiting.append({"kind": kind, "details": details, "when": fmt_date(data.local(a.created_at))})

    return _render_pdf(
        "report.html",
        generated="اتعمل " + fmt_datetime(data.now),
        title=f"تقريرك {PERIOD_AR[data.period]}",
        owner=data.user.display_name or data.user.email,
        start=fmt_date(data.start),
        end=fmt_date(data.end),
        stats=data.stats,
        summary=summary,
        overdue=[row(c, c.due_date) for c in data.overdue],
        upcoming=[row(c, c.due_date) for c in data.upcoming],
        completed=[row(c, c.updated_at) for c in data.completed],
        awaiting=awaiting,
        new_memories=[
            {"category": CATEGORY_AR[m.category], "entity": entity_ar(m.entity_name),
             "key": key_ar(m.attribute_key), "value": m.attribute_value}
            for m in data.new_memories
        ],
        include_sensitive=data.include_sensitive,
    )


def build_guide_pdf_sync(guide: GuideOut) -> bytes:
    label = "اتعمل " + fmt_datetime(guide.generated_at)
    return _render_pdf("guide.html", generated=label, g=guide, generated_label=label)


async def build_report_pdf(data: ReportData, summary: ReportSummary | None) -> bytes:
    # Rendering is CPU-bound: keep it off the event loop.
    return await asyncio.to_thread(build_report_pdf_sync, data, summary)


async def build_guide_pdf(guide: GuideOut) -> bytes:
    return await asyncio.to_thread(build_guide_pdf_sync, guide)
