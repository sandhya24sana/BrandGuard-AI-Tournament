import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_test_database_dir = tempfile.TemporaryDirectory(prefix="brandguard-test-bootstrap-")
os.environ["DATABASE_URL"] = " "
os.environ["DATABASE_PATH"] = str(Path(_test_database_dir.name) / "bootstrap.db")

from app import create_app
from database import get_db_connection
from routes.analysis import analyzer
from services.ai_service import AIService
from services.analyzer import GovernanceAnalyzer
from services.rules_engine import RuleBasedAnalyzer


class BrandGuardWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "DATABASE_URL": "",
            "DATABASE_PATH": str(Path(self.temp_dir.name) / "workflow.db"),
            "RATELIMIT_ENABLED": False,
        })
        self.client = self.app.test_client()
        analyzer.ai_service.gemini_key = ""

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_reviewable_content(self, current_version=1):
        brand_response = self.client.post(
            "/api/brands", json={"name": "Approval Gate Test Brand"}
        )
        self.assertEqual(brand_response.status_code, 200, brand_response.get_json())
        brand_id = brand_response.get_json()["brand_id"]

        content_response = self.client.post("/api/content", json={
            "brand_id": brand_id,
            "title": "Approval gate test content",
            "text": "Test content for review.",
        })
        self.assertEqual(content_response.status_code, 200, content_response.get_json())
        content_id = content_response.get_json()["content_id"]

        with self.app.app_context():
            conn = get_db_connection()
            try:
                conn.execute(
                    "UPDATE content_items SET current_version = ?, status = 'pending_review' WHERE id = ?",
                    (current_version, content_id),
                )
                if current_version > 1:
                    conn.execute("""
                        INSERT INTO content_versions (
                            content_id, version_number, text, corrected_text
                        )
                        VALUES (?, ?, ?, ?)
                    """, (
                        content_id, current_version, "Test content for review.", None
                    ))
                conn.commit()
            finally:
                conn.close()
        return content_id

    def _insert_review_audit(self, content_id, version, issues):
        with self.app.app_context():
            conn = get_db_connection()
            try:
                conn.execute("""
                    INSERT INTO analysis_results (
                        content_id, version_number, score, risk_level, sub_scores,
                        engine_used, issues, summary, corrected_text
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    content_id,
                    version,
                    40,
                    "CRITICAL",
                    json.dumps({}),
                    "RULE_BASED_FALLBACK",
                    json.dumps(issues),
                    "Isolated approval-gate test audit.",
                    "Test content for review.",
                ))
                conn.commit()
            finally:
                conn.close()

    def test_approval_is_blocked_by_critical_issue_in_latest_current_audit(self):
        content_id = self._create_reviewable_content()
        self._insert_review_audit(content_id, 1, [
            {"id": "critical-policy", "severity": "critical"}
        ])

        response = self.client.post("/api/reviews", json={
            "content_id": content_id,
            "content_version": 1,
            "reviewer_name": "Test Reviewer",
            "decision": "approved",
        })

        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertIn("1 unresolved Critical issue(s)", response.get_json()["error"])
        detail = self.client.get(f"/api/content/{content_id}").get_json()
        self.assertEqual(detail["content"]["status"], "pending_review")
        self.assertEqual(detail["reviews"], [])

        rejection = self.client.post("/api/reviews", json={
            "content_id": content_id,
            "content_version": 1,
            "reviewer_name": "Test Reviewer",
            "decision": "rejected",
        })
        self.assertEqual(rejection.status_code, 200, rejection.get_json())

    def test_approval_uses_latest_audit_for_current_version_only(self):
        content_id = self._create_reviewable_content(current_version=2)
        critical_issue = [{"id": "critical-policy", "severity": "CRITICAL"}]
        self._insert_review_audit(content_id, 1, critical_issue)
        self._insert_review_audit(content_id, 2, critical_issue)
        self._insert_review_audit(content_id, 2, [
            {"id": "warning-policy", "severity": "WARNING"}
        ])

        missing_version_response = self.client.post("/api/reviews", json={
            "content_id": content_id,
            "reviewer_name": "Test Reviewer",
            "decision": "approved",
        })
        self.assertEqual(
            missing_version_response.status_code,
            409,
            missing_version_response.get_json(),
        )
        self.assertIn("requires the current content version", missing_version_response.get_json()["error"])

        stale_response = self.client.post("/api/reviews", json={
            "content_id": content_id,
            "content_version": 1,
            "reviewer_name": "Test Reviewer",
            "decision": "approved",
        })
        self.assertEqual(stale_response.status_code, 409, stale_response.get_json())
        self.assertEqual(stale_response.get_json()["current_version"], 2)

        current_response = self.client.post("/api/reviews", json={
            "content_id": content_id,
            "content_version": 2,
            "reviewer_name": "Test Reviewer",
            "decision": "approved",
        })
        self.assertEqual(current_response.status_code, 200, current_response.get_json())
        detail = self.client.get(f"/api/content/{content_id}").get_json()
        self.assertEqual(detail["content"]["status"], "approved")
        self.assertEqual(detail["reviews"][0]["content_version"], 2)

    def test_approval_fails_closed_when_latest_audit_issues_are_invalid(self):
        content_id = self._create_reviewable_content()
        self._insert_review_audit(content_id, 1, [])
        with self.app.app_context():
            conn = get_db_connection()
            try:
                conn.execute(
                    "UPDATE analysis_results SET issues = ? WHERE content_id = ?",
                    ("not-json", content_id),
                )
                conn.commit()
            finally:
                conn.close()

        response = self.client.post("/api/reviews", json={
            "content_id": content_id,
            "content_version": 1,
            "reviewer_name": "Test Reviewer",
            "decision": "approved",
        })

        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertIn("could not be validated", response.get_json()["error"])
        detail = self.client.get(f"/api/content/{content_id}").get_json()
        self.assertEqual(detail["content"]["status"], "pending_review")
        self.assertEqual(detail["reviews"], [])

    def test_rule_corrections_replace_only_exact_fixable_spans(self):
        engine = RuleBasedAnalyzer(
            brand={"name": "Northstar", "primary_tone": "professional"},
            facts=[{
                "id": 1,
                "statement": "The organization was established in 2014.",
                "key_metric": "2014",
                "keywords": "organization, established"
            }],
            terminology=[{
                "term": "cheap",
                "replacement": "affordable",
                "active": 1
            }],
            claim_rules=[{
                "pattern_or_trigger": "guarantee outcomes",
                "required_disclaimer": "Add the approved disclaimer.",
                "active": 1
            }]
        )
        source_text = (
            "Cheap tools are cheap. The organization was established in 2024. "
            "We guarantee outcomes."
        )

        result = engine.analyze(source_text)

        self.assertEqual(
            result["corrected_text"],
            "Affordable tools are affordable. The organization was established in 2014. "
            "We guarantee outcomes."
        )
        term_issues = [issue for issue in result["issues"] if issue["category"] == "Terminology"]
        self.assertEqual(len(term_issues), 2)
        self.assertTrue(all(issue["auto_fixable"] for issue in term_issues))
        fact_issue = next(issue for issue in result["issues"] if issue["category"] == "Fact Accuracy")
        self.assertEqual(fact_issue["matched_text"], "2024")
        self.assertEqual(fact_issue["suggestion"], "2014")
        self.assertTrue(fact_issue["auto_fixable"])
        claim_issue = next(issue for issue in result["issues"] if issue["category"] == "Compliance & Claims")
        self.assertFalse(claim_issue["auto_fixable"])
        self.assertNotIn("Add the approved disclaimer", result["corrected_text"])

    def test_brand_revision_applies_each_span_once_and_rechecks_configured_rules(self):
        brand_id = self.client.post(
            "/api/brands", json={"name": "Revision Check Brand"}
        ).get_json()["brand_id"]
        for _ in range(2):
            term_response = self.client.post(
                f"/api/brands/{brand_id}/terminology",
                json={
                    "term": "miracle",
                    "replacement_text": "quality",
                    "severity": "critical",
                },
            )
            self.assertEqual(term_response.status_code, 200, term_response.get_json())

        claim_response = self.client.post(
            f"/api/brands/{brand_id}/claims",
            json={
                "rule_name": "Guaranteed results",
                "pattern_or_trigger": r"\bguarantees results for everyone\b",
                "replacement_text": "offers dependable value",
                "severity": "critical",
            },
        )
        self.assertEqual(claim_response.status_code, 200, claim_response.get_json())

        source_text = "Our miracle products. It guarantees results for everyone."
        audit = self.client.post("/api/analysis/audit", json={
            "brand_id": brand_id,
            "title": "Revision source preservation",
            "text": source_text,
        }).get_json()
        self.assertEqual(
            audit["result"]["corrected_text"],
            "Our quality products. It offers dependable value.",
        )
        self.assertTrue(audit["result"]["revision_compliance"]["approved"])
        self.assertEqual(
            audit["result"]["revision_compliance"]["remaining_issue_count"],
            0,
        )

        with self.app.app_context():
            conn = get_db_connection()
            try:
                saved_content = conn.execute(
                    "SELECT original_text FROM content_items WHERE id = ?",
                    (audit["content_id"],),
                ).fetchone()
            finally:
                conn.close()
        self.assertEqual(saved_content["original_text"], source_text)

        adopted = self.client.post(
            f"/api/analysis/apply-corrections/{audit['content_id']}",
            json={"apply_all": True, "content_version": audit["version"]},
        )
        self.assertEqual(adopted.status_code, 200, adopted.get_json())
        self.assertEqual(
            adopted.get_json()["applied_text"],
            "Our quality products. It offers dependable value.",
        )
        self.assertEqual(adopted.get_json()["correction_review"]["remaining_issue_count"], 0)
        self.assertEqual(adopted.get_json()["correction_review"]["remaining_critical_issue_count"], 0)
        self.assertTrue(adopted.get_json()["correction_review"]["compliant"])
        history = self.client.get(
            f"/api/content/{audit['content_id']}"
        ).get_json()["versions"]
        self.assertEqual([version["version_number"] for version in history], [2, 1])
        self.assertEqual(history[1]["text"], source_text)

    def test_brand_revision_is_not_approved_when_a_configured_violation_remains(self):
        brand_id = self.client.post(
            "/api/brands", json={"name": "Noncompliant Revision Brand"}
        ).get_json()["brand_id"]
        term_response = self.client.post(
            f"/api/brands/{brand_id}/terminology",
            json={
                "term": "miracle",
                "replacement_text": "miracle-safe",
                "severity": "critical",
            },
        )
        self.assertEqual(term_response.status_code, 200, term_response.get_json())

        audit = self.client.post("/api/analysis/audit", json={
            "brand_id": brand_id,
            "title": "Remaining violation",
            "text": "A miracle product.",
        }).get_json()
        self.assertEqual(audit["result"]["corrected_text"], "A miracle-safe product.")
        self.assertFalse(audit["result"]["revision_compliance"]["approved"])
        self.assertEqual(
            audit["result"]["revision_compliance"]["remaining_issue_count"],
            1,
        )

    def test_greenleaf_revision_keeps_partial_claims_manual_and_rechecks_every_rule(self):
        brand_id = self.client.post(
            "/api/brands", json={"name": "GreenLeaf Revision Regression"}
        ).get_json()["brand_id"]
        terminology = self.client.post(
            f"/api/brands/{brand_id}/terminology",
            json={
                "term": "miracle",
                "replacement_text": "Quality organic food",
                "severity": "critical",
            },
        )
        self.assertEqual(terminology.status_code, 200, terminology.get_json())
        claim_replacement = "Our products support everyday wellness."
        claim = self.client.post(
            f"/api/brands/{brand_id}/claims",
            json={
                "rule_name": "Medical Claims Restriction",
                "pattern_or_trigger": (
                    r"\b(cure diseases|prevent health problems|"
                    r"guarantees results for everyone|instant health improvements)\b"
                ),
                "replacement_text": claim_replacement,
                "severity": "critical",
            },
        )
        self.assertEqual(claim.status_code, 200, claim.get_json())

        source_text = (
            "Our miracle products cure diseases and prevent health problems. "
            "It guarantees results for everyone with instant health improvements."
        )
        audit = self.client.post("/api/analysis/audit", json={
            "brand_id": brand_id,
            "title": "GreenLeaf medical revision",
            "text": source_text,
        }).get_json()
        revision = audit["result"]["corrected_text"]
        claim_issues = [
            issue for issue in audit["result"]["issues"]
            if issue["category"] == "Compliance & Claims"
        ]
        self.assertEqual(len(claim_issues), 4)
        self.assertTrue(all(not issue["auto_fixable"] for issue in claim_issues))
        self.assertTrue(all(issue["manual_review_required"] for issue in claim_issues))
        self.assertEqual(
            revision,
            "Our Quality organic food products cure diseases and prevent health problems. "
            "It guarantees results for everyone with instant health improvements.",
        )
        self.assertNotIn(claim_replacement, revision)
        self.assertNotIn("miracle", revision.casefold())
        self.assertIn("cure diseases", revision.casefold())
        self.assertIn("prevent health problems", revision.casefold())
        self.assertIn("guarantees results for everyone", revision.casefold())
        self.assertIn("instant health improvements", revision.casefold())
        self.assertFalse(audit["result"]["revision_compliance"]["approved"])
        self.assertEqual(
            audit["result"]["revision_compliance"]["remaining_issue_count"],
            4,
        )

        adopted = self.client.post(
            f"/api/analysis/apply-corrections/{audit['content_id']}",
            json={"apply_all": True, "content_version": audit["version"]},
        )
        self.assertEqual(adopted.status_code, 200, adopted.get_json())
        self.assertEqual(adopted.get_json()["applied_text"], revision)
        self.assertEqual(adopted.get_json()["correction_review"]["remaining_issue_count"], 4)
        self.assertEqual(
            adopted.get_json()["correction_review"]["remaining_critical_issue_count"],
            4,
        )
        self.assertFalse(adopted.get_json()["correction_review"]["compliant"])
        history = self.client.get(
            f"/api/content/{audit['content_id']}"
        ).get_json()["versions"]
        self.assertEqual(history[1]["text"], source_text)

    def test_greenleaf_phrase_replacements_are_individually_applicable(self):
        brand_id = self.client.post(
            "/api/brands", json={"name": "GreenLeaf Phrase Replacement"}
        ).get_json()["brand_id"]
        replacements = {
            "cure diseases": "support a balanced and healthy lifestyle",
            "prevent health problems": "support overall wellness",
        }
        for matched_text, replacement_text in replacements.items():
            response = self.client.post(
                f"/api/brands/{brand_id}/claims",
                json={
                    "rule_name": f"Approved phrase: {matched_text}",
                    "pattern_or_trigger": rf"\b{matched_text}\b",
                    "replacement_text": replacement_text,
                    "severity": "critical",
                    "explanation": "Use the approved phrase-level wording.",
                },
            )
            self.assertEqual(response.status_code, 200, response.get_json())

        source_text = (
            "GreenLeaf Organics presents its miracle products that cure diseases "
            "and prevent health problems."
        )
        audit_response = self.client.post("/api/analysis/audit", json={
            "brand_id": brand_id,
            "title": "GreenLeaf phrase replacement",
            "text": source_text,
        })
        self.assertEqual(audit_response.status_code, 200, audit_response.get_json())
        audit = audit_response.get_json()
        issues = {
            issue["matched_text"]: issue
            for issue in audit["result"]["issues"]
            if issue.get("matched_text") in replacements
        }
        self.assertEqual(set(issues), set(replacements))
        for matched_text, replacement_text in replacements.items():
            with self.subTest(matched_text=matched_text):
                issue = issues[matched_text]
                self.assertEqual(issue["replacement_text"], replacement_text)
                self.assertTrue(issue["auto_fixable"])
                self.assertFalse(issue["manual_review_required"])
                location = issue["location"]
                self.assertEqual(
                    source_text[location["start"]:location["end"]],
                    matched_text,
                )

        first_issue = issues["cure diseases"]
        first_correction = self.client.post(
            f"/api/analysis/apply-corrections/{audit['content_id']}",
            json={
                "issue_ids": [first_issue["id"]],
                "content_version": audit["version"],
            },
        )
        self.assertEqual(first_correction.status_code, 200, first_correction.get_json())
        first_data = first_correction.get_json()
        expected_after_first = (
            "GreenLeaf Organics presents its miracle products that support a balanced "
            "and healthy lifestyle and prevent health problems."
        )
        self.assertEqual(first_data["applied_text"], expected_after_first)

        remaining_issue = next(
            issue for issue in first_data["result"]["issues"]
            if issue.get("matched_text") == "prevent health problems"
        )
        self.assertTrue(remaining_issue["auto_fixable"])
        second_correction = self.client.post(
            f"/api/analysis/apply-corrections/{audit['content_id']}",
            json={
                "issue_ids": [remaining_issue["id"]],
                "content_version": first_data["new_version"],
            },
        )
        self.assertEqual(second_correction.status_code, 200, second_correction.get_json())
        self.assertEqual(
            second_correction.get_json()["applied_text"],
            "GreenLeaf Organics presents its miracle products that support a balanced "
            "and healthy lifestyle and support overall wellness.",
        )
        self.assertEqual(
            second_correction.get_json()["applied_issue_ids"],
            [remaining_issue["id"]],
        )

    def test_empty_database_to_brand_delete_workflow(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        initial_brands = self.client.get("/api/brands").get_json()["brands"]
        self.assertEqual(initial_brands, [])
        self.assertEqual(self.client.get("/api/demo-samples").status_code, 404)

        missing_brand_analysis = self.client.post(
            "/api/analysis/quick-check", json={"text": "No selected brand"}
        )
        self.assertEqual(missing_brand_analysis.status_code, 400)
        self.assertFalse(self.client.post("/api/content", json={"text": "Draft"}).get_json()["success"])

        brand_response = self.client.post("/api/brands", json={
            "name": "Northstar Labs",
            "description": "A brand profile created by this test.",
            "industry": "Research",
            "tagline": "Research for teams",
            "primary_tone": "Clear and factual",
            "voice_traits": ["precise", "approachable"],
            "target_audience": "Research teams"
        })
        self.assertEqual(brand_response.status_code, 200, brand_response.get_json())
        brand_id = brand_response.get_json()["brand_id"]
        brand_details_response = self.client.get(f"/api/brands/{brand_id}")
        self.assertEqual(brand_details_response.status_code, 200)
        self.assertEqual(
            brand_details_response.get_json()["brand"]["description"],
            "A brand profile created by this test."
        )
        listed_brands = self.client.get("/api/brands").get_json()["brands"]
        self.assertTrue(any(brand["id"] == brand_id for brand in listed_brands))
        with self.app.app_context():
            conn = get_db_connection()
            try:
                persisted_brand = conn.execute(
                    "SELECT name, description FROM brands WHERE id = ?", (brand_id,)
                ).fetchone()
                self.assertIsNotNone(persisted_brand)
                self.assertEqual(persisted_brand["name"], "Northstar Labs")
                self.assertEqual(
                    persisted_brand["description"],
                    "A brand profile created by this test."
                )
            finally:
                conn.close()

        update_response = self.client.put(f"/api/brands/{brand_id}", json={
            "primary_tone": "Precise and accessible",
            "voice_traits": ["precise", "accessible", "calm"]
        })
        self.assertTrue(update_response.get_json()["success"])

        fact_response = self.client.post(f"/api/brands/{brand_id}/facts", json={
            "category": "Organization History",
            "statement": "The organization was established in 2014.",
            "key_metric": "2014",
            "keywords": "established, organization history"
        })
        self.assertEqual(fact_response.status_code, 200, fact_response.get_json())
        term_response = self.client.post(f"/api/brands/{brand_id}/terminology", json={
            "term": "best-in-class",
            "replacement": "well-suited",
            "reason": "Use substantiated descriptions.",
            "severity": "warning"
        })
        self.assertEqual(term_response.status_code, 200)
        claim_response = self.client.post(f"/api/brands/{brand_id}/claims", json={
            "rule_name": "No guaranteed outcomes",
            "pattern_or_trigger": "guarantee outcomes",
            "severity": "critical",
            "explanation": "Outcomes must not be guaranteed."
        })
        self.assertEqual(claim_response.status_code, 200, claim_response.get_json())
        unsafe_pattern = self.client.post(f"/api/brands/{brand_id}/claims", json={
            "rule_name": "Unsafe pattern",
            "pattern_or_trigger": "(a+)+$"
        })
        self.assertEqual(unsafe_pattern.status_code, 400)

        visual_response = self.client.put(f"/api/brands/{brand_id}/visual-rules", json={
            "primary_colors": [{"name": "Ocean", "hex": "#126abc"}],
            "secondary_colors": [],
            "forbidden_colors": [],
            "logo_rules": "Maintain clear space.",
            "font_family": "Brand Sans",
            "contrast_minimum": "4.5:1"
        })
        self.assertTrue(visual_response.get_json()["success"])

        first_audit = self.client.post("/api/analysis/audit", json={
            "brand_id": brand_id,
            "title": "Research overview",
            "text": "The organization was established in 2024. We guarantee outcomes."
        })
        self.assertEqual(first_audit.status_code, 200, first_audit.get_json())
        first_data = first_audit.get_json()
        content_id = first_data["content_id"]
        first_result = first_data["result"]
        self.assertEqual(first_data["version"], 1)
        self.assertEqual(first_data["status"], "pending_review")
        self.assertEqual(first_result["risk_level"], "CRITICAL")
        self.assertLessEqual(first_result["score"], 49)
        self.assertTrue(any(issue["severity"] == "CRITICAL" for issue in first_result["issues"]))
        self.assertNotIn("suitable for approval", first_result["summary"].lower())
        self.assertEqual(first_result["engine_used"], "RULE_BASED_FALLBACK")
        self.assertFalse(first_result["visual_validation"]["supported"])
        self.assertTrue(any(issue["category"] == "Fact Accuracy" for issue in first_result["issues"]))

        second_audit = self.client.post("/api/analysis/audit", json={
            "brand_id": brand_id,
            "content_id": content_id,
            "current_version": first_data["version"],
            "title": "Research overview",
            "text": "The organization was established in 2014. It has well-suited research tools."
        })
        self.assertEqual(second_audit.status_code, 200, second_audit.get_json())
        self.assertEqual(second_audit.get_json()["version"], 2)

        correction_response = self.client.post(
            f"/api/analysis/apply-corrections/{content_id}",
            json={"corrected_text": "The organization was established in 2014. It has research tools."}
        )
        self.assertEqual(correction_response.status_code, 200, correction_response.get_json())
        self.assertEqual(correction_response.get_json()["new_version"], 3)
        self.assertEqual(correction_response.get_json()["result"]["issues"], [])

        for decision in ("approved", "rejected", "changes_requested"):
            review_response = self.client.post("/api/reviews", json={
                "content_id": content_id,
                "reviewer_name": "Test Reviewer",
                "reviewer_role": "Review Team",
                "decision": decision,
                "content_version": 3,
                "comments": f"Recorded {decision} decision."
            })
            self.assertEqual(review_response.status_code, 200, review_response.get_json())
            self.assertEqual(review_response.get_json()["new_status"], decision)

        review_without_name = self.client.post("/api/reviews", json={
            "content_id": content_id, "decision": "approved"
        })
        self.assertEqual(review_without_name.status_code, 400)

        detail = self.client.get(f"/api/content/{content_id}").get_json()
        self.assertEqual(detail["content"]["status"], "changes_requested")
        self.assertEqual(len(detail["versions"]), 3)
        self.assertEqual(len(detail["reviews"]), 3)

        export = self.client.get(f"/api/history/export/{content_id}").get_json()["report"]
        self.assertEqual(export["content_metadata"]["status"], "changes_requested")
        self.assertEqual(len(export["version_history"]), 3)
        self.assertEqual(len(export["review_audit_log"]), 3)

        second_brand = self.client.post("/api/brands", json={"name": "Second Brand"}).get_json()["brand_id"]
        second_content = self.client.post("/api/analysis/audit", json={
            "brand_id": second_brand, "title": "Second brand content", "text": "Text for its own brand."
        })
        self.assertEqual(second_content.status_code, 200)
        scoped_stats = self.client.get(f"/api/history/dashboard-stats?brand_id={brand_id}").get_json()
        other_stats = self.client.get(f"/api/history/dashboard-stats?brand_id={second_brand}").get_json()
        self.assertEqual(scoped_stats["total_scanned"], 1)
        self.assertEqual(other_stats["total_scanned"], 1)
        self.assertEqual(
            self.client.get("/api/history/dashboard-stats?brand_id=1%20OR%201%3D1").status_code,
            400
        )

        with self.app.app_context():
            conn = get_db_connection()
            try:
                conn.execute("DELETE FROM brands WHERE id = ?", (brand_id,))
                conn.commit()
                for table in ("brand_facts", "brand_terminology", "brand_visual_rules",
                              "brand_claim_rules", "content_items", "content_versions",
                              "analysis_results", "reviews"):
                    self.assertEqual(
                        conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                        1 if table in (
                            "brand_visual_rules", "content_items", "content_versions", "analysis_results"
                        ) else 0,
                        table
                    )
            finally:
                conn.close()
        delete_response = self.client.delete(f"/api/brands/{second_brand}")
        self.assertTrue(delete_response.get_json()["success"])
        self.assertEqual(self.client.get("/api/brands").get_json()["brands"], [])

    def test_gemini_result_merge_preserves_corrected_text(self):
        scores = {
            "terminology": 100,
            "facts": 100,
            "tone": 100,
            "claims": 100,
            "accessibility": 100
        }
        rule_result = {
            "score": 100,
            "issues": [],
            "sub_scores": scores,
            "readability": {},
            "corrected_text": "Rule-based correction"
        }
        ai_result = {
            "score": 100,
            "issues": [],
            "sub_scores": scores,
            "corrected_text": "Gemini correction"
        }

        result = GovernanceAnalyzer()._merge_results(rule_result, ai_result)

        self.assertEqual(result["corrected_text"], "Rule-based correction")
        self.assertEqual(result["engine_used"], "AI_GEMINI")

    def test_gemini_merge_preserves_only_validated_replacements(self):
        scores = {
            "terminology": 100,
            "facts": 100,
            "tone": 100,
            "claims": 100,
            "accessibility": 100
        }
        issues = [
            {
                "id": "safe",
                "category": "Voice & Tone",
                "severity": "WARNING",
                "matched_text": "claim",
                "replacement_text": "statement",
                "auto_fixable": True,
                "location": {"start": 0, "end": 5}
            },
            {
                "id": "unsafe",
                "category": "Voice & Tone",
                "severity": "WARNING",
                "matched_text": "claim",
                "replacement_text": "Use: a statement",
                "auto_fixable": True,
                "location": {"start": 10, "end": 15}
            },
            {
                "id": "unvalidated",
                "category": "Voice & Tone",
                "severity": "WARNING",
                "matched_text": "claim",
                "replacement_text": "statement",
                "auto_fixable": False,
                "location": {"start": 20, "end": 25}
            }
        ]
        result = GovernanceAnalyzer()._merge_results(
            {
                "score": 100,
                "issues": [],
                "sub_scores": scores,
                "readability": {},
                "corrected_text": "Rule-based correction"
            },
            {
                "score": 100,
                "issues": issues,
                "sub_scores": scores,
                "corrected_text": "Gemini correction"
            }
        )

        by_id = {issue["id"]: issue for issue in result["issues"]}
        self.assertEqual(by_id["safe"]["replacement_text"], "statement")
        self.assertTrue(by_id["safe"]["auto_fixable"])
        self.assertIsNone(by_id["unsafe"]["replacement_text"])
        self.assertFalse(by_id["unsafe"]["auto_fixable"])
        self.assertTrue(by_id["unsafe"]["manual_review_required"])
        self.assertIsNone(by_id["unvalidated"]["replacement_text"])
        self.assertFalse(by_id["unvalidated"]["auto_fixable"])

    def test_quick_check_returns_ai_violations_and_identifies_the_engine(self):
        brand_id = self.client.post(
            "/api/brands", json={"name": "Test Brand"}
        ).get_json()["brand_id"]
        analyzer.ai_service.gemini_key = "unit-test-key"
        ai_result = {
            "score": 78,
            "sub_scores": {
                "terminology": 100,
                "facts": 100,
                "tone": 100,
                "claims": 74,
                "accessibility": 100
            },
            "issues": [{
                "id": "ai_claim",
                "category": "Compliance & Claims",
                "severity": "WARNING",
                "rule": "Unsupported outcome",
                "matched_text": "guarantees",
                "location": {"start": 12, "end": 22},
                "explanation": "The outcome is not supported by approved facts.",
                "suggestion": "may support"
            }],
            "corrected_text": "Our product may support results.",
            "summary": "A policy warning was detected."
        }

        with patch.object(analyzer.ai_service, "analyze_with_ai", return_value=ai_result):
            response = self.client.post("/api/analysis/quick-check", json={
                "brand_id": brand_id,
                "text": "Our product guarantees results."
            })

        self.assertEqual(response.status_code, 200, response.get_json())
        result = response.get_json()["result"]
        self.assertEqual(result["engine_used"], "AI_GEMINI")
        self.assertEqual(result["issues"][0]["severity"], "WARNING")
        self.assertEqual(
            result["issues"][0]["explanation"],
            "The outcome is not supported by approved facts."
        )
        self.assertEqual(result["issues"][0]["suggestion"], "may support")

    def test_gemini_service_builds_backend_request_and_validates_response(self):
        source_text = "This claim needs review."
        ai_response = {
            "score": 82,
            "sub_scores": {
                "terminology": 100,
                "facts": 100,
                "tone": 100,
                "claims": 74,
                "accessibility": 100
            },
            "issues": [{
                "severity": "WARNING",
                "matched_text": "claim",
                "location": {"start": 5, "end": 10},
                "explanation": "Review this claim.",
                "suggestion": "statement",
                "replacement_text": "statement"
            }],
            "corrected_text": "This statement needs review.",
            "summary": "A policy warning was detected."
        }
        client = Mock()
        client.models.generate_content.return_value.text = json.dumps(ai_response)
        service = AIService()
        service.gemini_key = "unit-test-key"

        with patch("services.ai_service.genai.Client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            result = service.analyze_with_ai(
                source_text,
                {"name": "Test Brand"},
                [],
                [],
                [],
                {},
                "Ad Copy",
                "Digital Ads"
            )

        self.assertEqual(result["engine_used"], "AI_GEMINI")
        self.assertEqual(result["corrected_text"], ai_response["corrected_text"])
        self.assertTrue(result["issues"][0]["auto_fixable"])
        self.assertEqual(result["issues"][0]["replacement_text"], "statement")
        client_factory.assert_called_once()
        self.assertEqual(client_factory.call_args.kwargs["api_key"], "unit-test-key")
        self.assertEqual(
            client_factory.call_args.kwargs["http_options"].timeout,
            60_000,
        )
        self.assertEqual(client.models.generate_content.call_args.kwargs["model"], "gemini-3.6-flash")
        self.assertEqual(
            client.models.generate_content.call_args.kwargs["config"].response_mime_type,
            "application/json",
        )

    def test_literal_ai_replacements_bind_to_exact_spans_and_current_claim_rules(self):
        brand_response = self.client.post(
            "/api/brands", json={"name": "GreenLeaf Auto Correction"}
        )
        brand_id = brand_response.get_json()["brand_id"]
        replacement = "Supports a balanced and healthy lifestyle."
        claim_response = self.client.post(
            f"/api/brands/{brand_id}/claims",
            json={
                "rule_name": "Medical Claims Restriction",
                "pattern_or_trigger": (
                    r"cures\|cure\|prevents disease\|guaranteed results"
                ),
                "severity": "critical",
                "replacement_text": replacement,
                "required_disclaimer": "Manual review is still required.",
                "explanation": "Unsupported medical claims require review.",
            },
        )
        self.assertEqual(claim_response.status_code, 200, claim_response.get_json())

        source_text = (
            "Our miracle products cure diseases. Guaranteed results for everyone!"
        )
        ai_payload = {
            "score": 10,
            "sub_scores": {
                "terminology": 49,
                "facts": 100,
                "tone": 100,
                "claims": 49,
                "accessibility": 100,
            },
            "issues": [
                {
                    "id": "ai_miracle",
                    "category": "terminology",
                    "severity": "CRITICAL",
                    "rule": "Prohibited Terminology",
                    "matched_text": "miracle",
                    "explanation": "This term is prohibited.",
                    "suggestion": "Replace with 'Quality organic food'.",
                },
                {
                    "id": "ai_cure",
                    "category": "claims",
                    "severity": "CRITICAL",
                    "rule": "Medical Claims Restriction",
                    "matched_text": "cure diseases",
                    "explanation": "This medical claim is unsupported.",
                    "suggestion": (
                        "Replace with approved messaging supporting a balanced lifestyle."
                    ),
                },
                {
                    "id": "ai_guaranteed",
                    "category": "claims",
                    "severity": "CRITICAL",
                    "rule": "Medical Claims Restriction",
                    "matched_text": "Guaranteed results",
                    "explanation": "This outcome is not guaranteed.",
                    "suggestion": (
                        "Remove guaranteed results claim and include the required medical disclaimer."
                    ),
                },
            ],
            "corrected_text": source_text,
            "summary": "Critical claims require review.",
        }

        def mocked_ai_analysis(text, *_args):
            return AIService._validate_response(ai_payload, text)

        with patch.object(analyzer.ai_service, "is_configured", return_value=True), \
             patch.object(analyzer.ai_service, "analyze_with_ai", side_effect=mocked_ai_analysis):
            audit_response = self.client.post("/api/analysis/audit", json={
                "brand_id": brand_id,
                "title": "GreenLeaf medical claims",
                "text": source_text,
            })
            self.assertEqual(audit_response.status_code, 200, audit_response.get_json())
            audit = audit_response.get_json()
            issues = audit["result"]["issues"]
            relevant = [
                issue for issue in issues
                if issue.get("matched_text") in {
                    "miracle", "cure diseases", "Guaranteed results"
                }
            ]
            self.assertEqual(len(relevant), 3)
            by_match = {issue["matched_text"]: issue for issue in relevant}
            expected_replacements = {
                "miracle": "Quality organic food",
                "cure diseases": replacement,
                "Guaranteed results": replacement,
            }
            for matched_text, expected in expected_replacements.items():
                with self.subTest(matched_text=matched_text):
                    issue = by_match[matched_text]
                    self.assertEqual(issue["auto_fixable"], matched_text == "miracle")
                    self.assertEqual(issue["replacement_text"], expected)
                    location = issue["location"]
                    self.assertEqual(
                        source_text[location["start"]:location["end"]],
                        matched_text,
                    )

            correction = self.client.post(
                f"/api/analysis/apply-corrections/{audit['content_id']}",
                json={
                    "issue_ids": [issue["id"] for issue in relevant],
                    "content_version": audit["version"],
                },
            )
        self.assertEqual(correction.status_code, 409, correction.get_json())
        self.assertCountEqual(
            correction.get_json()["manual_review_issue_ids"],
            [by_match["cure diseases"]["id"], by_match["Guaranteed results"]["id"]],
        )
        correction = self.client.post(
            f"/api/analysis/apply-corrections/{audit['content_id']}",
            json={
                "issue_ids": [by_match["miracle"]["id"]],
                "content_version": audit["version"],
            },
        )
        self.assertEqual(correction.status_code, 200, correction.get_json())
        self.assertEqual(
            correction.get_json()["applied_text"],
            "Our Quality organic food products cure diseases. Guaranteed results for everyone!",
        )
        self.assertEqual(correction.get_json()["new_version"], 2)
        stale_correction = self.client.post(
            f"/api/analysis/apply-corrections/{audit['content_id']}",
            json={
                "issue_ids": [issue["id"] for issue in relevant],
                "content_version": audit["version"],
            },
        )
        self.assertEqual(stale_correction.status_code, 409)

    def test_unquoted_ai_suggestion_and_ambiguous_spans_remain_manual(self):
        response = {
            "score": 49,
            "sub_scores": {
                "terminology": 49,
                "facts": 100,
                "tone": 100,
                "claims": 49,
                "accessibility": 100,
            },
            "issues": [
                {
                    "id": "manual",
                    "category": "terminology",
                    "severity": "CRITICAL",
                    "rule": "Prohibited Terminology",
                    "matched_text": "miracle",
                    "explanation": "Review this wording.",
                    "suggestion": "Choose approved terminology.",
                },
            ],
            "corrected_text": "miracle and miracle",
            "summary": "Manual review required.",
        }
        validated = AIService._validate_response(response, "miracle and miracle")
        self.assertIsNotNone(validated)
        issue = validated["issues"][0]
        self.assertEqual(issue["location"], {})
        self.assertIsNone(issue["replacement_text"])
        self.assertFalse(issue["auto_fixable"])

    def test_gemini_replacements_require_safe_text_and_an_exact_span(self):
        source_text = "This claim needs review."
        base_response = {
            "score": 82,
            "sub_scores": {
                "terminology": 100,
                "facts": 100,
                "tone": 100,
                "claims": 74,
                "accessibility": 100
            },
            "issues": [{
                "severity": "WARNING",
                "matched_text": "claim",
                "location": {"start": 5, "end": 10},
                "explanation": "Review this claim.",
                "suggestion": "statement",
                "replacement_text": "statement"
            }],
            "corrected_text": "This statement needs review.",
            "summary": "A policy warning was detected."
        }

        for replacement, location in (
            ("Use: a statement", {"start": 5, "end": 10}),
            ("claim", {"start": 5, "end": 10}),
            ("statement", {"start": 0, "end": 5}),
            (None, {"start": 5, "end": 10}),
        ):
            response = json.loads(json.dumps(base_response))
            response["issues"][0]["replacement_text"] = replacement
            response["issues"][0]["location"] = location

            result = AIService._validate_response(response, source_text)
            issue = result["issues"][0]
            self.assertIsNone(issue["replacement_text"])
            self.assertFalse(issue["auto_fixable"])
            self.assertTrue(issue["manual_review_required"])


if __name__ == "__main__":
    unittest.main()
