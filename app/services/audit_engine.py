"""Deterministic analytics for SEO & Content Decay Audit Hub."""

from collections import Counter, defaultdict
from collections.abc import Iterable
from decimal import Decimal
from itertools import pairwise
from math import fsum, isnan

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import PageMetricSnapshot, Project, QuarantineRecord, QueryPageMetric
from app.schemas import (
    ActionPlanItem,
    CannibalizationCluster,
    CompetingUrl,
    CtrOpportunityItem,
    DecayDriver,
    DecayStatus,
    PageAuditItem,
    ProjectAuditSummary,
    ResolutionStrategy,
)


DECAY_RECOVERY_FACTOR = 0.85
CTR_CAPTURE_FACTOR = 0.70

_CTR_CURVE = {
    1: 0.280, 2: 0.155, 3: 0.105, 4: 0.075, 5: 0.058, 6: 0.045, 7: 0.036,
    8: 0.030, 9: 0.025, 10: 0.021, 11: 0.016, 15: 0.010, 20: 0.006,
}
_DECAY_ACTIONS: dict[DecayDriver, str] = {
    "ranking_drop": "Глубокий рерайт и актуализация фактуры: страница потеряла позиции в топе",
    "ctr_erosion": "Переупаковка Title, Description и FAQ-сниппета: позиции держатся, но упала кликабельность",
    "demand_shift": "Расширение семантического ядра и добавление актуальных подтем под новый спрос",
    "mixed_decay": "Комплексное обновление контента, коммерческих блоков и внутренней перелинковки",
    "healthy": "Поддерживать актуальность и использовать страницу как донора перелинковки",
}
_RESOLUTION_ACTIONS: dict[ResolutionStrategy, str] = {
    "internal_linking_reanchor": "Усилить лидера: перенаправить внутренние анкорные ссылки со вторичных страниц на основной URL",
    "canonical_or_deoptimize": "Развести интенты: деоптимизировать информационную страницу под коммерческий ключ и перелинковать на посадочную",
    "301_redirect_and_merge": "Объединить контент в единый сильный материал и настроить 301-редирект со слабого дубля",
}


def expected_ctr_for_position(position: float) -> float:
    if isnan(position):
        raise ValueError("Position must not be NaN")
    if position <= 1:
        return _CTR_CURVE[1]
    if position > 20:
        return 0.005

    for (left, left_ctr), (right, right_ctr) in pairwise(_CTR_CURVE.items()):
        if position <= right:
            start = Decimal(str(left_ctr))
            end = Decimal(str(right_ctr))
            fraction = (Decimal(str(position)) - left) / (right - left)
            return float(start + (end - start) * fraction)

    raise ValueError("Position is outside the CTR curve")


def _clicks_change(prev_clicks: int, curr_clicks: int) -> float:
    if prev_clicks > 0:
        return round((curr_clicks - prev_clicks) / prev_clicks, 4)
    return 1.0 if curr_clicks > 0 else 0.0


def _volume_scale(pages: list[PageMetricSnapshot]) -> float:
    losses = sorted(
        page.prev_clicks - page.curr_clicks for page in pages if page.prev_clicks > page.curr_clicks
    )
    if not losses:
        return 200.0
    # Inclusive percentile with linear interpolation between adjacent observations.
    index, fraction = divmod((len(losses) - 1) * 9, 10)
    lower = losses[index]
    upper = losses[min(index + 1, len(losses) - 1)]
    return max(200.0, lower + (upper - lower) * fraction / 10)


def _decay_status(score: float, clicks_delta: int, clicks_change_pct: float) -> DecayStatus:
    if clicks_delta < 0:
        if score >= 70:
            return "critical_decay"
        if score >= 40:
            return "moderate_decay"
    if 20 <= score < 40:
        return "early_warning"
    if score < 20 and clicks_change_pct > 0.15 and clicks_delta >= 10:
        return "growing"
    return "stable"


def _decay_driver(
    status: DecayStatus, clicks_delta: int, position_delta: float,
    ctr_delta: float, impressions_change_pct: float,
) -> DecayDriver:
    if status in {"stable", "growing"}:
        return "healthy"
    if position_delta >= 2.5 and clicks_delta < 0:
        return "ranking_drop"
    if position_delta < 2.5 and ctr_delta <= -0.01:
        return "ctr_erosion"
    if impressions_change_pct <= -0.25 and position_delta < 2:
        return "demand_shift"
    return "mixed_decay"


