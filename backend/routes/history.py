
import json
import logging
from io import BytesIO
from flask import Blueprint, jsonify, send_file
from werkzeug.utils import secure_filename
from database import db_session, dict_from_row
from api_utils import is_positive_int, validate_query_id
from services.pdf_report import build_pdf_report

logger = logging.getLogger(__name__)

history_bp = Blueprint("history", __name__, url_prefix="/api/history")


def _load_pdf_audit_data(content_id, brand_id):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT c.*, b.name AS brand_name, b.industry, b.primary_tone
            FROM content_items c
            JOIN brands b ON c.brand_id = b.id
            WHERE c.id = ? AND c.brand_id = ?
            """,
            (content_id, brand_id),
        )
        item_row = cursor.fetchone()
        if not item_row:
            return None
        item = dict_from_row(item_row)

        cursor.execute(
            """
            SELECT *
            FROM analysis_results
            WHERE content_id = ? AND version_number = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (content_id, item["current_version"]),
        )
        analysis_row = cursor.fetchone()
        if not analysis_row:
            return {"item": item, "analysis": None}
        analysis = dict_from_row(analysis_row)

        for key in ("sub_scores", "issues"):
            value = analysis.get(key)
            if isinstance(value, str):
                try:
                    analysis[key] = json.loads(value)
                except (json.JSONDecodeError, TypeError) as error:
                    logger.warning(
                        "Could not decode stored PDF audit data "
                        "(exception_category=%s)",
                        type(error).__name__,
                    )

        cursor.execute(
            """
            SELECT *
            FROM reviews
            WHERE content_id = ?
            ORDER BY id DESC
            """,
            (content_id,),
        )
        reviews = [dict_from_row(row) for row in cursor.fetchall()]

        cursor.execute(
            """
            SELECT *
            FROM content_versions
            WHERE content_id = ?
            ORDER BY version_number ASC
            """,
            (content_id,),
        )
        versions = [dict_from_row(row) for row in cursor.fetchall()]

        cursor.execute(
            """
            SELECT m.issue_id, m.original_text, m.replacement_text,
                   m.start_offset, m.end_offset, m.created_at, m.updated_at,
                   v.version_number
            FROM manual_correction_drafts m
            JOIN content_versions v ON v.id = m.content_version_id
            WHERE m.content_id = ?
            ORDER BY v.version_number ASC, m.start_offset ASC, m.end_offset ASC
            """,
            (content_id,),
        )
        manual_corrections = [
            dict_from_row(row) for row in cursor.fetchall()
        ]

    return {
        "item": item,
        "analysis": analysis,
        "reviews": reviews,
        "versions": versions,
        "manual_corrections": manual_corrections,
    }


