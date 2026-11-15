from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer
from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_cpc: Mapped[float] = mapped_column(Float, default=1.80)
    conversion_rate: Mapped[float] = mapped_column(Float, default=0.025)
    avg_lead_value: Mapped[float] = mapped_column(Float, default=150.0)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    page_snapshots: Mapped[list[PageMetricSnapshot]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    query_metrics: Mapped[list[QueryPageMetric]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    import_batches: Mapped[list[ImportBatch]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    quarantine_records: Mapped[list[QuarantineRecord]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )


class PageMetricSnapshot(Base):
    __tablename__ = "page_metric_snapshots"
    __table_args__ = (UniqueConstraint("project_id", "url", name="uq_project_page_url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(1024), index=True)
    page_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_group: Mapped[str] = mapped_column(String(100), default="general")
    published_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_updated_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    prev_clicks: Mapped[int] = mapped_column(Integer, default=0)
    curr_clicks: Mapped[int] = mapped_column(Integer, default=0)
    prev_impressions: Mapped[int] = mapped_column(Integer, default=0)
    curr_impressions: Mapped[int] = mapped_column(Integer, default=0)
    prev_ctr: Mapped[float] = mapped_column(Float, default=0.0)
    curr_ctr: Mapped[float] = mapped_column(Float, default=0.0)
    prev_position: Mapped[float] = mapped_column(Float, default=0.0)
    curr_position: Mapped[float] = mapped_column(Float, default=0.0)

    project: Mapped[Project] = relationship(back_populates="page_snapshots")


class QueryPageMetric(Base):
    __tablename__ = "query_page_metrics"
    __table_args__ = (
        UniqueConstraint("project_id", "query", "url", name="uq_project_query_url"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    query: Mapped[str] = mapped_column(String(500), index=True)
    url: Mapped[str] = mapped_column(String(1024), index=True)
    prev_clicks: Mapped[int] = mapped_column(Integer, default=0)
    curr_clicks: Mapped[int] = mapped_column(Integer, default=0)
    prev_impressions: Mapped[int] = mapped_column(Integer, default=0)
    curr_impressions: Mapped[int] = mapped_column(Integer, default=0)
    curr_ctr: Mapped[float] = mapped_column(Float, default=0.0)
    curr_position: Mapped[float] = mapped_column(Float, default=0.0)
    intent: Mapped[str] = mapped_column(String(50), default="informational")

    project: Mapped[Project] = relationship(back_populates="query_metrics")


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    dataset_type: Mapped[str] = mapped_column(String(32))
    preset: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(255))
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    quarantined_rows: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    project: Mapped[Project] = relationship(back_populates="import_batches")
    quarantine_records: Mapped[list[QuarantineRecord]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", passive_deletes=True
    )


class QuarantineRecord(Base):
    __tablename__ = "quarantine_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    dataset_type: Mapped[str] = mapped_column(String(32))
    row_number: Mapped[int] = mapped_column(Integer)
    raw_payload: Mapped[dict] = mapped_column(JSON)
    error_reason: Mapped[str] = mapped_column(Text)

    batch: Mapped[ImportBatch] = relationship(back_populates="quarantine_records")
    project: Mapped[Project] = relationship(back_populates="quarantine_records")