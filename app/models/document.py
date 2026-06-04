from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func
from app.db.base import Base
import enum


class DocumentCategory(str, enum.Enum):
    # Income Tax
    circular_income_tax = "circular_income_tax"
    sro_income_tax = "sro_income_tax"
    income_tax_ordinance = "income_tax_ordinance"
    # Sales Tax
    circular_sales_tax = "circular_sales_tax"
    sro_sales_tax = "sro_sales_tax"
    sales_tax_act = "sales_tax_act"
    sales_tax_rules = "sales_tax_rules"
    # Federal Excise
    circular_federal_excise = "circular_federal_excise"
    sro_federal_excise = "sro_federal_excise"
    federal_excise_act = "federal_excise_act"
    # Customs
    circular_customs = "circular_customs"
    sro_customs = "sro_customs"
    customs_act = "customs_act"
    customs_rules = "customs_rules"
    # Cross-cutting
    finance_act = "finance_act"
    sro_withholding_tax = "sro_withholding_tax"
    press_release = "press_release"
    # Legacy fallbacks (keep so old DB rows still load)
    circular = "circular"
    sro = "sro"
    sales_tax = "sales_tax"


class DocumentStatus(enum.Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class FBRDocument(Base):
    __tablename__ = "fbr_documents"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    url = Column(String, unique=True, nullable=False)
    # Stored as plain VARCHAR so we can add categories without PostgreSQL ALTER TYPE
    category = Column(String, nullable=False, index=True)
    circular_number = Column(String, nullable=True)
    fiscal_year = Column(String, nullable=True, index=True)
    minio_path = Column(String, nullable=True)
    checksum = Column(String, nullable=True)
    status = Column(String, default=DocumentStatus.pending.value, index=True)
    chunk_count = Column(Integer, default=0)
    raw_text = Column(Text, nullable=True)
    doc_date = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