@history_bp.route("/dashboard-stats", methods=["GET"])
def dashboard_stats():
    brand_id = validate_query_id("brand_id")

    base_filter = " WHERE brand_id = ?" if brand_id is not None else ""
    params = [brand_id] if brand_id is not None else []

    try:
        with db_session() as conn:
            cursor = conn.cursor()

            # Total scanned
            cursor.execute(
                f"SELECT COUNT(*) AS total FROM content_items{base_filter}",
                params
            )
            total_scanned = cursor.fetchone()["total"]

            # Average score
            score_filter = (
                "WHERE brand_id = ? AND risk_score > 0"
                if brand_id is not None
                else "WHERE risk_score > 0"
            )
            cursor.execute(
                f"SELECT AVG(risk_score) AS avg_score FROM content_items {score_filter}",
                params
            )
            avg_row = cursor.fetchone()
            avg_score = round(avg_row["avg_score"] or 0, 1)

            # Status counts
            cursor.execute(
                f"""
                SELECT status, COUNT(*) AS count
                FROM content_items{base_filter}
                GROUP BY status
                """,
                params
            )
            status_counts = {
                row["status"]: row["count"]
                for row in cursor.fetchall()
            }

            # Recent items
            recent_filter = "WHERE c.brand_id = ?" if brand_id is not None else ""
            cursor.execute(
                f"""
                SELECT c.*, b.name AS brand_name
                FROM content_items c
                JOIN brands b ON c.brand_id = b.id
                {recent_filter}
                ORDER BY c.updated_at DESC
                LIMIT 6
                """,
                params
            )
            recent_items = [
                dict_from_row(row)
                for row in cursor.fetchall()
            ]

            # Latest analysis issues, optionally filtered by brand
            brand_filter = (
                "AND c.brand_id = ?"
                if brand_id is not None
                else ""
            )

            issue_params = (brand_id,) if brand_id is not None else ()

            cursor.execute(
                f"""
                SELECT ar.issues
                FROM analysis_results ar
                JOIN content_items c ON c.id = ar.content_id
                WHERE 1=1
                  {brand_filter}
                  AND ar.id = (
                      SELECT MAX(latest.id)
                      FROM analysis_results latest
                      WHERE latest.content_id = ar.content_id
                  )
                ORDER BY ar.id DESC
                LIMIT 50
                """,
                issue_params
            )
            rows = cursor.fetchall()

        violation_categories = {
            "Terminology": 0,
            "Fact Accuracy": 0,
            "Voice & Tone": 0,
            "Compliance & Claims": 0,
            "Accessibility & Clarity": 0
        }

        top_prohibited_terms = {}

        for row in rows:
            try:
                issues = (
                    json.loads(row["issues"])
                    if isinstance(row["issues"], str)
                    else row["issues"]
                )

                if not isinstance(issues, list):
                    continue

                for issue in issues:
                    if not isinstance(issue, dict):
                        continue

                    category = issue.get("category", "Terminology")

                    violation_categories[category] = (
                        violation_categories.get(category, 0) + 1
                    )

                    if category == "Terminology":
                        term = issue.get("matched_text", "").lower()
                        if term:
                            top_prohibited_terms[term] = (
                                top_prohibited_terms.get(term, 0) + 1
                            )

            except (TypeError, ValueError, AttributeError) as error:
                logger.warning(
                    "Skipping malformed stored analysis issues "
                    "(exception_category=%s)",
                    type(error).__name__,
                )

        sorted_terms = sorted(
            top_prohibited_terms.items(),
            key=lambda item: item[1],
            reverse=True
        )[:5]

        return jsonify({
            "success": True,
            "total_scanned": total_scanned,
            "average_score": avg_score,
            "status_breakdown": status_counts,
            "violation_categories": violation_categories,
            "top_prohibited_terms": [
                {"term": term, "count": count}
                for term, count in sorted_terms
            ],
            "recent_items": recent_items
        })

    except Exception as error:
        logger.error(
            "Error in dashboard_stats (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({"success": False, "error": "Could not retrieve dashboard statistics."}), 500


@history_bp.route("/export/<int:content_id>", methods=["GET"])
def export_audit_report(content_id):
    if not is_positive_int(content_id):
        return jsonify({"success": False, "error": "Content item not found"}), 404
    brand_id = validate_query_id("brand_id")
    try:
        with db_session() as conn:
            cursor = conn.cursor()

            query = """
                SELECT c.*, b.name AS brand_name, b.industry, b.primary_tone
                FROM content_items c
                JOIN brands b ON c.brand_id = b.id
                WHERE c.id = ?
            """
            params = [content_id]
            if brand_id is not None:
                query += " AND c.brand_id = ?"
                params.append(brand_id)
            cursor.execute(query, params)

            item = cursor.fetchone()

            if not item:
                return jsonify({
                    "success": False,
                    "error": "Content item not found"
                }), 404

            item_dict = dict_from_row(item)

            # Latest analysis
            cursor.execute(
                """
                SELECT *
                FROM analysis_results
                WHERE content_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (content_id,)
            )

            analysis_row = cursor.fetchone()
            analysis = (
                dict_from_row(analysis_row)
                if analysis_row
                else {}
            )

            if analysis:
                for key in ("sub_scores", "issues"):
                    value = analysis.get(key)

                    if value and isinstance(value, str):
                        try:
                            analysis[key] = json.loads(value)
                        except (json.JSONDecodeError, TypeError) as error:
                            logger.warning(
                                "Could not decode analysis field "
                                "(exception_category=%s)",
                                type(error).__name__,
                            )

            # Review history
            cursor.execute(
                """
                SELECT *
                FROM reviews
                WHERE content_id = ?
                ORDER BY id DESC
                """,
                (content_id,)
            )
            reviews = [
                dict_from_row(row)
                for row in cursor.fetchall()
            ]

            # Version history
            cursor.execute(
                """
                SELECT *
                FROM content_versions
                WHERE content_id = ?
                ORDER BY version_number ASC
                """,
                (content_id,)
            )
            versions = [
                dict_from_row(row)
                for row in cursor.fetchall()
            ]

        report = {
            "report_title": (
                f"BrandGuard AI Compliance Audit - {item_dict['title']}"
            ),
            "generated_at": item_dict.get("updated_at"),

            "brand": {
                "name": item_dict["brand_name"],
                "industry": item_dict["industry"],
                "primary_tone": item_dict["primary_tone"]
            },

            "content_metadata": {
                "title": item_dict["title"],
                "content_type": item_dict["content_type"],
                "channel": item_dict["channel"],
                "status": item_dict["status"],
                "version": item_dict["current_version"]
            },

            "compliance_summary": {
                "overall_score": item_dict["risk_score"],
                "risk_level": item_dict["risk_level"],
                "sub_scores": analysis.get("sub_scores", {}),
                "summary": analysis.get("summary", ""),
                "engine_used": analysis.get(
                    "engine_used",
                    "RULE_BASED_FALLBACK"
                )
            },

            "issues_detected": analysis.get("issues", []),
            "original_text": item_dict["original_text"],
            "corrected_text": item_dict["corrected_text"],
            "review_audit_log": reviews,
            "version_history": versions
        }

        return jsonify({
            "success": True,
            "report": report
        })

    except Exception as error:
        logger.error(
            "Error in export_audit_report (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({
            "success": False,
            "error": "Could not export the audit report."
        }), 500


@history_bp.route("/export/<int:content_id>/pdf", methods=["GET"])
def export_audit_report_pdf(content_id):
    if not is_positive_int(content_id):
        return jsonify({"success": False, "error": "Content item not found"}), 404
    brand_id = validate_query_id("brand_id", required=True)

    try:
        audit_data = _load_pdf_audit_data(content_id, brand_id)
        if not audit_data:
            return jsonify({
                "success": False,
                "error": "Content item not found for the requested brand.",
            }), 404
        analysis = audit_data.get("analysis")
        if not analysis:
            return jsonify({
                "success": False,
                "error": "No audit result is available for the current content version.",
            }), 404

        issues = analysis.get("issues")
        sub_scores = analysis.get("sub_scores")
        score = analysis.get("score")
        risk_level = analysis.get("risk_level")
        required_score_keys = {
            "facts", "claims", "terminology", "tone", "accessibility"
        }
        if (
            not isinstance(issues, list)
            or not all(isinstance(issue, dict) for issue in issues)
            or not isinstance(sub_scores, dict)
            or not required_score_keys.issubset(sub_scores)
            or isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not 0 <= score <= 100
            or not isinstance(risk_level, str)
            or not risk_level.strip()
            or any(
                isinstance(sub_scores[key], bool)
                or not isinstance(sub_scores[key], (int, float))
                or not 0 <= sub_scores[key] <= 100
                for key in required_score_keys
            )
        ):
            return jsonify({
                "success": False,
                "error": "The stored audit result is incomplete and cannot be exported.",
            }), 409

        item = audit_data["item"]
        versions = audit_data["versions"]
        current_version = item["current_version"]
        current_version_record = next(
            (
                version for version in versions
                if version["version_number"] == current_version
            ),
            None,
        )
        original_version = next(
            (version for version in versions if version["version_number"] == 1),
            None,
        )
        report = {
            "audit_id": analysis["id"],
            "content_id": item["id"],
            "audit_date": analysis.get("created_at"),
            "brand_name": item["brand_name"],
            "content_title": item.get("title"),
            "content_type": item.get("content_type"),
            "channel": item.get("channel"),
            "content_version": current_version,
            "overall_score": score,
            "risk_level": risk_level,
            "sub_scores": sub_scores,
            "assessment": analysis.get("summary"),
            "issues": issues,
            "issue_counts": _pdf_issue_counts(issues),
            "original_content": (
                original_version.get("text")
                if original_version
                else item.get("original_text")
            ),
            "audited_content": (
                current_version_record.get("text")
                if current_version_record
                else item.get("original_text")
            ),
            "correction_preview": analysis.get("corrected_text"),
            "versions": versions,
            "manual_corrections": audit_data["manual_corrections"],
            "reviews": audit_data["reviews"],
        }
        pdf_bytes = build_pdf_report(report)
        safe_brand_name = secure_filename(item.get("brand_name") or "") or "Brand"
        response = send_file(
            BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"BrandGuard_Audit_Report_{safe_brand_name}.pdf",
            max_age=0,
        )
        response.headers["Cache-Control"] = "no-store"
        return response
    except Exception as error:
        logger.error(
            "Error generating audit PDF (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({
            "success": False,
            "error": "Could not generate the audit report PDF.",
        }), 500


def _pdf_issue_counts(issues):
    counts = {
        "total": len(issues),
        "critical": 0,
        "warning": 0,
        "informational": 0,
    }
    for issue in issues:
        severity = str(issue.get("severity") or "").strip().lower()
        if severity == "critical":
            counts["critical"] += 1
        elif severity == "warning":
            counts["warning"] += 1
        elif severity in {"info", "informational"}:
            counts["informational"] += 1
    return counts
