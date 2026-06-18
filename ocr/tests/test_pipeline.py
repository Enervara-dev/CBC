"""
Unit tests for the core pipeline stages.
Run: pytest tests/
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from configs import lft_config as config


# ── Stage 3: Parser ──────────────────────────────────────────────────────────

class TestParser:
    def test_alias_resolution(self):
        from core.stage3_parser import _match_canonical
        assert _match_canonical("SGPT", config) == "ALT"
        assert _match_canonical("SGOT", config) == "AST"
        assert _match_canonical("GAMMA GT", config) == "GGT"
        assert _match_canonical("TOTAL BILIRUBIN", config) == "TOTAL_BILIRUBIN"
        assert _match_canonical("A/G RATIO", config) == "AG_RATIO"
        assert _match_canonical("PROTEIN A/G RATIO", config) == "AG_RATIO"

    def test_unknown_parameter(self):
        from core.stage3_parser import _match_canonical
        assert _match_canonical("BLOOD SUGAR FASTING", config) is None

    def test_table_parse(self):
        from core.stage3_parser import parse
        extraction = {
            "tables": [[
                ["SGPT (ALT)", "42", "U/L", "7-56"],
                ["SGOT (AST)", "35", "U/L", "10-40"],
                ["Alkaline Phosphatase", "95", "U/L", "44-147"],
            ]],
            "raw_text": "",
        }
        results = parse(extraction, config)
        params = {r["parameter"] for r in results}
        assert "ALT" in params
        assert "AST" in params
        assert "ALP" in params

    def test_lt_gt_values(self):
        from core.stage3_parser import _extract_number
        assert _extract_number("<10") == pytest.approx(9.9, abs=0.1)
        assert _extract_number(">12.00") == pytest.approx(12.12, abs=0.1)
        assert _extract_number("42") == 42.0
        assert _extract_number("N/A") is None

    def test_reference_extraction(self):
        from core.stage3_parser import _extract_reference
        assert _extract_reference("7 - 56") == (7.0, 56.0)
        assert _extract_reference("0.2–1.2") == (0.2, 1.2)
        assert _extract_reference("<0.3") == (0.0, 0.3)


# ── Stage 4: Flagging ────────────────────────────────────────────────────────

class TestFlagging:
    def _make(self, param, value, ref_low=None, ref_high=None):
        if ref_low is None and param in config.REFERENCE_RANGES:
            ref_low, ref_high, _ = config.REFERENCE_RANGES[param]
        return {"parameter": param, "value": value,
                "ref_low": ref_low, "ref_high": ref_high,
                "unit": "U/L", "raw_text": "", "source": "test"}

    def test_normal(self):
        from core.stage4_flagging import flag
        assert flag([self._make("ALT", 30)], config)[0]["flag"] == "NORMAL"

    def test_high(self):
        from core.stage4_flagging import flag
        assert flag([self._make("ALT", 100)], config)[0]["flag"] == "HIGH"

    def test_low(self):
        from core.stage4_flagging import flag
        assert flag([self._make("ALBUMIN", 3.0)], config)[0]["flag"] == "LOW"

    def test_critical_high(self):
        from core.stage4_flagging import flag
        m = flag([self._make("ALT", 600)], config)[0]
        assert m["flag"] == "CRITICAL_HIGH"
        assert m["is_critical"] is True

    def test_critical_low(self):
        from core.stage4_flagging import flag
        m = flag([self._make("ALBUMIN", 1.5)], config)[0]
        assert m["flag"] == "CRITICAL_LOW"

    def test_missing(self):
        from core.stage4_flagging import flag
        assert flag([self._make("ALT", None)], config)[0]["flag"] == "UNKNOWN"


# ── Stage 5: Rule-Based Condition Detection ──────────────────────────────────

class TestRuleDetection:
    def test_all_normal(self):
        from core.stage5_rules import detect
        markers = {m: (lo + hi) / 2 for m, (lo, hi, _) in config.REFERENCE_RANGES.items()}
        result = detect(markers, config)
        assert result["top_condition"] == "Normal"
        assert result["severity"] == "Normal"
        assert result["conditions"]["Normal"] == 1.0

    def test_hepatitis_high_alt(self):
        from core.stage5_rules import detect
        markers = {m: (lo + hi) / 2 for m, (lo, hi, _) in config.REFERENCE_RANGES.items()}
        markers["ALT"] = 250  # 4.5× ULN
        markers["AST"] = 180
        result = detect(markers, config)
        assert result["conditions"]["Hepatitis"] > 0.3

    def test_alcoholic_ast_alt_ratio(self):
        from core.stage5_rules import detect
        markers = {m: (lo + hi) / 2 for m, (lo, hi, _) in config.REFERENCE_RANGES.items()}
        markers["ALT"] = 35    # mildly elevated
        markers["AST"] = 100   # ratio = 2.86 → alcoholic
        markers["GGT"] = 200
        result = detect(markers, config)
        assert result["conditions"]["Alcoholic Liver Disease"] > 0.3

    def test_cholestasis_pattern(self):
        from core.stage5_rules import detect
        markers = {m: (lo + hi) / 2 for m, (lo, hi, _) in config.REFERENCE_RANGES.items()}
        markers["ALP"] = 500
        markers["GGT"] = 300
        markers["DIRECT_BILIRUBIN"] = 2.0
        result = detect(markers, config)
        assert result["conditions"]["Cholestasis"] > 0.4

    def test_cirrhosis_low_albumin(self):
        from core.stage5_rules import detect
        markers = {m: (lo + hi) / 2 for m, (lo, hi, _) in config.REFERENCE_RANGES.items()}
        markers["ALBUMIN"] = 2.0   # critically low
        markers["INR"] = 1.8
        markers["TOTAL_BILIRUBIN"] = 4.0
        result = detect(markers, config)
        assert result["conditions"]["Cirrhosis"] > 0.3

    def test_no_normal_with_disease(self):
        from core.stage5_rules import detect
        markers = {m: (lo + hi) / 2 for m, (lo, hi, _) in config.REFERENCE_RANGES.items()}
        markers["ALT"] = 300
        result = detect(markers, config)
        assert result["conditions"]["Normal"] == 0.0


# ── Stage 7: Summary ─────────────────────────────────────────────────────────

class TestSummary:
    def test_normal_summary(self):
        from core.stage7_summary import generate
        markers = [{"parameter": "ALT", "value": 30, "flag": "NORMAL",
                    "unit": "U/L", "ref_low": 7, "ref_high": 56,
                    "is_critical": False, "raw_text": ""}]
        conditions = {c: 0.0 for c in config.CONDITIONS}
        conditions["Normal"] = 1.0
        result = generate(markers, conditions, "Normal", "Normal", [], {"ALT": 30}, config)
        assert "normal" in result["overall_status"].lower()
        assert result["flagged_markers_summary"] == []

    def test_elevated_marker_in_summary(self):
        from core.stage7_summary import generate
        markers = [{"parameter": "ALT", "value": 92, "flag": "HIGH",
                    "unit": "U/L", "ref_low": 7, "ref_high": 56,
                    "is_critical": False, "raw_text": ""}]
        conditions = {c: 0.0 for c in config.CONDITIONS}
        conditions["Hepatitis"] = 0.78
        result = generate(markers, conditions, "Hepatitis", "Mild",
                          ["ALT", "AST"], {"ALT": 92}, config)
        assert any("ALT" in s for s in result["flagged_markers_summary"])

    def test_ast_alt_note(self):
        from core.stage7_summary import generate
        conditions = {c: 0.0 for c in config.CONDITIONS}
        conditions["Alcoholic Liver Disease"] = 0.8
        result = generate([], conditions, "Alcoholic Liver Disease", "Moderate",
                          ["GGT", "AST"], {"ALT": 30, "AST": 90}, config)
        assert result["ast_alt_note"] is not None
        assert "3.0" in result["ast_alt_note"]
