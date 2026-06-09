# ============================================================
# JSON Parser — extracted from GeminiProvider (SRP)
# Handles JSON parsing with regex-based fallback for malformed AI responses.
# ============================================================

import json
import re


class JsonParser:
    """Parse AI response text into a dict, with regex fallback for truncated JSON."""

    @classmethod
    def parse(cls, raw: str) -> dict:
        cleaned = cls._strip_markdown(raw)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        try:
            return cls._extract_partial_json(cleaned)
        except ValueError:
            raise ValueError(f"AI returned invalid JSON: {raw[:200]}")

    @staticmethod
    def _strip_markdown(text: str) -> str:
        cleaned = text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        return cleaned.strip()

    @staticmethod
    def _extract_partial_json(raw: str) -> dict:
        result = {}
        raw = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw)

        overall = re.search(r'"overall_band"\s*:\s*([\d.]+)', raw)
        if overall:
            result["overall_band"] = float(overall.group(1))

        criteria = {}
        criteria_pattern = re.findall(
            r'"(task_response|coherence_and_cohesion|lexical_resource|grammatical_range_and_accuracy'
            r'|fluency_and_coherence|pronunciation'
            r'|fluency|coherence|vocabulary_range|vocabulary_precision|paraphrasing'
            r'|grammar_range|grammar_accuracy|pronunciation_clarity'
            r'|task_fulfillment|position_clarity|paragraph_structure|cohesion_devices)"'
            r'\s*:\s*\{[^}]*?"score"\s*:\s*([\d.]+)[^}]*?\}',
            raw, re.DOTALL,
        )
        for name, score in criteria_pattern:
            comment_match = re.search(
                rf'"{re.escape(name)}"\s*:\s*\{{[^}}]*?"comment"\s*:\s*"([^"]*(?:\\.[^"]*)*)"',
                raw, re.DOTALL,
            )
            comment = comment_match.group(1) if comment_match else ""
            criteria[name] = {"score": float(score), "comment": comment}

        if criteria:
            result["criteria_scores"] = criteria

        feedback_match = re.search(r'"detailed_feedback"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', raw)
        result["detailed_feedback"] = feedback_match.group(1) if feedback_match else ""

        general_match = re.search(r'"general_feedback"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', raw)
        result["general_feedback"] = general_match.group(1) if general_match else ""

        corrections = []
        corr_matches = re.findall(r'\{(?:[^{}]|\{[^{}]*\})*\}', raw)
        for match in corr_matches:
            orig = re.search(r'"original"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', match)
            corr = re.search(r'"corrected"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', match)
            if orig and corr:
                corrections.append({
                    "original": orig.group(1),
                    "corrected": corr.group(1),
                    "explanation": "",
                })
        result["grammar_corrections"] = corrections

        if not result.get("overall_band"):
            raise ValueError("Could not extract overall_band from JSON")
        return result
