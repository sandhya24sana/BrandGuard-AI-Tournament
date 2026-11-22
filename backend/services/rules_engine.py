
import json
import re
from typing import List, Dict, Any, Optional, Tuple

from services.correction_engine import (
    apply_corrections,
    legacy_claim_replacement,
    legacy_terminology_replacement,
    safe_replacement_text,
)

_FACT_STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "by",
    "can't", "cannot", "do", "does", "don't", "doesn't", "for", "from",
    "has", "hasn't", "have", "haven't", "in", "into", "is", "isn't", "it",
    "its", "never", "no", "not", "of", "on", "or", "our", "the", "their",
    "this", "to", "was", "wasn't", "were", "weren't", "with", "without",
    "won't",
})
_FACT_CONTEXT_WORDS = frozenset({
    "brand", "business", "company", "device", "formula", "organization",
    "platform", "product", "service", "system", "team",
})
_FACT_NEGATION = re.compile(
    r"\b(?:no|not|never|without|cannot|can't|don't|doesn't|isn't|aren't|"
    r"wasn't|weren't|won't|hasn't|haven't|free\s+from|free\s+of)\b",
    re.IGNORECASE,
)
_FACT_OPPOSITES = (
    (r"\bnatural\b", r"\b(?:synthetic|artificial)\b"),
    (r"\bsynthetic\b", r"\b(?:natural|organic)\b"),
    (r"\bavailable\b", r"\b(?:unavailable|out\s+of\s+stock)\b"),
    (r"\bapproved\b", r"\b(?:unapproved|rejected|not\s+approved)\b"),
    (r"\ballowed\b", r"\b(?:prohibited|forbidden|not\s+allowed)\b"),
    (r"\bactive\b", r"\binactive\b"),
    (r"\bsupports?\b", r"\bopposes?\b"),
)
_FACT_VALUE_RELATION = re.compile(
    r"\b(?P<relation>based\s+in|located\s+in|headquartered\s+in|"
    r"founded\s+in|made\s+in|manufactured\s+in|produced\s+in)\s+"
    r"(?P<value>[^.!?;\n]+)",
    re.IGNORECASE,
)
_VOICE_TRAIT_RULES = (
    (
        ("warm", "friendly", "approachable", "empathetic", "supportive"),
        r"\b(?:cold|hostile|rude|dismissive|uncaring)\b",
        "This wording conflicts with the configured warm, friendly voice.",
        "Use respectful, welcoming language that fits the configured voice.",
    ),
    (
        ("calm", "reassuring", "measured"),
        r"\b(?:act\s+now|hurry|urgent(?:ly)?|immediately|don't\s+miss\s+out)\b",
        "This urgency conflicts with the configured calm, reassuring voice.",
        "Replace pressure language with measured, informative wording.",
    ),
    (
        ("confident", "decisive"),
        r"\b(?:maybe|perhaps|possibly|might|i\s+guess|sort\s+of|kind\s+of)\b",
        "This hedge may conflict with the configured confident, decisive voice.",
        "State only verified conclusions directly, without unnecessary hedging.",
    ),
    (
        ("concise", "brief", "direct"),
        r"\b(?:in\s+order\s+to|at\s+this\s+point\s+in\s+time|"
        r"it\s+is\s+important\s+to\s+note\s+that|with\s+regard\s+to)\b",
        "This filler phrase conflicts with the configured concise, direct voice.",
        "Remove the filler phrase while preserving the intended meaning.",
    ),
    (
        ("precise", "evidence-based", "fact-based"),
        r"\b(?:guaranteed?|proven|always|never|100\s*%)\b",
        "This absolute wording may conflict with the configured evidence-based voice.",
        "Verify the statement is supported by an approved fact or qualify it.",
    ),
    (
        ("accessible", "clear", "plain-language", "plain language", "simple"),
        r"\b(?:idempotent|stochastic|orthogonal|amortization|"
        r"kubernetes|latency|api)\b",
        "This technical term may conflict with the configured accessible voice.",
        "Define the term or replace it with plain-language wording.",
    ),
)
_AUDIENCE_GROUPS = (
    ("children", r"\b(?:children|kids|minors)\b"),
    ("parents", r"\bparents?\b"),
    ("consumers", r"\b(?:consumers?|customers?|everyday\s+users?)\b"),
    (
        "professionals",
        r"\b(?:medical\s+)?(?:professionals?|clinicians?|doctors?|nurses?|"
        r"researchers?|research\s+teams?)\b",
    ),
    (
        "technical users",
        r"\b(?:developers?|engineers?|programmers?|software\s+teams?)\b",
    ),
    ("students", r"\b(?:students?|teachers?|educators?)\b"),
    (
        "business leaders",
        r"\b(?:executives?|business\s+owners?|managers?)\b",
    ),
    (
        "general audience",
        r"\b(?:general\s+(?:audience|public)|non[- ]specialists?|"
        r"beginners?|non[- ]technical\s+users?)\b",
    ),
)
_NON_SPECIALIST_AUDIENCE = re.compile(
    r"\b(?:general\s+(?:audience|public)|non[- ]specialists?|beginners?|"
    r"non[- ]technical\s+users?|everyday\s+users?|consumers?)\b",
    re.IGNORECASE,
)
_AUDIENCE_JARGON = re.compile(
    r"\b(?:idempotent|stochastic|orthogonal|amortization|"
    r"kubernetes|latency|api)\b",
    re.IGNORECASE,
)


