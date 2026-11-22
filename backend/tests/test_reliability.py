
import json
import sys
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

import httpx2
from google.genai import errors, types

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_test_database_dir = tempfile.TemporaryDirectory(prefix="brandguard-test-bootstrap-")
os.environ["DATABASE_URL"] = " "
os.environ["DATABASE_PATH"] = str(Path(_test_database_dir.name) / "bootstrap.db")

from app import create_app
from api_utils import MAX_VISUAL_IMAGE_BYTES
from database import get_db_connection
from routes.analysis import analyzer
from services.ai_service import (
    AIService,
    GeminiRewriteValidationError,
    GeminiUnavailableError,
    GeminiVisualAnalysisUnavailableError,
    GeminiVisualAnalysisValidationError,
)
from services.analyzer import GovernanceAnalyzer


class BackendReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "DATABASE_URL": "",
            "DATABASE_PATH": str(Path(self.temp_dir.name) / "reliability.db"),
            "RATELIMIT_ENABLED": False,
        })
        self.client = self.app.test_client()
        self.original_gemini_key = analyzer.ai_service.gemini_key
        analyzer.ai_service.gemini_key = ""

    def tearDown(self):
        analyzer.ai_service.gemini_key = self.original_gemini_key
        self.temp_dir.cleanup()

    def create_brand(self, name="Reliability Brand"):
        response = self.client.post("/api/brands", json={"name": name})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["brand_id"]

    def assert_sanitized_log(
        self,
        logger_name,
        request,
        expected_status,
        expected_error=None,
        level="ERROR",
        exception_category="RuntimeError",
    ):
        with self.assertLogs(logger_name, level=level) as logs:
            response = request()

        self.assertEqual(response.status_code, expected_status)
        if expected_error is not None:
            self.assertEqual(response.get_json()["error"], expected_error)
        messages = "\n".join(logs.output)
        self.assertIn(
            f"exception_category={exception_category}",
            messages,
        )
        self.assertNotIn("sensitive-log-sentinel", messages)
        self.assertNotIn("Traceback", messages)
        return response

    def test_quick_check_uses_configured_non_numeric_facts_voice_and_audience(self):
        create_response = self.client.post("/api/brands", json={
            "name": "Deterministic Guidelines",
            "voice_traits": ["warm", "concise", "precise", "accessible"],
            "target_audience": "beginner consumers",
        })
        self.assertEqual(create_response.status_code, 200, create_response.get_json())
        brand_id = create_response.get_json()["brand_id"]

        fact_response = self.client.post(
            f"/api/brands/{brand_id}/facts",
            json={
                "category": "Ingredients",
                "statement": "The formula contains no synthetic fragrance.",
                "keywords": "formula, fragrance",
            },
        )
        self.assertEqual(fact_response.status_code, 200, fact_response.get_json())

        valid_response = self.client.post("/api/analysis/quick-check", json={
            "brand_id": brand_id,
            "text": (
                "The formula contains no synthetic fragrance. "
                "For consumers, this clear guide welcomes new users."
            ),
        })
        self.assertEqual(valid_response.status_code, 200, valid_response.get_json())
        valid_result = valid_response.get_json()["result"]
        self.assertEqual(valid_result["engine_used"], "RULE_BASED_FALLBACK")
        self.assertFalse(any(
            issue["category"] in {"Fact Accuracy", "Voice & Tone"}
            or issue["rule"] == "Target Audience Suitability"
            for issue in valid_result["issues"]
        ))

        invalid_response = self.client.post("/api/analysis/quick-check", json={
            "brand_id": brand_id,
            "text": (
                "The formula contains synthetic fragrance. "
                "Our cold service is available for clinicians. "
                "It is important to note that the idempotent API always helps."
            ),
        })
        self.assertEqual(invalid_response.status_code, 200, invalid_response.get_json())
        invalid_result = invalid_response.get_json()["result"]
        self.assertEqual(invalid_result["engine_used"], "RULE_BASED_FALLBACK")
        self.assertTrue(any(
            issue["category"] == "Fact Accuracy"
            and issue["approved_fact"]
            == "The formula contains no synthetic fragrance."
            for issue in invalid_result["issues"]
        ))
        self.assertTrue(any(
            issue["category"] == "Voice & Tone"
            and issue["rule"] == "Configured Voice Trait: warm"
            for issue in invalid_result["issues"]
        ))
        self.assertTrue(any(
            issue["category"] == "Voice & Tone"
            and issue["rule"] == "Configured Voice Trait: concise"
            for issue in invalid_result["issues"]
        ))
        self.assertTrue(any(
            issue["category"] == "Voice & Tone"
            and issue["rule"] == "Configured Voice Trait: precise"
            for issue in invalid_result["issues"]
        ))
        self.assertTrue(any(
            issue["category"] == "Voice & Tone"
            and issue["rule"] == "Configured Voice Trait: accessible"
            for issue in invalid_result["issues"]
        ))
        self.assertTrue(any(
            issue["rule"] == "Target Audience Suitability"
            for issue in invalid_result["issues"]
        ))
        self.assertFalse(invalid_result["visual_validation"]["supported"])

        unconfigured_brand_id = self.create_brand("No Optional Guidelines")
        unconfigured_response = self.client.post(
            "/api/analysis/quick-check",
            json={
                "brand_id": unconfigured_brand_id,
                "text": (
                    "The formula contains synthetic fragrance. "
                    "Our cold service is available for clinicians."
                ),
            },
        )
        self.assertEqual(
            unconfigured_response.status_code,
            200,
            unconfigured_response.get_json(),
        )
        unconfigured_issues = unconfigured_response.get_json()["result"]["issues"]
        self.assertFalse(any(
            issue["category"] in {"Fact Accuracy", "Voice & Tone"}
            or issue["rule"] == "Target Audience Suitability"
            for issue in unconfigured_issues
        ))

    def database_snapshot(self):
        tables = (
            "brands",
            "brand_facts",
            "brand_terminology",
            "brand_visual_rules",
            "brand_claim_rules",
            "content_items",
            "content_versions",
            "analysis_results",
            "reviews",
        )
        with self.app.app_context():
            conn = get_db_connection()
            try:
                return {
                    table: [
                        tuple(row)
                        for row in conn.execute(
                            f"SELECT * FROM {table} ORDER BY id"
                        ).fetchall()
                    ]
                    for table in tables
                }
            finally:
                conn.close()

    def test_visual_check_upload_uses_configured_rules_without_saving(self):
        brand_id = self.create_brand("Visual Test Brand")
        visual_rules = {
            "primary_colors": [{"name": "Ocean Blue", "hex": "#126ABC"}],
            "secondary_colors": [],
            "forbidden_colors": [{"name": "Competitor Red", "hex": "#CC2233"}],
            "logo_rules": "The approved logo must be visible.",
            "font_family": "Brand Sans",
            "contrast_minimum": "4.5:1",
        }
        saved_rules = self.client.put(
            f"/api/brands/{brand_id}/visual-rules",
            json=visual_rules,
        )
        self.assertEqual(saved_rules.status_code, 200, saved_rules.get_json())
        before = self.database_snapshot()

        visual_result = {
            "supported": True,
            "engine_used": "AI_GEMINI",
            "message": "Manual verification required.",
            "checks": [{
                "id": "brand_palette",
                "rule": "Obvious brand palette consistency",
                "status": "concern",
                "evidence": "A prominent red background is visible.",
                "confidence": "medium",
                "manual_review_required": True,
            }],
            "not_evaluated": [{
                "rule": "Font family",
                "reason": "Font identification is not reliable.",
            }],
        }
        image_bytes = b"\x89PNG\r\n\x1a\nmock image data"
        with patch.object(
            analyzer.ai_service,
            "analyze_visual_content",
            return_value=visual_result,
        ) as analyze_image:
            response = self.client.post(
                "/api/analysis/visual-check",
                data={
                    "brand_id": str(brand_id),
                    "file": (
                        BytesIO(image_bytes),
                        "creative sample.png",
                        "image/png",
                    ),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()
        self.assertFalse(payload["saved"])
        self.assertEqual(payload["filename"], "creative_sample.png")
        self.assertEqual(payload["visual_validation"], visual_result)
        analyze_image.assert_called_once()
        call_args = analyze_image.call_args.args
        self.assertEqual(call_args[0], image_bytes)
        self.assertEqual(call_args[1], "image/png")
        self.assertEqual(call_args[3]["primary_colors"], visual_rules["primary_colors"])
        self.assertEqual(before, self.database_snapshot())

    def test_visual_check_rejects_bad_uploads_and_reports_missing_ai_safely(self):
        brand_id = self.create_brand("Visual Validation Brand")
        wrong_type = self.client.post(
            "/api/analysis/visual-check",
            data={
                "brand_id": str(brand_id),
                "file": (BytesIO(b"not an image"), "fake.png", "image/png"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(wrong_type.status_code, 400, wrong_type.get_json())

        oversized = self.client.post(
            "/api/analysis/visual-check",
            data={
                "brand_id": str(brand_id),
                "file": (
                    BytesIO(b"\x89PNG\r\n\x1a\n" + b"x" * MAX_VISUAL_IMAGE_BYTES),
                    "large.png",
                    "image/png",
                ),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(oversized.status_code, 413, oversized.get_json())

        unavailable = self.client.post(
            "/api/analysis/visual-check",
            data={
                "brand_id": str(brand_id),
                "file": (
                    BytesIO(b"\x89PNG\r\n\x1a\nmock image"),
                    "valid.png",
                    "image/png",
                ),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(unavailable.status_code, 503, unavailable.get_json())
        self.assertEqual(
            unavailable.get_json()["error"],
            "Gemini visual review is not configured. Set GEMINI_API_KEY on the backend.",
        )

        with patch.object(
            analyzer.ai_service,
            "analyze_visual_content",
            side_effect=GeminiVisualAnalysisUnavailableError(
                "Gemini API quota or rate limit reached. Check the backend Gemini API usage and billing, or retry after the quota resets.",
                response_status=429,
            ),
        ):
            quota_exhausted = self.client.post(
                "/api/analysis/visual-check",
                data={
                    "brand_id": str(brand_id),
                    "file": (
                        BytesIO(b"\x89PNG\r\n\x1a\nmock image"),
                        "valid.png",
                        "image/png",
                    ),
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(quota_exhausted.status_code, 429)
        self.assertIn("quota", quota_exhausted.get_json()["error"])

    def test_api_security_limits_reject_oversized_json_text_and_invalid_ids(self):
        oversized_json = self.client.post(
            "/api/brands",
            json={"name": "Brand", "description": "x" * 1_100_000},
        )
        self.assertEqual(oversized_json.status_code, 413, oversized_json.get_json())

        oversized_text = self.client.post("/api/analysis/rewrite", json={
            "brand_id": 1,
            "text": "x" * 50_001,
        })
        self.assertEqual(oversized_text.status_code, 400, oversized_text.get_json())

        boolean_id = self.client.post("/api/analysis/quick-check", json={
            "brand_id": True,
            "text": "A valid-sized draft.",
        })
        self.assertEqual(boolean_id.status_code, 400, boolean_id.get_json())

        too_many_traits = self.client.post("/api/brands", json={
            "name": "Too many traits",
            "voice_traits": [f"trait-{index}" for index in range(21)],
        })
        self.assertEqual(too_many_traits.status_code, 400, too_many_traits.get_json())

    def test_parent_brand_is_checked_for_content_review_and_child_deletion(self):
        first_brand_id = self.create_brand("First brand")
        second_brand_id = self.create_brand("Second brand")
        content = self.client.post("/api/content", json={
            "brand_id": first_brand_id,
            "title": "Private relationship check",
            "text": "Draft content.",
        }).get_json()
        content_id = content["content_id"]

        fact = self.client.post(
            f"/api/brands/{first_brand_id}/facts",
            json={"statement": "A fact for the first brand."},
        ).get_json()
        fact_id = fact["fact_id"]

        detail = self.client.get(
            f"/api/content/{content_id}?brand_id={second_brand_id}"
        )
        self.assertEqual(detail.status_code, 404, detail.get_json())

        review = self.client.post("/api/reviews", json={
            "content_id": content_id,
            "brand_id": second_brand_id,
            "reviewer_name": "Reviewer",
            "decision": "rejected",
        })
        self.assertEqual(review.status_code, 404, review.get_json())

        correction = self.client.post(
            f"/api/analysis/apply-corrections/{content_id}",
            json={"brand_id": second_brand_id, "corrected_text": "Changed text."},
        )
        self.assertEqual(correction.status_code, 404, correction.get_json())

        deletion = self.client.delete(
            f"/api/brands/facts/{fact_id}?brand_id={second_brand_id}"
        )
        self.assertEqual(deletion.status_code, 404, deletion.get_json())
        first_brand = self.client.get(f"/api/brands/{first_brand_id}").get_json()
        self.assertEqual(len(first_brand["facts"]), 1)

    def test_internal_database_errors_are_not_returned_to_public_api(self):
        with patch(
            "routes.brands.db_session",
            side_effect=RuntimeError("sensitive database diagnostics"),
        ):
            response = self.client.get("/api/brands")

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("sensitive database diagnostics", response.get_data(as_text=True))
        self.assertEqual(
            response.get_json()["error"],
            "Could not retrieve brands.",
        )

    def test_gemini_analysis_and_rewrite_limits_return_safe_429(self):
        rate_app = create_app({
            "TESTING": True,
            "DATABASE_URL": "",
            "DATABASE_PATH": str(Path(self.temp_dir.name) / "rate-limits.db"),
            "RATELIMIT_ENABLED": True,
            "RATELIMIT_STORAGE_URI": "memory://",
            "RATELIMIT_KEY_PREFIX": "rate-limit-test",
            "GEMINI_ANALYSIS_RATE_LIMIT": "1 per minute",
            "GEMINI_REWRITE_RATE_LIMIT": "1 per minute",
        })
        rate_client = rate_app.test_client()
        brand_response = rate_client.post(
            "/api/brands",
            json={"name": "Rate Limit Test Brand"},
        )
        self.assertEqual(brand_response.status_code, 200, brand_response.get_json())
        brand_id = brand_response.get_json()["brand_id"]

        with patch(
            "routes.analysis.analyzer.analyze_content",
            return_value=self.mock_audit_result("A compliant draft."),
        ) as analyze_content:
            first_analysis = rate_client.post("/api/analysis/quick-check", json={
                "brand_id": brand_id,
                "text": "A compliant draft.",
            })
            self.assertEqual(first_analysis.status_code, 200, first_analysis.get_json())

            rejected_audit = rate_client.post("/api/analysis/audit", json={
                "brand_id": brand_id,
                "text": "Another draft.",
            })

        self.assertEqual(rejected_audit.status_code, 429, rejected_audit.get_json())
        self.assertEqual(
            rejected_audit.get_json()["error"],
            "Too many requests. Please try again later.",
        )
        self.assertNotIn("GEMINI_API_KEY", rejected_audit.get_data(as_text=True))
        analyze_content.assert_called_once()
        with rate_app.app_context():
            conn = get_db_connection()
            try:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM content_items").fetchone()[0],
                    0,
                )
            finally:
                conn.close()

        with patch(
            "routes.analysis.analyzer.rewrite_content",
            return_value="A rewritten draft.",
        ) as rewrite_content:
            first_rewrite = rate_client.post("/api/analysis/rewrite", json={
                "brand_id": brand_id,
                "text": "A draft.",
            })
            rejected_rewrite = rate_client.post("/api/analysis/rewrite", json={
                "brand_id": brand_id,
                "text": "Another draft.",
            })

        self.assertEqual(first_rewrite.status_code, 200, first_rewrite.get_json())
        self.assertEqual(rejected_rewrite.status_code, 429, rejected_rewrite.get_json())
        self.assertEqual(
            rejected_rewrite.get_json()["error"],
            "Too many requests. Please try again later.",
        )
        rewrite_content.assert_called_once()

    def test_production_rate_limit_storage_accepts_redis_environment_variables(self):
        configured_uris = (
            ("RATELIMIT_STORAGE_URI", "redis://redis.example:6379/0"),
            ("REDIS_URL", "rediss://redis.example:6380/0"),
        )
        for variable_name, redis_uri in configured_uris:
            with self.subTest(variable=variable_name):
                test_config = {
                    "TESTING": True,
                    "APP_ENV": "production",
                    "DATABASE_URL": "",
                    "DATABASE_PATH": str(
                        Path(self.temp_dir.name) / f"{variable_name}.db"
                    ),
                    "RATELIMIT_STORAGE_URI": None,
                    "REDIS_URL": None,
                }
                test_config[variable_name] = redis_uri
                with patch("app.init_db"), patch("app.limiter.init_app"):
                    production_app = create_app(test_config)
                self.assertEqual(
                    production_app.config["RATELIMIT_STORAGE_URI"],
                    redis_uri,
                )

        with patch("app.init_db"), patch("app.limiter.init_app"):
            production_app = create_app({
                "TESTING": True,
                "APP_ENV": "production",
                "DATABASE_URL": "",
                "DATABASE_PATH": str(
                    Path(self.temp_dir.name) / "redis-url-overrides-memory.db"
                ),
                "RATELIMIT_STORAGE_URI": "memory://",
                "REDIS_URL": "redis://redis.example:6379/0",
            })
        self.assertEqual(
            production_app.config["RATELIMIT_STORAGE_URI"],
            "redis://redis.example:6379/0",
        )

    def test_production_rate_limit_storage_rejects_missing_or_memory_storage(self):
        invalid_uris = (None, "memory://")
        for storage_uri in invalid_uris:
            with self.subTest(storage_uri=storage_uri):
                with patch("app.init_db"), patch("app.limiter.init_app"):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "Production rate limiting requires",
                    ):
                        create_app({
                            "TESTING": True,
                            "APP_ENV": "production",
                            "DATABASE_URL": "",
                            "DATABASE_PATH": str(
                                Path(self.temp_dir.name) / "missing-redis.db"
                            ),
                            "RATELIMIT_STORAGE_URI": storage_uri,
                            "REDIS_URL": None,
                        })

    def test_development_rate_limit_storage_defaults_to_memory(self):
        with patch("app.init_db"), patch("app.limiter.init_app"):
            development_app = create_app({
                "TESTING": True,
                "APP_ENV": "development",
                "DATABASE_URL": "",
                "DATABASE_PATH": str(
                    Path(self.temp_dir.name) / "development-rate-limits.db"
                ),
                "RATELIMIT_STORAGE_URI": None,
                "REDIS_URL": None,
            })
        self.assertEqual(
            development_app.config["RATELIMIT_STORAGE_URI"],
            "memory://",
        )

    @staticmethod
    def mock_audit_result(text):
        return {
            "score": 90,
            "risk_level": "LOW",
            "sub_scores": {
                "terminology": 90,
                "facts": 90,
                "tone": 90,
                "claims": 90,
                "accessibility": 90,
            },
            "issues": [],
            "summary": "Mocked audit result.",
            "corrected_text": text,
            "engine_used": "RULE_BASED_FALLBACK",
        }

    def test_audit_rejects_stale_current_version_without_creating_version(self):
        brand_id = self.create_brand()
        with patch(
            "routes.analysis.analyzer.analyze_content",
            return_value=self.mock_audit_result("Version one."),
        ) as analyze_content:
            initial_audit = self.client.post("/api/analysis/audit", json={
                "brand_id": brand_id,
                "text": "Version one.",
            })
            self.assertEqual(initial_audit.status_code, 200, initial_audit.get_json())
            content_id = initial_audit.get_json()["content_id"]
            before_stale_request = self.database_snapshot()

            stale_audit = self.client.post("/api/analysis/audit", json={
                "content_id": content_id,
                "current_version": 2,
                "brand_id": brand_id,
                "text": "Stale edit.",
            })

        self.assertEqual(stale_audit.status_code, 409, stale_audit.get_json())
        self.assertEqual(stale_audit.get_json()["current_version"], 1)
        self.assertEqual(self.database_snapshot(), before_stale_request)
        self.assertEqual(analyze_content.call_count, 1)

    def test_audit_saves_new_version_when_current_version_matches(self):
        brand_id = self.create_brand()
        with patch(
            "routes.analysis.analyzer.analyze_content",
            side_effect=[
                self.mock_audit_result("Version one."),
                self.mock_audit_result("Version two."),
            ],
        ):
            initial_audit = self.client.post("/api/analysis/audit", json={
                "brand_id": brand_id,
                "text": "Version one.",
            })
            self.assertEqual(initial_audit.status_code, 200, initial_audit.get_json())
            content_id = initial_audit.get_json()["content_id"]

            updated_audit = self.client.post("/api/analysis/audit", json={
                "content_id": content_id,
                "current_version": initial_audit.get_json()["version"],
                "brand_id": brand_id,
                "text": "Version two.",
            })

        self.assertEqual(updated_audit.status_code, 200, updated_audit.get_json())
        self.assertEqual(updated_audit.get_json()["version"], 2)
        detail = self.client.get(f"/api/content/{content_id}").get_json()
        self.assertEqual(len(detail["versions"]), 2)
        self.assertEqual(
            detail["versions"][0]["text"],
            "Version two.",
        )

    def test_audit_logs_total_duration_without_request_content(self):
        brand_id = self.create_brand()
        source_text = "Sensitive audit content must not appear in logs."
        with patch(
            "routes.analysis.analyzer.analyze_content",
            return_value=self.mock_audit_result(source_text),
        ):
            with self.assertLogs("routes.analysis", level="INFO") as logs:
                response = self.client.post("/api/analysis/audit", json={
                    "brand_id": brand_id,
                    "text": source_text,
                })

        self.assertEqual(response.status_code, 200, response.get_json())
        messages = "\n".join(logs.output)
        self.assertRegex(
            messages,
            r"Gemini audit total_duration_ms=\d+\.\d+ http_status=200",
        )
        self.assertNotIn(source_text, messages)
        self.assertNotIn("GEMINI_API_KEY", messages)

    def test_audit_logs_unconfigured_fallback_reason(self):
        brand_id = self.create_brand()
        source_text = "Sensitive text for an unconfigured audit."
        with self.assertLogs("services.analyzer", level="INFO") as logs:
            response = self.client.post("/api/analysis/audit", json={
                "brand_id": brand_id,
                "text": source_text,
            })

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(
            response.get_json()["result"]["engine_used"],
            "RULE_BASED_FALLBACK",
        )
        messages = "\n".join(logs.output)
        self.assertIn("final_fallback_reason=not_configured", messages)
        self.assertNotIn(source_text, messages)
        self.assertNotIn("GEMINI_API_KEY", messages)

    def test_audit_exception_logs_only_sanitized_category(self):
        brand_id = self.create_brand()
        sensitive_text = "Sensitive audit content and credentials."
        with patch(
            "routes.analysis.analyzer.analyze_content",
            return_value=self.mock_audit_result(sensitive_text),
        ), patch(
            "routes.analysis.db_session",
            side_effect=RuntimeError(sensitive_text),
        ):
            with self.assertLogs("routes.analysis", level="ERROR") as logs:
                response = self.client.post("/api/analysis/audit", json={
                    "brand_id": brand_id,
                    "text": sensitive_text,
                })

        self.assertEqual(response.status_code, 500)
        messages = "\n".join(logs.output)
        self.assertIn("exception_category=RuntimeError", messages)
        self.assertNotIn(sensitive_text, messages)
        self.assertNotIn("GEMINI_API_KEY", messages)

    def test_analysis_route_exceptions_log_only_sanitized_categories(self):
        brand_id = self.create_brand()
        source_text = "Stored source content."
        audit = self.client.post("/api/analysis/audit", json={
            "brand_id": brand_id,
            "text": source_text,
        })
        self.assertEqual(audit.status_code, 200, audit.get_json())
        content_id = audit.get_json()["content_id"]
        sensitive_data = (
            "SENSITIVE_USER_PROMPT_GEMINI_API_KEY=fake-test-secret"
        )
        routes = (
            (
                "quick-check",
                "routes.analysis.analyzer.analyze_content",
                lambda: self.client.post("/api/analysis/quick-check", json={
                    "brand_id": brand_id,
                    "text": sensitive_data,
                }),
                "Could not complete the content check.",
            ),
            (
                "rewrite",
                "routes.analysis.analyzer.rewrite_content",
                lambda: self.client.post("/api/analysis/rewrite", json={
                    "brand_id": brand_id,
                    "text": sensitive_data,
                }),
                "Could not generate an AI rewrite.",
            ),
            (
                "visual-check",
                "routes.analysis.analyzer.analyze_visual_content",
                lambda: self.client.post(
                    "/api/analysis/visual-check",
                    data={
                        "brand_id": str(brand_id),
                        "file": (
                            BytesIO(b"\x89PNG\r\n\x1a\nmock image"),
                            "creative.png",
                            "image/png",
                        ),
                    },
                    content_type="multipart/form-data",
                ),
                "Could not complete the visual review.",
            ),
            (
                "saved-content analysis",
                "routes.analysis.analyzer.analyze_content",
                lambda: self.client.post(
                    f"/api/analysis/run/{content_id}"
                ),
                "Could not complete the content analysis.",
            ),
            (
                "Apply",
                "routes.analysis.analyzer.analyze_content",
                lambda: self.client.post(
                    f"/api/analysis/apply-corrections/{content_id}",
                    json={
                        "brand_id": brand_id,
                        "corrected_text": sensitive_data,
                    },
                ),
                "Could not apply the requested correction.",
            ),
        )

        for route_name, patch_target, request, expected_error in routes:
            with self.subTest(route=route_name):
                with patch(
                    patch_target,
                    side_effect=RuntimeError(sensitive_data),
                ):
                    with self.assertLogs(
                        "routes.analysis", level="ERROR"
                    ) as logs:
                        response = request()

                self.assertEqual(response.status_code, 500)
                self.assertEqual(response.get_json()["error"], expected_error)
                messages = "\n".join(logs.output)
                self.assertIn("exception_category=RuntimeError", messages)
                self.assertNotIn(sensitive_data, messages)
                self.assertNotIn("fake-test-secret", messages)
                self.assertNotIn("Traceback", messages)

    def test_app_exception_handlers_log_only_sanitized_categories(self):
        from werkzeug.exceptions import InternalServerError

        sensitive_data = "sensitive-log-sentinel"

        def raise_internal_error():
            raise InternalServerError(description=sensitive_data)

        def raise_unhandled_error():
            raise RuntimeError(sensitive_data)

        self.app.add_url_rule(
            "/test/internal-error",
            endpoint="test_internal_error",
            view_func=raise_internal_error,
        )
        self.app.add_url_rule(
            "/test/unhandled-error",
            endpoint="test_unhandled_error",
            view_func=raise_unhandled_error,
        )
        self.app.config["PROPAGATE_EXCEPTIONS"] = False

        self.assert_sanitized_log(
            "app",
            lambda: self.client.get("/test/internal-error"),
            500,
            "Internal server error",
            exception_category="InternalServerError",
        )
        self.assert_sanitized_log(
            "app",
            lambda: self.client.get("/test/unhandled-error"),
            500,
            "An unexpected server error occurred.",
        )

    def test_brand_route_exceptions_log_only_sanitized_categories(self):
        sensitive_data = "sensitive-log-sentinel"
        requests = (
            (
                "get brands",
                lambda: self.client.get("/api/brands"),
                "Could not retrieve brands.",
            ),
            (
                "delete brand",
                lambda: self.client.delete("/api/brands/1"),
                "Could not delete the brand.",
            ),
            (
                "get brand details",
                lambda: self.client.get("/api/brands/1"),
                "Could not retrieve brand details.",
            ),
            (
                "create brand",
                lambda: self.client.post(
                    "/api/brands",
                    json={"name": sensitive_data},
                ),
                "Could not create the brand.",
            ),
            (
                "update brand",
                lambda: self.client.put(
                    "/api/brands/1",
                    json={"name": sensitive_data},
                ),
                "Could not update the brand.",
            ),
            (
                "add fact",
                lambda: self.client.post(
                    "/api/brands/1/facts",
                    json={"statement": sensitive_data},
                ),
                "Could not add the brand fact.",
            ),
            (
                "delete fact",
                lambda: self.client.delete(
                    "/api/brands/facts/1?brand_id=1",
                ),
                "Could not delete the brand fact.",
            ),
            (
                "add terminology",
                lambda: self.client.post(
                    "/api/brands/1/terminology",
                    json={"term": "disallowed", "replacement": sensitive_data},
                ),
                "Could not add the terminology rule.",
            ),
            (
                "delete terminology",
                lambda: self.client.delete(
                    "/api/brands/terminology/1?brand_id=1",
                ),
                "Could not delete the terminology rule.",
            ),
            (
                "update visual rules",
                lambda: self.client.put(
                    "/api/brands/1/visual-rules",
                    json={"logo_rules": sensitive_data},
                ),
                "Could not update visual guidelines.",
            ),
            (
                "add claim rule",
                lambda: self.client.post(
                    "/api/brands/1/claims",
                    json={
                        "rule_name": "Claim review",
                        "pattern_or_trigger": "guaranteed",
                        "explanation": sensitive_data,
                    },
                ),
                "Could not add the claim rule.",
            ),
            (
                "delete claim rule",
                lambda: self.client.delete(
                    "/api/brands/claims/1?brand_id=1",
                ),
                "Could not delete the claim rule.",
            ),
        )

        with patch(
            "routes.brands.db_session",
            side_effect=RuntimeError(sensitive_data),
        ):
            for route_name, request, expected_error in requests:
                with self.subTest(route=route_name):
                    self.assert_sanitized_log(
                        "routes.brands",
                        request,
                        500,
                        expected_error,
                    )

    def test_content_route_exceptions_log_only_sanitized_categories(self):
        sensitive_data = "sensitive-log-sentinel"
        with patch(
            "routes.content.extract_text_from_file",
            side_effect=RuntimeError(sensitive_data),
        ):
            self.assert_sanitized_log(
                "routes.content",
                lambda: self.client.post(
                    "/api/content/upload",
                    data={
                        "file": (
                            BytesIO(sensitive_data.encode("utf-8")),
                            "sensitive.txt",
                            "text/plain",
                        ),
                    },
                    content_type="multipart/form-data",
                ),
                400,
                "Could not read the uploaded file.",
            )

        requests = (
            (
                "list content",
                lambda: self.client.get("/api/content?brand_id=1"),
                "Could not retrieve content.",
            ),
            (
                "get content detail",
                lambda: self.client.get("/api/content/1"),
                "Could not retrieve content details.",
            ),
            (
                "create content",
                lambda: self.client.post(
                    "/api/content",
                    json={"brand_id": 1, "text": sensitive_data},
                ),
                "Could not create content.",
            ),
        )
        with patch(
            "routes.content.db_session",
            side_effect=RuntimeError(sensitive_data),
        ):
            for route_name, request, expected_error in requests:
                with self.subTest(route=route_name):
                    self.assert_sanitized_log(
                        "routes.content",
                        request,
                        500,
                        expected_error,
                    )

    def test_review_route_exceptions_log_only_sanitized_categories(self):
        sensitive_data = "sensitive-log-sentinel"
        requests = (
            (
                "submit review",
                lambda: self.client.post(
                    "/api/reviews",
                    json={
                        "content_id": 1,
                        "reviewer_name": sensitive_data,
                        "decision": "rejected",
                    },
                ),
                "Could not record the review decision.",
            ),
            (
                "get content reviews",
                lambda: self.client.get("/api/reviews/content/1"),
                "Could not retrieve content reviews.",
            ),
            (
                "get recent reviews",
                lambda: self.client.get("/api/reviews/recent"),
                "Could not retrieve recent reviews.",
            ),
        )
        with patch(
            "routes.reviews.db_session",
            side_effect=RuntimeError(sensitive_data),
        ):
            for route_name, request, expected_error in requests:
                with self.subTest(route=route_name):
                    self.assert_sanitized_log(
                        "routes.reviews",
                        request,
                        500,
                        expected_error,
                    )

    def test_history_route_exceptions_and_warnings_log_only_categories(self):
        sensitive_data = "sensitive-log-sentinel"
        error_requests = (
            (
                "dashboard stats",
                lambda: self.client.get("/api/history/dashboard-stats"),
                "Could not retrieve dashboard statistics.",
            ),
            (
                "export audit report",
                lambda: self.client.get("/api/history/export/1"),
                "Could not export the audit report.",
            ),
        )
        with patch(
            "routes.history.db_session",
            side_effect=RuntimeError(sensitive_data),
        ):
            for route_name, request, expected_error in error_requests:
                with self.subTest(route=route_name):
                    self.assert_sanitized_log(
                        "routes.history",
                        request,
                        500,
                        expected_error,
                    )

        brand_id = self.create_brand()
        with patch(
            "routes.analysis.analyzer.analyze_content",
            return_value=self.mock_audit_result(sensitive_data),
        ):
            audit = self.client.post("/api/analysis/audit", json={
                "brand_id": brand_id,
                "text": sensitive_data,
            })
        self.assertEqual(audit.status_code, 200, audit.get_json())
        audit_content_id = audit.get_json()["content_id"]

        with patch(
            "routes.history.json.loads",
            side_effect=json.JSONDecodeError(sensitive_data, "", 0),
        ):
            self.assert_sanitized_log(
                "routes.history",
                lambda: self.client.get("/api/history/dashboard-stats"),
                200,
                level="WARNING",
                exception_category="JSONDecodeError",
            )
            self.assert_sanitized_log(
                "routes.history",
                lambda: self.client.get(
                    f"/api/history/export/{audit_content_id}"
                ),
                200,
                level="WARNING",
                exception_category="JSONDecodeError",
            )

    def test_system_status_exception_logs_only_sanitized_category(self):
        sensitive_data = "sensitive-log-sentinel"
        with patch(
            "routes.analysis.analyzer.ai_service.is_configured",
            side_effect=RuntimeError(sensitive_data),
        ):
            self.assert_sanitized_log(
                "routes.analysis",
                lambda: self.client.get("/api/analysis/system-status"),
                500,
                "Could not retrieve AI service status.",
            )

    def test_rewrite_route_returns_preview_without_saving(self):
        brand_id = self.create_brand()
        before = self.database_snapshot()
        analyzer.ai_service.gemini_key = "unit-test-key"
        client = Mock()
        client.models.generate_content.return_value.text = json.dumps({
            "rewritten_text": "A brand-aligned rewrite."
        })

        with patch("services.ai_service.genai.Client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            response = self.client.post("/api/analysis/rewrite", json={
                "brand_id": brand_id,
                "text": "Original content.",
                "content_type": "Social Post",
                "channel": "Website",
            })

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json(), {
            "success": True,
            "rewritten_text": "A brand-aligned rewrite.",
            "engine_used": "AI_GEMINI",
            "saved": False,
        })
        self.assertEqual(self.database_snapshot(), before)
        client.models.generate_content.assert_called_once()

    def test_rewrite_post_is_registered_on_analysis_blueprint(self):
        rewrite_rules = [
            rule
            for rule in self.app.url_map.iter_rules()
            if rule.rule == "/api/analysis/rewrite"
        ]

        self.assertEqual(len(rewrite_rules), 1)
        self.assertEqual(rewrite_rules[0].endpoint, "analysis.rewrite_content")
        self.assertIn("POST", rewrite_rules[0].methods)

    def test_rewrite_route_validates_required_fields_and_missing_brand(self):
        missing_brand_id = self.client.post("/api/analysis/rewrite", json={
            "text": "Original content.",
        })
        self.assertEqual(missing_brand_id.status_code, 400, missing_brand_id.get_json())

        brand_id = self.create_brand()
        missing_text = self.client.post("/api/analysis/rewrite", json={
            "brand_id": brand_id,
            "text": " ",
        })
        self.assertEqual(missing_text.status_code, 400, missing_text.get_json())

        invalid_channel = self.client.post("/api/analysis/rewrite", json={
            "brand_id": brand_id,
            "text": "Original content.",
            "channel": [],
        })
        self.assertEqual(invalid_channel.status_code, 400, invalid_channel.get_json())

        unknown_brand = self.client.post("/api/analysis/rewrite", json={
            "brand_id": 999,
            "text": "Original content.",
        })
        self.assertEqual(unknown_brand.status_code, 404, unknown_brand.get_json())
        self.assertEqual(unknown_brand.get_json()["error"], "Brand not found.")

    def test_rewrite_route_reports_gemini_unavailable(self):
        brand_id = self.create_brand()
        analyzer.ai_service.gemini_key = "unit-test-key"
        client = Mock()
        client.models.generate_content.side_effect = [
            errors.ServerError(503, {"message": "provider unavailable"})
            for _ in range(3)
        ]

        with patch("services.ai_service.genai.Client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            with patch("services.ai_service.time.sleep"):
                response = self.client.post("/api/analysis/rewrite", json={
                    "brand_id": brand_id,
                    "text": "Original content.",
                })

        self.assertEqual(response.status_code, 503, response.get_json())
        self.assertIn("temporarily unavailable after retries", response.get_json()["error"])
        self.assertIn("AI rewrite could not be completed", response.get_json()["error"])

        client.models.generate_content.side_effect = errors.ClientError(
            429,
            {"message": "quota exhausted"},
        )
        with patch("services.ai_service.genai.Client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            response = self.client.post("/api/analysis/rewrite", json={
                "brand_id": brand_id,
                "text": "Original content.",
            })

        self.assertEqual(response.status_code, 429, response.get_json())
        self.assertIn("quota or rate limit", response.get_json()["error"])
        self.assertEqual(client.models.generate_content.call_count, 4)

    def test_rewrite_route_reports_invalid_gemini_response(self):
        brand_id = self.create_brand()
        analyzer.ai_service.gemini_key = "unit-test-key"
        client = Mock()
        client.models.generate_content.return_value.text = json.dumps({
            "rewritten_text": "",
        })

        with patch("services.ai_service.genai.Client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            response = self.client.post("/api/analysis/rewrite", json={
                "brand_id": brand_id,
                "text": "Original content.",
            })

        self.assertEqual(response.status_code, 502, response.get_json())
        self.assertEqual(
            response.get_json()["error"],
            "AI rewrite response could not be validated.",
        )

    def test_gemini_rewrite_returns_validated_text_with_brand_guidelines(self):
        source_text = "A product that cures all conditions."
        rewritten_text = "A product made with carefully selected ingredients."
        brand = {
            "name": "Northstar",
            "industry": "Wellness",
            "primary_tone": "Clear and reassuring",
            "voice_traits": ["evidence-based", "approachable"],
        }
        facts = [{"category": "Ingredients", "statement": "Made with plant-based ingredients."}]
        terminology = [{
            "term": "cure",
            "replacement": "support",
            "replacement_text": "support",
            "reason": "Avoid medical promises.",
        }]
        claim_rules = [{
            "rule_name": "Medical claims",
            "pattern_or_trigger": r"\bcure\b",
            "severity": "critical",
            "required_disclaimer": "Claims must be substantiated.",
            "replacement_text": "support general wellness",
        }]
        client = Mock()
        client.models.generate_content.return_value.text = json.dumps({
            "rewritten_text": rewritten_text
        })
        service = AIService()
        service.gemini_key = "unit-test-key"

        with patch("services.ai_service.genai.Client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            result = service.rewrite_with_ai(
                source_text,
                brand,
                facts,
                terminology,
                claim_rules,
                {"logo_rules": "Use approved logo."},
                "Ad Copy",
                "Website",
            )

        self.assertEqual(result, rewritten_text)
        request = client.models.generate_content.call_args.kwargs
        self.assertEqual(request["model"], "gemini-3.6-flash")
        self.assertEqual(request["config"].response_mime_type, "application/json")
        self.assertIn("Northstar", request["contents"])
        self.assertIn("Clear and reassuring", request["contents"])
        self.assertIn("Made with plant-based ingredients.", request["contents"])
        self.assertIn("Avoid medical promises.", request["contents"])
        self.assertIn("Medical claims", request["contents"])
        self.assertIn("Use approved logo.", request["contents"])
        self.assertIn(source_text, request["contents"])
        self.assertEqual(
            client_factory.call_args.kwargs["http_options"].timeout,
            60_000,
        )

    def test_gemini_visual_analysis_uses_image_input_and_marks_limits(self):
        image_bytes = b"\x89PNG\r\n\x1a\nmock image"
        visual_rules = {
            "primary_colors": [{"name": "Ocean Blue", "hex": "#126abc"}],
            "secondary_colors": [],
            "forbidden_colors": [{"name": "Competitor Red", "hex": "#cc2233"}],
            "logo_rules": "The approved logo must be visible.",
            "font_family": "Brand Sans",
            "contrast_minimum": "4.5:1",
        }
        requested_checks, _ = AIService._build_visual_checks(visual_rules)
        client = Mock()
        client.models.generate_content.return_value.text = json.dumps({
            "checks": [{
                "id": check["id"],
                "status": (
                    "concern" if check["id"] == "brand_palette" else "uncertain"
                ),
                "severity": (
                    "medium" if check["id"] == "brand_palette" else "undetermined"
                ),
                "explanation": (
                    "A large red area conflicts with the configured palette."
                    if check["id"] == "brand_palette"
                    else "The relevant detail cannot be confidently evaluated."
                ),
                "recommendation": (
                    "Use the configured brand palette."
                    if check["id"] == "brand_palette"
                    else "Manually verify this guideline."
                ),
                "confidence": "medium",
            } for check in requested_checks],
        })
        service = AIService()
        service.gemini_key = "unit-test-key"

        with patch("services.ai_service.genai.Client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            result = service.analyze_visual_content(
                image_bytes,
                "image/png",
                {"name": "Visual Test Brand"},
                visual_rules,
            )

        self.assertTrue(result["supported"])
        self.assertTrue(all(
            check["manual_review_required"] for check in result["checks"]
        ))
        self.assertEqual(result["checks"][1]["status"], "concern")
        self.assertEqual(result["checks"][1]["severity"], "medium")
        self.assertTrue(result["checks"][1]["explanation"])
        self.assertTrue(result["checks"][1]["recommendation"])
        self.assertEqual(result["checks"][3]["status"], "uncertain")
        check_ids = {check["id"] for check in result["checks"]}
        self.assertTrue({
            "logo_placement",
            "logo_distortion",
            "typography",
            "text_contrast",
        }.issubset(check_ids))
        not_evaluated_rules = {
            item["rule"] for item in result["not_evaluated"]
        }
        self.assertIn("Exact brand color values", not_evaluated_rules)
        self.assertIn("Exact font-family identification", not_evaluated_rules)
        self.assertIn("Exact WCAG contrast ratio", not_evaluated_rules)
        self.assertIn("Approved logo identity and exact clear-space measurement", not_evaluated_rules)

        request = client.models.generate_content.call_args.kwargs
        self.assertEqual(request["model"], "gemini-3.6-flash")
        self.assertEqual(request["config"].response_mime_type, "application/json")
        self.assertIn("Visual Test Brand", request["contents"][0])
        self.assertIn("The approved logo must be visible.", request["contents"][0])
        self.assertIn("do not claim exact hex", request["contents"][0])
        image_part = request["contents"][1]
        self.assertIsInstance(image_part, types.Part)
        self.assertEqual(image_part.inline_data.mime_type, "image/png")
        self.assertEqual(image_part.inline_data.data, image_bytes)
        self.assertEqual(
            client_factory.call_args.kwargs["http_options"].timeout,
            60_000,
        )

    def test_gemini_visual_analysis_rejects_invalid_response_and_unavailable_service(self):
        service = AIService()
        service.gemini_key = "unit-test-key"
        client = Mock()
        client.models.generate_content.return_value.text = json.dumps({
            "checks": [{
                "id": "unknown_rule",
                "status": "pass",
                "evidence": "The image looks fine.",
                "confidence": "high",
            }],
        })
        with patch("services.ai_service.genai.Client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            with self.assertRaises(GeminiVisualAnalysisValidationError):
                service.analyze_visual_content(
                    b"\x89PNG\r\n\x1a\nmock image",
                    "image/png",
                    {"name": "Visual Test Brand"},
                    {},
                )

        unconfigured_service = AIService()
        unconfigured_service.gemini_key = ""
        with self.assertRaises(GeminiVisualAnalysisUnavailableError):
            unconfigured_service.analyze_visual_content(
                b"\x89PNG\r\n\x1a\nmock image",
                "image/png",
                {"name": "Visual Test Brand"},
                {},
            )

    def test_gemini_visual_analysis_reports_quota_exhaustion_without_audit_result(self):
        service = AIService()
        service.gemini_key = "unit-test-key"
        client = Mock()
        client.models.generate_content.side_effect = [
            errors.ClientError(429, {"message": "quota exhausted"})
            for _ in range(3)
        ]

        with patch("services.ai_service.genai.Client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            with patch("services.ai_service.time.sleep"):
                with self.assertRaises(GeminiVisualAnalysisUnavailableError) as raised:
                    service.analyze_visual_content(
                        b"\x89PNG\r\n\x1a\nmock image",
                        "image/png",
                        {"name": "Visual Test Brand"},
                        {},
                    )

        self.assertIn("quota or rate limit", str(raised.exception))
        self.assertEqual(client.models.generate_content.call_count, 1)

    def test_gemini_rewrite_raises_when_provider_is_unavailable(self):
        service = AIService()
        service.gemini_key = "unit-test-key"
        client = Mock()
        client.models.generate_content.side_effect = [
            errors.ServerError(503, {"message": "provider unavailable"})
            for _ in range(3)
        ]

        with patch("services.ai_service.genai.Client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            with patch("services.ai_service.time.sleep") as sleep:
                with self.assertLogs("services.ai_service", level="WARNING") as logs:
                    with self.assertRaises(GeminiUnavailableError):
                        service.rewrite_with_ai(
                            "Original content",
                            {"name": "Northstar"},
                            [],
                            [],
                            [],
                            {},
                            "Ad Copy",
                            "Website",
                        )

        self.assertEqual(client.models.generate_content.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.5, 1.0])
        self.assertIn("AI rewrite is unavailable", "\n".join(logs.output))

    def test_gemini_rewrite_rejects_invalid_response(self):
        service = AIService()
        service.gemini_key = "unit-test-key"
        client = Mock()
        client.models.generate_content.return_value.text = json.dumps({
            "rewritten_text": " ",
            "explanation": "Unexpected extra field.",
        })

        with patch("services.ai_service.genai.Client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            with self.assertLogs("services.ai_service", level="WARNING"):
                with self.assertRaises(GeminiRewriteValidationError):
                    service.rewrite_with_ai(
                        "Original content",
                        {"name": "Northstar"},
                        [],
                        [],
                        [],
                        {},
                        "Ad Copy",
                        "Website",
                    )

        client.models.generate_content.assert_called_once()

    def test_rewrite_preview_loads_guidelines_without_changing_database_records(self):
        brand_id = self.create_brand("Rewrite Preview Brand")
        self.client.post(f"/api/brands/{brand_id}/facts", json={
            "category": "Product",
            "statement": "Made with plant-based ingredients.",
        })
        self.client.post(f"/api/brands/{brand_id}/terminology", json={
            "term": "miracle",
            "replacement_text": "carefully formulated",
            "replacement": "carefully formulated",
        })
        self.client.post(f"/api/brands/{brand_id}/claims", json={
            "rule_name": "No unsupported outcomes",
            "pattern_or_trigger": r"\bguaranteed results\b",
            "replacement_text": "designed for everyday use",
        })
        with self.app.app_context():
            conn = get_db_connection()
            try:
                conn.execute(
                    "UPDATE brands SET primary_tone = ?, voice_traits = ? WHERE id = ?",
                    ("Warm and clear", json.dumps(["evidence-based"]), brand_id),
                )
                conn.commit()
            finally:
                conn.close()

        def database_snapshot():
            tables = (
                "brands",
                "brand_facts",
                "brand_terminology",
                "brand_visual_rules",
                "brand_claim_rules",
                "content_items",
                "content_versions",
                "analysis_results",
                "reviews",
            )
            with self.app.app_context():
                conn = get_db_connection()
                try:
                    return {
                        table: [
                            tuple(row)
                            for row in conn.execute(
                                f"SELECT * FROM {table} ORDER BY id"
                            ).fetchall()
                        ]
                        for table in tables
                    }
                finally:
                    conn.close()

        before = database_snapshot()
        rewrite_analyzer = GovernanceAnalyzer()
        rewrite_analyzer.ai_service.gemini_key = "unit-test-key"
        client = Mock()
        client.models.generate_content.return_value.text = json.dumps({
            "rewritten_text": "Plant-based ingredients for everyday wellness."
        })

        with patch("services.ai_service.genai.Client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            with self.app.app_context():
                result = rewrite_analyzer.rewrite_content(
                    brand_id,
                    "Our miracle product guarantees results.",
                    "Ad Copy",
                    "Website",
                )

        self.assertEqual(result, "Plant-based ingredients for everyday wellness.")
        prompt = client.models.generate_content.call_args.kwargs["contents"]
        self.assertIn("Warm and clear", prompt)
        self.assertIn("Made with plant-based ingredients.", prompt)
        self.assertIn("No unsupported outcomes", prompt)
        self.assertIn("carefully formulated", prompt)
        self.assertEqual(database_snapshot(), before)

    def test_invalid_request_shapes_and_field_types_return_client_errors(self):
        self.assertEqual(self.client.post("/api/brands", json=[]).status_code, 400)
        malformed_json = self.client.post(
            "/api/brands",
            data="{",
            content_type="application/json",
        )
        self.assertEqual(malformed_json.status_code, 400, malformed_json.get_json())
        brand_id = self.create_brand()

        invalid_requests = (
            ("/api/analysis/quick-check", {
                "brand_id": True,
                "text": "valid text",
            }),
            ("/api/analysis/audit", {
                "brand_id": brand_id,
                "text": "valid text",
                "channel": [],
            }),
            (f"/api/brands/{brand_id}/facts", {
                "statement": [],
            }),
            (f"/api/brands/{brand_id}/claims", []),
            ("/api/content", {
                "brand_id": brand_id,
                "title": [],
                "text": "valid text",
            }),
        )
        for path, payload in invalid_requests:
            with self.subTest(path=path):
                response = self.client.post(path, json=payload)
                self.assertEqual(response.status_code, 400, response.get_json())
                self.assertFalse(response.get_json()["success"])

    def test_ai_timeout_retries_then_returns_none_for_rule_fallback(self):
        service = AIService()
        service.gemini_key = "test-key-never-log"
        client = Mock()
        client.models.generate_content.side_effect = httpx2.TimeoutException(
            "sensitive audit text"
        )
        with patch("services.ai_service.genai.Client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            with patch("services.ai_service.time.sleep") as sleep:
                with self.assertLogs("services.ai_service", level="WARNING") as logs:
                    result = service.analyze_with_ai(
                        "Sample content",
                        {"name": "Reliability Brand"},
                        [],
                        [],
                        [],
                        {},
                        "Ad Copy",
                        "Digital Ads",
                    )
        self.assertIsNone(result)
        self.assertEqual(client.models.generate_content.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.5, 1.0])
        messages = "\n".join(logs.output)
        self.assertEqual(messages.count("duration_ms="), 3)
        self.assertIn("exception_category=TimeoutException", messages)
        self.assertIn("final_fallback_reason=transport_error", messages)
        self.assertNotIn("sensitive audit text", messages)
        self.assertNotIn("Sample content", messages)
        self.assertNotIn("test-key-never-log", messages)

    def test_ai_retries_transient_http_error_and_recovers(self):
        ai_response = {
            "score": 90,
            "sub_scores": {
                "terminology": 90,
                "facts": 90,
                "tone": 90,
                "claims": 90,
                "accessibility": 90,
            },
            "issues": [],
            "corrected_text": "Sample content",
            "summary": "No policy issues detected.",
        }
        for status_code in (504, 503, 429):
            with self.subTest(status_code=status_code):
                service = AIService()
                service.gemini_key = "test-key-never-log"
                client = Mock()
                client.models.generate_content.side_effect = [
                    errors.ServerError(
                        status_code,
                        {"message": "sensitive audit text"},
                    ),
                    Mock(text=json.dumps(ai_response)),
                ]
                with patch("services.ai_service.genai.Client") as client_factory:
                    client_factory.return_value.__enter__.return_value = client
                    with patch("services.ai_service.time.sleep") as sleep:
                        with self.assertLogs(
                            "services.ai_service", level="INFO"
                        ) as logs:
                            result = service.analyze_with_ai(
                                "Sample content",
                                {"name": "Reliability Brand"},
                                [],
                                [],
                                [],
                                {},
                                "Ad Copy",
                                "Digital Ads",
                            )

                self.assertEqual(result["engine_used"], "AI_GEMINI")
                self.assertEqual(client.models.generate_content.call_count, 2)
                sleep.assert_called_once_with(0.5)
                messages = "\n".join(logs.output)
                self.assertIn(f"http_status={status_code}", messages)
                self.assertIn("exception_category=APIError", messages)
                self.assertIn("duration_ms=", messages)
                self.assertIn("exception_category=none outcome=success", messages)
                self.assertNotIn("sensitive audit text", messages)
                self.assertNotIn("Sample content", messages)
                self.assertNotIn("test-key-never-log", messages)

    def test_ai_logs_final_fallback_reason_for_invalid_analysis_schema(self):
        service = AIService()
        service.gemini_key = "test-key-never-log"
        client = Mock()
        client.models.generate_content.return_value.text = json.dumps({
            "score": 90,
            "issues": [],
        })

        with patch("services.ai_service.genai.Client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            with self.assertLogs("services.ai_service", level="WARNING") as logs:
                result = service.analyze_with_ai(
                    "Sample content",
                    {"name": "Reliability Brand"},
                    [],
                    [],
                    [],
                    {},
                    "Ad Copy",
                    "Digital Ads",
                )

        self.assertIsNone(result)
        messages = "\n".join(logs.output)
        self.assertIn(
            "final_fallback_reason=response_schema_validation_failed",
            messages,
        )
        self.assertNotIn("Sample content", messages)
        self.assertNotIn("test-key-never-log", messages)

    # NEW TEST: ReadError retry and recovery
    def test_ai_retries_read_error_and_recovers(self):
        ai_response = {
            "score": 90,
            "sub_scores": {
                "terminology": 90,
                "facts": 90,
                "tone": 90,
                "claims": 90,
                "accessibility": 90,
            },
            "issues": [],
            "corrected_text": "Sample content",
            "summary": "No policy issues detected.",
        }

        service = AIService()
        service.gemini_key = "test-key-never-log"
        client = Mock()
        client.models.generate_content.side_effect = [
            httpx2.ReadError("connection reset"),
            Mock(text=json.dumps(ai_response)),
        ]

        with patch("services.ai_service.genai.Client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            with patch("services.ai_service.time.sleep") as sleep:
                with self.assertLogs(
                    "services.ai_service", level="WARNING"
                ) as logs:
                    result = service.analyze_with_ai(
                        "Sample content",
                        {"name": "Reliability Brand"},
                        [],
                        [],
                        [],
                        {},
                        "Ad Copy",
                        "Digital Ads",
                    )

        self.assertEqual(result["engine_used"], "AI_GEMINI")
        self.assertEqual(client.models.generate_content.call_count, 2)
        sleep.assert_called_once_with(0.5)
        self.assertIn("connection failed", "\n".join(logs.output))
        self.assertNotIn("test-key-never-log", "\n".join(logs.output))

    def test_analysis_uses_rule_fallback_after_gemini_retries_are_exhausted(self):
        brand_id = self.create_brand()
        analyzer.ai_service.gemini_key = "test-key-never-log"
        client = Mock()
        client.models.generate_content.side_effect = [
            errors.ServerError(504, {"message": "sensitive audit text"})
            for _ in range(3)
        ]

        with patch("services.ai_service.genai.Client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            with patch("services.ai_service.time.sleep") as sleep:
                with self.assertLogs("services.ai_service", level="WARNING") as logs:
                    response = self.client.post("/api/analysis/quick-check", json={
                        "brand_id": brand_id,
                        "text": "Content for the transient-failure check.",
                    })

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(
            response.get_json()["result"]["engine_used"],
            "RULE_BASED_FALLBACK",
        )
        self.assertEqual(client.models.generate_content.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.5, 1.0])
        messages = "\n".join(logs.output)
        self.assertIn("HTTP 504", messages)
        self.assertIn("gateway timeout", messages.lower())
        self.assertIn("final_fallback_reason=http_error", messages)
        self.assertEqual(messages.count("duration_ms="), 3)
        self.assertNotIn("sensitive audit text", messages)
        self.assertNotIn("test-key-never-log", messages)
        http_options = client_factory.call_args.kwargs["http_options"]
        self.assertEqual(http_options.timeout, 60_000)

    def test_ai_does_not_retry_permanent_api_errors(self):
        service = AIService()
        service.gemini_key = "test-key-never-log"
        client = Mock()
        client.models.generate_content.side_effect = errors.ClientError(
            403, {"message": "sensitive audit text"}
        )
        with patch("services.ai_service.genai.Client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            with patch("services.ai_service.time.sleep") as sleep:
                with self.assertLogs("services.ai_service", level="WARNING") as logs:
                    result = service.analyze_with_ai(
                        "Sample content",
                        {"name": "Reliability Brand"},
                        [],
                        [],
                        [],
                        {},
                        "Ad Copy",
                        "Digital Ads",
                    )

        self.assertIsNone(result)
        client.models.generate_content.assert_called_once()
        sleep.assert_not_called()
        messages = "\n".join(logs.output)
        self.assertIn("HTTP 403", messages)
        self.assertIn("not retried", messages.lower())
        self.assertIn("final_fallback_reason=http_error", messages)
        self.assertIn("duration_ms=", messages)
        self.assertNotIn("sensitive audit text", messages)
        self.assertNotIn("Sample content", messages)
        self.assertNotIn("test-key-never-log", messages)

    def test_audit_and_draft_preserve_source_whitespace_and_offsets(self):
        brand_id = self.create_brand()
        term_response = self.client.post(
            f"/api/brands/{brand_id}/terminology",
            json={"term": "cheap", "replacement_text": "affordable"},
        )
        self.assertEqual(term_response.status_code, 200, term_response.get_json())

        source_text = " \ncheap \t"
        quick_check = self.client.post("/api/analysis/quick-check", json={
            "brand_id": brand_id,
            "text": source_text,
        })
        self.assertEqual(quick_check.status_code, 200, quick_check.get_json())
        issue = next(
            issue for issue in quick_check.get_json()["result"]["issues"]
            if issue["category"] == "Terminology"
        )
        self.assertEqual(issue["location"], {"start": 2, "end": 7})

        audit = self.client.post("/api/analysis/audit", json={
            "brand_id": brand_id,
            "title": "Whitespace preservation",
            "text": source_text,
        })
        self.assertEqual(audit.status_code, 200, audit.get_json())
        content_id = audit.get_json()["content_id"]
        details = self.client.get(f"/api/content/{content_id}").get_json()
        self.assertEqual(details["content"]["original_text"], source_text)
        self.assertEqual(details["versions"][0]["text"], source_text)

        draft = self.client.post("/api/content", json={
            "brand_id": brand_id,
            "title": "Whitespace draft",
            "text": source_text,
        })
        self.assertEqual(draft.status_code, 200, draft.get_json())
        draft_details = self.client.get(
            f"/api/content/{draft.get_json()['content_id']}"
        ).get_json()
        self.assertEqual(draft_details["content"]["original_text"], source_text)
        self.assertEqual(draft_details["versions"][0]["text"], source_text)

    def test_dashboard_uses_only_each_contents_latest_analysis(self):
        brand_id = self.create_brand()
        term_response = self.client.post(
            f"/api/brands/{brand_id}/terminology",
            json={"term": "cheap", "replacement_text": "affordable"},
        )
        self.assertEqual(term_response.status_code, 200, term_response.get_json())

        first_audit = self.client.post("/api/analysis/audit", json={
            "brand_id": brand_id,
            "title": "Corrected later",
            "text": "cheap",
        }).get_json()
        updated_audit = self.client.post("/api/analysis/audit", json={
            "brand_id": brand_id,
            "content_id": first_audit["content_id"],
            "current_version": first_audit["version"],
            "title": "Corrected later",
            "text": "affordable",
        })
        self.assertEqual(updated_audit.status_code, 200, updated_audit.get_json())

        stats = self.client.get(
            f"/api/history/dashboard-stats?brand_id={brand_id}"
        ).get_json()
        self.assertEqual(stats["violation_categories"]["Terminology"], 0)
        self.assertEqual(stats["top_prohibited_terms"], [])

        second_audit = self.client.post("/api/analysis/audit", json={
            "brand_id": brand_id,
            "title": "Current violation",
            "text": "cheap",
        })
        self.assertEqual(second_audit.status_code, 200, second_audit.get_json())
        stats = self.client.get(
            f"/api/history/dashboard-stats?brand_id={brand_id}"
        ).get_json()
        self.assertEqual(stats["violation_categories"]["Terminology"], 1)
        self.assertEqual(stats["top_prohibited_terms"], [{"term": "cheap", "count": 1}])

    def test_review_is_bound_to_the_analyzed_version_and_records_its_version(self):
        brand_id = self.create_brand()
        first_audit = self.client.post("/api/analysis/audit", json={
            "brand_id": brand_id,
            "title": "Versioned review",
            "text": "First draft.",
        }).get_json()
        content_id = first_audit["content_id"]

        first_review = self.client.post("/api/reviews", json={
            "content_id": content_id,
            "content_version": first_audit["version"],
            "reviewer_name": "Reviewer",
            "decision": "approved",
        })
        self.assertEqual(first_review.status_code, 200, first_review.get_json())
        detail = self.client.get(f"/api/content/{content_id}").get_json()
        self.assertEqual(detail["reviews"][0]["content_version"], 1)

        second_audit = self.client.post("/api/analysis/audit", json={
            "brand_id": brand_id,
            "content_id": content_id,
            "current_version": first_audit["version"],
            "title": "Versioned review",
            "text": "Updated draft.",
        }).get_json()
        stale_review = self.client.post("/api/reviews", json={
            "content_id": content_id,
            "content_version": first_audit["version"],
            "reviewer_name": "Reviewer",
            "decision": "approved",
        })
        self.assertEqual(stale_review.status_code, 409, stale_review.get_json())
        self.assertEqual(
            self.client.get(f"/api/content/{content_id}").get_json()["reviews"],
            detail["reviews"],
        )

        legacy_review = self.client.post("/api/reviews", json={
            "content_id": content_id,
            "reviewer_name": "Legacy Client",
            "decision": "changes_requested",
        })
        self.assertEqual(legacy_review.status_code, 200, legacy_review.get_json())
        latest_detail = self.client.get(f"/api/content/{content_id}").get_json()
        self.assertEqual(latest_detail["reviews"][0]["content_version"], second_audit["version"])

    def test_invalid_recent_review_limits_and_missing_guidelines_are_reported(self):
        for invalid_limit in ("abc", "0", "101", "-1"):
            with self.subTest(limit=invalid_limit):
                response = self.client.get(f"/api/reviews/recent?limit={invalid_limit}")
                self.assertEqual(response.status_code, 400, response.get_json())

        valid_limit = self.client.get("/api/reviews/recent?limit=1")
        self.assertEqual(valid_limit.status_code, 200, valid_limit.get_json())

        missing_guidelines = (
            "/api/brands/facts/999?brand_id=1",
            "/api/brands/terminology/999?brand_id=1",
            "/api/brands/claims/999?brand_id=1",
        )
        for path in missing_guidelines:
            with self.subTest(path=path):
                response = self.client.delete(path)
                self.assertEqual(response.status_code, 404, response.get_json())
                self.assertFalse(response.get_json()["success"])


if __name__ == "__main__":
    unittest.main()
