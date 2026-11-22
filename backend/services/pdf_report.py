import json
from html import escape
from io import BytesIO
from pathlib import Path

import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


_NAVY = colors.HexColor("#0f2747")
_BLUE = colors.HexColor("#2563eb")
_PALE_BLUE = colors.HexColor("#eff6ff")
_PALE_GREY = colors.HexColor("#f8fafc")
_MID_GREY = colors.HexColor("#64748b")
_DARK_GREY = colors.HexColor("#334155")
_BORDER = colors.HexColor("#dbe3ee")
_DISCLAIMER = (
    "BrandGuard AI automated audit results are advisory and do not replace "
    "required human review, professional advice, or legal approval."
)


def _register_fonts():
    font_dir = Path(reportlab.__file__).resolve().parent / "fonts"
    regular_path = font_dir / "Vera.ttf"
    bold_path = font_dir / "VeraBd.ttf"
    if regular_path.is_file() and bold_path.is_file():
        if "BrandGuard-Regular" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("BrandGuard-Regular", str(regular_path)))
        if "BrandGuard-Bold" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("BrandGuard-Bold", str(bold_path)))
        pdfmetrics.registerFontFamily(
            "BrandGuard-Regular",
            normal="BrandGuard-Regular",
            bold="BrandGuard-Bold",
            italic="BrandGuard-Regular",
            boldItalic="BrandGuard-Bold",
        )
        return "BrandGuard-Regular", "BrandGuard-Bold"
    return "Helvetica", "Helvetica-Bold"


_FONT, _FONT_BOLD = _register_fonts()


def _plain_text(value):
    if value is None or value == "":
        return "Not recorded"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _paragraph(value, style):
    text = escape(_plain_text(value))
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")
    return Paragraph(text, style)


def _risk_label(value):
    label = str(value).strip().upper()
    if label == "MODERATE":
        return "Medium (MODERATE)"
    return label.title() if label else "Not recorded"


def _issue_correction_status(issue):
    if issue.get("manual_review_required") is True:
        return "Manual review required; no automatic correction was applied."
    if issue.get("auto_fixable") is True:
        return "Validated automatic correction available; this report did not apply it."
    return "No validated automatic correction; human review is required."


def _issue_counts(issues):
    counts = {"total": len(issues), "critical": 0, "warning": 0, "informational": 0}
    for issue in issues:
        severity = str(issue.get("severity") or "").strip().lower()
        if severity == "critical":
            counts["critical"] += 1
        elif severity == "warning":
            counts["warning"] += 1
        elif severity in {"info", "informational"}:
            counts["informational"] += 1
    return counts


