"""Sandbox validation for transform rules.

Three responsibilities:
1. validate_rule_params  — structural check that params match the operation
2. evaluate_compute_expr — safe evaluation of COMPUTE expressions via simpleeval
3. sandbox_validate_rules — full dry-run of an ordered rule list against
                            synthetic answers built from source-version questions
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Sequence

from simpleeval import EvalWithCompoundTypes, InvalidExpression

from app.models.enums import TransformOperation


# ── 1. Operation-specific params validation ──────────────────────────

_PARAMS_SCHEMA: Dict[TransformOperation, List[tuple]] = {
    TransformOperation.IDENTITY: [],
    TransformOperation.RENAME: [],
    TransformOperation.DROP: [],
    TransformOperation.DEFAULT_VALUE: [],
    TransformOperation.MAP_VALUES: [
        ("mapping", dict, True),
    ],
    TransformOperation.COERCE_TYPE: [
        ("to_type", str, True),
    ],
    TransformOperation.SPLIT: [
        ("separator", str, True),
        ("index", int, True),
    ],
    TransformOperation.MERGE: [
        ("sources", list, True),
        ("separator", str, False),
    ],
    TransformOperation.COMPUTE: [
        ("expr", str, True),
    ],
}


def validate_rule_params(
    operation: TransformOperation,
    params: Dict[str, Any],
) -> List[Dict[str, str]]:
    """Return a list of error dicts for invalid/missing params keys.

    An empty list means the params are structurally valid for the operation.
    """
    errors: List[Dict[str, str]] = []
    schema = _PARAMS_SCHEMA.get(operation, [])

    for key, expected_type, required in schema:
        if key not in params:
            if required:
                errors.append({
                    "field": f"params.{key}",
                    "message": f"Missing required param '{key}' for operation '{operation.value}'.",
                })
            continue
        val = params[key]
        if not isinstance(val, expected_type):
            errors.append({
                "field": f"params.{key}",
                "message": (
                    f"Param '{key}' must be {expected_type.__name__}, "
                    f"got {type(val).__name__}."
                ),
            })

    if operation == TransformOperation.MERGE:
        sources = params.get("sources", [])
        if isinstance(sources, list):
            for i, s in enumerate(sources):
                if not isinstance(s, str):
                    errors.append({
                        "field": f"params.sources[{i}]",
                        "message": f"Each source must be a string, got {type(s).__name__}.",
                    })

    return errors


# ── 2. simpleeval-based COMPUTE expression engine ────────────────────

def _compute_age_from_dob(value: str) -> str:
    try:
        dob = date.fromisoformat(value)
        today = date.today()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        return str(age)
    except (ValueError, TypeError):
        return ""


_LEGACY_BUILTINS: Dict[str, Callable] = {
    "age_from_dob": _compute_age_from_dob,
    "upper": lambda v: v.upper() if isinstance(v, str) else str(v).upper(),
    "lower": lambda v: v.lower() if isinstance(v, str) else str(v).lower(),
    "strip": lambda v: v.strip() if isinstance(v, str) else str(v).strip(),
    "concat": lambda *parts, sep=" ": sep.join(str(p) for p in parts if p).strip(),
}

_SAFE_FUNCTIONS: Dict[str, Callable] = {
    "len": len,
    "str": str,
    "int": int,
    "float": float,
    "round": round,
    "abs": abs,
    "min": min,
    "max": max,
    "bool": bool,
    **_LEGACY_BUILTINS,
}

_SAFE_NAMES: Dict[str, Any] = {
    "True": True,
    "False": False,
    "None": None,
    "pi": math.pi,
}


def _make_evaluator(
    names: Dict[str, Any] | None = None,
) -> EvalWithCompoundTypes:
    """Build a locked-down simpleeval evaluator."""
    evaluator = EvalWithCompoundTypes(
        names=names or {},
        functions=_SAFE_FUNCTIONS.copy(),
    )
    evaluator.names.update(_SAFE_NAMES)
    return evaluator


def evaluate_compute_expr(
    expr: str,
    source_values: Dict[str, Optional[str]],
) -> str | None:
    """Evaluate a COMPUTE expression and return the string result.

    *source_values* maps variable names (source unique_keys or ``value`` for
    single-source rules) to their current answer strings.

    Raises ``ComputeExpressionError`` on any failure.
    """
    names: Dict[str, Any] = {}
    for k, v in source_values.items():
        names[k] = v

    evaluator = _make_evaluator(names)
    try:
        result = evaluator.eval(expr)
    except Exception as exc:
        raise ComputeExpressionError(str(exc)) from exc

    if result is None:
        return None
    return str(result)


_SAMPLE_INPUTS = ["sample", "42", "2000-01-15"]


def validate_compute_expression(
    expr: str,
    source_names: List[str],
) -> List[Dict[str, str]]:
    """Validate a COMPUTE expression without real data.

    Injects placeholder values for each source name and tries to evaluate.
    For single-source rules, ``value`` is also available as an alias
    (mirroring the executor convention).

    Because placeholder data may not match the actual runtime type, the
    validator tries multiple sample values (string, numeric string, date
    string) and only rejects the expression if ALL of them fail.  This
    separates true syntax / safety errors from benign type-mismatch issues.

    Returns a list of error dicts (empty = valid).
    """
    errors: List[Dict[str, str]] = []
    last_exc: Optional[str] = None

    for sample in _SAMPLE_INPUTS:
        sample_values: Dict[str, Optional[str]] = {}
        for name in source_names:
            sample_values[name] = sample
        if len(source_names) <= 1:
            sample_values["value"] = sample

        try:
            evaluate_compute_expr(expr, sample_values)
            return []
        except ComputeExpressionError as exc:
            last_exc = str(exc)

    if last_exc is not None:
        errors.append({
            "field": "params.expr",
            "message": f"Expression validation failed: {last_exc}",
        })

    return errors


class ComputeExpressionError(Exception):
    """Raised when a COMPUTE expression fails evaluation."""


# ── 3. Full dry-run sandbox ──────────────────────────────────────────

_SAMPLE_VALUES: Dict[str, str] = {
    "text": "sample_text",
    "date": "2000-01-15",
    "dropdown": "option_a",
    "radio": "option_a",
    "checkbox": "option_a,option_b",
    "fileUpload": "https://example.com/sample.pdf",
    "signature": "data:image/png;base64,AAAA",
}


@dataclass
class SandboxRuleResult:
    """Outcome of dry-running a single rule."""
    rule_index: int
    target_unique_key: str
    operation: str
    success: bool
    output_value: Optional[str] = None
    errors: List[Dict[str, str]] = field(default_factory=list)
    warnings: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class SandboxValidationResult:
    """Aggregate result of dry-running an entire rule set."""
    valid: bool
    rule_results: List[SandboxRuleResult] = field(default_factory=list)
    errors: List[Dict[str, str]] = field(default_factory=list)


def _generate_synthetic_answers(
    source_questions: Dict[str, Any],
) -> Dict[str, Optional[str]]:
    """Build plausible sample answers from source question metadata.

    ``source_questions`` is a ``{unique_key: question_obj}`` mapping where
    question_obj has at least ``.field_type`` and optionally ``.options``.
    """
    answers: Dict[str, Optional[str]] = {}
    for key, q in source_questions.items():
        ft = getattr(q, "field_type", "text") or "text"
        options = getattr(q, "options", None)
        if options and len(options) > 0:
            answers[key] = getattr(options[0], "value", "option_a")
        else:
            answers[key] = _SAMPLE_VALUES.get(ft, "sample_text")
    return answers


def sandbox_validate_rules(
    rules: Sequence[Any],
    source_questions: Dict[str, Any],
    target_questions: Dict[str, Any],
) -> SandboxValidationResult:
    """Dry-run every rule against synthetic data and collect results.

    Uses the real ``_apply_operation`` logic from the executor so the sandbox
    faithfully mirrors production behaviour.
    """
    from app.services.transforms.executor import _apply_operation  # noqa: WPS433

    synthetic_answers = _generate_synthetic_answers(source_questions)

    all_errors: List[Dict[str, str]] = []
    rule_results: List[SandboxRuleResult] = []
    after_answers: Dict[str, Optional[str]] = {}

    sorted_rules = sorted(rules, key=lambda r: r.display_order)

    for idx, rule in enumerate(sorted_rules):
        rule_errors: List[Dict] = []
        rule_warnings: List[Dict] = []

        param_errors = validate_rule_params(rule.operation, rule.params or {})
        if param_errors:
            rule_results.append(SandboxRuleResult(
                rule_index=idx,
                target_unique_key=rule.target_unique_key,
                operation=rule.operation.value if hasattr(rule.operation, "value") else str(rule.operation),
                success=False,
                errors=param_errors,
            ))
            all_errors.extend(param_errors)
            continue

        if rule.source_unique_key and rule.source_unique_key not in source_questions:
            if rule.operation not in (
                TransformOperation.DEFAULT_VALUE,
                TransformOperation.DROP,
            ):
                err = {
                    "field": "source_unique_key",
                    "message": (
                        f"Source key '{rule.source_unique_key}' does not exist "
                        f"in source version questions."
                    ),
                }
                rule_results.append(SandboxRuleResult(
                    rule_index=idx,
                    target_unique_key=rule.target_unique_key,
                    operation=rule.operation.value if hasattr(rule.operation, "value") else str(rule.operation),
                    success=False,
                    errors=[err],
                ))
                all_errors.append(err)
                continue

        if rule.target_unique_key not in target_questions:
            if rule.operation != TransformOperation.DROP:
                err = {
                    "field": "target_unique_key",
                    "message": (
                        f"Target key '{rule.target_unique_key}' does not exist "
                        f"in target version questions."
                    ),
                }
                rule_results.append(SandboxRuleResult(
                    rule_index=idx,
                    target_unique_key=rule.target_unique_key,
                    operation=rule.operation.value if hasattr(rule.operation, "value") else str(rule.operation),
                    success=False,
                    errors=[err],
                ))
                all_errors.append(err)
                continue

        try:
            tgt_key, value = _apply_operation(
                rule, synthetic_answers, rule_errors, rule_warnings,
            )
        except Exception as exc:
            err = {
                "field": "operation",
                "message": f"Dry-run failed: {exc}",
            }
            rule_results.append(SandboxRuleResult(
                rule_index=idx,
                target_unique_key=rule.target_unique_key,
                operation=rule.operation.value if hasattr(rule.operation, "value") else str(rule.operation),
                success=False,
                errors=[err],
            ))
            all_errors.append(err)
            continue

        if value != "__DROP__":
            after_answers[tgt_key] = value

        has_errors = bool(rule_errors)
        rule_results.append(SandboxRuleResult(
            rule_index=idx,
            target_unique_key=rule.target_unique_key,
            operation=rule.operation.value if hasattr(rule.operation, "value") else str(rule.operation),
            success=not has_errors,
            output_value=value if value != "__DROP__" else None,
            errors=rule_errors,
            warnings=rule_warnings,
        ))
        if has_errors:
            all_errors.extend(rule_errors)

    return SandboxValidationResult(
        valid=len(all_errors) == 0,
        rule_results=rule_results,
        errors=all_errors,
    )
