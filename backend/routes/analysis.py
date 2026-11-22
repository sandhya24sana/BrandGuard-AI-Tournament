import json
import logging
import re
import time
from functools import wraps
from flask import Blueprint, current_app, jsonify, request
from werkzeug.utils import secure_filename
from api_utils import (
    MAX_GEMINI_TEXT_LENGTH,
    MAX_VISUAL_IMAGE_BYTES,
    is_positive_int,
    json_object_payload,
    validate_id_field,
    validate_list_field,
    validate_query_id,
    validate_required_fields,
    validate_string_fields,
)
from database import PostgresConnection, db_session, dict_from_row
from services.correction_engine import (
    apply_corrections as apply_correction_plan,
    validate_replacement_text,
)
from services.analyzer import GovernanceAnalyzer
from services.ai_service import (
    GeminiRewriteValidationError,
    GeminiUnavailableError,
    GeminiVisualAnalysisUnavailableError,
    GeminiVisualAnalysisValidationError,
)
from extensions import limiter

logger = logging.getLogger(__name__)

analysis_bp = Blueprint("analysis", __name__, url_prefix="/api/analysis")
analyzer = GovernanceAnalyzer()

def _detect_visual_image_mime(image_bytes):
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if (
        len(image_bytes) >= 12
        and image_bytes[:4] == b"RIFF"
        and image_bytes[8:12] == b"WEBP"
    ):
        return "image/webp"
    return None


gemini_analysis_limit = limiter.shared_limit(
    lambda: current_app.config["GEMINI_ANALYSIS_RATE_LIMIT"],
    scope="gemini-analysis",
)