def _make_styles():
    regular, bold = _FONT, _FONT_BOLD
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="BGTitle",
        parent=styles["Title"],
        fontName=bold,
        fontSize=20,
        leading=25,
        textColor=_NAVY,
        alignment=TA_LEFT,
        spaceAfter=5,
    ))
    styles.add(ParagraphStyle(
        name="BGSubtitle",
        parent=styles["Normal"],
        fontName=regular,
        fontSize=11,
        leading=15,
        textColor=_MID_GREY,
        spaceAfter=12,
    ))
    styles.add(ParagraphStyle(
        name="BGSection",
        parent=styles["Heading2"],
        fontName=bold,
        fontSize=12,
        leading=16,
        textColor=_NAVY,
        spaceBefore=13,
        spaceAfter=7,
        keepWithNext=True,
    ))
    styles.add(ParagraphStyle(
        name="BGBody",
        parent=styles["BodyText"],
        fontName=regular,
        fontSize=8.5,
        leading=12,
        textColor=_DARK_GREY,
        spaceAfter=4,
        wordWrap="CJK",
    ))
    styles.add(ParagraphStyle(
        name="BGLabel",
        parent=styles["Normal"],
        fontName=bold,
        fontSize=7.5,
        leading=10,
        textColor=_MID_GREY,
        spaceBefore=3,
        spaceAfter=2,
        keepWithNext=True,
    ))
    styles.add(ParagraphStyle(
        name="BGIssueTitle",
        parent=styles["Heading3"],
        fontName=bold,
        fontSize=9.5,
        leading=13,
        textColor=_NAVY,
        spaceBefore=6,
        spaceAfter=5,
        keepWithNext=True,
        wordWrap="CJK",
    ))
    styles.add(ParagraphStyle(
        name="BGSmall",
        parent=styles["Normal"],
        fontName=regular,
        fontSize=7.5,
        leading=10,
        textColor=_DARK_GREY,
        wordWrap="CJK",
    ))
    styles.add(ParagraphStyle(
        name="BGTableHeader",
        parent=styles["Normal"],
        fontName=bold,
        fontSize=7.5,
        leading=10,
        textColor=colors.white,
    ))
    styles.add(ParagraphStyle(
        name="BGFooter",
        parent=styles["Normal"],
        fontName=regular,
        fontSize=6.5,
        leading=8,
        textColor=_MID_GREY,
    ))
    styles.add(ParagraphStyle(
        name="BGRight",
        parent=styles["Normal"],
        fontName=bold,
        fontSize=9,
        leading=12,
        textColor=_NAVY,
        alignment=TA_RIGHT,
    ))
    return styles


def _section(story, title, styles):
    story.append(Paragraph(escape(title), styles["BGSection"]))


def _append_labeled_value(story, label, value, styles):
    story.append(Paragraph(escape(label), styles["BGLabel"]))
    story.append(_paragraph(value, styles["BGBody"]))


def _draw_page(canvas, document):
    canvas.saveState()
    page_width, page_height = A4
    canvas.setFillColor(_NAVY)
    canvas.setFont(_FONT_BOLD, 9)
    canvas.drawString(document.leftMargin, page_height - 25, "BrandGuard AI")
    canvas.setFillColor(_MID_GREY)
    canvas.setFont(_FONT, 7.5)
    canvas.drawRightString(
        page_width - document.rightMargin,
        page_height - 25,
        "BRAND COMPLIANCE AUDIT",
    )
    canvas.setStrokeColor(_BORDER)
    canvas.setLineWidth(0.6)
    canvas.line(
        document.leftMargin,
        page_height - 32,
        page_width - document.rightMargin,
        page_height - 32,
    )
    footer = Paragraph(escape(_DISCLAIMER), _make_styles()["BGFooter"])
    footer_width = document.width - 55
    footer.wrap(footer_width, 22)
    footer.drawOn(canvas, document.leftMargin, 16)
    canvas.setStrokeColor(_BORDER)
    canvas.line(document.leftMargin, 37, page_width - document.rightMargin, 37)
    canvas.setFillColor(_MID_GREY)
    canvas.setFont(_FONT, 7)
    canvas.drawRightString(
        page_width - document.rightMargin,
        19,
        f"Page {document.page}",
    )
    canvas.restoreState()