def validate_claim_pattern(pattern: str) -> None:
    if len(pattern) > 128:
        raise ValueError("Claim patterns must be 128 characters or fewer")

    if (
        re.search(r"\([^)]*[+*{][^)]*\)[+*{]", pattern)
        or re.search(r"\([^)]*\|[^)]*\)[+*{]", pattern)
        or len(re.findall(r"\.\*[+*]|\.\+[+*]", pattern)) > 1
    ):
        raise ValueError(
            "Claim pattern contains an unsafe nested or ambiguous quantifier"
        )

    try:
        re.compile(pattern)
    except re.error as error:
        raise ValueError(f"Invalid claim pattern: {error}") from error


def _claim_pattern_variants(rule: Dict[str, Any], pattern: str) -> List[str]:
    rule_name = str(rule.get("rule_name") or "").casefold()
    is_medical_claim = "medical" in rule_name and "claim" in rule_name
    is_health_claim = "health" in rule_name and "claim" in rule_name

    if is_medical_claim or is_health_claim:
        # Normalize legacy stored alternatives in memory; do not rewrite saved rules.
        pattern = pattern.replace(r"\|", "|")

    patterns = [pattern]
    if is_medical_claim:
        patterns.append(
            r"\b(?:cure|treat|prevent)s?(?:\s+(?:diseases?|health problems?))?\b"
        )

    return list(dict.fromkeys(patterns))