def _log_audit_duration(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        started_at = time.perf_counter()
        http_status = "unknown"
        try:
            result = view(*args, **kwargs)
            response = result[0] if isinstance(result, tuple) and result else result
            if (
                isinstance(result, tuple)
                and len(result) > 1
                and isinstance(result[1], int)
            ):
                http_status = result[1]
            else:
                http_status = getattr(response, "status_code", "unknown")
            return result
        finally:
            duration_ms = (time.perf_counter() - started_at) * 1000
            logger.info(
                "Gemini audit total_duration_ms=%.1f http_status=%s",
                duration_ms,
                http_status,
            )

    return wrapped


@analysis_bp.route("/system-status", methods=["GET"])
def system_status():
    try:
        ai_configured = analyzer.ai_service.is_configured()
        provider = analyzer.ai_service.get_provider_name()
        return jsonify({
            "success": True,
            "ai_configured": ai_configured,
            "active_engine": provider,
            "mode": "Gemini AI configured; rule-based fallback available" if ai_configured else "Rule-Based Fallback",
            "offline_ready": True
        })
    except Exception as error:
        logger.error(
            "Error in system-status (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({"success": False, "error": "Could not retrieve AI service status."}), 500

@analysis_bp.route("/quick-check", methods=["POST"])
@gemini_analysis_limit
def quick_check():
    data = json_object_payload()
    validate_string_fields(
        data,
        ("text", "content_type", "channel"),
        max_lengths={
            "text": MAX_GEMINI_TEXT_LENGTH,
            "content_type": 100,
            "channel": 200,
        },
    )
    validate_required_fields(data, ("brand_id", "text"))
    brand_id = validate_id_field(data, "brand_id", required=True)
    text = data.get("text", "")
    if not isinstance(text, str):
        return jsonify({"success": False, "error": "Content text must be a string"}), 400
    content_type = data.get("content_type") or "Ad Copy"
    channel = data.get("channel") or "Digital Ads"

    if not text.strip():
        return jsonify({"success": False, "error": "Content text is required for quick check"}), 400

    try:
        result = analyzer.analyze_content(brand_id, text, content_type, channel)
        return jsonify({"success": True, "result": result})
    except ValueError as e:
        return jsonify({"success": False, "error": "Brand not found."}), 404
    except Exception as error:
        logger.error(
            "Error in quick_check (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({"success": False, "error": "Could not complete the content check."}), 500


@analysis_bp.route("/rewrite", methods=["POST"])
@limiter.limit(
    lambda: current_app.config["GEMINI_REWRITE_RATE_LIMIT"],
    scope="gemini-rewrite",
)
def rewrite_content():
    data = json_object_payload()
    validate_string_fields(
        data,
        ("text", "content_type", "channel"),
        max_lengths={
            "text": MAX_GEMINI_TEXT_LENGTH,
            "content_type": 100,
            "channel": 200,
        },
    )
    validate_required_fields(data, ("brand_id", "text"))
    brand_id = validate_id_field(data, "brand_id", required=True)
    text = data.get("text", "")
    if not isinstance(text, str) or not text.strip():
        return jsonify({"success": False, "error": "Content text is required for rewriting"}), 400

    content_type = data.get("content_type") or "Ad Copy"
    channel = data.get("channel") or "Digital Ads"

    try:
        rewritten_text = analyzer.rewrite_content(
            brand_id,
            text,
            content_type,
            channel,
        )
        return jsonify({
            "success": True,
            "rewritten_text": rewritten_text,
            "engine_used": "AI_GEMINI",
            "saved": False,
        })
    except ValueError as error:
        return jsonify({"success": False, "error": "Brand not found."}), 404
    except GeminiUnavailableError as error:
        logger.warning("Gemini rewrite is unavailable.")
        return jsonify({
            "success": False,
            "error": str(error),
        }), error.response_status
    except GeminiRewriteValidationError:
        logger.warning("Gemini rewrite response failed validation.")
        return jsonify({"success": False, "error": "AI rewrite response could not be validated."}), 502
    except Exception as error:
        logger.error(
            "Error in rewrite_content (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({"success": False, "error": "Could not generate an AI rewrite."}), 500


@analysis_bp.route("/visual-check", methods=["POST"])
@gemini_analysis_limit
def visual_check():
    if (
        request.content_length is not None
        and request.content_length > MAX_VISUAL_IMAGE_BYTES + 65_536
    ):
        return jsonify({
            "success": False,
            "error": "Image upload must be 5 MB or smaller.",
        }), 413

    raw_brand_id = request.form.get("brand_id", "")
    if (
        len(raw_brand_id) > 19
        or not re.fullmatch(r"[0-9]+", raw_brand_id)
        or not is_positive_int(int(raw_brand_id))
    ):
        return jsonify({
            "success": False,
            "error": "A valid brand_id is required.",
        }), 400
    brand_id = int(raw_brand_id)

    image_file = request.files.get("file")
    if image_file is None or not image_file.filename:
        return jsonify({"success": False, "error": "An image file is required."}), 400
    if len(image_file.filename) > 255:
        return jsonify({
            "success": False,
            "error": "Filename must be 255 characters or fewer.",
        }), 400

    image_bytes = image_file.stream.read(MAX_VISUAL_IMAGE_BYTES + 1)
    if len(image_bytes) > MAX_VISUAL_IMAGE_BYTES:
        return jsonify({
            "success": False,
            "error": "Image upload must be 5 MB or smaller.",
        }), 413
    if not image_bytes:
        return jsonify({"success": False, "error": "The uploaded image is empty."}), 400

    mime_type = _detect_visual_image_mime(image_bytes)
    declared_mime = (image_file.mimetype or "").lower()
    if (
        mime_type is None
        or (
            declared_mime not in {"", "application/octet-stream", "binary/octet-stream"}
            and declared_mime != mime_type
        )
    ):
        return jsonify({
            "success": False,
            "error": "Upload a valid PNG, JPEG, or WebP image.",
        }), 400

    try:
        visual_validation = analyzer.analyze_visual_content(
            brand_id,
            image_bytes,
            mime_type,
        )
        return jsonify({
            "success": True,
            "filename": secure_filename(image_file.filename),
            "saved": False,
            "visual_validation": visual_validation,
        })
    except ValueError:
        return jsonify({"success": False, "error": "Brand not found."}), 404
    except GeminiVisualAnalysisUnavailableError as error:
        logger.warning("Gemini visual analysis is unavailable.")
        return jsonify({
            "success": False,
            "error": str(error),
        }), error.response_status
    except GeminiVisualAnalysisValidationError:
        logger.warning("Gemini visual analysis response failed validation.")
        return jsonify({
            "success": False,
            "error": "AI visual review response could not be validated.",
        }), 502
    except Exception as error:
        logger.error(
            "Error in visual_check (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({
            "success": False,
            "error": "Could not complete the visual review.",
        }), 500


@analysis_bp.route("/audit", methods=["POST"])
@analysis_bp.route("/run", methods=["POST"])
@gemini_analysis_limit
@_log_audit_duration
def audit_content():
    """
    Unified, atomic content audit endpoint.
    Accepts text, brand_id, title, content_type, channel, and optional content_id.
    Creates or updates content item, runs analysis, records version, and returns result.
    """
    data = json_object_payload()
    validate_string_fields(
        data,
        ("title", "content_type", "channel", "text"),
        max_lengths={
            "title": 250,
            "content_type": 100,
            "channel": 200,
            "text": MAX_GEMINI_TEXT_LENGTH,
        },
    )
    validate_required_fields(data, ("brand_id", "text"))
    content_id = data.get("content_id")
    current_version = data.get("current_version")
    brand_id = validate_id_field(data, "brand_id", required=True)
    title = data.get("title", "")
    content_type = data.get("content_type") or "Ad Copy"
    channel = data.get("channel") or "Digital Ads"
    text = data.get("text", "")

    if not isinstance(text, str):
        return jsonify({"success": False, "error": "Content text must be a string"}), 400
    if content_id is not None and not is_positive_int(content_id):
        return jsonify({"success": False, "error": "A valid content_id is required"}), 400
    if current_version is not None and not is_positive_int(current_version):
        return jsonify({"success": False, "error": "A valid current_version is required"}), 400
    if content_id is not None and current_version is None:
        return jsonify({
            "success": False,
            "error": "current_version is required when updating existing content",
        }), 400
    title = title.strip() if isinstance(title, str) else ""
    if not text.strip():
        return jsonify({"success": False, "error": "Content text is required for analysis."}), 400

    try:
        existing_content = None
        if content_id:
            with db_session() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT brand_id, current_version FROM content_items WHERE id = ?",
                    (content_id,)
                )
                existing_content = cursor.fetchone()
            if not existing_content:
                return jsonify({"success": False, "error": "Content item not found"}), 404
            if existing_content["brand_id"] != brand_id:
                return jsonify({"success": False, "error": "Content does not belong to the selected brand"}), 400
            if existing_content["current_version"] != current_version:
                return jsonify({
                    "success": False,
                    "error": "Content changed after this edit began. Reload the latest version before saving.",
                    "current_version": existing_content["current_version"],
                }), 409

        # Run analysis (AI or Rule-based fallback)
        result = analyzer.analyze_content(brand_id, text, content_type, channel)

        with db_session() as conn:
            cursor = conn.cursor()

            if content_id:
                version_num = existing_content["current_version"] + 1
                cursor.execute("""
                    UPDATE content_items
                    SET original_text = ?,
                        risk_score = ?,
                        risk_level = ?,
                        corrected_text = ?,
                        content_type = ?,
                        channel = ?,
                        current_version = ?,
                        status = 'pending_review',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND current_version = ?
                """, (
                    text, result["score"], result["risk_level"], result["corrected_text"],
                    content_type, channel, version_num, content_id,
                    current_version,
                ))
                if cursor.rowcount != 1:
                    return jsonify({
                        "success": False,
                        "error": "Content changed while the audit was running. Run the audit again.",
                    }), 409
                target_id = content_id
            else:
                cursor.execute("""
                    INSERT INTO content_items (brand_id, title, content_type, channel, original_text, corrected_text, current_version, status, risk_score, risk_level)
                    VALUES (?, ?, ?, ?, ?, ?, 1, 'pending_review', ?, ?)
                """, (
                    brand_id, title or "Untitled Content", content_type, channel,
                    text, result["corrected_text"], result["score"], result["risk_level"]
                ))
                target_id = cursor.lastrowid
                version_num = 1

            cursor.execute("""
                INSERT INTO content_versions (content_id, version_number, text, corrected_text, risk_score, risk_level)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                target_id, version_num, text, result["corrected_text"],
                result["score"], result["risk_level"]
            ))

            # Save analysis results
            cursor.execute("""
                INSERT INTO analysis_results 
                (content_id, version_number, score, risk_level, sub_scores, engine_used, issues, summary, corrected_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                target_id,
                version_num,
                result["score"],
                result["risk_level"],
                json.dumps(result["sub_scores"]),
                result["engine_used"],
                json.dumps(result["issues"]),
                result["summary"],
                result["corrected_text"]
            ))
            analysis_id = cursor.lastrowid

        result["analysis_id"] = analysis_id
        return jsonify({
            "success": True,
            "content_id": target_id,
            "version": version_num,
            "status": "pending_review",
            "result": result
        })
    except Exception as error:
        logger.error(
            "Error in audit_content (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({"success": False, "error": "Could not complete the content audit."}), 500

@analysis_bp.route("/run/<content_id>", methods=["POST"])
@gemini_analysis_limit
def run_analysis_by_id(content_id):
    """Fallback route for existing content item by ID."""
    try:
        cid = int(content_id)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "A valid content ID is required."}), 400
    if not is_positive_int(cid):
        return jsonify({"success": False, "error": "A valid content ID is required."}), 400
    requested_brand_id = validate_query_id("brand_id")

    try:
        with db_session() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM content_items WHERE id = ?"
            params = [cid]
            if requested_brand_id is not None:
                query += " AND brand_id = ?"
                params.append(requested_brand_id)
            cursor.execute(query, params)
            content_row = cursor.fetchone()
            if not content_row:
                return jsonify({"success": False, "error": "Content item not found"}), 404
            content = dict_from_row(content_row)
            if len(content.get("original_text") or "") > MAX_GEMINI_TEXT_LENGTH:
                return jsonify({
                    "success": False,
                    "error": "Saved content exceeds the analysis size limit.",
                }), 413

        result = analyzer.analyze_content(
            content["brand_id"], 
            content["original_text"], 
            content["content_type"], 
            content["channel"]
        )

        version_num = content["current_version"] + 1
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE content_items
                SET current_version = ?,
                    status = 'pending_review',
                    risk_score = ?,
                    risk_level = ?,
                    corrected_text = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND current_version = ?
            """, (
                version_num,
                result["score"],
                result["risk_level"],
                result["corrected_text"],
                cid,
                content["current_version"],
            ))
            if cursor.rowcount != 1:
                return jsonify({
                    "success": False,
                    "error": "Content changed while the analysis was running. Run the audit again.",
                }), 409

            cursor.execute("""
                INSERT INTO content_versions (content_id, version_number, text, corrected_text, risk_score, risk_level)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                cid, version_num, content["original_text"], result["corrected_text"],
                result["score"], result["risk_level"]
            ))

            cursor.execute("""
                INSERT INTO analysis_results 
                (content_id, version_number, score, risk_level, sub_scores, engine_used, issues, summary, corrected_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                cid,
                version_num,
                result["score"],
                result["risk_level"],
                json.dumps(result["sub_scores"]),
                result["engine_used"],
                json.dumps(result["issues"]),
                result["summary"],
                result["corrected_text"]
            ))
            analysis_id = cursor.lastrowid

        result["analysis_id"] = analysis_id
        return jsonify({
            "success": True, "content_id": cid, "version": version_num,
            "status": "pending_review", "result": result
        })
    except Exception as error:
        logger.error(
            "Error in run_analysis_by_id (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({"success": False, "error": "Could not complete the content analysis."}), 500


def _manual_correction_preview(source_text, drafts):
    ordered = sorted(drafts, key=lambda draft: (draft["start_offset"], draft["end_offset"]))
    previous_end = -1
    for draft in ordered:
        start = draft["start_offset"]
        end = draft["end_offset"]
        if (
            start < 0
            or end <= start
            or end > len(source_text)
            or source_text[start:end] != draft["original_text"]
            or start < previous_end
        ):
            return None
        previous_end = end

    preview_text = source_text
    for draft in reversed(ordered):
        preview_text = (
            preview_text[:draft["start_offset"]]
            + draft["replacement_text"]
            + preview_text[draft["end_offset"]:]
        )
    return {
        "corrected_text": preview_text,
        "corrections": [
            {
                "issue_id": draft["issue_id"],
                "original_text": draft["original_text"],
                "replacement_text": draft["replacement_text"],
                "start": draft["start_offset"],
                "end": draft["end_offset"],
            }
            for draft in ordered
        ],
        "correction_count": len(ordered),
        "manual_review_required": True,
        "compliance_approved": False,
    }


@analysis_bp.route("/manual-corrections", methods=["POST"])
def approve_manual_correction():
    data = json_object_payload()
    validate_string_fields(
        data,
        ("issue_id", "original_text", "replacement_text", "source_text"),
        max_lengths={
            "issue_id": 250,
            "original_text": MAX_GEMINI_TEXT_LENGTH,
            "replacement_text": 1000,
            "source_text": MAX_GEMINI_TEXT_LENGTH,
        },
    )
    validate_required_fields(
        data,
        (
            "content_id",
            "brand_id",
            "current_version_id",
            "issue_id",
            "original_text",
            "replacement_text",
            "source_text",
            "source_start",
            "source_end",
        ),
    )
    content_id = validate_id_field(data, "content_id", required=True)
    brand_id = validate_id_field(data, "brand_id", required=True)
    current_version_id = validate_id_field(
        data, "current_version_id", required=True
    )
    issue_id = data["issue_id"]
    original_text = data["original_text"]
    source_text = data["source_text"]
    start = data["source_start"]
    end = data["source_end"]
    if not isinstance(issue_id, str) or not issue_id.strip():
        return jsonify({"success": False, "error": "A valid issue_id is required."}), 400
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or start < 0
        or end <= start
    ):
        return jsonify({
            "success": False,
            "error": "source_start and source_end must identify a valid source span.",
        }), 400

    try:
        replacement_text = validate_replacement_text(
            data["replacement_text"], original_text
        )
    except ValueError as error:
        return jsonify({"success": False, "error": str(error)}), 400

    try:
        with db_session() as conn:
            cursor = conn.cursor()
            content_query = "SELECT * FROM content_items WHERE id = ?"
            if isinstance(conn, PostgresConnection):
                content_query += " FOR UPDATE"
            else:
                cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(content_query, (content_id,))
            content_row = cursor.fetchone()
            if not content_row:
                return jsonify({"success": False, "error": "Content item not found."}), 404
            content = dict_from_row(content_row)
            if content["brand_id"] != brand_id:
                return jsonify({
                    "success": False,
                    "error": "Content not found for the requested brand.",
                }), 404
            if source_text != content["original_text"]:
                return jsonify({
                    "success": False,
                    "error": "The source text changed. Run the audit again before approving this correction.",
                    "stale": True,
                }), 409

            cursor.execute("""
                SELECT id, version_number, text
                FROM content_versions
                WHERE id = ? AND content_id = ?
            """, (current_version_id, content_id))
            version_row = cursor.fetchone()
            if (
                not version_row
                or version_row["version_number"] != content["current_version"]
                or version_row["text"] != content["original_text"]
            ):
                return jsonify({
                    "success": False,
                    "error": "The selected content version is stale. Run the audit again.",
                    "stale": True,
                    "current_version": content["current_version"],
                }), 409

            cursor.execute("""
                SELECT issues
                FROM analysis_results
                WHERE content_id = ? AND version_number = ?
                ORDER BY id DESC
                LIMIT 1
            """, (content_id, content["current_version"]))
            analysis_row = cursor.fetchone()
            if not analysis_row:
                return jsonify({
                    "success": False,
                    "error": "No current audit exists for this content version.",
                    "stale": True,
                }), 409
            try:
                issues = (
                    json.loads(analysis_row["issues"])
                    if isinstance(analysis_row["issues"], str)
                    else analysis_row["issues"]
                )
            except (TypeError, ValueError):
                issues = None
            if not isinstance(issues, list):
                return jsonify({
                    "success": False,
                    "error": "The saved audit issues are invalid. Run the audit again.",
                }), 409
            issue = next(
                (
                    saved_issue for saved_issue in issues
                    if isinstance(saved_issue, dict)
                    and saved_issue.get("id") == issue_id
                ),
                None,
            )
            if issue is None:
                return jsonify({
                    "success": False,
                    "error": "This issue is no longer present in the current audit.",
                    "stale": True,
                }), 409
            if not (
                issue.get("manual_review_required") is True
                or issue.get("auto_fixable") is False
            ):
                return jsonify({
                    "success": False,
                    "error": "This issue has a validated automatic correction. Use Apply instead.",
                }), 409

            location = issue.get("location")
            if (
                not isinstance(location, dict)
                or location.get("start") != start
                or location.get("end") != end
                or issue.get("matched_text") != original_text
                or end > len(source_text)
                or source_text[start:end] != original_text
            ):
                return jsonify({
                    "success": False,
                    "error": "The detected source span no longer matches. Run the audit again.",
                    "stale": True,
                }), 409

            cursor.execute("""
                SELECT issue_id, original_text, replacement_text,
                       start_offset, end_offset
                FROM manual_correction_drafts
                WHERE content_id = ? AND content_version_id = ?
            """, (content_id, current_version_id))
            existing_drafts = [
                dict_from_row(row) for row in cursor.fetchall()
            ]
            for draft in existing_drafts:
                if draft["issue_id"] == issue_id:
                    continue
                if start < draft["end_offset"] and draft["start_offset"] < end:
                    return jsonify({
                        "success": False,
                        "error": "This correction overlaps another approved correction. Review the shared text only once.",
                        "overlap": True,
                    }), 409

            preview_drafts = [
                {
                    **draft,
                    "replacement_text": (
                        replacement_text if draft["issue_id"] == issue_id
                        else draft.get("replacement_text")
                    ),
                    "start_offset": (
                        start if draft["issue_id"] == issue_id
                        else draft["start_offset"]
                    ),
                    "end_offset": (
                        end if draft["issue_id"] == issue_id
                        else draft["end_offset"]
                    ),
                }
                for draft in existing_drafts
            ]
            if not any(draft["issue_id"] == issue_id for draft in existing_drafts):
                preview_drafts.append({
                    "issue_id": issue_id,
                    "original_text": original_text,
                    "replacement_text": replacement_text,
                    "start_offset": start,
                    "end_offset": end,
                })
            preview = _manual_correction_preview(
                content["original_text"], preview_drafts
            )
            if preview is None:
                return jsonify({
                    "success": False,
                    "error": "An existing approved correction no longer matches the source. Run the audit again.",
                    "stale": True,
                }), 409
            if len(preview["corrected_text"]) > MAX_GEMINI_TEXT_LENGTH:
                return jsonify({
                    "success": False,
                    "error": "Corrected preview exceeds the content size limit.",
                }), 413

            cursor.execute("""
                INSERT INTO manual_correction_drafts (
                    content_id, content_version_id, issue_id, original_text,
                    replacement_text, start_offset, end_offset, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT (content_id, content_version_id, issue_id)
                DO UPDATE SET
                    original_text = excluded.original_text,
                    replacement_text = excluded.replacement_text,
                    start_offset = excluded.start_offset,
                    end_offset = excluded.end_offset,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                content_id, current_version_id, issue_id, original_text,
                replacement_text, start, end,
            ))

        return jsonify({
            "success": True,
            "content_id": content_id,
            "current_version_id": current_version_id,
            "preview_state": preview,
        })
    except Exception as error:
        logger.error(
            "Manual correction approval error (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({
            "success": False,
            "error": "Could not validate and preview the manual correction.",
        }), 500


