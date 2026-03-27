"""Configurable verification runtime for onboarding submissions.

This module implements a demo-ready, submission-scoped verification engine with
enough structure to support future Temporal orchestration and external decision
engines cleanly.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.tenant.submission import Submission
from app.models.tenant.verification import VerificationRun, VerificationStepRun
from app.schemas.submissions.verification import (
    VerificationActionRequest,
    VerificationRunRead,
    VerificationStartRequest,
    VerificationStepRunRead,
)
from app.services import tenant_templates as tenant_template_svc

RUN_TERMINAL_STATUSES = {"completed", "failed", "manual_review", "cancelled", "expired"}
STEP_TERMINAL_STATUSES = {"completed", "failed", "skipped", "expired"}


@dataclass(frozen=True)
class _FlowContext:
    submission: Submission
    flow_key: str
    flow_config: dict[str, Any]
    rules_config: dict[str, Any]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bad_request(message: str, *, code: str = "invalid_request", details: Any | None = None) -> HTTPException:
    payload: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        payload["details"] = details
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=payload)


def _not_found(message: str, *, code: str = "not_found") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": code, "message": message},
    )


def _unprocessable(message: str, *, code: str = "unprocessable", details: Any | None = None) -> HTTPException:
    payload: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        payload["details"] = details
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=payload)


async def get_latest_verification_run(
    submission_id: UUID,
    session: AsyncSession,
) -> VerificationRunRead | None:
    submission = await _get_submission(submission_id, session)
    run = await _get_latest_run(submission.id, session)
    if run is None:
        return None
    return await _serialize_run(run, session)


async def start_verification(
    submission_id: UUID,
    body: VerificationStartRequest,
    session: AsyncSession,
) -> VerificationRunRead:
    ctx = await _load_flow_context(submission_id, session, flow_key=body.flow_key)
    run = await _get_latest_run(ctx.submission.id, session)

    if run is None or not run.is_active:
        run = VerificationRun(
            submission_id=ctx.submission.id,
            template_version_id=ctx.submission.template_version_id,
            flow_key=ctx.flow_key,
            journey=body.journey,
            status="pending",
            rules_snapshot=deepcopy(ctx.flow_config),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        await _initialize_steps(run, ctx.flow_config, session)
    else:
        run.flow_key = ctx.flow_key
        run.journey = body.journey or run.journey
        run.rules_snapshot = deepcopy(ctx.flow_config)

    if body.deferred:
        run.status = "pending"
        run.current_step_key = None
        run.deferred_until = _now()
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return await _serialize_run(run, session)

    await _advance_run(run, ctx, session)
    return await _serialize_run(run, session)


async def submit_step_action(
    submission_id: UUID,
    step_key: str,
    body: VerificationActionRequest,
    session: AsyncSession,
) -> VerificationRunRead:
    submission = await _get_submission(submission_id, session)
    run = await _get_latest_run(submission.id, session)
    if run is None or not run.is_active:
        raise _not_found("Verification run not found for submission.", code="verification_run_not_found")
    ctx = await _load_flow_context(submission_id, session, flow_key=run.flow_key)

    step = await _get_step_run(run.id, step_key, session)
    if step is None:
        raise _not_found(f"Verification step '{step_key}' not found.", code="verification_step_not_found")
    if step.status != "waiting_user_action":
        raise _unprocessable(
            f"Verification step '{step_key}' is not waiting for user action.",
            code="verification_step_not_waiting",
        )

    await _apply_user_action(run, step, ctx.flow_config, body)
    session.add(step)
    await session.commit()
    await _advance_run(run, ctx, session)
    return await _serialize_run(run, session)


async def append_verification_summary(
    submission_id: UUID,
    session: AsyncSession,
    payload: dict[str, Any],
) -> dict[str, Any]:
    latest = await get_latest_verification_run(submission_id, session)
    payload["verification"] = latest
    return payload


async def _get_submission(submission_id: UUID, session: AsyncSession) -> Submission:
    result = await session.exec(select(Submission).where(Submission.id == submission_id))
    submission = result.first()
    if submission is None:
        raise _not_found("Submission not found.", code="submission_not_found")
    return submission


async def _load_flow_context(
    submission_id: UUID,
    session: AsyncSession,
    *,
    flow_key: str,
) -> _FlowContext:
    submission = await _get_submission(submission_id, session)
    template_config = await tenant_template_svc.get_tenant_template_definition_with_config(
        submission.template_version_id,
        session,
    )
    rules_config = template_config.get("rules_config", {}) if isinstance(template_config, dict) else {}
    verification = (rules_config or {}).get("verification_flow")
    if not isinstance(verification, dict):
        raise _unprocessable(
            "The template version does not define a verification_flow.",
            code="verification_flow_missing",
        )

    configured_flow_key = str(verification.get("flow_key") or "default")
    if flow_key and flow_key != configured_flow_key:
        raise _unprocessable(
            f"Requested flow_key '{flow_key}' does not match the pinned verification flow '{configured_flow_key}'.",
            code="verification_flow_not_found",
        )

    steps = verification.get("steps")
    if not isinstance(steps, list) or not steps:
        raise _unprocessable(
            "verification_flow.steps must be a non-empty list.",
            code="verification_flow_invalid",
        )

    return _FlowContext(
        submission=submission,
        flow_key=configured_flow_key,
        flow_config=verification,
        rules_config=rules_config,
    )


async def _get_latest_run(submission_id: UUID, session: AsyncSession) -> VerificationRun | None:
    result = await session.exec(
        select(VerificationRun)
        .where(VerificationRun.submission_id == submission_id)
        .order_by(VerificationRun.created_at.desc())
    )
    return result.first()


async def _get_step_run(run_id: UUID, step_key: str, session: AsyncSession) -> VerificationStepRun | None:
    result = await session.exec(
        select(VerificationStepRun)
        .where(VerificationStepRun.run_id == run_id)
        .where(VerificationStepRun.step_key == step_key)
    )
    return result.first()


async def _list_step_runs(run_id: UUID, session: AsyncSession) -> list[VerificationStepRun]:
    result = await session.exec(
        select(VerificationStepRun)
        .where(VerificationStepRun.run_id == run_id)
        .order_by(VerificationStepRun.created_at, VerificationStepRun.step_key)
    )
    return list(result.all())


async def _initialize_steps(
    run: VerificationRun,
    flow_config: dict[str, Any],
    session: AsyncSession,
) -> None:
    existing = await _list_step_runs(run.id, session)
    if existing:
        return

    steps = flow_config.get("steps", [])
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_key = str(step.get("key") or "").strip()
        if not step_key:
            raise _unprocessable("Each verification step must define a non-empty key.", code="verification_flow_invalid")
        row = VerificationStepRun(
            run_id=run.id,
            submission_id=run.submission_id,
            step_key=step_key,
            display_name=(step.get("name") or step_key)[:255],
            step_type=str(step.get("type") or "challenge_response"),
            adapter_key=str(step.get("adapter") or step.get("type") or "unknown"),
            status="pending",
            depends_on=[str(v) for v in step.get("depends_on", []) if isinstance(v, str) and v],
            config_snapshot=deepcopy(step),
        )
        session.add(row)

    await session.commit()


async def _advance_run(run: VerificationRun, ctx: _FlowContext, session: AsyncSession) -> None:
    steps = await _list_step_runs(run.id, session)
    by_key = {step.step_key: step for step in steps}
    progress = True
    run.started_at = run.started_at or _now()
    run.deferred_until = None

    while progress:
        progress = False
        for step in steps:
            if step.status in STEP_TERMINAL_STATUSES or step.status == "waiting_user_action":
                continue
            if not _deps_satisfied(step, by_key):
                continue

            if step.step_type == "challenge_response":
                changed = _start_challenge_step(step, ctx.submission, ctx.flow_config, by_key)
            elif step.step_type == "comparison":
                changed = _run_comparison_step(step, ctx.submission, by_key)
            else:
                step.status = "failed"
                step.outcome = "fail"
                step.error_details = {"code": "unsupported_step_type", "message": f"Unsupported step type '{step.step_type}'."}
                changed = True

            if changed:
                session.add(step)
                progress = True
                if step.status == "waiting_user_action":
                    break

        if progress:
            await session.commit()
            steps = await _list_step_runs(run.id, session)
            by_key = {step.step_key: step for step in steps}

    await _finalize_run(run, ctx, steps, session)


def _deps_satisfied(step: VerificationStepRun, by_key: dict[str, VerificationStepRun]) -> bool:
    for dep in step.depends_on:
        dep_step = by_key.get(dep)
        if dep_step is None:
            return False
        if dep_step.status not in STEP_TERMINAL_STATUSES:
            return False
    return True


def _start_challenge_step(
    step: VerificationStepRun,
    submission: Submission,
    flow_config: dict[str, Any],
    by_key: dict[str, VerificationStepRun],
) -> bool:
    cfg = step.config_snapshot or {}
    adapter = step.adapter_key
    input_snapshot = _resolve_input_map(cfg.get("input"), submission, by_key)
    step.input_snapshot = input_snapshot
    step.started_at = step.started_at or _now()

    if adapter == "demo_phone_otp":
        phone_number = input_snapshot.get("phone_number")
        if not isinstance(phone_number, str) or not phone_number.strip():
            step.status = "failed"
            step.outcome = "fail"
            step.error_details = {"code": "missing_phone_number", "message": "Phone number is required for demo_phone_otp."}
            return True
        expected_code = str(cfg.get("demo_code") or "111111")
        step.status = "waiting_user_action"
        step.waiting_for = "otp_code"
        step.correlation_id = f"phone-otp:{submission.id}:{step.step_key}"
        step.action_schema = {
            "action": "submit_code",
            "fields": [{"name": "otp_code", "type": "string", "required": True}],
            "delivery": {"channel": "sms", "target": phone_number, "demo_code": expected_code},
        }
        step.output_snapshot = {"channel": "sms", "target": phone_number}
        step.result_snapshot = {"phase": "challenge_sent"}
        return True

    if adapter == "demo_fayda_otp":
        national_id = input_snapshot.get("national_id")
        if not isinstance(national_id, str) or not national_id.strip():
            step.status = "failed"
            step.outcome = "fail"
            step.error_details = {"code": "missing_national_id", "message": "National ID is required for demo_fayda_otp."}
            return True
        registry = _demo_registry(flow_config, cfg)
        profile = registry.get(national_id)
        if not isinstance(profile, dict):
            step.status = "failed"
            step.outcome = "fail"
            step.error_details = {"code": "fayda_profile_not_found", "message": "No Fayda demo profile found for the given national ID."}
            return True
        registered_phone = str(profile.get("registered_phone") or "")
        expected_code = str(profile.get("otp_code") or cfg.get("demo_code") or "222222")
        step.status = "waiting_user_action"
        step.waiting_for = "otp_code"
        step.correlation_id = f"fayda-otp:{submission.id}:{step.step_key}"
        step.action_schema = {
            "action": "submit_code",
            "fields": [{"name": "otp_code", "type": "string", "required": True}],
            "delivery": {
                "channel": "fayda_registered_phone",
                "target": registered_phone,
                "demo_code": expected_code,
                "national_id": national_id,
            },
        }
        step.output_snapshot = {"registered_phone": registered_phone, "national_id": national_id}
        step.result_snapshot = {"phase": "challenge_sent"}
        return True

    step.status = "failed"
    step.outcome = "fail"
    step.error_details = {"code": "unsupported_adapter", "message": f"Unsupported challenge adapter '{adapter}'."}
    return True


def _run_comparison_step(
    step: VerificationStepRun,
    submission: Submission,
    by_key: dict[str, VerificationStepRun],
) -> bool:
    cfg = step.config_snapshot or {}
    pairs = cfg.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        step.status = "failed"
        step.outcome = "fail"
        step.error_details = {"code": "comparison_pairs_missing", "message": "Comparison steps require a non-empty pairs list."}
        return True

    comparisons: list[dict[str, Any]] = []
    total_score = 0.0
    count = 0

    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        left = _resolve_ref(pair.get("left"), submission, by_key)
        right = _resolve_ref(pair.get("right"), submission, by_key)
        score = _string_similarity(left, right)
        comparisons.append(
            {
                "label": pair.get("label") or f"{pair.get('left')} vs {pair.get('right')}",
                "left": left,
                "right": right,
                "score": score,
            }
        )
        total_score += score
        count += 1

    if count == 0:
        step.status = "failed"
        step.outcome = "fail"
        step.error_details = {"code": "comparison_pairs_invalid", "message": "No valid comparison pairs were produced."}
        return True

    avg_score = round(total_score / count, 4)
    pass_score = float(cfg.get("pass_score_gte", 0.9))
    review_score = float(cfg.get("review_score_gte", 0.75))
    if avg_score >= pass_score:
        outcome = "pass"
    elif avg_score >= review_score:
        outcome = "review"
    else:
        outcome = "fail"

    step.started_at = step.started_at or _now()
    step.completed_at = _now()
    step.status = "completed"
    step.outcome = outcome
    step.input_snapshot = {"pairs": comparisons}
    step.output_snapshot = {"score": avg_score, "comparisons": comparisons}
    step.result_snapshot = {"outcome": outcome, "score": avg_score, "comparisons": comparisons}
    step.action_schema = {}
    step.error_details = {}
    return True


async def _apply_user_action(
    run: VerificationRun,
    step: VerificationStepRun,
    flow_config: dict[str, Any],
    body: VerificationActionRequest,
) -> None:
    code = body.payload.get("otp_code")
    if not isinstance(code, str) or not code.strip():
        raise _unprocessable("Missing otp_code in action payload.", code="otp_code_missing")

    cfg = step.config_snapshot or {}
    step.attempt_count += 1
    step.error_details = {}

    if step.adapter_key == "demo_phone_otp":
        expected = str(cfg.get("demo_code") or "111111")
        if code.strip() != expected:
            _mark_retry_or_fail(step, max_attempts=int(cfg.get("max_attempts", 3)))
            return
        phone_number = str((step.input_snapshot or {}).get("phone_number") or "")
        step.completed_at = _now()
        step.status = "completed"
        step.outcome = "pass"
        step.waiting_for = None
        step.action_schema = {}
        step.output_snapshot = {"phone_number": phone_number, "verified": True}
        step.result_snapshot = {
            "outcome": "pass",
            "attributes": {"phone_number": phone_number, "phone_verified": True},
        }
        return

    if step.adapter_key == "demo_fayda_otp":
        national_id = str((step.input_snapshot or {}).get("national_id") or "")
        profile = _demo_registry(flow_config, cfg).get(national_id)
        expected = str((profile or {}).get("otp_code") or cfg.get("demo_code") or "222222")
        if not isinstance(profile, dict):
            step.status = "failed"
            step.outcome = "fail"
            step.error_details = {"code": "fayda_profile_not_found", "message": "No Fayda demo profile found for the given national ID."}
            return
        if code.strip() != expected:
            _mark_retry_or_fail(step, max_attempts=int(cfg.get("max_attempts", 3)))
            return
        attributes = {
            "national_id": national_id,
            "first_name": profile.get("first_name"),
            "last_name": profile.get("last_name"),
            "registered_phone": profile.get("registered_phone"),
            "date_of_birth": profile.get("date_of_birth"),
        }
        step.completed_at = _now()
        step.status = "completed"
        step.outcome = "pass"
        step.waiting_for = None
        step.action_schema = {}
        step.output_snapshot = {"attributes": attributes}
        step.result_snapshot = {"outcome": "pass", "attributes": attributes}
        return

    raise _unprocessable(
        f"Unsupported action adapter '{step.adapter_key}'.",
        code="unsupported_adapter",
    )


def _mark_retry_or_fail(step: VerificationStepRun, *, max_attempts: int) -> None:
    if step.attempt_count >= max_attempts:
        step.completed_at = _now()
        step.status = "failed"
        step.outcome = "fail"
        step.waiting_for = None
        step.action_schema = {}
        step.error_details = {"code": "max_attempts_reached", "message": "The verification step exhausted its allowed OTP attempts."}
        step.result_snapshot = {"outcome": "fail", "reason": "max_attempts_reached"}
        return

    step.status = "waiting_user_action"
    step.outcome = "pending"
    step.error_details = {"code": "invalid_otp", "message": "The provided OTP code is invalid. Try again."}


async def _finalize_run(
    run: VerificationRun,
    ctx: _FlowContext,
    steps: list[VerificationStepRun],
    session: AsyncSession,
) -> None:
    run.current_step_key = None

    waiting = next((step for step in steps if step.status == "waiting_user_action"), None)
    if waiting is not None:
        run.status = "waiting_user_action"
        run.current_step_key = waiting.step_key
        run.is_active = True
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return

    if any(step.status not in STEP_TERMINAL_STATUSES for step in steps):
        run.status = "in_progress"
        run.is_active = True
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return

    facts = _build_facts(ctx.submission, steps)
    decision_payload = _evaluate_demo_decision(ctx.flow_config, facts)

    run.facts_snapshot = facts
    run.result_snapshot = decision_payload
    run.decision = decision_payload.get("decision")
    run.kyc_level = decision_payload.get("kyc_level")
    run.completed_at = _now()
    run.is_active = False

    if run.decision == "approved":
        run.status = "completed"
    elif run.decision == "rejected":
        run.status = "failed"
    else:
        run.status = "manual_review"

    verification_summary = {
        "run_id": str(run.id),
        "status": run.status,
        "decision": run.decision,
        "kyc_level": run.kyc_level,
        "steps": {
            step.step_key: {
                "status": step.status,
                "outcome": step.outcome,
                "result": step.result_snapshot,
            }
            for step in steps
        },
    }
    computed = deepcopy(ctx.submission.computed_data or {})
    computed["verification"] = verification_summary
    ctx.submission.computed_data = computed

    validation = deepcopy(ctx.submission.validation_results or {})
    validation["decision"] = decision_payload
    ctx.submission.validation_results = validation

    session.add(ctx.submission)
    session.add(run)
    await session.commit()
    await session.refresh(run)


def _build_facts(submission: Submission, steps: list[VerificationStepRun]) -> dict[str, Any]:
    return {
        "submission": {
            "id": str(submission.id),
            "template_id": str(submission.template_id),
            "template_version_id": str(submission.template_version_id),
            "submitter_id": submission.submitter_id,
            "external_ref": submission.external_ref,
            "status": submission.status.value if hasattr(submission.status, "value") else str(submission.status),
        },
        "answers": deepcopy(submission.form_data or {}),
        "computed_data": deepcopy(submission.computed_data or {}),
        "steps": {
            step.step_key: {
                "status": step.status,
                "outcome": step.outcome,
                "input": deepcopy(step.input_snapshot or {}),
                "output": deepcopy(step.output_snapshot or {}),
                "result": deepcopy(step.result_snapshot or {}),
            }
            for step in steps
        },
    }


def _evaluate_demo_decision(flow_config: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    decision_cfg = flow_config.get("decision")
    if not isinstance(decision_cfg, dict):
        return {
            "decision": "manual_review",
            "kyc_level": "pending_review",
            "matched_rule": None,
            "reason_codes": ["decision_config_missing"],
        }

    rules = decision_cfg.get("rules")
    if isinstance(rules, list):
        for idx, rule in enumerate(rules):
            if not isinstance(rule, dict):
                continue
            if _rule_matches(rule, facts):
                return {
                    "decision": str(rule.get("decision") or "manual_review"),
                    "kyc_level": str(rule.get("kyc_level") or "pending_review"),
                    "matched_rule": idx,
                    "reason_codes": [str(v) for v in rule.get("reason_codes", []) if isinstance(v, str)],
                }

    fallback = decision_cfg.get("fallback")
    if isinstance(fallback, dict):
        return {
            "decision": str(fallback.get("decision") or "manual_review"),
            "kyc_level": str(fallback.get("kyc_level") or "pending_review"),
            "matched_rule": None,
            "reason_codes": [str(v) for v in fallback.get("reason_codes", []) if isinstance(v, str)],
        }

    return {
        "decision": "manual_review",
        "kyc_level": "pending_review",
        "matched_rule": None,
        "reason_codes": ["decision_fallback_used"],
    }


def _rule_matches(rule: dict[str, Any], facts: dict[str, Any]) -> bool:
    conditions = rule.get("all")
    if not isinstance(conditions, list) or not conditions:
        return False
    return all(_condition_matches(condition, facts) for condition in conditions if isinstance(condition, dict))


def _condition_matches(condition: dict[str, Any], facts: dict[str, Any]) -> bool:
    fact_path = condition.get("fact")
    if not isinstance(fact_path, str) or not fact_path.strip():
        return False
    actual = _lookup_mapping(facts, fact_path)

    if "equals" in condition:
        return actual == condition.get("equals")
    if "gte" in condition:
        try:
            return float(actual) >= float(condition.get("gte"))
        except Exception:
            return False
    if "lte" in condition:
        try:
            return float(actual) <= float(condition.get("lte"))
        except Exception:
            return False
    if "in" in condition and isinstance(condition.get("in"), list):
        return actual in condition.get("in")
    if condition.get("truthy") is True:
        return bool(actual)
    return False


def _resolve_input_map(
    spec: Any,
    submission: Submission,
    by_key: dict[str, VerificationStepRun],
) -> dict[str, Any]:
    if not isinstance(spec, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in spec.items():
        out[key] = _resolve_ref(value, submission, by_key)
    return out


def _resolve_ref(
    ref: Any,
    submission: Submission,
    by_key: dict[str, VerificationStepRun],
) -> Any:
    if not isinstance(ref, str):
        return ref
    raw = ref.strip()
    if raw.startswith("$"):
        raw = raw[1:]

    if raw.startswith("answers."):
        return _lookup_mapping(submission.form_data or {}, raw[len("answers."):])
    if raw.startswith("submission."):
        return _lookup_object(submission, raw[len("submission."):])
    if raw.startswith("steps."):
        parts = raw.split(".")
        if len(parts) < 3:
            return None
        step = by_key.get(parts[1])
        if step is None:
            return None
        if parts[2] == "output":
            return _lookup_mapping(step.output_snapshot or {}, ".".join(parts[3:]))
        if parts[2] == "result":
            return _lookup_mapping(step.result_snapshot or {}, ".".join(parts[3:]))
        return None
    return raw


def _lookup_object(obj: Any, path: str) -> Any:
    current = obj
    for part in path.split("."):
        if not part:
            continue
        current = getattr(current, part, None)
        if current is None:
            return None
        if hasattr(current, "value"):
            current = current.value
    return current


def _lookup_mapping(mapping: Any, path: str) -> Any:
    if not path:
        return mapping
    current = mapping
    for part in path.split("."):
        if not part:
            continue
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def _string_similarity(left: Any, right: Any) -> float:
    if left is None or right is None:
        return 0.0
    left_s = str(left).strip().lower()
    right_s = str(right).strip().lower()
    if not left_s or not right_s:
        return 0.0
    if left_s == right_s:
        return 1.0
    return round(SequenceMatcher(None, left_s, right_s).ratio(), 4)


def _demo_registry(flow_config: dict[str, Any], step_cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    registry = flow_config.get("demo_registry")
    if not isinstance(registry, dict):
        registry = step_cfg.get("demo_registry")
    return registry if isinstance(registry, dict) else {}


async def _serialize_run(run: VerificationRun, session: AsyncSession) -> VerificationRunRead:
    steps = await _list_step_runs(run.id, session)
    return VerificationRunRead(
        id=run.id,
        submission_id=run.submission_id,
        template_version_id=run.template_version_id,
        flow_key=run.flow_key,
        journey=run.journey,
        status=run.status,
        decision=run.decision,
        kyc_level=run.kyc_level,
        current_step_key=run.current_step_key,
        is_active=run.is_active,
        rules_snapshot=deepcopy(run.rules_snapshot or {}),
        facts_snapshot=deepcopy(run.facts_snapshot or {}),
        result_snapshot=deepcopy(run.result_snapshot or {}),
        started_at=run.started_at,
        completed_at=run.completed_at,
        deferred_until=run.deferred_until,
        steps=[
            VerificationStepRunRead(
                id=step.id,
                run_id=step.run_id,
                submission_id=step.submission_id,
                step_key=step.step_key,
                display_name=step.display_name,
                step_type=step.step_type,
                adapter_key=step.adapter_key,
                status=step.status,
                outcome=step.outcome,
                attempt_count=step.attempt_count,
                waiting_for=step.waiting_for,
                correlation_id=step.correlation_id,
                input_snapshot=deepcopy(step.input_snapshot or {}),
                output_snapshot=deepcopy(step.output_snapshot or {}),
                result_snapshot=deepcopy(step.result_snapshot or {}),
                action_schema=deepcopy(step.action_schema or {}),
                error_details=deepcopy(step.error_details or {}),
                started_at=step.started_at,
                completed_at=step.completed_at,
                expires_at=step.expires_at,
            )
            for step in steps
        ],
    )
