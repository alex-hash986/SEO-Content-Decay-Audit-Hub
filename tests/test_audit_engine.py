import pytest

from app import database
from app.models import ImportBatch, PageMetricSnapshot, Project, QuarantineRecord, QueryPageMetric
from app.services.audit_engine import (
    _decay_status,
    audit_pages,
    build_action_plan,
    build_project_summary,
    detect_cannibalization,
    expected_ctr_for_position,
    find_ctr_opportunities,
)


def page(url="/page", **changes):
    values = dict(
        url=url, prev_clicks=0, curr_clicks=0, prev_impressions=0, curr_impressions=0,
        prev_ctr=0.0, curr_ctr=0.0, prev_position=1.0, curr_position=1.0,
    )
    return PageMetricSnapshot(**(values | changes))


def query(url, clicks, impressions, position, *, term="keyword", intent="informational"):
    return QueryPageMetric(
        query=term, url=url, curr_clicks=clicks, curr_impressions=impressions,
        curr_ctr=clicks / impressions if impressions else 0,
        curr_position=position, intent=intent,
    )


@pytest.mark.parametrize(("position", "expected"), [
    (-1, 0.28), (1, 0.28), (2, 0.155), (3, 0.105), (4, 0.075), (5, 0.058),
    (6, 0.045), (7, 0.036), (8, 0.030), (9, 0.025), (10, 0.021), (11, 0.016),
    (15, 0.010), (20, 0.006), (1.5, 0.2175), (10.5, 0.0185), (13, 0.013),
    (17.5, 0.008), (20.001, 0.005),
])
def test_expected_ctr_curve(position, expected):
    assert expected_ctr_for_position(position) == pytest.approx(expected)


def test_metric_deltas_rounding_and_zero_baselines():
    pages = [
        page("/deltas", prev_clicks=3, curr_clicks=4, prev_impressions=6, curr_impressions=7,
             prev_ctr=0.43789, curr_ctr=0.41234, prev_position=3.123, curr_position=4.456),
        page("/new", curr_clicks=12, curr_impressions=500),
        page("/empty"),
    ]
    by_url = {row.url: row for row in audit_pages(pages)}
    deltas, new, empty = (by_url[url] for url in ("/deltas", "/new", "/empty"))
    assert (deltas.clicks_delta, deltas.clicks_change_pct) == (1, 0.3333)
    assert (deltas.impressions_delta, deltas.impressions_change_pct) == (1, 0.1667)
    assert (deltas.ctr_delta, deltas.position_delta) == (-0.0256, 1.33)
    assert (new.clicks_change_pct, new.impressions_change_pct, new.decay_status) == (1.0, 0.0, "growing")
    assert (empty.clicks_change_pct, empty.impressions_change_pct, empty.decay_status) == (0.0, 0.0, "stable")


@pytest.mark.parametrize(("snapshot", "score", "status", "driver"), [
    (page(prev_clicks=1000, curr_clicks=300, prev_impressions=10000, curr_impressions=5000,
          prev_ctr=0.10, curr_ctr=0.06, curr_position=9), 100.0, "critical_decay", "ranking_drop"),
    (page(prev_clicks=100, curr_clicks=40), 52.5, "moderate_decay", "mixed_decay"),
    (page(prev_clicks=100, curr_clicks=70, prev_ctr=0.1, curr_ctr=0.06),
     36.2, "early_warning", "ctr_erosion"),
    (page(prev_clicks=100, curr_clicks=70, prev_impressions=1000, curr_impressions=750),
     28.8, "early_warning", "demand_shift"),
    (page(prev_clicks=10, curr_clicks=10, prev_impressions=100, curr_impressions=50,
          prev_ctr=0.1, curr_ctr=0.06, curr_position=9), 30.0, "early_warning", "mixed_decay"),
    (page(prev_clicks=100, curr_clicks=115), 0.0, "stable", "healthy"),
    (page(prev_clicks=100, curr_clicks=116), 0.0, "growing", "healthy"),
])
def test_decay_scores_statuses_and_drivers(snapshot, score, status, driver):
    result, = audit_pages([snapshot])
    assert (result.decay_score, result.decay_status, result.decay_driver) == (score, status, driver)
    assert result.recommended_action