def _audit_page(page: PageMetricSnapshot, volume_scale: float) -> PageAuditItem:
    clicks_delta = page.curr_clicks - page.prev_clicks
    clicks_change_pct = _clicks_change(page.prev_clicks, page.curr_clicks)
    impressions_delta = page.curr_impressions - page.prev_impressions
    impressions_change_pct = (
        round(impressions_delta / page.prev_impressions, 4) if page.prev_impressions > 0 else 0.0
    )
    ctr_delta = round(page.curr_ctr - page.prev_ctr, 4)
    position_delta = round(page.curr_position - page.prev_position, 2)

    score = 0.0
    if clicks_delta < 0:
        if page.prev_clicks > 0:
            score += min(1.0, abs(clicks_change_pct) / 0.60) * 45.0
        score += min(1.0, abs(clicks_delta) / volume_scale) * 25.0
    if position_delta > 0:
        score += min(1.0, position_delta / 8.0) * 15.0
    if ctr_delta < 0 and page.prev_ctr > 0:
        score += min(1.0, abs(ctr_delta) / max(0.01, page.prev_ctr * 0.40)) * 10.0
    if impressions_delta < 0 and page.prev_impressions > 0:
        score += min(1.0, abs(impressions_change_pct) / 0.50) * 5.0

    score = round(min(100.0, score), 1)
    status = _decay_status(score, clicks_delta, clicks_change_pct)
    driver = _decay_driver(status, clicks_delta, position_delta, ctr_delta, impressions_change_pct)
    return PageAuditItem(
        url=page.url, page_title=page.page_title, content_group=page.content_group,
        prev_clicks=page.prev_clicks, curr_clicks=page.curr_clicks,
        prev_impressions=page.prev_impressions, curr_impressions=page.curr_impressions,
        prev_ctr=page.prev_ctr, curr_ctr=page.curr_ctr,
        prev_position=page.prev_position, curr_position=page.curr_position,
        clicks_delta=clicks_delta, clicks_change_pct=clicks_change_pct,
        impressions_delta=impressions_delta, impressions_change_pct=impressions_change_pct,
        ctr_delta=ctr_delta, position_delta=position_delta,
        decay_score=score, decay_status=status, decay_driver=driver,
        recommended_action=_DECAY_ACTIONS[driver],
    )


def audit_pages(pages: Iterable[PageMetricSnapshot]) -> list[PageAuditItem]:
    """Score the pages of one project against their shared click-loss scale."""
    pages = list(pages)
    volume_scale = _volume_scale(pages)
    audits = [_audit_page(page, volume_scale) for page in pages]
    return sorted(audits, key=lambda page: (-page.decay_score, page.url))


def find_ctr_opportunities(pages: Iterable[PageMetricSnapshot]) -> list[CtrOpportunityItem]:
    opportunities = []
    for page in pages:
        if not 1 <= page.curr_position <= 10 or page.curr_impressions < 300:
            continue
        expected = expected_ctr_for_position(page.curr_position)
        # Multiplying binary floats would admit some CTRs exactly at the cutoff.
        if Decimal(str(page.curr_ctr)) >= Decimal(str(expected)) * Decimal("0.75"):
            continue

        gap = round(expected - page.curr_ctr, 4)
        extra_clicks = max(1, round(page.curr_impressions * gap))
        score = (1 - page.curr_ctr / expected) * 50 + min(1.0, extra_clicks / 300) * 50
        opportunities.append(CtrOpportunityItem(
            url=page.url, curr_impressions=page.curr_impressions, curr_position=page.curr_position,
            curr_ctr=page.curr_ctr, expected_ctr=expected, ctr_gap=gap,
            potential_extra_clicks=extra_clicks, snippet_opportunity_score=score,
        ))
    return sorted(opportunities, key=lambda item: (-item.snippet_opportunity_score, item.url))


