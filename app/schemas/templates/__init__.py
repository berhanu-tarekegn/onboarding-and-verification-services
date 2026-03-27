"""Template form schema definitions — normalized question/group Pydantic schemas."""

from app.schemas.templates.form_schema import (
    FieldType,
    QuestionOptionCreate,
    QuestionOptionRead,
    BaselineQuestionOptionRead,
    QuestionCreate,
    QuestionRead,
    BaselineQuestionRead,
    QuestionGroupCreate,
    QuestionGroupRead,
    BaselineQuestionGroupRead,
    # Legacy field-type classes kept for backward compat
    TextField,
    DropdownField,
    RadioField,
    CheckboxField,
    DateField,
    FileUploadField,
    SignatureField,
)

__all__ = [
    "FieldType",
    "QuestionOptionCreate",
    "QuestionOptionRead",
    "BaselineQuestionOptionRead",
    "QuestionCreate",
    "QuestionRead",
    "BaselineQuestionRead",
    "QuestionGroupCreate",
    "QuestionGroupRead",
    "BaselineQuestionGroupRead",
    "TextField",
    "DropdownField",
    "RadioField",
    "CheckboxField",
    "DateField",
    "FileUploadField",
    "SignatureField",
]