@pytest.mark.parametrize(("score", "delta", "relative", "expected"), [
    (70, -1, -0.1, "critical_decay"), (69.9, -1, -0.1, "moderate_decay"),
    (40, -1, -0.1, "moderate_decay"), (39.9, -1, -0.1, "early_warning"),
    (20, 0, 0.0, "early_warning"), (19.9, 10, 0.1501, "growing"),
    (19.9, 9, 1.0, "stable"), (19.9, 10, 0.15, "stable"),
])
def test_status_cutoffs(score, delta, relative, expected):
    assert _decay_status(score, delta, relative) == expected


def test_volume_scale_uses_positive_losses_project_percentile_and_floor():
    small = page("/small", prev_clicks=10, curr_clicks=5)
    medium = page("/medium", prev_clicks=1000, curr_clicks=500)
    large = page("/large", prev_clicks=2000, curr_clicks=1000)
    assert audit_pages([small])[0].decay_score == 38.1

    result = audit_pages([small, medium, large, page("/no-loss"), page("/gain", curr_clicks=1500)])
    assert [row.url for row in result] == ["/large", "/medium", "/small", "/gain", "/no-loss"]
    # p90 of [5, 500, 1000] is 900. Every losing page changes by -50%.
    assert [row.decay_score for row in result[:3]] == [62.5, 51.4, 37.6]


def test_driver_thresholds_precedence_and_ctr_denominator_floor():
    rows = audit_pages([
        page("/rank", prev_clicks=100, curr_clicks=50, prev_ctr=0.02, curr_ctr=0.01,
             curr_position=3.5, prev_impressions=1000, curr_impressions=500),
        page("/ctr", prev_clicks=100, curr_clicks=50, prev_ctr=0.02, curr_ctr=0.01,
             curr_position=3.49, prev_impressions=1000, curr_impressions=500),
        page("/ctr-floor", prev_clicks=100, curr_clicks=50, prev_ctr=0.01, curr_ctr=0.008),
    ])
    by_url = {row.url: row for row in rows}
    assert by_url["/rank"].decay_driver == "ranking_drop"
    assert by_url["/ctr"].decay_driver == "ctr_erosion"
    assert by_url["/ctr-floor"].decay_score == 45.8
    assert by_url["/rank"].recommended_action.startswith("Глубокий рерайт")


def test_ctr_opportunity_thresholds_and_order():
    pages = [
        page("/low-impressions", curr_impressions=299),
        page("/below-top-ten", curr_impressions=999, curr_position=10.01),
        page("/unknown-position", curr_impressions=999, curr_position=0),
        page("/at-cutoff", curr_impressions=300, curr_ctr=0.21),
        page("/fractional-cutoff", curr_impressions=300, curr_ctr=0.163125, curr_position=1.5),
        page("/shortfall", curr_impressions=300, curr_ctr=0.14),
        page("/saturated", curr_impressions=2000),
        page("/exact-tenth", curr_impressions=300, curr_position=10, curr_ctr=0.015),
    ]
    saturated, shortfall, tenth = find_ctr_opportunities(reversed(pages))
    assert [item.url for item in (saturated, shortfall, tenth)] == ["/saturated", "/shortfall", "/exact-tenth"]
    assert saturated.potential_extra_clicks == 560
    assert saturated.snippet_opportunity_score == 100
    assert shortfall.expected_ctr == 0.28
    assert shortfall.ctr_gap == 0.14
    assert shortfall.potential_extra_clicks == 42
    assert shortfall.snippet_opportunity_score == pytest.approx(32.0)
    assert tenth.potential_extra_clicks == 2


def test_cannibalization_three_urls_normalization_tiebreakers_and_dispersion():
    rows = [
        query("/b", 30, 300, 4, term=" keyword "),
        query("/a", 30, 400, 4, term="KEYWORD"),
        query("/c", 30, 300, 5, term="Keyword"),
    ]
    result, = detect_cannibalization(rows)
    assert detect_cannibalization(reversed(rows)) == [result]
    assert (result.normalized_query, result.primary_url) == ("keyword", "/a")
    assert result.total_clicks == 90
    assert result.total_impressions == 1000
    assert [url.url for url in result.competing_urls] == ["/a", "/b", "/c"]
    assert [url.is_primary for url in result.competing_urls] == [True, False, False]
    assert [url.click_share for url in result.competing_urls] == pytest.approx([1 / 3] * 3)
    assert [url.impression_share for url in result.competing_urls] == [0.4, 0.3, 0.3]
    assert result.cannibalization_score == 85.0
    assert result.estimated_lost_clicks == 27