def _aggregate_query_url(url: str, rows: list[QueryPageMetric]) -> CompetingUrl:
    clicks = sum(row.curr_clicks for row in rows)
    impressions = sum(row.curr_impressions for row in rows)
    if len(rows) == 1:
        ctr = rows[0].curr_ctr
        position = rows[0].curr_position
    else:
        # Separate case/whitespace variants can exist for a single URL.
        ctr = clicks / impressions if impressions else 0.0
        positioned = [row for row in rows if row.curr_position > 0]
        position_weight = sum(row.curr_impressions for row in positioned)
        if position_weight:
            position = fsum(row.curr_position * row.curr_impressions for row in positioned) / position_weight
        else:
            position = fsum(row.curr_position for row in positioned) / len(positioned) if positioned else 0.0

    return CompetingUrl(
        url=url, curr_clicks=clicks, curr_impressions=impressions, curr_ctr=ctr,
        curr_position=position, click_share=0.0, impression_share=0.0, is_primary=False,
    )


def _resolution_strategy(competing: list[CompetingUrl], intents: set[str]) -> ResolutionStrategy:
    primary_position = competing[0].curr_position
    duplicate_positions = [url.curr_position for url in competing[1:] if url.curr_position > 0]
    if 0 < primary_position <= 5 and duplicate_positions and min(duplicate_positions) > 10:
        return "internal_linking_reanchor"
    if len(intents) > 1:
        return "canonical_or_deoptimize"
    return "301_redirect_and_merge"


def detect_cannibalization(rows: Iterable[QueryPageMetric]) -> list[CannibalizationCluster]:
    by_query = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_query[row.query.strip().lower()][row.url].append(row)

    clusters = []
    for query, by_url in by_query.items():
        if len(by_url) < 2:
            continue
        competing = sorted(
            (_aggregate_query_url(url, metrics) for url, metrics in by_url.items()),
            key=lambda item: (-item.curr_clicks, item.curr_position, -item.curr_impressions, item.url),
        )
        total_impressions = sum(item.curr_impressions for item in competing)
        if total_impressions < 200:
            continue
        total_clicks = sum(item.curr_clicks for item in competing)
        primary = competing[0]
        primary_volume = primary.curr_clicks if total_clicks else primary.curr_impressions
        if 100 * primary_volume >= 95 * (total_clicks or total_impressions):
            continue

        for item in competing:
            item.click_share = item.curr_clicks / total_clicks if total_clicks else 0.0
            item.impression_share = item.curr_impressions / total_impressions
        primary.is_primary = True

        shares = [item.click_share if total_clicks else item.impression_share for item in competing]
        dispersion = 1.0 - fsum(share ** 2 for share in shares)
        max_dispersion = (len(competing) - 1) / len(competing)
        norm_dispersion = min(1.0, dispersion / max_dispersion)
        score = round(min(100.0, norm_dispersion * 70 + min(1.0, total_impressions / 2000) * 30), 1)
        if total_clicks:
            lost_clicks = max(1, round(total_clicks * (0.15 + 0.15 * norm_dispersion)))
        else:
            lost_clicks = max(1, round(total_impressions * 0.01))

        intents = {row.intent.strip().lower() for metrics in by_url.values() for row in metrics}
        strategy = _resolution_strategy(competing, intents)
        clusters.append(CannibalizationCluster(
            normalized_query=query, primary_url=primary.url, competing_urls=competing,
            total_clicks=total_clicks, total_impressions=total_impressions,
            cannibalization_score=score, estimated_lost_clicks=lost_clicks,
            resolution_strategy=strategy, resolution_recommendation=_RESOLUTION_ACTIONS[strategy],
        ))
    return sorted(clusters, key=lambda cluster: (-cluster.cannibalization_score, cluster.normalized_query))


