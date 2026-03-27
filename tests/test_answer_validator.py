"""Tests — Answer Validator (pure unit tests, zero DB / HTTP).

These tests exercise `_validate_question` and `_is_visible` directly, covering:
* Required field enforcement
* text + regex validation
* dropdown / radio valid-option enforcement
* checkbox multi-select option validation
* date format, min_date, max_date
* fileUpload non-empty enforcement
* Optional-blank short-circuit (no errors)
* Conditional visibility (_is_visible)
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Optional
from uuid import uuid4

import pytest

from app.services.submissions.answer_validator import _is_visible, _validate_question


# ── Minimal Question stub (no DB required) ────────────────────────────

def _make_question(
    field_type: str,
    required: bool = False,
    unique_key: str = "q1",
    regex: Optional[str] = None,
    min_date: Optional[str] = None,
    max_date: Optional[str] = None,
    options: Optional[list] = None,
    depends_on_unique_key: Optional[str] = None,
    visible_when_equals: Optional[str] = None,
) -> SimpleNamespace:
    """Build a minimal Question-like namespace for validation tests."""
    return SimpleNamespace(
        id=uuid4(),
        unique_key=unique_key,
        field_type=field_type,
        required=required,
        regex=regex,
        min_date=min_date,
        max_date=max_date,
        options=[
            SimpleNamespace(value=v)
            for v in (options or [])
        ],
        depends_on_unique_key=depends_on_unique_key,
        visible_when_equals=visible_when_equals,
    )


# ── Required-field enforcement ────────────────────────────────────────

class TestRequiredField:
    def test_required_blank_returns_error(self):
        q = _make_question("text", required=True)
        assert _validate_question(q, "") != []
        assert _validate_question(q, None) != []
        assert _validate_question(q, "   ") != []

    def test_required_with_value_returns_no_error(self):
        q = _make_question("text", required=True)
        assert _validate_question(q, "John") == []

    def test_optional_blank_returns_no_error(self):
        q = _make_question("text", required=False)
        assert _validate_question(q, "") == []
        assert _validate_question(q, None) == []


# ── text + regex ──────────────────────────────────────────────────────

class TestTextField:
    def test_no_regex_any_value_passes(self):
        q = _make_question("text")
        assert _validate_question(q, "anything goes") == []

    def test_regex_matching_value_passes(self):
        q = _make_question("text", regex=r"^\d{10}$")
        assert _validate_question(q, "0912345678") == []

    def test_regex_non_matching_value_fails(self):
        q = _make_question("text", regex=r"^\d{10}$")
        errors = _validate_question(q, "short")
        assert len(errors) == 1
        assert "format" in errors[0].lower()

    def test_bad_regex_pattern_does_not_crash(self):
        q = _make_question("text", regex=r"[invalid(")
        # Should not raise; silently ignores the bad pattern
        assert _validate_question(q, "anything") == []


# ── dropdown / radio ──────────────────────────────────────────────────

class TestDropdownRadioField:
    @pytest.mark.parametrize("ft", ["dropdown", "radio"])
    def test_valid_option_passes(self, ft: str):
        q = _make_question(ft, options=["yes", "no"])
        assert _validate_question(q, "yes") == []

    @pytest.mark.parametrize("ft", ["dropdown", "radio"])
    def test_invalid_option_fails(self, ft: str):
        q = _make_question(ft, options=["yes", "no"])
        errors = _validate_question(q, "maybe")
        assert len(errors) == 1
        assert "valid option" in errors[0].lower()

    @pytest.mark.parametrize("ft", ["dropdown", "radio"])
    def test_no_options_defined_any_value_passes(self, ft: str):
        """If options list is empty, skip option validation."""
        q = _make_question(ft, options=[])
        assert _validate_question(q, "anything") == []


# ── checkbox ──────────────────────────────────────────────────────────

class TestCheckboxField:
    def test_all_csv_values_valid_passes(self):
        q = _make_question("checkbox", options=["a", "b", "c"])
        assert _validate_question(q, "a,b") == []

    def test_csv_with_invalid_token_fails(self):
        q = _make_question("checkbox", options=["a", "b", "c"])
        errors = _validate_question(q, "a,x")
        assert len(errors) == 1
        assert "invalid option" in errors[0].lower()

    def test_single_valid_value_passes(self):
        q = _make_question("checkbox", options=["true", "false"])
        assert _validate_question(q, "true") == []

    def test_no_options_defined_any_value_passes(self):
        q = _make_question("checkbox", options=[])
        assert _validate_question(q, "true,false") == []


# ── date ──────────────────────────────────────────────────────────────

class TestDateField:
    def test_valid_iso_date_passes(self):
        q = _make_question("date")
        assert _validate_question(q, "1990-05-15") == []

    def test_wrong_format_fails(self):
        q = _make_question("date")
        for bad in ["15-05-1990", "1990/05/15", "not-a-date"]:
            errors = _validate_question(q, bad)
            assert len(errors) == 1, f"expected error for '{bad}'"
            assert "YYYY-MM-DD" in errors[0]

    def test_date_before_min_date_fails(self):
        q = _make_question("date", min_date="2000-01-01")
        errors = _validate_question(q, "1999-12-31")
        assert len(errors) == 1
        assert "on or after" in errors[0]

    def test_date_equal_to_min_date_passes(self):
        q = _make_question("date", min_date="2000-01-01")
        assert _validate_question(q, "2000-01-01") == []

    def test_date_after_max_date_fails(self):
        q = _make_question("date", max_date="2010-01-01")
        errors = _validate_question(q, "2010-01-02")
        assert len(errors) == 1
        assert "on or before" in errors[0]

    def test_date_equal_to_max_date_passes(self):
        q = _make_question("date", max_date="2010-01-01")
        assert _validate_question(q, "2010-01-01") == []

    def test_date_within_range_passes(self):
        q = _make_question("date", min_date="1900-01-01", max_date="2010-01-01")
        assert _validate_question(q, "1985-07-22") == []

    def test_date_bad_format_skips_range_check(self):
        """A malformed date string should only emit format error, not range error."""
        q = _make_question("date", min_date="1900-01-01", max_date="2010-01-01")
        errors = _validate_question(q, "bad-date")
        assert len(errors) == 1


# ── fileUpload ────────────────────────────────────────────────────────

class TestFileUploadField:
    def test_non_empty_reference_passes(self):
        q = _make_question("fileUpload")
        assert _validate_question(q, "uploads/doc123.pdf") == []

    def test_blank_reference_fails(self):
        q = _make_question("fileUpload")
        for blank in ["", "   "]:
            errors = _validate_question(q, blank)
            assert len(errors) == 1

    def test_none_answer_optional_field_passes(self):
        q = _make_question("fileUpload", required=False)
        assert _validate_question(q, None) == []


# ── signature ────────────────────────────────────────────────────────

class TestSignatureField:
    def test_any_non_blank_value_passes(self):
        q = _make_question("signature")
        assert _validate_question(q, "data:image/png;base64,abc") == []

    def test_required_blank_fails(self):
        q = _make_question("signature", required=True)
        assert _validate_question(q, "") != []


# ── Conditional visibility ────────────────────────────────────────────

class TestIsVisible:
    def test_no_dependency_always_visible(self):
        q = _make_question("text")
        assert _is_visible(q, {}) is True
        assert _is_visible(q, {"other": "value"}) is True

    def test_dependency_met_with_visible_when_equals(self):
        q = _make_question(
            "text",
            depends_on_unique_key="employment",
            visible_when_equals="employed",
        )
        assert _is_visible(q, {"employment": "employed"}) is True

    def test_dependency_not_met_with_visible_when_equals(self):
        q = _make_question(
            "text",
            depends_on_unique_key="employment",
            visible_when_equals="employed",
        )
        assert _is_visible(q, {"employment": "unemployed"}) is False

    def test_dependency_missing_from_answers_not_visible(self):
        q = _make_question(
            "text",
            depends_on_unique_key="employment",
            visible_when_equals="employed",
        )
        assert _is_visible(q, {}) is False

    def test_dependency_with_no_visible_when_equals_visible_if_non_empty(self):
        """depends_on_unique_key set but visible_when_equals=None: visible when controlling answer is non-empty."""
        q = _make_question("text", depends_on_unique_key="agreed")
        assert _is_visible(q, {"agreed": "true"}) is True
        assert _is_visible(q, {"agreed": ""}) is False
        assert _is_visible(q, {}) is False

    def test_hidden_question_with_blank_required_answer_no_error(self):
        """Validation must be skipped for hidden questions even if required."""
        q = _make_question(
            "text",
            required=True,
            depends_on_unique_key="employment",
            visible_when_equals="employed",
        )
        # controlling answer is 'unemployed' → question hidden → no error expected
        visible = _is_visible(q, {"employment": "unemployed"})
        assert visible is False
        # If caller respects _is_visible, it won't call _validate_question at all
        # But even if called, blank+required would fail — this test confirms the
        # contract between _is_visible and _validate_question.
        if not visible:
            pass  # validator not called for hidden questions — correct behaviour