@analysis_bp.route("/manual-corrections/<int:content_id>", methods=["GET", "DELETE"])
def manual_corrections_for_content(content_id):
    if not is_positive_int(content_id):
        return jsonify({"success": False, "error": "A valid content ID is required."}), 400
    requested_brand_id = validate_query_id("brand_id", required=True)
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM content_items WHERE id = ? AND brand_id = ?",
                (content_id, requested_brand_id),
            )
            content_row = cursor.fetchone()
            if not content_row:
                return jsonify({
                    "success": False,
                    "error": "Content not found for the requested brand.",
                }), 404
            content = dict_from_row(content_row)

            if request.method == "DELETE":
                current_version_id = validate_query_id(
                    "current_version_id", required=True
                )
                cursor.execute("""
                    DELETE FROM manual_correction_drafts
                    WHERE content_id = ? AND content_version_id = ?
                """, (content_id, current_version_id))
                return jsonify({"success": True})

            cursor.execute("""
                SELECT id
                FROM content_versions
                WHERE content_id = ? AND version_number = ?
            """, (content_id, content["current_version"]))
            version_row = cursor.fetchone()
            if not version_row:
                return jsonify({
                    "success": False,
                    "error": "The current content version could not be verified.",
                }), 409
            current_version_id = version_row["id"]
            cursor.execute("""
                SELECT issue_id, original_text, replacement_text,
                       start_offset, end_offset
                FROM manual_correction_drafts
                WHERE content_id = ? AND content_version_id = ?
                ORDER BY start_offset, end_offset
            """, (content_id, current_version_id))
            drafts = [dict_from_row(row) for row in cursor.fetchall()]
            preview = _manual_correction_preview(
                content["original_text"], drafts
            )
            if preview is None:
                return jsonify({
                    "success": False,
                    "error": "A saved manual correction no longer matches this version.",
                }), 409
            return jsonify({
                "success": True,
                "content_id": content_id,
                "current_version_id": current_version_id,
                "preview_state": preview,
            })
    except Exception as error:
        logger.error(
            "Manual correction preview error (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({
            "success": False,
            "error": "Could not retrieve the manual correction preview.",
        }), 500