class RuleBasedAnalyzer:
    """
    Deterministic rule-based brand governance engine.
    Detects terminology issues, fact inaccuracies, tone mismatches,
    unsupported claims, and readability/accessibility problems.
    Operates offline without requiring external AI keys.
    """

    def __init__(
        self,
        brand: Dict[str, Any],
        facts: List[Dict[str, Any]],
        terminology: List[Dict[str, Any]],
        visual_rules: Dict[str, Any] = None,
        claim_rules: List[Dict[str, Any]] = None,
    ):
        self.brand = brand
        self.facts = facts or []
        self.terminology = terminology or []
        self.visual_rules = visual_rules or {}
        self.claim_rules = claim_rules or []

    def analyze(
        self,
        text: str,
        content_type: str = "Ad Copy",
        channel: str = "Digital",
    ) -> Dict[str, Any]:
        issues: List[Dict[str, Any]] = []

        term_issues, term_penalty = self._check_terminology(text)
        issues.extend(term_issues)

        fact_issues, fact_penalty = self._check_facts(text)
        issues.extend(fact_issues)

        tone_issues, tone_penalty = self._check_tone(text, content_type)
        issues.extend(tone_issues)

        audience_issues, audience_penalty = self._check_audience(text)
        issues.extend(audience_issues)

        claim_issues, claim_penalty = self._check_claims(text)
        issues.extend(claim_issues)

        access_issues, access_penalty, readability_stats = (
            self._check_accessibility(text)
        )
        issues.extend(access_issues)

        score_terminology = max(0, 100 - term_penalty)
        score_facts = max(0, 100 - fact_penalty)
        score_tone = max(0, 100 - tone_penalty)
        score_claims = max(0, 100 - claim_penalty)
        score_accessibility = max(
            0, 100 - access_penalty - audience_penalty
        )

        category_scores = {
            "Terminology": "terminology",
            "Fact Accuracy": "facts",
            "Voice & Tone": "tone",
            "Compliance & Claims": "claims",
            "Accessibility & Clarity": "accessibility",
        }

        sub_scores = {
            "terminology": score_terminology,
            "facts": score_facts,
            "tone": score_tone,
            "claims": score_claims,
            "accessibility": score_accessibility,
        }

        for issue in issues:
            category_score = category_scores.get(issue.get("category"))

            if not category_score:
                continue

            severity = issue.get("severity", "").upper()
            ceiling = 49 if severity == "CRITICAL" else 74

            if severity in ("CRITICAL", "WARNING"):
                sub_scores[category_score] = min(
                    sub_scores[category_score], ceiling
                )

        overall_score = round(
            (sub_scores["facts"] * 0.30)
            + (sub_scores["claims"] * 0.25)
            + (sub_scores["terminology"] * 0.25)
            + (sub_scores["tone"] * 0.10)
            + (sub_scores["accessibility"] * 0.10)
        )

        overall_score = max(5, min(100, overall_score))

        severities = {
            issue.get("severity", "").upper() for issue in issues
        }

        if "CRITICAL" in severities:
            overall_score = min(overall_score, 49)
        elif "WARNING" in severities:
            overall_score = min(overall_score, 89)

        if overall_score >= 90:
            risk_level = "LOW"
            risk_badge = "Low Detected Risk - Human Review Required"
        elif overall_score >= 75:
            risk_level = "MODERATE"
            risk_badge = "Moderate Risk - Minor Revisions Needed"
        elif overall_score >= 50:
            risk_level = "HIGH"
            risk_badge = "High Risk - Substantial Issues Detected"
        else:
            risk_level = "CRITICAL"
            risk_badge = "Critical Risk - Immediate Remediation Required"

        corrected_text = self._generate_corrected_text(text, issues)
        summary = self._generate_summary(overall_score, risk_level, issues)

        return {
            "score": overall_score,
            "risk_level": risk_level,
            "risk_badge": risk_badge,
            "sub_scores": sub_scores,
            "readability": readability_stats,
            "issues": issues,
            "summary": summary,
            "corrected_text": corrected_text,
            "engine_used": "RULE_BASED_FALLBACK",
        }

    @staticmethod
    def _configured_values(value: Any) -> List[str]:
        if isinstance(value, list):
            return [item.strip() for item in value if isinstance(item, str) and item.strip()]
        if not isinstance(value, str) or not value.strip():
            return []

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [
                item.strip()
                for item in parsed
                if isinstance(item, str) and item.strip()
            ]
        if isinstance(parsed, str) and parsed.strip():
            return [parsed.strip()]
        return [item.strip() for item in value.split(",") if item.strip()]

    @staticmethod
    def _term_pattern(term: str) -> re.Pattern:
        escaped = re.escape(term.strip()).replace(r"\ ", r"\s+")
        return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)

    @classmethod
    def _fact_terms(cls, statement: str, configured_keywords: List[str]) -> List[str]:
        if configured_keywords:
            return list(dict.fromkeys(configured_keywords))
        return list(dict.fromkeys(
            token
            for token in re.findall(r"[\w'-]+", statement, re.UNICODE)
            if len(token) >= 3 and token.casefold() not in _FACT_STOP_WORDS
        ))

    @classmethod
    def _has_fact_evidence(
        cls,
        sentence: str,
        terms: List[str],
    ) -> bool:
        matched_terms = {
            term.casefold()
            for term in terms
            if cls._term_pattern(term).search(sentence)
        }
        return bool(matched_terms)

    @staticmethod
    def _is_negated_near(text: str, start: int) -> bool:
        tokens = list(re.finditer(r"[\w']+", text, re.UNICODE))
        anchor_index = next(
            (index for index, token in enumerate(tokens) if token.start() >= start),
            len(tokens),
        )
        first = max(0, anchor_index - 3)
        window = tokens[first:min(len(tokens), anchor_index + 2)]
        for index, token in enumerate(window):
            if token.group(0).casefold() == "not":
                following = window[index + 1].group(0).casefold() if index + 1 < len(window) else ""
                if following == "only":
                    continue
            if _FACT_NEGATION.fullmatch(token.group(0)):
                return True
            if token.group(0).casefold() == "free" and index + 1 < len(window):
                if window[index + 1].group(0).casefold() in {"from", "of"}:
                    return True
        return False

    @classmethod
    def _fact_contradiction(
        cls,
        statement: str,
        sentence: str,
    ) -> Optional[Tuple[str, int, int]]:
        approved_relations = list(_FACT_VALUE_RELATION.finditer(statement))
        stated_relations = list(_FACT_VALUE_RELATION.finditer(sentence))
        for approved in approved_relations:
            approved_value = re.split(
                r"\b(?:and|but|because|while|which)\b",
                approved.group("value"),
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip(" ,")
            approved_tokens = {
                token.casefold()
                for token in re.findall(r"[\w'-]+", approved_value, re.UNICODE)
                if token.casefold() not in _FACT_STOP_WORDS
            }
            for stated in stated_relations:
                if approved.group("relation").casefold() != stated.group("relation").casefold():
                    continue
                stated_value = re.split(
                    r"\b(?:and|but|because|while|which)\b",
                    stated.group("value"),
                    maxsplit=1,
                    flags=re.IGNORECASE,
                )[0].strip(" ,")
                stated_tokens = {
                    token.casefold()
                    for token in re.findall(r"[\w'-]+", stated_value, re.UNICODE)
                    if token.casefold() not in _FACT_STOP_WORDS
                }
                if (
                    approved_tokens
                    and stated_tokens
                    and not approved_tokens.issubset(stated_tokens)
                    and not stated_tokens.issubset(approved_tokens)
                ):
                    value_start, value_end = stated.span("value")
                    leading = len(stated.group("value")) - len(
                        stated.group("value").lstrip()
                    )
                    value_start += leading
                    value_end = value_start + len(stated_value)
                    return (
                        sentence[value_start:value_end],
                        value_start,
                        value_end,
                    )

        statement_tokens = [
            token for token in re.findall(r"[\w'-]+", statement, re.UNICODE)
            if (
                len(token) >= 3
                and token.casefold() not in _FACT_STOP_WORDS
                and token.casefold() not in _FACT_CONTEXT_WORDS
            )
        ]
        for token in dict.fromkeys(statement_tokens):
            pattern = cls._term_pattern(token)
            approved_matches = list(pattern.finditer(statement))
            stated_matches = list(pattern.finditer(sentence))
            for approved_match in approved_matches:
                approved_negated = cls._is_negated_near(
                    statement, approved_match.start()
                )
                for stated_match in stated_matches:
                    stated_negated = cls._is_negated_near(
                        sentence, stated_match.start()
                    )
                    if approved_negated != stated_negated:
                        return (
                            stated_match.group(0),
                            stated_match.start(),
                            stated_match.end(),
                        )

        for approved_pattern, contrary_pattern in _FACT_OPPOSITES:
            approved_match = re.search(approved_pattern, statement, re.IGNORECASE)
            contrary_match = re.search(contrary_pattern, sentence, re.IGNORECASE)
            if (
                approved_match
                and contrary_match
                and not re.search(approved_pattern, sentence, re.IGNORECASE)
            ):
                return (
                    contrary_match.group(0),
                    contrary_match.start(),
                    contrary_match.end(),
                )
            contrary_approved = re.search(
                contrary_pattern, statement, re.IGNORECASE
            )
            approved_in_sentence = re.search(
                approved_pattern, sentence, re.IGNORECASE
            )
            if (
                contrary_approved
                and approved_in_sentence
                and not re.search(contrary_pattern, sentence, re.IGNORECASE)
            ):
                return (
                    approved_in_sentence.group(0),
                    approved_in_sentence.start(),
                    approved_in_sentence.end(),
                )
        return None

    def _check_terminology(
        self, text: str
    ) -> Tuple[List[Dict[str, Any]], int]:
        issues = []
        penalty = 0

        for rule_index, item in enumerate(self.terminology):
            if not item.get("active", 1):
                continue

            rule_id = item.get("id")
            if rule_id is None:
                rule_id = rule_index

            term = item.get("term", "").strip()
            if not term:
                continue

            pattern = re.compile(
                rf"(?<!\w){re.escape(term)}(?!\w)",
                re.IGNORECASE,
            )

            matches = list(pattern.finditer(text))
            replacement_text = legacy_terminology_replacement(item)

            for match in matches:
                matched_snippet = match.group(0)
                start, end = match.span()
                severity = (item.get("severity") or "warning").lower()

                replacement = safe_replacement_text(
                    replacement_text, matched_snippet
                )

                if severity == "critical":
                    penalty += 20
                elif severity == "warning":
                    penalty += 12
                else:
                    penalty += 6

                issues.append({
                    "id": f"term_{rule_id}_{start}_{end}",
                    "category": "Terminology",
                    "severity": severity.upper(),
                    "rule": f"Prohibited Term: '{term}'",
                    "matched_text": matched_snippet,
                    "location": {"start": start, "end": end},
                    "explanation": (
                        item.get("reason")
                        or f"'{term}' violates the "
                        f"{self.brand.get('name', 'company')} "
                        "vocabulary standard."
                    ),
                    "suggestion": replacement or "",
                    "replacement_text": replacement,
                    "auto_fixable": replacement is not None,
                    "manual_review_required": replacement is None,
                    "alternatives": [replacement] if replacement else [],
                })

        return issues, min(100, penalty)

    def _check_facts(
        self, text: str
    ) -> Tuple[List[Dict[str, Any]], int]:
        issues = []
        penalty = 0

        sentences = [
            (match.group(0), match.start())
            for match in re.finditer(r"[^.!?\n]+(?:[.!?]+|$)", text)
        ]

        number_pattern = re.compile(r"\b\d[\d,]*(?:\.\d+)?%?\+?\b")

        for fact in self.facts:
            statement = (fact.get("statement") or "").strip()
            key_metric = (fact.get("key_metric") or "").strip()
            keywords = self._configured_values(fact.get("keywords"))
            fact_terms = self._fact_terms(statement, keywords)

            approved_values = {}

            for approved_match in number_pattern.finditer(
                f"{statement} {key_metric}"
            ):
                value = approved_match.group(0)
                approved_values.setdefault(
                    value.replace(",", "").rstrip("%+"), value
                )

            approved_numbers = set(approved_values)

            if not statement or not fact_terms:
                continue

            for sentence, offset in sentences:
                if not self._has_fact_evidence(
                    sentence, fact_terms
                ):
                    continue

                stated_matches = list(number_pattern.finditer(sentence))

                conflicting_matches = [
                    match for match in stated_matches
                    if match.group(0).replace(",", "").rstrip("%+")
                    not in approved_numbers
                ]
                if approved_numbers and conflicting_matches:
                    match = conflicting_matches[0]
                    start = offset + match.start()
                    end = offset + match.end()
                    replacement = (
                        next(iter(approved_values.values()))
                        if len(approved_values) == 1
                        else ""
                    )
                    replacement = safe_replacement_text(
                        replacement, match.group(0)
                    )
                    penalty += 25
                    issues.append({
                        "id": f"fact_{fact.get('id', len(issues))}_{start}",
                        "category": "Fact Accuracy",
                        "severity": "WARNING",
                        "rule": f"Approved Fact: {fact.get('category') or 'Brand fact'}",
                        "matched_text": match.group(0),
                        "location": {"start": start, "end": end},
                        "explanation": (
                            "This numeric claim conflicts with the "
                            "approved fact configured for this brand."
                        ),
                        "suggestion": replacement or "",
                        "replacement_text": replacement,
                        "approved_fact": statement,
                        "auto_fixable": replacement is not None,
                        "manual_review_required": replacement is None,
                    })
                    break

                contradiction = self._fact_contradiction(
                    statement, sentence
                )
                if contradiction:
                    matched_text, start, end = contradiction
                    penalty += 25
                    issues.append({
                        "id": f"fact_{fact.get('id', len(issues))}_{offset + start}",
                        "category": "Fact Accuracy",
                        "severity": "WARNING",
                        "rule": f"Approved Fact: {fact.get('category') or 'Brand fact'}",
                        "matched_text": matched_text,
                        "location": {
                            "start": offset + start,
                            "end": offset + end,
                        },
                        "explanation": (
                            "This wording appears to contradict the "
                            "approved brand fact. Verify it against the "
                            "configured statement."
                        ),
                        "suggestion": (
                            "Review the claim against the approved fact; "
                            "no automatic replacement is safe."
                        ),
                        "replacement_text": None,
                        "approved_fact": statement,
                        "auto_fixable": False,
                        "manual_review_required": True,
                    })
                    break

        return issues, min(100, penalty)

    def _check_tone(
        self, text: str, content_type: str
    ) -> Tuple[List[Dict[str, Any]], int]:
        issues = []
        penalty = 0

        tone_profile = (
            self.brand.get("primary_tone") or ""
        ).lower()

        voice_traits = self._configured_values(self.brand.get("voice_traits"))
        tone_profile += " " + " ".join(voice_traits).lower()

        slang_patterns = []

        if any(
            term in tone_profile
            for term in (
                "formal",
                "professional",
                "corporate",
                "technical",
            )
        ):
            slang_patterns.extend([
                (
                    r"\b(bruh|tbh|cray|cray-cray|heck of a|kick-ass|"
                    r"freaking|omg|y'all|lit|super hyped)\b",
                    "Informal slang does not match the brand's configured voice.",
                    "WARNING",
                ),
                (
                    r"\b(insane|crazy|ridiculous)\s+"
                    r"(speed|performance|value|deal|pricing)\b",
                    "Hyperbolic phrasing does not match the brand's configured voice.",
                    "WARNING",
                ),
                (
                    r"\b(game[- ]changer|next-gen synergy|silver bullet|"
                    r"paradigm shift)\b",
                    "This buzzword may not match the brand's configured voice.",
                    "INFO",
                ),
            ])

        slang_patterns.append((
            r"\b(act now|hurry before it's gone|limited time offer|"
            r"buy now or regret it)\b",
            "Aggressive pressure language may require brand or legal review.",
            "WARNING",
        ))

        for pat, reason, severity in slang_patterns:
            for match in re.finditer(pat, text, re.IGNORECASE):
                start, end = match.span()
                penalty += 15 if severity == "CRITICAL" else 8

                issues.append({
                    "id": f"tone_{start}_{end}",
                    "category": "Voice & Tone",
                    "severity": severity,
                    "rule": "Brand Tone & Formality Standards",
                    "matched_text": match.group(0),
                    "location": {"start": start, "end": end},
                    "explanation": reason,
                    "suggestion": (
                        "Rephrase with professional, objective language."
                    ),
                    "replacement_text": None,
                    "auto_fixable": False,
                    "manual_review_required": True,
                })

        existing_spans = {
            (
                issue["location"]["start"],
                issue["location"]["end"],
            )
            for issue in issues
            if isinstance(issue.get("location"), dict)
        }
        for trait_index, (aliases, pattern, explanation, suggestion) in enumerate(
            _VOICE_TRAIT_RULES
        ):
            matched_trait = next(
                (
                    trait for trait in voice_traits
                    if any(
                        re.search(
                            rf"(?<!\w){re.escape(alias)}(?!\w)",
                            trait,
                            re.IGNORECASE,
                        )
                        for alias in aliases
                    )
                ),
                next(
                    (
                        alias for alias in aliases
                        if re.search(
                            rf"(?<!\w){re.escape(alias)}(?!\w)",
                            tone_profile,
                            re.IGNORECASE,
                        )
                    ),
                    None,
                ),
            )
            if not matched_trait:
                continue
            for match in re.finditer(pattern, text, re.IGNORECASE):
                start, end = match.span()
                if (start, end) in existing_spans:
                    continue
                issues.append({
                    "id": f"voice_trait_{trait_index}_{start}_{end}",
                    "category": "Voice & Tone",
                    "severity": "WARNING",
                    "rule": f"Configured Voice Trait: {matched_trait}",
                    "matched_text": match.group(0),
                    "location": {"start": start, "end": end},
                    "explanation": explanation,
                    "suggestion": suggestion,
                    "replacement_text": None,
                    "auto_fixable": False,
                    "manual_review_required": True,
                })
                existing_spans.add((start, end))
                penalty += 10

        exclamation_matches = list(re.finditer(r"!{2,}", text))

        if exclamation_matches:
            penalty += 10

            for match in exclamation_matches:
                start, end = match.span()

                issues.append({
                    "id": f"tone_excl_{start}",
                    "category": "Voice & Tone",
                    "severity": "WARNING",
                    "rule": "Punctuation & Professional Restraint",
                    "matched_text": match.group(0),
                    "location": {"start": start, "end": end},
                    "explanation": (
                        "Multiple exclamation marks appear spammy and "
                        "violate enterprise copy standards."
                    ),
                    "suggestion": ".",
                    "replacement_text": ".",
                    "auto_fixable": True,
                })

        all_caps_match = re.search(
            r"\b[A-Z]{3,}(?:\s+[A-Z]{3,}){2,}\b", text
        )

        if all_caps_match:
            penalty += 15
            start, end = all_caps_match.span()

            issues.append({
                "id": f"tone_caps_{start}",
                "category": "Voice & Tone",
                "severity": "WARNING",
                "rule": "Capitalization & Typography",
                "matched_text": all_caps_match.group(0),
                "location": {"start": start, "end": end},
                "explanation": (
                    "All-caps text reads as shouting and damages brand credibility."
                ),
                "suggestion": all_caps_match.group(0).capitalize(),
                "replacement_text": all_caps_match.group(0).capitalize(),
                "auto_fixable": True,
            })

        return issues, min(100, penalty)

    def _check_audience(
        self, text: str
    ) -> Tuple[List[Dict[str, Any]], int]:
        audience = self.brand.get("target_audience")
        if not isinstance(audience, str) or not audience.strip():
            return [], 0

        configured_groups = {
            group
            for group, pattern in _AUDIENCE_GROUPS
            if re.search(pattern, audience, re.IGNORECASE)
        }
        issues = []
        penalty = 0
        seen_spans = set()

        if configured_groups:
            for group, pattern in _AUDIENCE_GROUPS:
                if group in configured_groups:
                    continue
                direct_address = re.compile(
                    rf"\b(?:for|to|dear)\s+(?:the\s+)?(?:{pattern})",
                    re.IGNORECASE,
                )
                for match in direct_address.finditer(text):
                    if match.span() in seen_spans:
                        continue
                    seen_spans.add(match.span())
                    issues.append({
                        "id": f"audience_{match.start()}_{match.end()}",
                        "category": "Accessibility & Clarity",
                        "severity": "WARNING",
                        "rule": "Target Audience Suitability",
                        "matched_text": match.group(0),
                        "location": {
                            "start": match.start(),
                            "end": match.end(),
                        },
                        "explanation": (
                            f"The content explicitly addresses {group}, "
                            f"which does not match the configured audience "
                            f"({audience.strip()})."
                        ),
                        "suggestion": (
                            "Address the configured audience, or verify "
                            "that the target audience setting is correct."
                        ),
                        "replacement_text": None,
                        "auto_fixable": False,
                        "manual_review_required": True,
                    })
                    penalty += 15

        if _NON_SPECIALIST_AUDIENCE.search(audience):
            match = _AUDIENCE_JARGON.search(text)
            if match:
                issues.append({
                    "id": f"audience_jargon_{match.start()}_{match.end()}",
                    "category": "Accessibility & Clarity",
                    "severity": "WARNING",
                    "rule": "Target Audience Suitability",
                    "matched_text": match.group(0),
                    "location": {"start": match.start(), "end": match.end()},
                    "explanation": (
                        "This technical term may be unclear to the configured "
                        "non-specialist audience."
                    ),
                    "suggestion": (
                        "Define the term or replace it with plain-language "
                        "wording appropriate for the audience."
                    ),
                    "replacement_text": None,
                    "auto_fixable": False,
                    "manual_review_required": True,
                })
                penalty += 10

        return issues, min(100, penalty)

    def _check_claims(
        self, text: str
    ) -> Tuple[List[Dict[str, Any]], int]:
        issues = []
        penalty = 0

        for rule_index, rule in enumerate(self.claim_rules):
            if not rule.get("active", 1):
                continue

            rule_id = rule.get("id")
            if rule_id is None:
                rule_id = rule_index

            pattern_str = rule.get("pattern_or_trigger", "")
            if not pattern_str:
                continue

            patterns = _claim_pattern_variants(rule, pattern_str)
            for pattern in patterns:
                validate_claim_pattern(pattern)

            try:
                matches = {}
                for pattern in patterns:
                    for match in re.finditer(pattern, text, re.IGNORECASE):
                        matches.setdefault(match.span(), match.group(0))

                maximal_matches = {
                    span: matched_text
                    for span, matched_text in matches.items()
                    if not any(
                        other_span != span
                        and other_span[0] <= span[0]
                        and span[1] <= other_span[1]
                        for other_span in matches
                    )
                }
                for (start, end), matched_text in sorted(maximal_matches.items()):
                    severity = (
                        rule.get("severity") or "critical"
                    ).upper()

                    replacement = safe_replacement_text(
                        legacy_claim_replacement(rule),
                        matched_text,
                    )

                    penalty += 30 if severity == "CRITICAL" else 15

                    issues.append({
                        "id": f"claim_{rule_id}_{start}_{end}",
                        "category": "Compliance & Claims",
                        "severity": severity,
                        "rule": rule.get(
                            "rule_name", "Regulatory Compliance"
                        ),
                        "matched_text": matched_text,
                        "location": {"start": start, "end": end},
                        "explanation": (
                            rule.get("explanation")
                            or "Unsupported absolute claim."
                        ),
                        "suggestion": replacement or "",
                        "replacement_text": replacement,
                        "required_disclaimer": (
                            rule.get("required_disclaimer") or ""
                        ),
                        "auto_fixable": replacement is not None,
                        "manual_review_required": replacement is None,
                    })

            except re.error as error:
                raise ValueError(
                    f"Invalid saved claim pattern in rule "
                    f"'{rule.get('rule_name', '')}': {error}"
                ) from error

        unsupported_patterns = [
            (
                r"\b100%\s+(?:guaranteed|risk-free|money-back|secure)\b",
                "Absolute warranties create strict legal liability.",
                "CRITICAL",
                "backed by enterprise SLA and satisfaction guarantees",
            ),
            (
                r"\b(the\s+only\s+cloud\s+provider|"
                r"unmatched\s+by\s+anyone|the\s+world's\s+best)\b",
                "Unsubstantiated superlative without documented third-party ranking.",
                "WARNING",
                "a premier enterprise cloud platform",
            ),
            (
                r"\b(unlimited\s+free\s+tier|"
                r"free\s+forever\s+with\s+no\s+limits)\b",
                "Misleading claim; all developer tiers carry documented fair-use caps.",
                "CRITICAL",
                "generous developer tier with transparent quotas",
            ),
        ]

        for pattern_index, (pattern, explanation, severity, suggestion) in enumerate(
            unsupported_patterns
        ):
            for match in re.finditer(
                pattern, text, re.IGNORECASE
            ):
                start, end = match.span()

                if any(
                    issue["location"] == {"start": start, "end": end}
                    for issue in issues
                    if issue["category"] == "Compliance & Claims"
                ):
                    continue

                penalty += 25 if severity == "CRITICAL" else 12

                replacement = safe_replacement_text(
                    suggestion, match.group(0)
                )

                issues.append({
                    "id": f"claim_gen_{pattern_index}_{start}_{end}",
                    "category": "Compliance & Claims",
                    "severity": severity,
                    "rule": "Substantiation & Advertising Truth Standard",
                    "matched_text": match.group(0),
                    "location": {"start": start, "end": end},
                    "explanation": explanation,
                    "suggestion": replacement or "",
                    "replacement_text": replacement,
                    "auto_fixable": replacement is not None,
                    "manual_review_required": replacement is None,
                })

        return issues, min(100, penalty)

    def _check_accessibility(
        self, text: str
    ) -> Tuple[List[Dict[str, Any]], int, Dict[str, Any]]:
        issues = []
        penalty = 0

        sentence_spans = []

        for match in re.finditer(r"[^.!?]+", text):
            raw_sentence = match.group(0)
            sentence = raw_sentence.strip()

            if not sentence:
                continue

            start = (
                match.start()
                + len(raw_sentence)
                - len(raw_sentence.lstrip())
            )

            sentence_spans.append(
                (sentence, start, start + len(sentence))
            )

        sentences = [
            sentence for sentence, _, _ in sentence_spans
        ]

        words = re.findall(r"\b[A-Za-z0-9'-]+\b", text)

        word_count = len(words)
        sentence_count = max(1, len(sentences))

        def count_syllables(word):
            word = word.lower()

            if len(word) <= 3:
                return 1

            word = re.sub(r"(?:[^laeiouy]|ed|es|e)$", "", word)
            word = re.sub(r"^y", "", word)

            syllables = len(
                re.findall(r"[aeiouy]{1,2}", word)
            )

            return max(1, syllables)

        total_syllables = (
            sum(count_syllables(word) for word in words)
            if words else 0
        )

        words_per_sentence = (
            word_count / sentence_count
            if sentence_count > 0 else 0
        )

        syllables_per_word = (
            total_syllables / word_count
            if word_count > 0 else 0
        )

        reading_ease = (
            206.835
            - (1.015 * words_per_sentence)
            - (84.6 * syllables_per_word)
        )

        reading_ease = round(
            max(0, min(100, reading_ease)), 1
        )

        grade_level = (
            (0.39 * words_per_sentence)
            + (11.8 * syllables_per_word)
            - 15.59
        )

        grade_level = round(max(1, grade_level), 1)

        for sentence, start, end in sentence_spans:
            sentence_words = re.findall(
                r"\b[A-Za-z0-9'-]+\b", sentence
            )

            if len(sentence_words) > 32:
                penalty += 10

                issues.append({
                    "id": f"access_long_sent_{start}_{end}",
                    "category": "Accessibility & Clarity",
                    "severity": "WARNING",
                    "rule": "Sentence Length & Cognitive Load",
                    "matched_text": text[start:end],
                    "location": {"start": start, "end": end},
                    "explanation": (
                        f"Sentence contains {len(sentence_words)} words. "
                        "Split sentences into clearer 15-20 word "
                        "statements for cognitive accessibility."
                    ),
                    "suggestion": (
                        "Break sentence into two shorter, active statements."
                    ),
                    "auto_fixable": False,
                })

        click_matches = list(
            re.finditer(
                r"\b(click\s+here|click\s+this\s+link|tap\s+here)\b",
                text,
                re.IGNORECASE,
            )
        )

        for match in click_matches:
            penalty += 8
            start, end = match.span()

            issues.append({
                "id": f"access_link_{start}",
                "category": "Accessibility & Clarity",
                "severity": "INFO",
                "rule": "WCAG Accessible Hyperlink Context",
                "matched_text": match.group(0),
                "location": {"start": start, "end": end},
                "explanation": (
                    "'Click here' lacks semantic context for "
                    "screen-reader users. Use descriptive action "
                    "verbs like 'Explore the platform' or "
                    "'Download benchmark report'."
                ),
                "suggestion": (
                    "Use a descriptive link label based on the link destination."
                ),
                "replacement_text": None,
                "auto_fixable": False,
                "manual_review_required": True,
            })

        readability_stats = {
            "word_count": word_count,
            "sentence_count": sentence_count,
            "reading_ease": reading_ease,
            "grade_level": grade_level,
            "reading_time_sec": round(word_count / 3.3),
        }

        return issues, min(100, penalty), readability_stats

    def _generate_corrected_text(
        self, text: str, issues: List[Dict[str, Any]]
    ) -> str:
        """Build a preview using only validated, exact-span replacements."""

        correction_plan = apply_corrections(text, issues)
        conflicting_issue_ids = set(
            correction_plan.get("conflicting_issue_ids", [])
        )

        for issue in issues:
            reason = correction_plan["manual_review"].get(
                issue.get("id")
            )

            if reason:
                issue["auto_fixable"] = False
                issue["manual_review_required"] = True
                issue["manual_review_reason"] = reason
                if issue.get("id") in conflicting_issue_ids:
                    issue["replacement_choice_required"] = True

        return correction_plan["text"]

    def _generate_summary(
        self,
        score: int,
        risk_level: str,
        issues: List[Dict[str, Any]],
    ) -> str:
        critical_count = sum(
            1 for issue in issues
            if issue["severity"] == "CRITICAL"
        )

        warning_count = sum(
            1 for issue in issues
            if issue["severity"] == "WARNING"
        )

        info_count = sum(
            1 for issue in issues
            if issue["severity"] == "INFO"
        )

        if critical_count:
            assessment = "Critical policy violations were detected."
        elif warning_count:
            assessment = "Policy warnings were detected."
        elif info_count:
            assessment = "Only informational observations were detected."
        else:
            assessment = "No configured policy violations were detected."

        return (
            f"{assessment} Score: {score}/100 ({risk_level} risk); "
            f"{critical_count} critical, {warning_count} warning, "
            f"and {info_count} informational issue(s). "
            "This automated result is not an approval; human review "
            "is required before publication."
        )
