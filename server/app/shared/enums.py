"""Domain enums shared across modules. Values are the exact strings stored in the DB."""

from enum import StrEnum


class MemoryCategory(StrEnum):
    PERSONAL = "personal"
    PREFERENCE = "preference"
    RELATIONSHIP = "relationship"
    HEALTH = "health"
    WORK = "work"
    FINANCE = "finance"
    OTHER = "other"


class SourceType(StrEnum):
    VOICE = "voice"
    TEXT = "text"
    INFERRED = "inferred"
    INTEGRATION = "integration"
    MANUAL = "manual"


class PrivacyLevel(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    SENSITIVE = "sensitive"


class CommitmentStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    SNOOZED = "snoozed"


class EventType(StrEnum):
    TRAVEL = "travel"
    MEDICAL = "medical"
    MEETING = "meeting"
    SOCIAL = "social"
    FINANCIAL = "financial"
    OTHER = "other"


class RevisionActor(StrEnum):
    USER = "user"
    ORCHESTRATOR = "orchestrator"
    SYSTEM = "system"


class ApprovalActionType(StrEnum):
    UPDATE_MEMORY = "update_memory"
    APPROVE_COMMITMENT = "approve_commitment"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"  # replaced by a newer proposal, or the target changed