def build_pdf_report(report):
    styles = _make_styles()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=48,
        rightMargin=48,
        topMargin=50,
        bottomMargin=52,
        title="Brand Compliance Audit Report",
        author="BrandGuard AI",
        subject=f"Audit report {report['audit_id']}",
    )
    story = [
        Paragraph("Brand Compliance Audit Report", styles["BGTitle"]),
        _paragraph(report["brand_name"], styles["BGSubtitle"]),
    ]

    metadata_rows = [
        [
            Paragraph("AUDIT ID", styles["BGLabel"]),
            _paragraph(report["audit_id"], styles["BGBody"]),
            Paragraph("AUDIT DATE", styles["BGLabel"]),
            _paragraph(report["audit_date"], styles["BGBody"]),
        ],
        [
            Paragraph("CONTENT TYPE", styles["BGLabel"]),
            _paragraph(report["content_type"], styles["BGBody"]),
            Paragraph("CONTENT VERSION", styles["BGLabel"]),
            _paragraph(report["content_version"], styles["BGBody"]),
        ],
        [
            Paragraph("CONTENT TITLE", styles["BGLabel"]),
            _paragraph(report["content_title"], styles["BGBody"]),
            Paragraph("CHANNEL", styles["BGLabel"]),
            _paragraph(report["channel"], styles["BGBody"]),
        ],
        [
            Paragraph("OVERALL SCORE", styles["BGLabel"]),
            Paragraph(f"{report['overall_score']}/100", styles["BGRight"]),
            Paragraph("RISK LEVEL", styles["BGLabel"]),
            _paragraph(_risk_label(report["risk_level"]), styles["BGBody"]),
        ],
    ]
    metadata = Table(
        metadata_rows,
        colWidths=[
            document.width * 0.19,
            document.width * 0.31,
            document.width * 0.19,
            document.width * 0.31,
        ],
        hAlign="LEFT",
    )
    metadata.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _PALE_BLUE),
        ("BOX", (0, 0), (-1, -1), 0.5, _BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([metadata, Spacer(1, 4)])

    _section(story, "Governance Summary", styles)
    governance_labels = [
        ("facts", "Fact Accuracy"),
        ("claims", "Claim Safety"),
        ("terminology", "Terminology"),
        ("tone", "Voice & Tone"),
        ("accessibility", "Accessibility"),
    ]
    governance_rows = [[
        Paragraph("CATEGORY", styles["BGTableHeader"]),
        Paragraph("SCORE", styles["BGTableHeader"]),
    ]]
    for score_key, label in governance_labels:
        governance_rows.append([
            _paragraph(label, styles["BGBody"]),
            _paragraph(f"{report['sub_scores'][score_key]}/100", styles["BGBody"]),
        ])
    governance_table = Table(
        governance_rows,
        colWidths=[document.width * 0.72, document.width * 0.28],
        repeatRows=1,
    )
    governance_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _NAVY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _PALE_GREY]),
        ("GRID", (0, 0), (-1, -1), 0.35, _BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(governance_table)

    _section(story, "Audit Summary", styles)
    counts = report["issue_counts"]
    count_rows = [[
        Paragraph("TOTAL ISSUES", styles["BGTableHeader"]),
        Paragraph("CRITICAL", styles["BGTableHeader"]),
        Paragraph("WARNINGS", styles["BGTableHeader"]),
        Paragraph("INFORMATIONAL", styles["BGTableHeader"]),
    ], [
        _paragraph(counts["total"], styles["BGRight"]),
        _paragraph(counts["critical"], styles["BGRight"]),
        _paragraph(counts["warning"], styles["BGRight"]),
        _paragraph(counts["informational"], styles["BGRight"]),
    ]]
    count_table = Table(count_rows, colWidths=[document.width / 4] * 4)
    count_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _NAVY),
        ("BACKGROUND", (0, 1), (-1, 1), _PALE_GREY),
        ("BOX", (0, 0), (-1, -1), 0.5, _BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, _BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([count_table, Spacer(1, 5)])
    _append_labeled_value(story, "Overall assessment", report["assessment"], styles)
    _append_labeled_value(
        story,
        "Human review",
        "Required before publication. Automated results are advisory.",
        styles,
    )

    _section(story, "Detailed Findings", styles)
    if not report["issues"]:
        story.append(_paragraph("No issues were detected in this audit.", styles["BGBody"]))
    for index, issue in enumerate(report["issues"], start=1):
        title = issue.get("rule") or issue.get("title") or issue.get("category") or f"Issue {index}"
        story.append(_paragraph(f"{index}. {title}", styles["BGIssueTitle"]))
        severity = issue.get("severity") or "Not recorded"
        category = issue.get("category") or "Not recorded"
        badge = Table([[
            _paragraph(f"Severity: {severity}", styles["BGSmall"]),
            _paragraph(f"Category: {category}", styles["BGSmall"]),
        ]], colWidths=[document.width / 2, document.width / 2])
        badge.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), _PALE_GREY),
            ("BOX", (0, 0), (-1, -1), 0.4, _BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(badge)
        _append_labeled_value(story, "Detected source text", issue.get("matched_text"), styles)
        location = issue.get("location")
        if isinstance(location, dict):
            _append_labeled_value(
                story,
                "Source offsets",
                f"{location.get('start', 'Not recorded')}–{location.get('end', 'Not recorded')}",
                styles,
            )
        _append_labeled_value(
            story,
            "Explanation",
            issue.get("explanation") or issue.get("description"),
            styles,
        )
        _append_labeled_value(story, "Recommendation", issue.get("suggestion"), styles)
        _append_labeled_value(
            story,
            "Validated replacement",
            issue.get("replacement_text"),
            styles,
        )
        if issue.get("required_disclaimer"):
            _append_labeled_value(
                story,
                "Required disclaimer",
                issue["required_disclaimer"],
                styles,
            )
        _append_labeled_value(
            story,
            "Correction status",
            _issue_correction_status(issue),
            styles,
        )
        story.append(HRFlowable(
            width="100%",
            thickness=0.4,
            color=_BORDER,
            spaceBefore=5,
            spaceAfter=4,
        ))

    _section(story, "Correction and Version Information", styles)
    _append_labeled_value(
        story,
        "Original content (version 1)",
        report["original_content"],
        styles,
    )
    _append_labeled_value(
        story,
        "Audited source content (current version)",
        report["audited_content"],
        styles,
    )
    _append_labeled_value(
        story,
        "Stored audit correction preview",
        report["correction_preview"],
        styles,
    )

    story.append(Paragraph("Saved versions", styles["BGIssueTitle"]))
    if not report["versions"]:
        story.append(_paragraph("No saved version records were found.", styles["BGBody"]))
    for version in report["versions"]:
        version_text = (
            f"Version {version.get('version_number')} · "
            f"{version.get('created_at') or 'Timestamp unavailable'} · "
            f"Score {version.get('risk_score') if version.get('risk_score') is not None else 'Not recorded'}"
        )
        if version.get("risk_level"):
            version_text += f" · Risk {version['risk_level']}"
        story.append(_paragraph(version_text, styles["BGBody"]))

    story.append(Paragraph("Approved manual correction previews", styles["BGIssueTitle"]))
    if not report["manual_corrections"]:
        story.append(_paragraph(
            "No approved manual correction previews are recorded for this content.",
            styles["BGBody"],
        ))
    for correction in report["manual_corrections"]:
        story.append(_paragraph(
            f"Version {correction.get('version_number')} · Issue {correction.get('issue_id')} · "
            f"Offsets {correction.get('start_offset')}–{correction.get('end_offset')}",
            styles["BGSmall"],
        ))
        _append_labeled_value(
            story,
            "Original span",
            correction.get("original_text"),
            styles,
        )
        _append_labeled_value(
            story,
            "User-approved replacement",
            correction.get("replacement_text"),
            styles,
        )
        story.append(_paragraph(
            "Approved for preview; not saved as a new content version.",
            styles["BGSmall"],
        ))

    story.append(Paragraph("Human Review Status", styles["BGIssueTitle"]))
    if not report["reviews"]:
        story.append(_paragraph("No human review decision is recorded.", styles["BGBody"]))
    for review in report["reviews"]:
        story.append(_paragraph(
            f"{review.get('decision', 'Not recorded')} · "
            f"{review.get('reviewer_name', 'Reviewer not recorded')} · "
            f"{review.get('created_at', 'Timestamp unavailable')}",
            styles["BGBody"],
        ))
        if review.get("comments"):
            _append_labeled_value(story, "Review comments", review["comments"], styles)

    document.build(story, onFirstPage=_draw_page, onLaterPages=_draw_page)
    return buffer.getvalue()
