from typing import Annotated, Literal

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.projects import DbSession, project_or_404
from app.models import PageMetricSnapshot, QueryPageMetric
from app.schemas import (
    ActionPlanItem,
    CannibalizationCluster,
    CtrOpportunityItem,
    DecayStatus,
    PageAuditItem,
    ProjectAuditSummary,
)
from app.services.audit_engine import (
    audit_pages,
    build_action_plan,
    build_project_summary,
    detect_cannibalization,
    find_ctr_opportunities,
)


router = APIRouter(prefix="/api/projects/{project_id}", tags=["audit"])
ScoreFilter = Annotated[float | None, Query(ge=0, le=100)]
PageSort = Literal["decay_score", "clicks_delta", "curr_clicks", "position_delta"]


def _load_pages(db: DbSession, project_id: int) -> list[PageMetricSnapshot]:
    return list(db.scalars(select(PageMetricSnapshot).where(PageMetricSnapshot.project_id == project_id)))


def _load_clusters(db: DbSession, project_id: int) -> list[CannibalizationCluster]:
    rows = db.scalars(select(QueryPageMetric).where(QueryPageMetric.project_id == project_id))
    return detect_cannibalization(rows)


@router.get("/summary")
def project_summary(project_id: int, db: DbSession) -> ProjectAuditSummary:
    return build_project_summary(db, project_or_404(db, project_id))


@router.get("/decay-pages")
def decay_pages(
    project_id: int,
    db: DbSession,
    status: DecayStatus | None = None,
    content_group: str | None = None,
    min_decay_score: ScoreFilter = None,
    search: str | None = None,
    sort_by: PageSort = "decay_score",
    order: Literal["asc", "desc"] = "desc",
) -> list[PageAuditItem]:
    project_or_404(db, project_id)
    pages = _load_pages(db, project_id)
    term = search.casefold() if search else ""
    # Scores always use the loss distribution of the entire project.
    items = [
        item for item in audit_pages(pages)
        if (status is None or item.decay_status == status)
        and (content_group is None or item.content_group == content_group)
        and (min_decay_score is None or item.decay_score >= min_decay_score)
        and (term in item.url.casefold() or term in (item.page_title or "").casefold())
    ]
    direction = -1 if order == "desc" else 1
    return sorted(items, key=lambda item: (direction * getattr(item, sort_by), item.url))


@router.get("/ctr-opportunities")
def ctr_opportunities(
    project_id: int,
    db: DbSession,
    min_impressions: Annotated[int | None, Query(ge=0)] = None,
    max_position: Annotated[float | None, Query(ge=1, allow_inf_nan=False)] = None,
) -> list[CtrOpportunityItem]:
    project_or_404(db, project_id)
    return [
        item for item in find_ctr_opportunities(_load_pages(db, project_id))
        if (min_impressions is None or item.curr_impressions >= min_impressions)
        and (max_position is None or item.curr_position <= max_position)
    ]


@router.get("/cannibalization")
def cannibalization(
    project_id: int, db: DbSession, search: str | None = None,
) -> list[CannibalizationCluster]:
    project_or_404(db, project_id)
    clusters = _load_clusters(db, project_id)
    if not search:
        return clusters
    term = search.casefold()
    return [
        cluster for cluster in clusters
        if term in cluster.normalized_query.casefold()
        or any(term in item.url.casefold() for item in cluster.competing_urls)
    ]


@router.get("/action-plan")
def action_plan(
    project_id: int,
    db: DbSession,
    min_priority: ScoreFilter = None,
    limit: Annotated[int | None, Query(ge=0)] = None,
) -> list[ActionPlanItem]:
    project = project_or_404(db, project_id)
    pages = _load_pages(db, project_id)
    plan = build_action_plan(project, audit_pages(pages), find_ctr_opportunities(pages), _load_clusters(db, project_id))
    filtered = [item for item in plan if min_priority is None or item.priority_score >= min_priority]
    return filtered[:limit]
