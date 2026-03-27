"""Shared enums used across models and schemas."""

from enum import Enum


class TemplateType(str, Enum):
    """Type of onboarding/KYC template.

    Baseline templates are organized by template type and business level
    (for example `kyc` level 1, 2, 3).
    """

    KYC = "kyc"
    KYB = "kyb"


class TransformOperation(str, Enum):
    """Supported per-rule transform operations for answer migration.

    When a tenant publishes a new template version, a TransformRuleSet is
    created containing one TransformRule per question mapping.  Each rule
    declares one of these operations to describe how the source answer value
    should be converted into the target answer value.
    """

    IDENTITY = "identity"
    """Copy the answer verbatim — same unique_key, same field_type."""

    RENAME = "rename"
    """Question unique_key changed but semantics are identical; copy value."""

    MAP_VALUES = "map_values"
    """Remap discrete option values (dropdown / radio / checkbox).

    params: {"mapping": {"old": "new", ...}, "default": null | "fallback"}
    """

    COERCE_TYPE = "coerce_type"
    """Convert the answer between field types.

    params: {"from_type": "text", "to_type": "date", "format": "MM/DD/YYYY"}
    """

    SPLIT = "split"
    """Extract a piece of one source field into a target field.

    params: {"separator": " ", "index": 0}
    Multiple rules can share the same source_unique_key with different indices.
    """

    MERGE = "merge"
    """Join multiple source fields into a single target field.

    params: {"sources": ["first_name", "last_name"], "separator": " "}
    """

    DEFAULT_VALUE = "default_value"
    """Target question is new — inject a static default value.

    params: {"value": "N/A"}  (null means leave the answer blank)
    """

    COMPUTE = "compute"
    """Derive the target value from a user-defined expression evaluated via
    simpleeval in a secure sandbox.

    params: {"expr": "<expression>", "sources": ["date_of_birth"]}

    Expressions can reference source values by name.  For single-source rules
    the value is also available as ``value``.  Legacy builtins (age_from_dob,
    upper, lower, strip, concat) remain callable.

    Examples:
        {"expr": "upper(value)", "sources": ["name"]}
        {"expr": "int(value) + 1", "sources": ["age"]}
        {"expr": "age_from_dob(value)", "sources": ["date_of_birth"]}
    """

    DROP = "drop"
    """Source field was intentionally removed; no value is carried forward.

    params: {"reason": "Field removed per compliance update v2"}
    """


class RuleSetStatus(str, Enum):
    """Lifecycle status of a TransformRuleSet."""

    DRAFT = "draft"
    """Auto-generated or manually edited; not yet applied to any submission."""

    PUBLISHED = "published"
    """Frozen and ready to apply; no further edits allowed."""

    ARCHIVED = "archived"
    """Superseded by a newer ruleset; kept for audit purposes only."""


class DefinitionReviewStatus(str, Enum):
    """Review lifecycle for tenant-owned template definitions."""

    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"


class DefinitionReviewAction(str, Enum):
    """Audit actions recorded during definition review."""

    SUBMITTED = "submitted"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
