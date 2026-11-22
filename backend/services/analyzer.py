
import json
import logging
from typing import Dict, Any, List, Optional, Tuple

from database import db_session, dict_from_row
from services.correction_engine import (
    apply_corrections,
    attach_replacement_options,
    safe_replacement_text,
)
from services.rules_engine import RuleBasedAnalyzer
from services.ai_service import AIService

logger = logging.getLogger(__name__)


class GovernanceAnalyzer:
    """
    Main BrandGuard Governance Engine.
    Coordinates database retrieval of active brand guidelines,
    invokes AI or Rule-Based engine, and merges results.
    """

    def __init__(self):
        self.ai_service = AIService()

    def _load_brand_context(
        self,
        brand_id: int,
    ) -> Tuple[
        Dict[str, Any],
        List[Dict[str, Any]],
        List[Dict[str, Any]],
        Dict[str, Any],
        List[Dict[str, Any]],
    ]:
        with db_session() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM brands WHERE id = ?", (brand_id,))
            brand_row = cursor.fetchone()
            if not brand_row:
                raise ValueError(f"Brand ID {brand_id} not found.")
            brand = dict_from_row(brand_row)

            cursor.execute(
                "SELECT * FROM brand_facts WHERE brand_id = ? AND active = 1",
                (brand_id,),
            )
            facts = [dict_from_row(row) for row in cursor.fetchall()]

            cursor.execute(
                "SELECT * FROM brand_terminology WHERE brand_id = ? AND active = 1",
                (brand_id,),
            )
            terminology = [dict_from_row(row) for row in cursor.fetchall()]

            cursor.execute(
                "SELECT * FROM brand_visual_rules WHERE brand_id = ?",
                (brand_id,),
            )
            visual_row = cursor.fetchone()
            visual_rules = dict_from_row(visual_row) if visual_row else {}

            for key in (
                "primary_colors",
                "secondary_colors",
                "forbidden_colors",
            ):
                if isinstance(visual_rules.get(key), str):
                    try:
                        visual_rules[key] = json.loads(visual_rules[key])
                    except json.JSONDecodeError:
                        logger.error(
                            "Stored visual guideline %s for brand %s is invalid JSON",
                            key,
                            brand_id,
                        )
                        visual_rules[key] = []

            cursor.execute(
                "SELECT * FROM brand_claim_rules WHERE brand_id = ? AND active = 1",
                (brand_id,),
            )
            claim_rules = [dict_from_row(row) for row in cursor.fetchall()]

        return brand, facts, terminology, visual_rules, claim_rules

    def rewrite_content(
        self,
        brand_id: int,
        text: str,
        content_type: str = "Ad Copy",
        channel: str = "Digital Ads",
    ) -> str:
        brand, facts, terminology, visual_rules, claim_rules = (
            self._load_brand_context(brand_id)
        )
        return self.ai_service.rewrite_with_ai(
            text,
            brand,
            facts,
            terminology,
            claim_rules,
            visual_rules,
            content_type,
            channel,
        )

    def analyze_visual_content(
        self,
        brand_id: int,
        image_bytes: bytes,
        mime_type: str,
    ) -> Dict[str, Any]:
        brand, _facts, _terminology, visual_rules, _claim_rules = (
            self._load_brand_context(brand_id)
        )
        return self.ai_service.analyze_visual_content(
            image_bytes,
            mime_type,
            brand,
            visual_rules,
        )

    def analyze_content(
        self,
        brand_id: int,
        text: str,
        content_type: str = "Ad Copy",
        channel: str = "Digital Ads",
    ) -> Dict[str, Any]:
        brand, facts, terminology, visual_rules, claim_rules = (
            self._load_brand_context(brand_id)
        )

        # Always run deterministic rules as the foundational ground truth.
        rule_engine = RuleBasedAnalyzer(
            brand,
            facts,
            terminology,
            visual_rules,
            claim_rules,
        )

        rule_result = rule_engine.analyze(text, content_type, channel)

        # Check whether AI is configured and attempt AI analysis.
        ai_result = None

        if self.ai_service.is_configured():
            ai_result = self.ai_service.analyze_with_ai(
                text,
                brand,
                facts,
                terminology,
                claim_rules,
                visual_rules,
                content_type,
                channel,
            )
        else:
            logger.info(
                "Gemini analysis fallback final_fallback_reason=not_configured"
            )

        # Merge AI findings with deterministic rule-based findings.
        if ai_result:
            result = self._merge_results(rule_result, ai_result, text)
        else:
            result = rule_result

        # Build replacement options and preserve conflicting suggestions
        # for manual user selection.
        attach_replacement_options(text, result.get("issues", []))

        # Apply only validated, safe replacements.
        # Conflicting suggestions and manual-review issues are excluded.
        preview_issues = [
            issue
            for issue in result.get("issues", [])
            if issue.get("auto_fixable") is True
            and issue.get("replacement_choice_required") is not True
        ]

        corrected_text = apply_corrections(text, preview_issues)["text"]

        # Recheck the corrected content using deterministic rules.
        recheck = rule_engine.analyze(
            corrected_text,
            content_type,
            channel,
        )

        remaining_issues = list(recheck.get("issues", []))
        corrected_folded = corrected_text.casefold()

        # Keep AI issues that still appear in the corrected content.
        remaining_issues.extend(
            issue
            for issue in result.get("issues", [])
            if issue.get("source") == "AI"
            and isinstance(issue.get("matched_text"), str)
            and issue["matched_text"].casefold() in corrected_folded
        )

        result["corrected_text"] = corrected_text

        result["revision_compliance"] = {
            "approved": not remaining_issues,
            "remaining_issue_count": len(remaining_issues),
            "remaining_issues": remaining_issues,
        }

        result["visual_validation"] = {
            "supported": False,
            "message": (
                "No image was supplied to this text audit, so visual guidelines "
                "were not evaluated. Upload an image for a separate visual review."
            ),
        }

        return result

    def _merge_results(
        self,
        rule_res: Dict[str, Any],
        ai_res: Dict[str, Any],
        source_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Merges AI insights with strict deterministic rule enforcement.
        Ensures configured rules remain the foundational source of truth.
        """

        combined_issues = list(rule_res.get("issues", []))

        def category_key(issue):
            category = str(issue.get("category") or "").casefold()

            if "claim" in category:
                return "claims"

            if "terminology" in category:
                return "terminology"

            return category

        existing_matches = {
            (
                category_key(issue),
                (
                    issue.get("trigger_text")
                    or issue.get("matched_text", "")
                ).casefold(),
                (
                    issue.get("trigger_location")
                    or issue.get("location")
                    or {}
                ).get("start"),
                (
                    issue.get("trigger_location")
                    or issue.get("location")
                    or {}
                ).get("end"),
            )
            for issue in combined_issues
        }

        def overlaps_existing_claim(ai_issue):
            if category_key(ai_issue) != "claims":
                return False
            ai_location = ai_issue.get("location") or {}
            ai_start, ai_end = ai_location.get("start"), ai_location.get("end")
            if (
                not isinstance(ai_start, int)
                or isinstance(ai_start, bool)
                or not isinstance(ai_end, int)
                or isinstance(ai_end, bool)
                or ai_start >= ai_end
            ):
                return False
            ai_rule = str(ai_issue.get("rule") or "").strip().casefold()

            for existing_issue in combined_issues:
                if (
                    category_key(existing_issue) != "claims"
                    or str(existing_issue.get("rule") or "").strip().casefold()
                    != ai_rule
                ):
                    continue
                location = (
                    existing_issue.get("trigger_location")
                    or existing_issue.get("location")
                    or {}
                )
                start, end = location.get("start"), location.get("end")
                if (
                    isinstance(start, int)
                    and not isinstance(start, bool)
                    and isinstance(end, int)
                    and not isinstance(end, bool)
                    and start < end
                    and start < ai_end
                    and ai_start < end
                ):
                    return True
            return False

        # Add AI issues that were not already detected by deterministic rules.
        for ai_issue in ai_res.get("issues", []):
            matched = ai_issue.get("matched_text", "")
            location = ai_issue.get("location") or {}

            identity = (
                category_key(ai_issue),
                matched.casefold(),
                location.get("start"),
                location.get("end"),
            )

            has_current_span = source_text is None or (
                isinstance(location, dict)
                and isinstance(location.get("start"), int)
                and not isinstance(location.get("start"), bool)
                and isinstance(location.get("end"), int)
                and not isinstance(location.get("end"), bool)
                and 0 <= location["start"] < location["end"] <= len(source_text)
                and source_text[
                    location["start"]:location["end"]
                ] == matched
            )

            if (
                matched
                and identity not in existing_matches
                and not overlaps_existing_claim(ai_issue)
            ):
                ai_issue["source"] = "AI"

                replacement_text = (
                    safe_replacement_text(
                        ai_issue.get("replacement_text"),
                        matched,
                    )
                    if has_current_span
                    and ai_issue.get("auto_fixable") is True
                    else None
                )

                # For claim issues, use a configured deterministic replacement
                # only when there is exactly one valid matching replacement.
                if (
                    replacement_text is None
                    and has_current_span
                    and source_text is not None
                    and category_key(ai_issue) == "claims"
                ):
                    ai_location = ai_issue.get("location") or {}
                    configured_replacements = set()

                    for rule_issue in rule_res.get("issues", []):
                        rule_location = (
                            rule_issue.get("trigger_location")
                            or rule_issue.get("location")
                            or {}
                        )
                        rule_match = (
                            rule_issue.get("trigger_text")
                            or rule_issue.get("matched_text")
                        )

                        if (
                            category_key(rule_issue) == "claims"
                            and rule_issue.get("auto_fixable") is True
                            and rule_location == ai_location
                            and isinstance(rule_location.get("start"), int)
                            and not isinstance(rule_location.get("start"), bool)
                            and isinstance(rule_location.get("end"), int)
                            and not isinstance(rule_location.get("end"), bool)
                            and 0 <= rule_location["start"] < rule_location["end"] <= len(source_text)
                            and source_text[
                                rule_location["start"]:rule_location["end"]
                            ] == rule_match
                        ):
                            configured_replacement = safe_replacement_text(
                                rule_issue.get("replacement_text"),
                                matched,
                            )

                            if configured_replacement is not None:
                                configured_replacements.add(configured_replacement)

                    if len(configured_replacements) == 1:
                        replacement_text = configured_replacements.pop()

                ai_issue["replacement_text"] = replacement_text
                ai_issue["auto_fixable"] = replacement_text is not None
                ai_issue["manual_review_required"] = replacement_text is None

                combined_issues.append(ai_issue)
                existing_matches.add(identity)

        final_score = min(rule_res["score"], ai_res["score"])

        severities = {
            issue.get("severity", "").upper()
            for issue in combined_issues
        }

        if "CRITICAL" in severities:
            final_score = min(final_score, 49)
        elif "WARNING" in severities:
            final_score = min(final_score, 89)

        if final_score >= 90:
            risk_level = "LOW"
            risk_badge = "Low Detected Risk - Human Review Required"
        elif final_score >= 75:
            risk_level = "MODERATE"
            risk_badge = "Moderate Risk - Revisions Recommended"
        elif final_score >= 50:
            risk_level = "HIGH"
            risk_badge = "High Risk - Substantial Issues Detected"
        else:
            risk_level = "CRITICAL"
            risk_badge = "Critical Risk - Immediate Remediation Required"

        category_scores = {
            "Terminology": "terminology",
            "Fact Accuracy": "facts",
            "Voice & Tone": "tone",
            "Compliance & Claims": "claims",
            "Accessibility & Clarity": "accessibility",
        }

        sub_scores = dict(
            ai_res.get("sub_scores")
            or rule_res.get("sub_scores", {})
        )

        for issue in combined_issues:
            score_name = category_scores.get(issue.get("category"))
            severity = issue.get("severity", "").upper()

            if score_name and severity in ("CRITICAL", "WARNING"):
                ceiling = 49 if severity == "CRITICAL" else 74
                sub_scores[score_name] = min(
                    sub_scores.get(score_name, 100),
                    ceiling,
                )

        critical_count = sum(
            issue.get("severity", "").upper() == "CRITICAL"
            for issue in combined_issues
        )

        warning_count = sum(
            issue.get("severity", "").upper() == "WARNING"
            for issue in combined_issues
        )

        info_count = sum(
            issue.get("severity", "").upper() == "INFO"
            for issue in combined_issues
        )

        if critical_count:
            assessment = "Critical policy violations were detected."
        elif warning_count:
            assessment = "Policy warnings were detected."
        elif info_count:
            assessment = "Only informational observations were detected."
        else:
            assessment = "No configured policy violations were detected."

        corrected_text = rule_res.get("corrected_text", "")

        return {
            "score": final_score,
            "risk_level": risk_level,
            "risk_badge": risk_badge,
            "sub_scores": sub_scores,
            "readability": rule_res.get("readability"),
            "issues": combined_issues,
            "summary": (
                f"{assessment} Score: {final_score}/100 ({risk_level} risk); "
                f"{critical_count} critical, {warning_count} warning, "
                f"and {info_count} informational issue(s). "
                "This automated result is not an approval; "
                "human review is required before publication."
            ),
            "corrected_text": corrected_text,
            "engine_used": "AI_GEMINI",
        }