def build_action_plan(
    project: Project,
    pages: Iterable[PageAuditItem],
    opportunities: Iterable[CtrOpportunityItem],
    clusters: Iterable[CannibalizationCluster],
) -> list[ActionPlanItem]:
    """Credit each cluster once to its primary URL and use the strongest cluster per URL."""
    ctr_by_url = {item.url: item for item in opportunities}
    impact_by_url: dict[str, float] = {}
    bonus_by_url: Counter[str] = Counter()
    for cluster in clusters:
        bonus_by_url[cluster.primary_url] += cluster.estimated_lost_clicks
        for item in cluster.competing_urls:
            impact_by_url[item.url] = max(impact_by_url.get(item.url, 0.0), cluster.cannibalization_score)

    plan = []
    for page in pages:
        opportunity = ctr_by_url.get(page.url)
        if page.decay_score < 20 and opportunity is None and page.url not in impact_by_url:
            continue
        lost_clicks = max(0, -page.clicks_delta)
        extra_clicks = opportunity.potential_extra_clicks if opportunity else 0
        bonus_clicks = bonus_by_url[page.url]
        recoverable_clicks = round(
            lost_clicks * DECAY_RECOVERY_FACTOR + extra_clicks * CTR_CAPTURE_FACTOR + bonus_clicks
        )
        leads = round(recoverable_clicks * project.conversion_rate, 2)
        plan.append(ActionPlanItem(
            url=page.url, decay_status=page.decay_status, decay_score=page.decay_score,
            snippet_opportunity_score=opportunity.snippet_opportunity_score if opportunity else 0.0,
            cannibalization_impact=impact_by_url.get(page.url, 0.0), monetary_rank_score=0.0,
            lost_clicks=lost_clicks, potential_extra_clicks=extra_clicks,
            cannibalization_bonus_clicks=bonus_clicks, recoverable_clicks=recoverable_clicks,
            equivalent_ad_value=round(recoverable_clicks * project.default_cpc, 2),
            projected_leads=leads, projected_revenue_impact=round(leads * project.avg_lead_value, 2),
            currency=project.currency, priority_score=0.0,
        ))

    max_revenue = max((item.projected_revenue_impact for item in plan), default=0.0)
    for item in plan:
        if max_revenue > 0:
            item.monetary_rank_score = item.projected_revenue_impact / max_revenue * 100
        item.priority_score = (
            0.45 * item.decay_score
            + 0.25 * item.snippet_opportunity_score
            + 0.15 * item.cannibalization_impact
            + 0.15 * item.monetary_rank_score
        )
    return sorted(plan, key=lambda item: (-item.priority_score, item.url))


def build_project_summary(db: Session, project: Project) -> ProjectAuditSummary:
    """Read and aggregate metrics for a persisted project."""
    pages = list(db.scalars(select(PageMetricSnapshot).where(PageMetricSnapshot.project_id == project.id)))
    queries = db.scalars(select(QueryPageMetric).where(QueryPageMetric.project_id == project.id))
    audited = audit_pages(pages)
    opportunities = find_ctr_opportunities(pages)
    clusters = detect_cannibalization(queries)
    plan = build_action_plan(project, audited, opportunities, clusters)
    quarantined_rows = db.scalar(
        select(func.count()).select_from(QuarantineRecord).where(QuarantineRecord.project_id == project.id)
    )
    return summarize_audit(project, audited, opportunities, clusters, plan, quarantined_rows)


def summarize_audit(
    project: Project,
    audited: list[PageAuditItem],
    opportunities: list[CtrOpportunityItem],
    clusters: list[CannibalizationCluster],
    plan: list[ActionPlanItem],
    quarantined_rows: int,
) -> ProjectAuditSummary:
    """Aggregate reports computed from the same set of project metrics."""
    prev_clicks = sum(page.prev_clicks for page in audited)
    curr_clicks = sum(page.curr_clicks for page in audited)
    pages_by_status: dict[DecayStatus, int] = {
        "critical_decay": 0, "moderate_decay": 0, "early_warning": 0, "stable": 0, "growing": 0,
    }
    for page in audited:
        pages_by_status[page.decay_status] += 1

    return ProjectAuditSummary(
        project_id=project.id, currency=project.currency, pages_count=len(audited),
        prev_clicks=prev_clicks, curr_clicks=curr_clicks, clicks_delta=curr_clicks - prev_clicks,
        clicks_change_pct=_clicks_change(prev_clicks, curr_clicks), pages_by_status=pages_by_status,
        ctr_opportunities_count=len(opportunities), cannibalization_clusters_count=len(clusters),
        recoverable_clicks=sum(item.recoverable_clicks for item in plan),
        equivalent_ad_value=round(fsum(item.equivalent_ad_value for item in plan), 2),
        projected_leads=round(fsum(item.projected_leads for item in plan), 2),
        projected_revenue_impact=round(fsum(item.projected_revenue_impact for item in plan), 2),
        quarantined_rows_count=quarantined_rows,
    )
