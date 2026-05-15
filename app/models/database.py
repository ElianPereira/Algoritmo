"""
SQLAlchemy ORM models for persisting screening results.
Uses async engine; migrations managed by Alembic.
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ScreeningResultORM(Base):
    __tablename__ = "screening_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    company_name: Mapped[str | None] = mapped_column(String(200))
    screening_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    # Financial metrics
    z_score: Mapped[float | None] = mapped_column(Float)
    f_score: Mapped[int | None] = mapped_column(Integer)
    pe_ratio: Mapped[float | None] = mapped_column(Float)
    pb_ratio: Mapped[float | None] = mapped_column(Float)
    debt_to_equity: Mapped[float | None] = mapped_column(Float)
    current_ratio: Mapped[float | None] = mapped_column(Float)

    # Valuation
    current_price: Mapped[float | None] = mapped_column(Float)
    fair_value_dcf: Mapped[float | None] = mapped_column(Float)
    safety_margin: Mapped[float | None] = mapped_column(Float)
    upside_potential: Mapped[float | None] = mapped_column(Float)

    # Cash flow quality
    quality_ratio: Mapped[float | None] = mapped_column(Float)
    is_suspicious: Mapped[bool] = mapped_column(Boolean, default=False)

    # Summary
    risk_level: Mapped[str] = mapped_column(String(20), default="unknown")
    passes_filters: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # JSON blobs for full data
    financials_json: Mapped[str | None] = mapped_column(Text)
    valuation_json: Mapped[str | None] = mapped_column(Text)
    downside_risks_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_sr_ticker_date", "ticker", "screening_date"),
        Index("ix_sr_passes_filters", "passes_filters"),
    )


class DailySummaryORM(Base):
    __tablename__ = "daily_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    screening_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    total_screened: Mapped[int] = mapped_column(Integer, default=0)
    passed_filters: Mapped[int] = mapped_column(Integer, default=0)
    safe_zone: Mapped[int] = mapped_column(Integer, default=0)
    grey_zone: Mapped[int] = mapped_column(Integer, default=0)
    distress_zone: Mapped[int] = mapped_column(Integer, default=0)
    top_opportunities_json: Mapped[str | None] = mapped_column(Text)
    errors_json: Mapped[str | None] = mapped_column(Text)