@analysis_bp.route("/apply-corrections/<int:content_id>", methods=["POST"])
@gemini_analysis_limit
def apply_corrections(content_id):
    data = json_object_payload()
    apply_all = data.get("apply_all", False)
    issue_ids = data.get("issue_ids")
    content_version = data.get("content_version")
    legacy_text = data.get("corrected_text")
    source_text = data.get("source_text")
    brand_id = validate_id_field(data, "brand_id")
    if source_text is not None and not isinstance(source_text, str):
        return jsonify({
            "success": False,
            "error": "source_text must be a string.",
        }), 400
    if issue_ids is not None:
        issue_ids = validate_list_field(
            data,
            "issue_ids",
            max_items=100,
            item_type=str,
            required=True,
        )
    if not isinstance(apply_all, bool):
        return jsonify({"success": False, "error": "apply_all must be a boolean"}), 400
    if issue_ids is not None and (
        not issue_ids or not all(issue_id for issue_id in issue_ids)
    ):
        return jsonify({"success": False, "error": "issue_ids must be a non-empty list of issue IDs"}), 400
    if content_version is not None and not is_positive_int(content_version):
        return jsonify({"success": False, "error": "content_version must be a positive integer"}), 400
    if issue_ids is not None and content_version is None:
        return jsonify({"success": False, "error": "content_version is required for individual corrections"}), 400
    if apply_all and issue_ids is not None:
        return jsonify({"success": False, "error": "Choose either apply_all or issue_ids"}), 400
    if legacy_text is not None and (apply_all or issue_ids is not None):
        return jsonify({"success": False, "error": "Choose one correction request format"}), 400
    if issue_ids is None and not apply_all and legacy_text is None:
        return jsonify({"success": False, "error": "A correction selection is required"}), 400
    if legacy_text is not None and (
        not isinstance(legacy_text, str)
        or not legacy_text
        or len(legacy_text) > MAX_GEMINI_TEXT_LENGTH
    ):
        return jsonify({"success": False, "error": f"corrected_text must contain 1 to {MAX_GEMINI_TEXT_LENGTH} characters"}), 400

    try:
        if not is_positive_int(content_id):
            return jsonify({"success": False, "error": "A valid content ID is required."}), 404
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM content_items WHERE id = ?", (content_id,))
            content_row = cursor.fetchone()
            if not content_row:
                return jsonify({"success": False, "error": f"Content item {content_id} not found"}), 404
            content = dict_from_row(content_row)
            if brand_id is not None and content["brand_id"] != brand_id:
                return jsonify({
                    "success": False,
                    "error": "Content not found for the requested brand.",
                }), 404
            if len(content.get("original_text") or "") > MAX_GEMINI_TEXT_LENGTH:
                return jsonify({
                    "success": False,
                    "error": "Saved content exceeds the analysis size limit.",
                }), 413
            if content_version is not None and content_version != content["current_version"]:
                return jsonify({
                    "success": False,
                    "error": "The selected correction belongs to an older content version. Run the audit again.",
                    "current_version": content["current_version"],
                }), 409
            if source_text is not None and source_text != (content.get("original_text") or ""):
                return jsonify({
                    "success": False,
                    "error": "The source text changed. Run the audit again before applying corrections.",
                    "current_version": content["current_version"],
                }), 409
            issues = []
            if apply_all or issue_ids is not None:
                cursor.execute("""
                    SELECT issues
                    FROM analysis_results
                    WHERE content_id = ? AND version_number = ?
                    ORDER BY id DESC
                    LIMIT 1
                """, (content_id, content["current_version"]))
                analysis_row = cursor.fetchone()
                if not analysis_row:
                    return jsonify({
                        "success": False,
                        "error": "No audit result exists for the current content version. Run the audit again.",
                    }), 409
                saved_issues = analysis_row["issues"]
                issues = json.loads(saved_issues) if isinstance(saved_issues, str) else saved_issues
                if not isinstance(issues, list) or not all(isinstance(issue, dict) for issue in issues):
                    return jsonify({
                        "success": False,
                        "error": "The saved audit issues are invalid. Run the audit again.",
                    }), 409

        source_text = content["original_text"]
        applied_issue_ids = []
        correction_plan = None

        if apply_all or issue_ids is not None:
            requested_ids = None if apply_all else list(dict.fromkeys(issue_ids))
            if requested_ids is not None:
                available_ids = {issue.get("id") for issue in issues}
                missing_ids = sorted(set(requested_ids) - available_ids)
                if missing_ids:
                    return jsonify({
                        "success": False,
                        "error": "The selected issue is no longer present. Run the audit again.",
                        "stale_issue_ids": missing_ids,
                    }), 409
                option_issue_ids = set()
                for issue in issues:
                    options = issue.get("replacement_options")
                    if not isinstance(options, list):
                        continue
                    option_issue_ids.update(
                        option["issue_id"]
                        for option in options
                        if isinstance(option, dict)
                        and isinstance(option.get("issue_id"), str)
                    )
                individually_fixable_ids = {
                    issue.get("id")
                    for issue in issues
                    if issue.get("auto_fixable") is True
                } | option_issue_ids
                manual_only_ids = sorted(set(requested_ids) - individually_fixable_ids)
                if manual_only_ids:
                    return jsonify({
                        "success": False,
                        "error": "The selected issue has no validated replacement and requires manual review.",
                        "manual_review_issue_ids": manual_only_ids,
                    }), 409
            correction_plan = apply_correction_plan(source_text, issues, requested_ids)
            new_text = correction_plan["text"]
            applied_issue_ids = correction_plan["applied_issue_ids"]
            if requested_ids is not None and set(applied_issue_ids) != set(requested_ids):
                return jsonify({
                    "success": False,
                    "error": "The selected issue has no safe, current replacement and requires manual review.",
                    "manual_review": correction_plan["manual_review"],
                }), 409
            if not applied_issue_ids:
                return jsonify({
                    "success": False,
                    "error": (
                        "No safe, validated automatic corrections are available to apply."
                        if apply_all
                        else "There are no safe corrections to apply."
                    ),
                    "manual_review": correction_plan["manual_review"],
                }), 409
        else:
            # Keep the pre-existing API payload available for older clients and explicit edits.
            new_text = legacy_text
            if new_text == source_text:
                return jsonify({"success": False, "error": "The submitted text has no changes."}), 409

        if len(new_text) > MAX_GEMINI_TEXT_LENGTH:
            return jsonify({
                "success": False,
                "error": f"Corrected text exceeds the {MAX_GEMINI_TEXT_LENGTH}-character limit.",
            }), 413

        new_version = content["current_version"] + 1

        # Run fresh analysis on new text
        result = analyzer.analyze_content(
            content["brand_id"], 
            new_text, 
            content["content_type"], 
            content["channel"]
        )
        unresolved_applied_issue_ids = []
        if correction_plan:
            previous_issues = {
                issue.get("id"): issue for issue in issues
                if issue.get("id") in applied_issue_ids
            }
            for replacement in correction_plan["replacement_locations"]:
                for issue_id in replacement["issue_ids"]:
                    previous_issue = previous_issues.get(issue_id, {})
                    still_present = any(
                        issue.get("category") == previous_issue.get("category")
                        and issue.get("rule") == previous_issue.get("rule")
                        and (issue.get("location") or {}).get("start") == replacement["start"]
                        and (issue.get("location") or {}).get("end") == replacement["end"]
                        for issue in result.get("issues", [])
                    )
                    if still_present:
                        unresolved_applied_issue_ids.append(issue_id)

        remaining_issues = result.get("issues", [])
        remaining_critical_issues = [
            issue for issue in remaining_issues
            if str(issue.get("severity") or "").upper() == "CRITICAL"
        ]
        correction_review = {
            "manual_review_required": True,
            "grammar_validated": False,
            "remaining_issue_count": len(remaining_issues),
            "remaining_critical_issue_count": len(remaining_critical_issues),
            "compliant": not remaining_issues,
            "unresolved_applied_issue_ids": unresolved_applied_issue_ids,
        }

        if apply_all:
            return jsonify({
                "success": True,
                "preview_only": True,
                "applied_text": new_text,
                "applied_issue_ids": applied_issue_ids,
                "correction_review": correction_review,
                "result": result,
                "message": "Validated corrections preview is ready for review.",
            })

        with db_session() as conn:
            cursor = conn.cursor()
            # Update content item
            cursor.execute("""
                UPDATE content_items
                SET original_text = ?,
                    corrected_text = ?,
                    current_version = ?,
                    risk_score = ?,
                    risk_level = ?,
                    status = 'pending_review',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND current_version = ?
            """, (
                new_text,
                result["corrected_text"],
                new_version,
                result["score"],
                result["risk_level"],
                content_id,
                content["current_version"],
            ))
            if cursor.rowcount != 1:
                return jsonify({
                    "success": False,
                    "error": "Content changed while the correction was being prepared. Run the audit again.",
                }), 409

            # Record new version
            cursor.execute("""
                INSERT INTO content_versions (content_id, version_number, text, corrected_text, risk_score, risk_level)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (content_id, new_version, new_text, result["corrected_text"], result["score"], result["risk_level"]))

            # Record analysis result
            cursor.execute("""
                INSERT INTO analysis_results 
                (content_id, version_number, score, risk_level, sub_scores, engine_used, issues, summary, corrected_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                content_id,
                new_version,
                result["score"],
                result["risk_level"],
                json.dumps(result["sub_scores"]),
                result["engine_used"],
                json.dumps(result["issues"]),
                result["summary"],
                result["corrected_text"]
            ))

        return jsonify({
            "success": True, 
            "new_version": new_version, 
            "applied_text": new_text,
            "applied_issue_ids": applied_issue_ids,
            "correction_review": correction_review,
            "result": result, 
            "message": f"Applied corrections and updated to version {new_version}."
        })
    except Exception as error:
        logger.error(
            "Error in apply_corrections (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({"success": False, "error": "Could not apply the requested correction."}), 500