def test_cannibalization_merges_query_variants_for_same_url():
    rows = [
        query("/a", 9, 300, 2, term=" KEYWORD"),
        query("/a", 6, 100, 6, term="keyword "),
        query("/b", 15, 300, 7, term="keyword"),
    ]
    assert detect_cannibalization(rows[:2]) == []
    result, = detect_cannibalization(rows)
    lead = result.competing_urls[0]
    assert result.primary_url == "/a"
    assert (lead.curr_clicks, lead.curr_impressions, lead.curr_position) == (15, 400, 3)
    assert lead.curr_ctr == pytest.approx(15 / 400)


@pytest.mark.parametrize(("clicks", "impressions", "secondary_impressions"), [
    ((98, 2), 150, 150),
    ((95, 5), 100, 100),
    ((3, 2), 100, 99),
    ((0, 0), 196, 4),
])
def test_cannibalization_excludes_concentrated_or_small_clusters(clicks, impressions, secondary_impressions):
    assert detect_cannibalization([
        query("/p", clicks[0], impressions, 2),
        query("/s", clicks[1], secondary_impressions, 3),
    ]) == []


def test_cannibalization_falls_back_to_impressions_when_clicks_are_zero():
    result, = detect_cannibalization([query("/s", 0, 100, 4), query("/p", 0, 100, 2)])
    assert (result.primary_url, result.total_clicks, result.estimated_lost_clicks) == ("/p", 0, 2)
    assert result.cannibalization_score == 73
    assert [url.impression_share for url in result.competing_urls] == [0.5, 0.5]
    assert [url.click_share for url in result.competing_urls] == [0, 0]


@pytest.mark.parametrize(("primary", "secondary", "third", "intent", "strategy"), [
    (5, 11, 15, "commercial", "internal_linking_reanchor"),
    (5, 11, 10, "commercial", "canonical_or_deoptimize"),
    (5, 10, 11, "informational", "301_redirect_and_merge"),
    (5.01, 11, 15, "informational", "301_redirect_and_merge"),
    (0, 11, 15, "informational", "301_redirect_and_merge"),
])
def test_cannibalization_resolution_uses_best_duplicate(primary, secondary, third, intent, strategy):
    result, = detect_cannibalization([
        query("/primary", 50, 500, primary),
        query("/second", 30, 300, secondary, intent=intent),
        query("/third", 20, 200, third),
    ])
    assert result.resolution_strategy == strategy
    assert result.resolution_recommendation


def test_action_plan_values_money_weights_and_single_cluster_bonus():
    project = Project(default_cpc=2.5, conversion_rate=0.04, avg_lead_value=120, currency="EUR")
    pages = [
        page("/a", prev_clicks=100, curr_clicks=40, prev_impressions=1000, curr_impressions=1000,
             prev_ctr=0.2, curr_ctr=0.1),
        page("/b", prev_clicks=10, curr_clicks=20, prev_position=11, curr_position=11),
        page("/c", curr_impressions=300),
        page("/healthy"),
    ]
    clusters = detect_cannibalization([query("/a", 60, 500, 2), query("/b", 40, 500, 3)])
    lead, snippet, duplicate = build_action_plan(
        project, audit_pages(pages), find_ctr_opportunities(pages), clusters,
    )
    assert [item.url for item in (lead, snippet, duplicate)] == ["/a", "/c", "/b"]
    assert (lead.lost_clicks, lead.potential_extra_clicks, lead.cannibalization_bonus_clicks) == (60, 180, 29)
    assert (lead.recoverable_clicks, lead.equivalent_ad_value, lead.projected_leads) == (206, 515.0, 8.24)
    assert (lead.projected_revenue_impact, lead.currency, lead.monetary_rank_score) == (988.8, "EUR", 100)
    assert lead.priority_score == pytest.approx(0.45 * 62.5 + 0.25 * 62.1428571429 + 0.15 * 82.2 + 15)
    assert snippet.recoverable_clicks == 59
    assert snippet.monetary_rank_score == pytest.approx(2.36 / 8.24 * 100)
    assert duplicate.cannibalization_impact == 82.2
    assert duplicate.cannibalization_bonus_clicks == duplicate.recoverable_clicks == 0


