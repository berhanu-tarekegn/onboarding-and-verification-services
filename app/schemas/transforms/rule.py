"""Pydantic schemas for TransformRuleSet and TransformRule CRUD."""

from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import RuleSetStatus, TransformOperation


# ── TransformRule schemas ─────────────────────────────────────────────

class TransformRuleCreate(BaseModel):
    """Request body for adding a single rule to a rule set."""

    source_unique_key: Optional[str] = Field(
        default=None,
        max_length=255,
        description="unique_key of the source question. Null for DEFAULT_VALUE / DROP.",
    )
    target_unique_key: str = Field(
        max_length=255,
        description="unique_key of the target question.",
    )
    operation: TransformOperation
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Operation-specific parameters (see TransformOperation docs).",
    )
    display_order: int = Field(default=0, ge=0)
    is_required: bool = Field(
        default=False,
        description="If True, transform failure blocks the migration.",
    )

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="after")
    def _validate_params_for_operation(self) -> "TransformRuleCreate":
        from app.services.transforms.sandbox import validate_rule_params

        errors = validate_rule_params(self.operation, self.params)
        if errors:
            messages = "; ".join(e["message"] for e in errors)
            raise ValueError(f"Invalid params for '{self.operation.value}': {messages}")
        return self


class TransformRuleRead(TransformRuleCreate):
    """Response model for a single transform rule."""

    id: UUID
    rule_set_id: UUID

    model_config = ConfigDict(from_attributes=True)


class TransformRuleUpdate(BaseModel):
    """Request body for patching a single rule (partial update)."""

    source_unique_key: Optional[str] = Field(default=None, max_length=255)
    target_unique_key: Optional[str] = Field(default=None, max_length=255)
    operation: Optional[TransformOperation] = None
    params: Optional[Dict[str, Any]] = None
    display_order: Optional[int] = Field(default=None, ge=0)
    is_required: Optional[bool] = None

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="after")
    def _validate_params_for_operation(self) -> "TransformRuleUpdate":
        if self.operation is not None and self.params is not None:
            from app.services.transforms.sandbox import validate_rule_params

            errors = validate_rule_params(self.operation, self.params)
            if errors:
                messages = "; ".join(e["message"] for e in errors)
                raise ValueError(
                    f"Invalid params for '{self.operation.value}': {messages}"
                )
        return self


# ── TransformRuleSet schemas ──────────────────────────────────────────

class TransformRuleSetCreate(BaseModel):
    """Request body for manually creating a transform rule set."""

    source_version_id: UUID = Field(
        description="The old template version whose answers will be migrated.",
    )
    target_version_id: UUID = Field(
        description="The new template version answers will be migrated into.",
    )
    changelog: Optional[str] = None
    rules: List[TransformRuleCreate] = Field(
        default_factory=list,
        description="Initial set of rules (may be empty and added later).",
    )

    model_config = ConfigDict(extra="ignore")


class TransformRuleSetRead(BaseModel):
    """Response model for a transform rule set, including its rules."""

    id: UUID
    template_id: UUID
    source_version_id: UUID
    target_version_id: UUID
    status: RuleSetStatus
    auto_generated: bool
    changelog: Optional[str]
    rules: List[TransformRuleRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class TransformRuleSetUpdate(BaseModel):
    """Request body for updating a draft rule set's metadata."""

    changelog: Optional[str] = None

    model_config = ConfigDict(extra="ignore")


class TransformRuleSetGenerateRequest(BaseModel):
    """Request body for auto-generating a draft rule set via version diff."""

    source_version_id: UUID = Field(
        description="The version to migrate answers FROM.",
    )
    target_version_id: UUID = Field(
        description="The version to migrate answers TO.",
    )
    changelog: Optional[str] = Field(
        default=None,
        description="Optional description of what changed between versions.",
    )

    model_config = ConfigDict(extra="ignore")


# ── Sandbox validation response schemas ──────────────────────────────

class SandboxRuleError(BaseModel):
    """A single validation error for one rule in the sandbox dry-run."""

    field: str = Field(description="The param or field that failed (e.g. 'params.mapping').")
    message: str = Field(description="Human-readable description of the error.")


class SandboxRuleResultRead(BaseModel):
    """Outcome of dry-running a single rule in the sandbox."""

    rule_index: int
    target_unique_key: str
    operation: str
    success: bool
    output_value: Optional[str] = None
    errors: List[SandboxRuleError] = Field(default_factory=list)
    warnings: List[SandboxRuleError] = Field(default_factory=list)


class SandboxValidationResultRead(BaseModel):
    """Aggregate result returned when sandbox validation fails on publish."""

    valid: bool
    rule_results: List[SandboxRuleResultRead] = Field(default_factory=list)
    errors: List[SandboxRuleError] = Field(default_factory=list)
