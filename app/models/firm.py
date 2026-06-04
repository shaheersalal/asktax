from sqlalchemy import Column, Integer, String, DateTime, Boolean, Enum, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.db.base import Base
import enum


class FirmPlan(enum.Enum):
    trial = "trial"
    basic = "basic"
    pro = "pro"


class FirmStatus(enum.Enum):
    trial = "trial"
    active = "active"
    suspended = "suspended"
    cancelled = "cancelled"


class Firm(Base):
    __tablename__ = "firms"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    slug = Column(String(100), unique=True, nullable=False)  # e.g. "jsa-karachi"
    billing_email = Column(String(200), nullable=False)
    phone = Column(String(50), nullable=True)

    plan = Column(Enum(FirmPlan), default=FirmPlan.trial, nullable=False)
    status = Column(Enum(FirmStatus), default=FirmStatus.trial, nullable=False)

    seats_limit = Column(Integer, default=5)           # max users allowed
    queries_per_seat = Column(Integer, default=200)    # per user per month

    trial_ends_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    users = relationship("User", back_populates="firm", lazy="dynamic")