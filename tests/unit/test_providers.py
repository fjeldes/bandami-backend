"""
Unit tests for AI providers — JSON parsing, partial JSON extraction, retry logic.
"""

import json
import pytest

from app.services.providers.gemini import GeminiProvider


# ── Full JSON parsing ──

VALID_EVAL = {
    "overall_band": 6.5,
    "criteria_scores": {
        "task_response": {"score": 6.0, "comment": "Addresses task."},
        "coherence_and_cohesion": {"score": 6.5, "comment": "Well organized."},
        "lexical_resource": {"score": 7.0, "comment": "Good vocab."},
        "grammatical_range_and_accuracy": {"score": 6.5, "comment": "Mostly accurate."},
    },
    "detailed_feedback": "Good essay overall.",
    "grammar_corrections": [
        {"original": "she go", "corrected": "she goes", "explanation": "SVA"},
    ],
}


class TestGeminiJsonParsing:
    def setup_method(self):
        self.provider = GeminiProvider()

    def test_valid_json_parses(self):
        raw = json.dumps(VALID_EVAL)
        result = self.provider._parse_json(raw)
        assert result["overall_band"] == 6.5
        assert result["criteria_scores"]["task_response"]["score"] == 6.0
        assert len(result["grammar_corrections"]) == 1

    def test_json_with_markdown_fence(self):
        raw = "```json\n" + json.dumps(VALID_EVAL) + "\n```"
        result = self.provider._parse_json(raw)
        assert result["overall_band"] == 6.5

    def test_json_with_plain_markdown_fence(self):
        raw = "```\n" + json.dumps(VALID_EVAL) + "\n```"
        result = self.provider._parse_json(raw)
        assert result["overall_band"] == 6.5

    def test_json_with_leading_whitespace(self):
        raw = "  \n  " + json.dumps(VALID_EVAL) + "\n  "
        result = self.provider._parse_json(raw)
        assert result["overall_band"] == 6.5

    def test_invalid_json_raises_value_error(self):
        raw = "not json at all {{{"
        with pytest.raises(ValueError, match="AI returned invalid JSON"):
            self.provider._parse_json(raw)


# ── Partial JSON extraction (fallback when JSON is truncated/invalid) ──

class TestExtractPartialJson:
    def setup_method(self):
        self.provider = GeminiProvider()

    def test_extracts_overall_band(self):
        raw = '{"overall_band": 7.0, "criteria_scores": {}'
        result = self.provider._extract_partial_json(raw)
        assert result["overall_band"] == 7.0

    def test_extracts_criteria_scores(self):
        raw = """
        {
          "overall_band": 6.5,
          "criteria_scores": {
            "task_response": {"score": 6.0, "comment": "Okay response."},
            "coherence_and_cohesion": {"score": 6.5, "comment": "Organized."},
            "lexical_resource": {"score": 7.0, "comment": "Nice words."},
            "grammatical_range_and_accuracy": {"score": 6.0, "comment": "Some errors."}
          },
          "detailed_feedback": "Good job."
        }
        """
        result = self.provider._extract_partial_json(raw)
        assert result["overall_band"] == 6.5
        assert "task_response" in result["criteria_scores"]
        assert result["criteria_scores"]["lexical_resource"]["score"] == 7.0

    def test_extracts_fluency_and_coherence(self):
        raw = """
        {
          "overall_band": 7.0,
          "criteria_scores": {
            "fluency_and_coherence": {"score": 6.5, "comment": "Flows well."},
            "pronunciation": {"score": 7.0, "comment": "Clear."}
          }
        }
        """
        result = self.provider._extract_partial_json(raw)
        assert result["overall_band"] == 7.0
        assert result["criteria_scores"]["fluency_and_coherence"]["score"] == 6.5
        assert result["criteria_scores"]["pronunciation"]["score"] == 7.0

    def test_extracts_detailed_feedback(self):
        raw = '{"overall_band": 6.0, "detailed_feedback": "You need to improve grammar.", "criteria_scores": {}}'
        result = self.provider._extract_partial_json(raw)
        assert result["detailed_feedback"] == "You need to improve grammar."

    def test_extracts_grammar_corrections(self):
        raw = """
        {
          "overall_band": 6.0,
          "criteria_scores": {},
          "grammar_corrections": [
            {"original": "she go", "corrected": "she goes", "explanation": "SVA"},
            {"original": "they was", "corrected": "they were", "explanation": "plural"}
          ]
        }
        """
        result = self.provider._extract_partial_json(raw)
        assert len(result["grammar_corrections"]) >= 1

    def test_truncated_json_still_extracts_score(self):
        raw = '{"overall_band": 7.5, "criteria_scores": {"task_response": {"score": 7.0'
        result = self.provider._extract_partial_json(raw)
        assert result["overall_band"] == 7.5

    def test_missing_overall_band_raises(self):
        raw = '{"criteria_scores": {}}'
        with pytest.raises(ValueError, match="Could not extract overall_band"):
            self.provider._extract_partial_json(raw)

    def test_removes_control_characters(self):
        raw = '{"overall_band": 6.0, "criteria_scores": \x00{}}'
        result = self.provider._extract_partial_json(raw)
        assert result["overall_band"] == 6.0

    def test_complex_escaped_quotes_in_comment(self):
        raw = r"""{
          "overall_band": 6.5,
          "criteria_scores": {
            "task_response": {"score": 6.5, "comment": "You used \"good\" evidence."}
          }
        }"""
        result = self.provider._extract_partial_json(raw)
        assert result["overall_band"] == 6.5
