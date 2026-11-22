import json
import logging
from flask import Blueprint, request, jsonify
from api_utils import (
    is_positive_int,
    json_object_payload,
    validate_query_id,
    validate_required_fields,
    validate_string_fields,
)
from database import db_session, dict_from_row

logger = logging.getLogger(__name__)

reviews_bp = Blueprint("reviews", __name__, url_prefix="/api/reviews")

@reviews_bp.route("", methods=["POST"])
def submit_review():
    data = json_object_payload()
    validate_string_fields(
        data,
        ("reviewer_name", "reviewer_role", "decision", "comments"),
        max_lengths={
            "reviewer_name": 200,
            "reviewer_role": 200,
            "decision": 30,
            "comments": 5000,
        },
    )
    validate_required_fields(data, ("content_id", "reviewer_name", "decision"))
    content_id = data.get("content_id")
    brand_id = data.get("brand_id")
    reviewer_name = data.get("reviewer_name", "")
    reviewer_role = data.get("reviewer_role", "")
    decision = data.get("decision", "")
    comments = data.get("comments", "")
    content_version = data.get("content_version")
    if not is_positive_int(content_id):
        return jsonify({"success": False, "error": "A valid content_id is required"}), 400
    if brand_id is not None and not is_positive_int(brand_id):
        return jsonify({"success": False, "error": "A valid brand_id is required"}), 400
    if content_version is not None and not is_positive_int(content_version):
        return jsonify({"success": False, "error": "content_version must be a positive integer"}), 400
    if not isinstance(reviewer_name, str) or not reviewer_name.strip():
        return jsonify({"success": False, "error": "Reviewer name is required"}), 400
    if not isinstance(reviewer_role, str) or not isinstance(comments, str) or not isinstance(decision, str):
        return jsonify({"success": False, "error": "Review fields must be strings"}), 400
    reviewer_name = reviewer_name.strip()
    reviewer_role = reviewer_role.strip()
    decision = decision.lower().strip()
    comments = comments.strip()

    valid_decisions = {"approved", "rejected", "changes_requested"}
    if decision not in valid_decisions:
        return jsonify({"success": False, "error": f"Invalid decision. Must be one of: {', '.join(valid_decisions)}"}), 400

    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT current_version, brand_id
                FROM content_items
                WHERE id = ?
            """, (content_id,))
            content = cursor.fetchone()
            if not content:
                return jsonify({"success": False, "error": "Content item not found"}), 404
            if brand_id is not None and content["brand_id"] != brand_id:
                return jsonify({"success": False, "error": "Content item not found for the requested brand"}), 404
            current_version = content["current_version"]
            if decision == "approved" and content_version is None:
                return jsonify({
                    "success": False,
                    "error": (
                        f"Approval requires the current content version (v{current_version}). "
                        "Reload the latest version and submit the approval again."
                    ),
                    "current_version": current_version,
                }), 409
            if content_version is not None and content_version != current_version:
                return jsonify({
                    "success": False,
                    "error": "Content changed after this review was opened. Review the latest version.",
                    "current_version": current_version,
                }), 409

            cursor.execute("""
                SELECT issues
                FROM analysis_results
                WHERE content_id = ? AND version_number = ?
                ORDER BY id DESC
                LIMIT 1
            """, (content_id, current_version))
            latest_analysis = cursor.fetchone()
            if not latest_analysis:
                return jsonify({
                    "success": False,
                    "error": "Analyze the current content version before recording a human review decision"
                }), 409

            if decision == "approved":
                try:
                    issues = latest_analysis["issues"]
                    if isinstance(issues, str):
                        issues = json.loads(issues)
                except (TypeError, json.JSONDecodeError):
                    issues = None

                if not isinstance(issues, list) or not all(
                    isinstance(issue, dict) for issue in issues
                ):
                    return jsonify({
                        "success": False,
                        "error": (
                            "Approval blocked: the latest audit for this content "
                            "version could not be validated. Run the audit again before approving."
                        ),
                    }), 409

                critical_count = sum(
                    str(issue.get("severity") or "").strip().upper() == "CRITICAL"
                    for issue in issues
                )
                if critical_count:
                    return jsonify({
                        "success": False,
                        "error": (
                            f"Approval blocked: the latest audit for version {current_version} "
                            f"contains {critical_count} unresolved Critical issue(s). "
                            "Resolve them and run a new audit before approving."
                        ),
                    }), 409

            # Update content status
            cursor.execute("""
                UPDATE content_items
                SET status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND current_version = ?
            """, (decision, content_id, current_version))
            if cursor.rowcount != 1:
                return jsonify({
                    "success": False,
                    "error": "Content changed while the review was being recorded. Review the latest version.",
                }), 409

            # Insert review audit log
            cursor.execute("""
                INSERT INTO reviews (
                    content_id, content_version, reviewer_name, reviewer_role, decision, comments
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                content_id, current_version, reviewer_name, reviewer_role, decision, comments
            ))
            review_id = cursor.lastrowid

        return jsonify({
            "success": True,
            "review_id": review_id,
            "new_status": decision,
            "message": f"Content successfully marked as '{decision.replace('_', ' ').title()}' by {reviewer_name}."
        })
    except Exception as error:
        logger.error(
            "Error in submit_review (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({"success": False, "error": "Could not record the review decision."}), 500

@reviews_bp.route("/content/<int:content_id>", methods=["GET"])
def get_content_reviews(content_id):
    if not is_positive_int(content_id):
        return jsonify({"success": False, "error": "Content item not found"}), 404
    brand_id = validate_query_id("brand_id")
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            if brand_id is not None:
                cursor.execute(
                    "SELECT 1 FROM content_items WHERE id = ? AND brand_id = ?",
                    (content_id, brand_id),
                )
                if not cursor.fetchone():
                    return jsonify({"success": False, "error": "Content item not found for the requested brand"}), 404
            cursor.execute("""
                SELECT * FROM reviews 
                WHERE content_id = ? 
                ORDER BY created_at DESC
            """, (content_id,))
            reviews = [dict_from_row(r) for r in cursor.fetchall()]
        return jsonify({"success": True, "reviews": reviews})
    except Exception as error:
        logger.error(
            "Error in get_content_reviews (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({"success": False, "error": "Could not retrieve content reviews."}), 500

@reviews_bp.route("/recent", methods=["GET"])
def get_recent_reviews():
    raw_limit = request.args.get("limit", "20")
    try:
        limit = int(raw_limit)
    except ValueError:
        return jsonify({"success": False, "error": "limit must be an integer from 1 to 100"}), 400
    if not 1 <= limit <= 100:
        return jsonify({"success": False, "error": "limit must be an integer from 1 to 100"}), 400
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT r.*, c.title as content_title, c.content_type, b.name as brand_name
                FROM reviews r
                JOIN content_items c ON r.content_id = c.id
                JOIN brands b ON c.brand_id = b.id
                ORDER BY r.created_at DESC
                LIMIT ?
            """, (limit,))
            reviews = [dict_from_row(r) for r in cursor.fetchall()]
        return jsonify({"success": True, "reviews": reviews})
    except Exception as error:
        logger.error(
            "Error in get_recent_reviews (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({"success": False, "error": "Could not retrieve recent reviews."}), 500
