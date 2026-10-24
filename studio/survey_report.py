"""A site's utility survey report, structured to PAS 128:2022's Type B minimum contents.

The structure is studio/report_spec.yaml, built from the public PAS 128:2022 client guides (the BSI
text is not held); the report says so on its first page. Everything in it is either measured by
the Studio or typed into the project form, and each section says which. Nothing is graded: the
quality-level rule is evidence/quality.py's, which declines every QL-B grade this system cannot
support.

Sign-off happens here, not on paper: a report is a DRAFT until someone other than the person who
filled in its project form approves it, under their own account, stating their competence. The
approval holds only while the report says exactly what it said when approved: any change to the
data or the form makes it a draft again.
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import matplotlib

matplotlib.use("Agg")  # no display on a server
import matplotlib.pyplot as plt
import yaml
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from core.annotation_io import job_lock, safe_job_dir, write_text_atomically

SPEC = yaml.safe_load((Path(__file__).resolve().parent / "report_spec.yaml").read_text(encoding="utf-8"))
_SITES_ROOT = Path("annotations") / "_sites"
FORM_FIELDS = {  # what only a person on the project can say
    "client": "Client",
    "project": "Project and purpose of the survey",
    "requirements": "Survey requirements (what the client asked for, accuracy needed, areas)",
    "area": "Survey area as agreed with the client",
    "surveyed_on": "Date(s) of the survey",
    "surveyors": "Surveyors and their competence (training, qualifications)",
    "weather": "Weather and ground conditions on the day",
    "site_notes": "Site limitations noticed on the day (traffic, parked cars, access)",
    "photographs": "Photographs taken, and where they are kept",
    "debrief": "Debrief with the client: date, attendees, outcome",
}
_MAX_FIELD = 2000


# ---------------------------------------------------------------- the project form and sign-off


def _path(site: str) -> Path:
    return safe_job_dir(_SITES_ROOT, site) / "report.json"


def load(site: str) -> dict[str, Any]:
    path = _path(site)
    if not path.exists():
        return {"form": {}, "prepared_by": "", "form_saved_at": "", "approvals": []}
    return dict(json.loads(path.read_text(encoding="utf-8")))


def save_form(site: str, form: Any, user: str) -> dict[str, Any]:
    if not isinstance(form, dict) or not set(form) <= set(FORM_FIELDS):
        raise ValueError(f"the project form takes only these fields: {', '.join(FORM_FIELDS)}")
    clean = {key: str(value).strip()[:_MAX_FIELD] for key, value in form.items() if str(value).strip()}
    with job_lock(safe_job_dir(_SITES_ROOT, site)):
        state = {**load(site), "form": clean, "prepared_by": user,
                 "form_saved_at": datetime.now(UTC).isoformat(timespec="seconds")}
        write_text_atomically(_path(site), json.dumps(state, indent=2) + "\n")
    return state


def approve(site: str, user: str, competence: str, content_hash: str) -> dict[str, Any]:
    """Record an approval of the report as it reads now. The preparer cannot approve their own report."""
    competence = competence.strip()[:_MAX_FIELD]
    if not competence:
        raise ValueError("say what qualifies you to approve the report (training, qualifications)")
    with job_lock(safe_job_dir(_SITES_ROOT, site)):
        state = load(site)
        if not state["prepared_by"]:
            raise ValueError("fill in and save the project form before the report can be approved")
        if state["prepared_by"].lower() == user.lower():
            raise ValueError("the person who prepared the report cannot approve it; another account must")
        approval = {"by": user, "competence": competence, "at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "content_hash": content_hash}
        state = {**state, "approvals": [*state["approvals"], approval]}
        write_text_atomically(_path(site), json.dumps(state, indent=2) + "\n")
    return approval


def status(state: dict[str, Any], content_hash: str) -> dict[str, Any]:
    """APPROVED only if the newest approval was given to this exact content."""
    latest = state["approvals"][-1] if state["approvals"] else None
    if latest and latest["content_hash"] == content_hash:
        return {"state": "APPROVED", "approval": latest}
    return {"state": "DRAFT", "approval": None,
            "why": "changed since it was approved" if latest else "not yet approved by a second person"}


def content_hash(content: dict[str, Any], state: dict[str, Any]) -> str:
    payload = json.dumps([content, state["form"], state["prepared_by"]], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- the PDF


_styles = getSampleStyleSheet()
_BODY = ParagraphStyle("body", parent=_styles["BodyText"], fontSize=9, leading=12)
_SMALL = ParagraphStyle("small", parent=_BODY, fontSize=7.5, leading=9.5, textColor=colors.HexColor("#555555"))
_WARN = ParagraphStyle("warn", parent=_BODY, textColor=colors.HexColor("#8a4b00"), fontName="Helvetica-Bold")
_H = ParagraphStyle("h", parent=_styles["Heading3"], fontSize=11.5, spaceBefore=10, spaceAfter=3)


def _p(text: object, style: ParagraphStyle = _BODY) -> Paragraph:
    return Paragraph(escape(str(text)), style)


def _table(rows: list[list[Any]], widths: list[float]) -> Table:
    table = Table([[_p(c, _SMALL) for c in row] for row in rows], colWidths=[w * cm for w in widths], repeatRows=1)
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#bbbbbb")),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return table


def plan_png(content: dict[str, Any]) -> io.BytesIO:
    """The site in plan, in local metres, north up: what was placed, what was found, what records say."""
    fig, ax = plt.subplots(figsize=(7.2, 5.4), dpi=150)
    colour = {"surveyed": "#16a34a", "gps_track": "#2563eb"}
    for line in content["plan"]["lines"]:
        xs, ys = zip(*line["en"], strict=True)
        ax.plot(xs, ys, color=colour.get(line["source"], "#555555"), linewidth=1.2)
        ax.annotate(line["label"], line["en"][0], fontsize=5, color="#333333")
    for spot in content["plan"]["located"]:
        ax.add_patch(plt.Circle(spot["en"], spot["radius_m"], fill=False, linestyle="--", color="#d97706", linewidth=0.8))
        ax.annotate(f"{spot['label']} (location only)", spot["en"], fontsize=5, color="#b45309")
    if content["plan"]["targets"]:
        xs, ys = zip(*content["plan"]["targets"], strict=True)
        ax.scatter(xs, ys, s=4, color="#71717a", label="detected target", zorder=3)
    for run in content["plan"]["runs"]:
        xs, ys = zip(*run["en"], strict=True)
        ax.plot(xs, ys, color="#16a34a" if run["status"] == "confirmed" else "#f59e0b", linewidth=2.2,
                linestyle="-" if run["status"] == "confirmed" else "--", zorder=4)
    if content["plan"]["records"]:
        xs, ys = zip(*(r["en"] for r in content["plan"]["records"]), strict=True)
        ax.scatter(xs, ys, s=14, marker="x", color="#dc2626", label="utility record call-out", zorder=5)
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_xlabel("east of site origin (m)")
    ax.set_ylabel("north of site origin (m)")
    ax.grid(linewidth=0.3, alpha=0.5)
    ax.legend(fontsize=6, loc="best")
    ax.tick_params(labelsize=6)
    buffer = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buffer, format="png")
    plt.close(fig)
    buffer.seek(0)
    return buffer


def pdf(site: str, content: dict[str, Any], state: dict[str, Any], digest: str) -> bytes:
    sign = status(state, digest)
    form = state["form"]
    story: list[Any] = [
        _p(f"Utility survey report — site {site}", _styles["Title"]),
        _p(f"{sign['state']}" + (f" ({sign['why']})" if sign["state"] == "DRAFT" else "")
           + f" · generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} UTC · content {digest}",
           _WARN if sign["state"] == "DRAFT" else _BODY),
        Spacer(1, 4), _p(SPEC["basis"], _SMALL), Spacer(1, 2),
        _p("Every line below is either measured by GPR Studio or written in the project form, as each section "
           "says. Nothing is graded on the PAS 128 quality-level scale: see the last sections.", _SMALL),
    ]
    for section in SPEC["sections"]:
        story += [_p(f"{section['id']}  {section['title']}", _H),
                  _p(f"Requirement: {section['requirement']} ({SPEC['sources'][section['source']]}) · filled from "
                     f"{'the Studio data' if section['filled_by'] == 'data' else 'the project form' if section['filled_by'] == 'form' else 'data and the project form'}",
                     _SMALL)]
        story += _section(section["id"], content, form)
    story += [_p("Sign-off", _H), _table([
        ["Role", "Name (account)", "Competence", "When"],
        ["Prepared by", state["prepared_by"] or "—", form.get("surveyors", "—"), state["form_saved_at"] or "—"],
        ["Approved by", *((sign["approval"]["by"], sign["approval"]["competence"], sign["approval"]["at"])
                          if sign["approval"] else ("— not approved —", "", ""))],
    ], [3, 4, 6.5, 3.5])]
    buffer = io.BytesIO()

    def stamp(canvas: Any, _doc: Any) -> None:
        if sign["state"] == "DRAFT":
            canvas.saveState()
            canvas.setFont("Helvetica-Bold", 60)
            canvas.setFillColor(colors.Color(0.85, 0.2, 0.2, alpha=0.12))
            canvas.translate(A4[0] / 2, A4[1] / 2)
            canvas.rotate(45)
            canvas.drawCentredString(0, 0, "DRAFT")
            canvas.restoreState()
        canvas.setFont("Helvetica", 7)
        canvas.drawString(1.5 * cm, 1 * cm, f"GPR Studio · site {site} · {sign['state']} · content {digest}")

    SimpleDocTemplate(buffer, pagesize=A4, title=f"Utility survey report {site}", leftMargin=1.5 * cm,
                      rightMargin=1.5 * cm, topMargin=1.5 * cm, bottomMargin=1.6 * cm).build(
        story, onFirstPage=stamp, onLaterPages=stamp)
    return buffer.getvalue()


def _form(form: dict[str, str], key: str) -> list[Any]:
    value = form.get(key)
    return [_p(f"{FORM_FIELDS[key]}: {value}")] if value else [_p(f"{FORM_FIELDS[key]}: not provided in the project form.", _WARN)]


def _section(section_id: str, c: dict[str, Any], form: dict[str, str]) -> list[Any]:
    if section_id == "01":
        return [*(item for k in ("client", "project", "requirements", "area") for item in _form(form, k)), _p(c["area"])]
    if section_id == "02":
        return [_p(item) for item in c["methods"]]
    if section_id == "03a":
        return [Image(plan_png(c), width=17 * cm, height=12.75 * cm), _p(c["plan"]["note"], _SMALL)]
    if section_id == "03b":
        return [_table([["Line", "Recorded", "Length", "Traces", "Channels", "Header ε", "Position"], *c["metadata"]],
                       [3.2, 2.8, 1.6, 1.4, 3.4, 1.6, 4])]
    if section_id == "03c":
        return [_p(item) for item in c["success"]]
    if section_id == "03d":
        missing = c["records"]["not_detected"]
        if not c["plan"]["records"]:
            return [_p(c["records"]["note"])]  # nothing in the area to have missed
        return [_p(c["records"]["note"], _SMALL),
                *([_p(item) for item in missing] if missing else [_p("None: every record call-out in the area has a detection nearby.")])]
    if section_id == "03e":
        return [_p(item) for item in c["other_features"]] or [_p("None detected.")]
    if section_id == "03f":
        return [_p(item) for item in c["records"]["conflicts"]] or [_p("No conflicts found within the matching tolerance.")]
    if section_id == "03g":
        return _form(form, "photographs")
    if section_id == "03h":
        return [_p(f"• {item}") for item in c["recommendations"]]
    if section_id == "03i":
        return [_p(item) for item in c["calibration"]]
    if section_id == "03j":
        return [_p(item) for item in c["software"]]
    if section_id == "03k":
        return [_p(c["penetration"]["summary"]),
                _table([["Line", "Usable depth (RAD)", "Limited by"], *c["penetration"]["rows"]], [4, 3.5, 10])]
    if section_id == "03l":
        return [*_form(form, "weather"), *_form(form, "site_notes"), *(_p(w, _WARN) for w in c["conditions"])]
    if section_id == "04":
        return _form(form, "debrief")
    if section_id == "QL":
        rows = [["Run", "Status", "Lines", "Depth (m)", "Quality level", "Why"], *c["quality"]["rows"]]
        return [_p(c["quality"]["note"]), _table(rows, [2.6, 2, 1.2, 2, 2.6, 7.6])] if c["quality"]["rows"] \
            else [_p(c["quality"]["note"]), _p("No utility runs were found on this site.")]
    if section_id == "archive":
        return [_p(item) for item in c["archive"]]
    return [_p("This section is in the specification but the Studio has nothing to fill it with.", _WARN)]
