import json
import logging
import re
from flask import Blueprint, jsonify
from database import db_session, dict_from_row
from api_utils import (
    is_positive_int,
    json_object_payload,
    validate_list_field,
    validate_query_id,
    validate_required_fields,
    validate_string_fields,
)
from services.correction_engine import validate_replacement_text
from services.rules_engine import validate_claim_pattern

logger = logging.getLogger(__name__)

brands_bp = Blueprint("brands", __name__, url_prefix="/api/brands")

@brands_bp.route("", methods=["GET"])
def get_brands():
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT b.*, 
                    (SELECT COUNT(*) FROM brand_facts WHERE brand_id = b.id) as facts_count,
                    (SELECT COUNT(*) FROM brand_terminology WHERE brand_id = b.id) as terminology_count,
                    (SELECT COUNT(*) FROM brand_claim_rules WHERE brand_id = b.id) as claim_count,
                    (SELECT COUNT(*) FROM content_items WHERE brand_id = b.id) as content_count
                FROM brands b ORDER BY b.id ASC
            """)
            brands = [dict_from_row(r) for r in cursor.fetchall()]

        for b in brands:
            if b.get("voice_traits") and isinstance(b["voice_traits"], str):
                try:
                    b["voice_traits"] = json.loads(b["voice_traits"])
                except:
                    b["voice_traits"] = []

        return jsonify({"success": True, "brands": brands})
    except Exception as error:
        logger.error(
            "Error in get_brands (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({"success": False, "error": "Could not retrieve brands."}), 500

@brands_bp.route("/<int:brand_id>", methods=["DELETE"])
def delete_brand(brand_id):
    if not is_positive_int(brand_id):
        return jsonify({"success": False, "error": "Brand not found"}), 404
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM brands WHERE id = ?", (brand_id,))
            if cursor.rowcount == 0:
                return jsonify({"success": False, "error": "Brand not found"}), 404
        return jsonify({"success": True, "message": "Brand and related records deleted"})
    except Exception as error:
        logger.error(
            "Error deleting brand (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({"success": False, "error": "Could not delete the brand."}), 500

@brands_bp.route("/<int:brand_id>", methods=["GET"])
def get_brand_details(brand_id):
    if not is_positive_int(brand_id):
        return jsonify({"success": False, "error": "Brand not found"}), 404
    try:
        with db_session() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM brands WHERE id = ?", (brand_id,))
            brand_row = cursor.fetchone()
            if not brand_row:
                return jsonify({"success": False, "error": "Brand not found"}), 404
            brand = dict_from_row(brand_row)

            if brand.get("voice_traits") and isinstance(brand["voice_traits"], str):
                try:
                    brand["voice_traits"] = json.loads(brand["voice_traits"])
                except:
                    brand["voice_traits"] = []

            # Facts
            cursor.execute("SELECT * FROM brand_facts WHERE brand_id = ? ORDER BY id DESC", (brand_id,))
            facts = [dict_from_row(r) for r in cursor.fetchall()]

            # Terminology
            cursor.execute("SELECT * FROM brand_terminology WHERE brand_id = ? ORDER BY severity DESC, id DESC", (brand_id,))
            terminology = [dict_from_row(r) for r in cursor.fetchall()]

            # Visual Rules
            cursor.execute("SELECT * FROM brand_visual_rules WHERE brand_id = ?", (brand_id,))
            visual_row = cursor.fetchone()
            visual = dict_from_row(visual_row) if visual_row else {}
            if visual:
                for k in ["primary_colors", "secondary_colors", "forbidden_colors"]:
                    if visual.get(k) and isinstance(visual[k], str):
                        try:
                            visual[k] = json.loads(visual[k])
                        except:
                            visual[k] = []

            # Claim rules
            cursor.execute("SELECT * FROM brand_claim_rules WHERE brand_id = ? ORDER BY id DESC", (brand_id,))
            claims = [dict_from_row(r) for r in cursor.fetchall()]

        return jsonify({
            "success": True,
            "brand": brand,
            "facts": facts,
            "terminology": terminology,
            "visual_rules": visual,
            "claim_rules": claims
        })
    except Exception as error:
        logger.error(
            "Error in get_brand_details (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({"success": False, "error": "Could not retrieve brand details."}), 500

@brands_bp.route("", methods=["POST"])
def create_brand():
    data = json_object_payload()
    validate_string_fields(
        data,
        ("name", "tagline", "description", "industry", "primary_tone", "target_audience"),
        max_lengths={
            "name": 200,
            "tagline": 500,
            "description": 5000,
            "industry": 200,
            "primary_tone": 200,
            "target_audience": 1000,
        },
    )
    validate_required_fields(data, ("name",))
    name = data.get("name", "")
    if not isinstance(name, str) or not name.strip():
        return jsonify({"success": False, "error": "Brand name is required"}), 400

    tagline = data.get("tagline") or ""
    description = data.get("description") or ""
    industry = (data.get("industry") or "").strip()
    primary_tone = (data.get("primary_tone") or "").strip()
    voice_traits = data.get("voice_traits", [])
    if not isinstance(voice_traits, list):
        return jsonify({"success": False, "error": "Voice traits must be a list of strings"}), 400
    voice_traits = validate_list_field(data, "voice_traits", max_items=20, item_type=str)
    if any(len(item) > 100 for item in voice_traits):
        return jsonify({"success": False, "error": "Each voice trait must be 100 characters or fewer"}), 400
    voice_traits = json.dumps(voice_traits)
    target_audience = data.get("target_audience") or ""

    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO brands (name, tagline, description, industry, primary_tone, voice_traits, target_audience)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (name, tagline, description, industry, primary_tone, voice_traits, target_audience))
            brand_id = cursor.lastrowid

            cursor.execute("""
                INSERT INTO brand_visual_rules (brand_id, primary_colors, secondary_colors, forbidden_colors, logo_rules, font_family, contrast_minimum)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (brand_id, "[]", "[]", "[]", "", "", ""))

        return jsonify({"success": True, "brand_id": brand_id, "message": "Brand created successfully"})
    except Exception as error:
        logger.error(
            "Error creating brand (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({"success": False, "error": "Could not create the brand."}), 500

@brands_bp.route("/<int:brand_id>", methods=["PUT"])
def update_brand(brand_id):
    if not is_positive_int(brand_id):
        return jsonify({"success": False, "error": "Brand not found"}), 404
    data = json_object_payload()
    validate_string_fields(
        data,
        ("name", "tagline", "description", "industry", "primary_tone", "target_audience"),
        max_lengths={
            "name": 200,
            "tagline": 500,
            "description": 5000,
            "industry": 200,
            "primary_tone": 200,
            "target_audience": 1000,
        },
    )
    if "name" in data and data["name"] is not None and not data["name"].strip():
        return jsonify({"success": False, "error": "Brand name cannot be empty"}), 400
    voice_traits = data.get("voice_traits")
    if isinstance(voice_traits, list):
        voice_traits = validate_list_field(data, "voice_traits", max_items=20, item_type=str)
        if any(len(item) > 100 for item in voice_traits):
            return jsonify({"success": False, "error": "Each voice trait must be 100 characters or fewer"}), 400
        voice_traits = json.dumps(voice_traits)
    elif voice_traits is not None:
        return jsonify({"success": False, "error": "Voice traits must be a list of strings"}), 400

    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE brands
                SET name = COALESCE(?, name),
                    tagline = COALESCE(?, tagline),
                    description = COALESCE(?, description),
                    industry = COALESCE(?, industry),
                    primary_tone = COALESCE(?, primary_tone),
                    voice_traits = COALESCE(?, voice_traits),
                    target_audience = COALESCE(?, target_audience),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                data.get("name"), data.get("tagline"), data.get("description"),
                data.get("industry"), data.get("primary_tone"), voice_traits,
                data.get("target_audience"), brand_id
            ))
            if cursor.rowcount == 0:
                return jsonify({"success": False, "error": "Brand not found"}), 404
        return jsonify({"success": True, "message": "Brand updated successfully"})
    except Exception as error:
        logger.error(
            "Error updating brand (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({"success": False, "error": "Could not update the brand."}), 500

# Facts CRUD
@brands_bp.route("/<int:brand_id>/facts", methods=["POST"])
def add_fact(brand_id):
    if not is_positive_int(brand_id):
        return jsonify({"success": False, "error": "Brand not found"}), 404
    data = json_object_payload()
    validate_string_fields(
        data,
        ("statement", "category", "key_metric", "keywords"),
        max_lengths={
            "statement": 5000,
            "category": 100,
            "key_metric": 1000,
            "keywords": 1000,
        },
    )
    validate_required_fields(data, ("statement",))
    statement = (data.get("statement") or "").strip()
    if not statement:
        return jsonify({"success": False, "error": "Fact statement is required"}), 400

    category = data.get("category") or "General"
    key_metric = data.get("key_metric") or ""
    keywords = data.get("keywords") or ""

    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM brands WHERE id = ?", (brand_id,))
            if not cursor.fetchone():
                return jsonify({"success": False, "error": "Brand not found"}), 404
            cursor.execute("""
                INSERT INTO brand_facts (brand_id, category, statement, key_metric, keywords)
                VALUES (?, ?, ?, ?, ?)
            """, (brand_id, category, statement, key_metric, keywords))
            fact_id = cursor.lastrowid
        return jsonify({"success": True, "fact_id": fact_id, "message": "Fact added successfully"})
    except Exception as error:
        logger.error(
            "Error adding fact (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({"success": False, "error": "Could not add the brand fact."}), 500

@brands_bp.route("/facts/<int:fact_id>", methods=["DELETE"])
def delete_fact(fact_id):
    if not is_positive_int(fact_id):
        return jsonify({"success": False, "error": "Fact not found"}), 404
    brand_id = validate_query_id("brand_id", required=True)
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM brand_facts WHERE id = ? AND brand_id = ?",
                (fact_id, brand_id),
            )
            if cursor.rowcount == 0:
                return jsonify({"success": False, "error": "Fact not found"}), 404
        return jsonify({"success": True, "message": "Fact deleted"})
    except Exception as error:
        logger.error(
            "Error deleting fact (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({"success": False, "error": "Could not delete the brand fact."}), 500

# Terminology CRUD
@brands_bp.route("/<int:brand_id>/terminology", methods=["POST"])
def add_terminology(brand_id):
    if not is_positive_int(brand_id):
        return jsonify({"success": False, "error": "Brand not found"}), 404
    data = json_object_payload()
    validate_string_fields(
        data,
        ("term", "replacement", "replacement_text", "reason", "category", "severity"),
        max_lengths={
            "term": 500,
            "replacement": 1000,
            "replacement_text": 1000,
            "reason": 2000,
            "category": 100,
            "severity": 20,
        },
    )
    term = data.get("term", "")
    if not isinstance(term, str):
        return jsonify({"success": False, "error": "Term must be a string"}), 400
    term = term.strip()
    legacy_replacement = data.get("replacement", "")
    replacement_text = data.get("replacement_text")
    if replacement_text is None:
        if not isinstance(legacy_replacement, str):
            return jsonify({"success": False, "error": "Replacement must be a string"}), 400
        legacy_replacement = legacy_replacement.strip()
        replacement_text = None if "/" in legacy_replacement else legacy_replacement
    elif not isinstance(replacement_text, str):
        return jsonify({"success": False, "error": "Replacement text must be a string"}), 400
    else:
        replacement_text = replacement_text.strip()
        legacy_replacement = (
            legacy_replacement.strip() if isinstance(legacy_replacement, str) else ""
        ) or replacement_text

    if not term or not legacy_replacement:
        return jsonify({"success": False, "error": "Both term and replacement are required"}), 400
    if replacement_text is not None:
        try:
            replacement_text = validate_replacement_text(replacement_text, term)
        except ValueError as error:
            return jsonify({"success": False, "error": str(error)}), 400

    reason = data.get("reason") or ""
    category = data.get("category") or "Terminology"
    severity = (data.get("severity") or "warning").lower()
    if severity not in {"critical", "warning", "info"}:
        return jsonify({"success": False, "error": "Severity must be critical, warning, or info"}), 400

    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM brands WHERE id = ?", (brand_id,))
            if not cursor.fetchone():
                return jsonify({"success": False, "error": "Brand not found"}), 404
            cursor.execute("""
                INSERT INTO brand_terminology (
                    brand_id, term, replacement, replacement_text, reason, category, severity
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (brand_id, term, legacy_replacement, replacement_text, reason, category, severity))
            term_id = cursor.lastrowid
        return jsonify({"success": True, "term_id": term_id, "message": "Terminology rule added"})
    except Exception as error:
        logger.error(
            "Error adding terminology (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({"success": False, "error": "Could not add the terminology rule."}), 500

@brands_bp.route("/terminology/<int:term_id>", methods=["DELETE"])
def delete_terminology(term_id):
    if not is_positive_int(term_id):
        return jsonify({"success": False, "error": "Terminology rule not found"}), 404
    brand_id = validate_query_id("brand_id", required=True)
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM brand_terminology WHERE id = ? AND brand_id = ?",
                (term_id, brand_id),
            )
            if cursor.rowcount == 0:
                return jsonify({"success": False, "error": "Terminology rule not found"}), 404
        return jsonify({"success": True, "message": "Terminology rule deleted"})
    except Exception as error:
        logger.error(
            "Error deleting terminology (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({"success": False, "error": "Could not delete the terminology rule."}), 500

# Visual Rules Update
@brands_bp.route("/<int:brand_id>/visual-rules", methods=["PUT"])
def update_visual_rules(brand_id):
    if not is_positive_int(brand_id):
        return jsonify({"success": False, "error": "Brand not found"}), 404
    data = json_object_payload()
    colors = [
        data.get("primary_colors", []),
        data.get("secondary_colors", []),
        data.get("forbidden_colors", [])
    ]
    if any(not isinstance(color_list, list) for color_list in colors):
        return jsonify({"success": False, "error": "Color guidelines must be lists"}), 400
    for field_name in ("primary_colors", "secondary_colors", "forbidden_colors"):
        validate_list_field(data, field_name, max_items=50, item_type=dict)
    for color_list in colors:
        for color in color_list:
            if (
                not isinstance(color, dict)
                or not isinstance(color.get("name"), str)
                or not color["name"].strip()
                or not isinstance(color.get("hex"), str)
                or not re.fullmatch(r"#[0-9a-f]{3}(?:[0-9a-f]{3})?", color["hex"], re.IGNORECASE)
                or ("reason" in color and not isinstance(color["reason"], str))
                or len(color.get("name", "")) > 100
                or len(color.get("reason", "")) > 1000
            ):
                return jsonify({
                    "success": False,
                    "error": "Each color must have a name, valid hex value, and optional string reason",
                }), 400
    validate_string_fields(
        data,
        ("logo_rules", "font_family", "contrast_minimum"),
        max_lengths={"logo_rules": 2000, "font_family": 200, "contrast_minimum": 100},
    )
    primary_colors, secondary_colors, forbidden_colors = map(json.dumps, colors)
    logo_rules = data.get("logo_rules") or ""
    font_family = data.get("font_family") or ""
    contrast_minimum = data.get("contrast_minimum") or ""

    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM brands WHERE id = ?", (brand_id,))
            if not cursor.fetchone():
                return jsonify({"success": False, "error": "Brand not found"}), 404
            cursor.execute("""
                INSERT INTO brand_visual_rules (brand_id, primary_colors, secondary_colors, forbidden_colors, logo_rules, font_family, contrast_minimum)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(brand_id) DO UPDATE SET
                    primary_colors = excluded.primary_colors,
                    secondary_colors = excluded.secondary_colors,
                    forbidden_colors = excluded.forbidden_colors,
                    logo_rules = excluded.logo_rules,
                    font_family = excluded.font_family,
                    contrast_minimum = excluded.contrast_minimum,
                    updated_at = CURRENT_TIMESTAMP
            """, (brand_id, primary_colors, secondary_colors, forbidden_colors, logo_rules, font_family, contrast_minimum))
        return jsonify({"success": True, "message": "Visual guidelines updated successfully"})
    except Exception as error:
        logger.error(
            "Error updating visual rules (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({"success": False, "error": "Could not update visual guidelines."}), 500

# Claim Rules CRUD
@brands_bp.route("/<int:brand_id>/claims", methods=["POST"])
def add_claim_rule(brand_id):
    if not is_positive_int(brand_id):
        return jsonify({"success": False, "error": "Brand not found"}), 404
    data = json_object_payload()
    validate_string_fields(
        data,
        (
            "rule_name", "pattern_or_trigger", "severity", "required_disclaimer",
            "replacement_text", "replacement", "explanation",
        ),
        max_lengths={
            "rule_name": 200,
            "pattern_or_trigger": 512,
            "severity": 20,
            "required_disclaimer": 2000,
            "replacement_text": 1000,
            "replacement": 1000,
            "explanation": 2000,
        },
    )
    rule_name = data.get("rule_name", "")
    pattern = data.get("pattern_or_trigger", "")
    if not isinstance(rule_name, str) or not isinstance(pattern, str):
        return jsonify({"success": False, "error": "Rule name and pattern must be strings"}), 400
    rule_name = rule_name.strip()
    pattern = pattern.strip()
    if not rule_name or not pattern:
        return jsonify({"success": False, "error": "Rule name and pattern are required"}), 400
    try:
        validate_claim_pattern(pattern)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400

    severity = (data.get("severity") or "critical").lower()
    if severity not in {"critical", "warning", "info"}:
        return jsonify({"success": False, "error": "Severity must be critical, warning, or info"}), 400
    disclaimer = data.get("required_disclaimer") or ""
    replacement_text = data.get("replacement_text", data.get("replacement", "")) or ""
    explanation = data.get("explanation") or ""
    if not all(isinstance(value, str) for value in (disclaimer, replacement_text, explanation)):
        return jsonify({"success": False, "error": "Claim guidance and replacement fields must be strings"}), 400
    if replacement_text.strip():
        try:
            replacement_text = validate_replacement_text(replacement_text)
        except ValueError as error:
            return jsonify({"success": False, "error": str(error)}), 400
    else:
        replacement_text = None

    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM brands WHERE id = ?", (brand_id,))
            if not cursor.fetchone():
                return jsonify({"success": False, "error": "Brand not found"}), 404
            cursor.execute("""
                INSERT INTO brand_claim_rules (
                    brand_id, rule_name, pattern_or_trigger, severity,
                    required_disclaimer, replacement_text, explanation
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (brand_id, rule_name, pattern, severity, disclaimer, replacement_text, explanation))
            rule_id = cursor.lastrowid
        return jsonify({"success": True, "rule_id": rule_id, "message": "Claim rule added"})
    except Exception as error:
        logger.error(
            "Error adding claim rule (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({"success": False, "error": "Could not add the claim rule."}), 500

@brands_bp.route("/claims/<int:claim_id>", methods=["DELETE"])
def delete_claim_rule(claim_id):
    if not is_positive_int(claim_id):
        return jsonify({"success": False, "error": "Claim rule not found"}), 404
    brand_id = validate_query_id("brand_id", required=True)
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM brand_claim_rules WHERE id = ? AND brand_id = ?",
                (claim_id, brand_id),
            )
            if cursor.rowcount == 0:
                return jsonify({"success": False, "error": "Claim rule not found"}), 404
        return jsonify({"success": True, "message": "Claim rule deleted"})
    except Exception as error:
        logger.error(
            "Error deleting claim rule (exception_category=%s)",
            type(error).__name__,
        )
        return jsonify({"success": False, "error": "Could not delete the claim rule."}), 500
