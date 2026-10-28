import datetime as dt
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint, false, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PromptType(StrEnum):
    """What a prompt is for. To add one: a value here, then a migration that seeds its v1."""

    RECEIPT_EXTRACTION = "receipt_extraction"  # receipt/invoice photo -> structured draft


class Prompt(Base):
    """A versioned model prompt. Rows are never edited: a change is a new version, then activated."""

    __tablename__ = "prompts"
    __table_args__ = (
        CheckConstraint(f"type IN ({', '.join(repr(t.value) for t in PromptType)})", name="ck_prompts_type"),
        UniqueConstraint("type", "version", name="uq_prompts_type_version"),
        Index("uq_prompts_one_active", "type", unique=True, postgresql_where=text("is_active")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    template: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
