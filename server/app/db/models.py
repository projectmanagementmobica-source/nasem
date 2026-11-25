"""Model registry: import every model so Base.metadata is complete (used by Alembic)."""

from app.db.base import Base
from app.modules.approvals.models import PendingAction
from app.modules.auth.models import RefreshToken, User
from app.modules.gateway.models import GatewayUsage
from app.modules.push.models import PushSubscription, ScheduledPush
from app.modules.access.models import ActivationRequest, Device
from app.modules.family.models import FamilyEvent, FamilyItem, FamilyLink, FamilyRequest
from app.modules.backup.models import UserBackup
from app.modules.scheduler.models import WakeSlot
from app.modules.commitments.models import Commitment
from app.modules.events.models import EventContext
from app.modules.privacy.keys import UserKey
from app.modules.memory.models import Memory, MemoryEmbedding, MemoryRevision

__all__ = ["Base", "WakeSlot", "UserBackup", "FamilyEvent", "FamilyItem", "FamilyLink", "FamilyRequest", "ActivationRequest", "Device", "PushSubscription", "ScheduledPush", "GatewayUsage", "UserKey", "RefreshToken", "User", "PendingAction", "Commitment", "EventContext", "Memory", "MemoryEmbedding", "MemoryRevision"]
