from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Float
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.db.base import Base


class QueryHistory(Base):
    __tablename__ = "query_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    query_text = Column(Text, nullable=False)
    query_language = Column(String, default="en")
    response_en = Column(Text, nullable=True)
    response_ur = Column(Text, nullable=True)
    sources_used = Column(Text, nullable=True)
    tokens_used = Column(Integer, default=0)
    client_ip = Column(String, nullable=True)
    title = Column(String(200), nullable=True)
    confidence_score = Column(Float, nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    share_token = Column(String(64), nullable=True, unique=True, index=True)
    session_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="query_history")