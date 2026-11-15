from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


ProjectName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Domain = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Slug = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Currency = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10)]
NonNegativeFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]
ConversionRate = Annotated[float, Field(ge=0, le=1)]
DatasetType = Literal["pages", "queries"]
ImportPreset = Literal["gsc", "yandex_webmaster", "standard"]


class ProjectCreate(BaseModel):
    name: ProjectName
    domain: Domain
    slug: Slug | None = None
    description: str | None = None
    default_cpc: NonNegativeFloat = 1.80
    conversion_rate: ConversionRate = 0.025
    avg_lead_value: NonNegativeFloat = 150.0
    currency: Currency = "USD"


class ProjectUpdate(BaseModel):
    name: ProjectName | None = None
    domain: Domain | None = None
    description: str | None = None
    default_cpc: NonNegativeFloat | None = None
    conversion_rate: ConversionRate | None = None
    avg_lead_value: NonNegativeFloat | None = None
    currency: Currency | None = None

    @field_validator("name", "domain", "default_cpc", "conversion_rate", "avg_lead_value", "currency")
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError("Field cannot be null")
        return value


class TimestampedRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def as_utc(cls, value: datetime) -> datetime:
        # SQLite returns naive datetimes for timezone-aware columns.
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class ProjectRead(TimestampedRead):
    id: int
    name: str
    domain: str
    slug: str
    description: str | None
    default_cpc: float
    conversion_rate: float
    avg_lead_value: float
    currency: str
    is_demo: bool
    pages_count: int = 0
    queries_count: int = 0


class ImportSummaryResponse(TimestampedRead):
    total_rows: int
    valid_rows: int
    quarantined_rows: int


class ImportBatchRead(ImportSummaryResponse):
    id: int
    project_id: int
    dataset_type: DatasetType
    preset: ImportPreset
    filename: str


class QuarantineRecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: int
    project_id: int
    dataset_type: DatasetType
    row_number: int
    raw_payload: dict
    error_reason: str


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str

DecayStatus = Literal["critical_decay", "moderate_decay", "early_warning", "stable", "growing"]
DecayDriver = Literal["ranking_drop", "ctr_erosion", "demand_shift", "mixed_decay", "healthy"]
ResolutionStrategy = Literal[
    "internal_linking_reanchor", "canonical_or_deoptimize", "301_redirect_and_merge",
]


class PageAuditItem(BaseModel):
    url: str
    page_title: str | None
    content_group: str | None = None
    prev_clicks: int
    curr_clicks: int
    prev_impressions: int
    curr_impressions: int
    prev_ctr: float
    curr_ctr: float
    prev_position: float
    curr_position: float
    clicks_delta: int
    clicks_change_pct: float
    impressions_delta: int
    impressions_change_pct: float
    ctr_delta: float
    position_delta: float
    decay_score: float
    decay_status: DecayStatus
    decay_driver: DecayDriver
    recommended_action: str


class CtrOpportunityItem(BaseModel):
    url: str
    curr_impressions: int
    curr_position: float
    curr_ctr: float
    expected_ctr: float
    ctr_gap: float
    potential_extra_clicks: int
    snippet_opportunity_score: float


class CompetingUrl(BaseModel):
    url: str
    curr_clicks: int
    curr_impressions: int
    curr_ctr: float
    curr_position: float
    click_share: float
    impression_share: float
    is_primary: bool


class CannibalizationCluster(BaseModel):
    normalized_query: str
    primary_url: str
    competing_urls: list[CompetingUrl]
    total_clicks: int
    total_impressions: int
    cannibalization_score: float
    estimated_lost_clicks: int
    resolution_strategy: ResolutionStrategy
    resolution_recommendation: str


class ActionPlanItem(BaseModel):
    url: str
    decay_status: DecayStatus
    decay_score: float
    snippet_opportunity_score: float
    cannibalization_impact: float
    monetary_rank_score: float
    lost_clicks: int
    potential_extra_clicks: int
    cannibalization_bonus_clicks: int
    recoverable_clicks: int
    equivalent_ad_value: float
    projected_leads: float
    projected_revenue_impact: float
    currency: str
    priority_score: float


class ProjectAuditSummary(BaseModel):
    project_id: int
    currency: str
    pages_count: int
    prev_clicks: int
    curr_clicks: int
    clicks_delta: int
    clicks_change_pct: float
    pages_by_status: dict[DecayStatus, int]
    ctr_opportunities_count: int
    cannibalization_clusters_count: int
    recoverable_clicks: int
    equivalent_ad_value: float
    projected_leads: float
    projected_revenue_impact: float
    quarantined_rows_count: int
