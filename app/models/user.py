from sqlalchemy import Column, Integer, String, DateTime, Boolean, Enum, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.db.base import Base
import enum


class SubscriptionTier(enum.Enum):
    free = "free"
    basic = "basic"
    professional = "professional"
    enterprise = "enterprise"


class UserRole(enum.Enum):
    individual = "individual"   # standalone user, no firm
    firm_owner = "firm_owner"   # created the firm
    firm_admin = "firm_admin"   # can manage seats
    firm_member = "firm_member" # regular seat


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(200), unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=True)  # nullable for pending invites
    full_name = Column(String(200), nullable=True)

    # Plan (for individual users)
    subscription_tier = Column(Enum(SubscriptionTier), default=SubscriptionTier.free)
    queries_used_this_month = Column(Integer, default=0)

    # Account status
    status = Column(String(50), default="active")  # active / blocked / temporarily_blocked

    # Firm relationship
    firm_id = Column(Integer, ForeignKey("firms.id"), nullable=True, index=True)
    role = Column(Enum(UserRole), default=UserRole.individual)

    # Invite system
    invite_token = Column(String(100), nullable=True, unique=True)
    invite_accepted = Column(Boolean, default=False)
    invited_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    avatar_url = Column(String, nullable=True)
    trial_expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    firm = relationship("Firm", back_populates="users")
    query_history = relationship("QueryHistory", back_populates="user", lazy="dynamic")

    # ── helpers ──────────────────────────────────────────────────────────────
    @property
    def is_firm_user(self) -> bool:
        return self.firm_id is not None

    @property
    def is_firm_admin(self) -> bool:
        return self.role in (UserRole.firm_owner, UserRole.firm_admin)

    @property
    def effective_query_limit(self) -> int:
        """Return monthly query limit for this user."""
        _limits = {
            SubscriptionTier.free: 20,
            SubscriptionTier.basic: 200,
            SubscriptionTier.professional: 999999,
            SubscriptionTier.enterprise: 999999,
        }
        # Admin-granted individual trial overrides firm plan
        from datetime import datetime, timezone as _tz
        if self.trial_expires_at and self.trial_expires_at > datetime.now(_tz.utc):
            return _limits.get(self.subscription_tier, 20)
        if self.is_firm_user and self.firm:
            plan = self.firm.plan.value
            if plan == "pro":
                return 999999
            return self.firm.queries_per_seat
        return _limits.get(self.subscription_tier, 20)

    @property
    def queries_remaining(self) -> int:
        limit = self.effective_query_limit
        if limit >= 999999:
            return 999999
        return max(0, limit - self.queries_used_this_month)