def test_action_plan_zero_revenue_and_ties():
    project = Project(default_cpc=2, conversion_rate=0.0, avg_lead_value=120, currency="USD")
    pages = [page("/b", curr_impressions=300), page("/a", curr_impressions=300)]
    plan = build_action_plan(project, audit_pages(pages), find_ctr_opportunities(pages), [])
    assert [item.url for item in plan] == ["/a", "/b"]
    assert all(item.equivalent_ad_value == 118 for item in plan)
    assert all(item.projected_revenue_impact == item.monetary_rank_score == 0 for item in plan)



def test_action_plan_uses_strongest_cluster_impact_and_sums_primary_bonuses():
    project = Project(default_cpc=1, conversion_rate=0.1, avg_lead_value=10, currency="USD")
    pages = audit_pages([page("/a"), page("/b"), page("/c")])
    clusters = detect_cannibalization([
        query("/a", 50, 500, 2, term="strong"),
        query("/b", 50, 500, 3, term="strong"),
        query("/a", 10, 100, 2, term="weak"),
        query("/c", 10, 100, 3, term="weak"),
    ])
    assert [cluster.normalized_query for cluster in clusters] == ["strong", "weak"]
    plan = {item.url: item for item in build_action_plan(project, pages, [], clusters)}
    assert plan["/a"].cannibalization_impact == 85
    assert plan["/c"].cannibalization_impact == 73
    assert plan["/a"].cannibalization_bonus_clicks == 30 + 6
    assert plan["/b"].cannibalization_bonus_clicks == plan["/c"].cannibalization_bonus_clicks == 0

def test_empty_project_summary(client):
    with database.SessionLocal.begin() as db:
        project = Project(name="Empty", domain="empty.example", slug="empty")
        db.add(project)
        db.flush()
        summary = build_project_summary(db, project).model_dump()
        assert (summary.pop("project_id"), summary.pop("currency")) == (project.id, "USD")
        assert summary.pop("pages_by_status") == dict.fromkeys(
            ["critical_decay", "moderate_decay", "early_warning", "stable", "growing"], 0,
        )
        assert all(value == 0 for value in summary.values())


def test_populated_project_summary_isolates_project_and_aggregates_all_kpis(client):
    with database.SessionLocal.begin() as db:
        project = Project(
            name="Audit", domain="audit.example", slug="audit",
            default_cpc=2.5, conversion_rate=0.04, avg_lead_value=120, currency="EUR",
        )
        another = Project(name="Another", domain="other.example", slug="another")
        db.add_all([project, another])
        db.flush()
        project.page_snapshots.extend([
            page("/a", prev_clicks=100, curr_clicks=40, prev_impressions=1000, curr_impressions=1000,
                 prev_ctr=0.2, curr_ctr=0.1),
            page("/b", prev_clicks=10, curr_clicks=20, prev_position=11, curr_position=11),
        ])
        for owner in (project, another):
            owner.query_metrics.extend([query("/a", 60, 500, 2), query("/b", 40, 500, 3)])
            batch = ImportBatch(dataset_type="pages", preset="standard", filename="export.csv")
            owner.import_batches.append(batch)
            owner.quarantine_records.append(QuarantineRecord(
                batch=batch, dataset_type="pages", row_number=1, raw_payload={}, error_reason="url is required",
            ))
        another.page_snapshots.append(page("/a", curr_clicks=999))
        db.flush()

        summary = build_project_summary(db, project)
        assert (summary.project_id, summary.currency, summary.pages_count) == (project.id, "EUR", 2)
        assert (summary.prev_clicks, summary.curr_clicks, summary.clicks_delta) == (110, 60, -50)
        assert summary.clicks_change_pct == -0.4545
        assert summary.pages_by_status == {
            "critical_decay": 0, "moderate_decay": 1, "early_warning": 0, "stable": 0, "growing": 1,
        }
        assert summary.ctr_opportunities_count == summary.cannibalization_clusters_count == 1
        assert (summary.recoverable_clicks, summary.equivalent_ad_value) == (206, 515)
        assert (summary.projected_leads, summary.projected_revenue_impact) == (8.24, 988.8)
        assert summary.quarantined_rows_count == 1
