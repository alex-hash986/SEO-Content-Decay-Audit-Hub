import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import database
from app.main import app
from app.models import ImportBatch, PageMetricSnapshot, Project, QuarantineRecord, QueryPageMetric


CYBERNEURO = "cyberneuro-blog-ai-agency"
FLOWSTACK = "flowstack-b2b-saas-kb"
REPORTS = ("summary", "decay-pages", "ctr-opportunities", "cannibalization", "action-plan")


@pytest.fixture
def demo_client(isolated_database):
    with TestClient(app) as client:
        yield client


def get_json(client, path, **params):
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def projects_by_slug(client):
    return {row["slug"]: row for row in get_json(client, "/api/projects")}


def create_project(client, **kwargs):
    payload = {"name": "Custom workspace", "domain": "custom.example", **kwargs}
    response = client.post("/api/projects", json=payload)
    assert response.status_code == 201
    return response.json()


def test_startup_creates_two_demos_and_restart_leaves_data_intact(isolated_database):
    with TestClient(app) as client:
        projects = projects_by_slug(client)
        assert set(projects) == {CYBERNEURO, FLOWSTACK}
        for slug, name, domain, currency, cpc, conversion, value, pages, queries in [
            (CYBERNEURO, "CyberNeuro Blog & AI Agency", "cyberneuro.ru", "RUB", 240, 0.022, 45000, 18, 26),
            (FLOWSTACK, "FlowStack B2B SaaS Knowledge Base", "flowstack.io", "USD", 4.50, 0.03, 600, 16, 22),
        ]:
            project = projects[slug]
            assert project["is_demo"] is True
            assert project["description"]
            assert [project[key] for key in (
                "name", "domain", "currency", "default_cpc", "conversion_rate",
                "avg_lead_value", "pages_count", "queries_count",
            )] == [name, domain, currency, cpc, conversion, value, pages, queries]
        cyber = projects[CYBERNEURO]
        assert client.patch(f"/api/projects/{cyber['id']}", json={"default_cpc": 300}).status_code == 200
        projects = projects_by_slug(client)

    with TestClient(app) as client:
        assert projects_by_slug(client) == projects
        for slug, preset, quantity in [(CYBERNEURO, "yandex_webmaster", 4), (FLOWSTACK, "gsc", 3)]:
            project = projects[slug]
            path = f"/api/projects/{project['id']}"
            batch, = get_json(client, f"{path}/import/batches")
            rows = get_json(client, f"{path}/quarantine")
            assert batch["preset"] == preset
            assert batch["dataset_type"] == "pages"
            assert (batch["valid_rows"], batch["quarantined_rows"], batch["total_rows"]) == (
                project["pages_count"], quantity, project["pages_count"] + quantity,
            )
            assert len(rows) == quantity
            assert all(row["batch_id"] == batch["id"] and row["error_reason"] for row in rows)


def test_startup_skips_database_containing_only_user_projects(isolated_database):
    database.init_db()
    with database.SessionLocal.begin() as db:
        db.add(Project(name="Existing", slug="existing", domain="existing.example"))

    with TestClient(app) as client:
        projects = projects_by_slug(client)
        assert set(projects) == {"existing"}
        assert projects["existing"]["is_demo"] is False


def test_reset_restores_both_demos_and_preserves_user_data(demo_client):
    client = demo_client
    demos = projects_by_slug(client)
    cyber_path = f"/api/projects/{demos[CYBERNEURO]['id']}"
    flow_path = f"/api/projects/{demos[FLOWSTACK]['id']}"
    user = create_project(client)
    user_path = f"/api/projects/{user['id']}"
    imported = client.post(
        f"{user_path}/import/pages",
        files={"file": ("custom.csv", b"url,curr_clicks\n/keep,12\n/rejected,-2\n")},
    )
    assert imported.status_code == 201
    user_state = [get_json(client, user_path + suffix) for suffix in ("", "/summary", "/quarantine")]
    reference_summary = get_json(client, f"{cyber_path}/summary")
    assert client.patch(cyber_path, json={"name": "Modified", "default_cpc": 1}).status_code == 200
    assert client.delete(flow_path).status_code == 204

    response = client.post("/api/projects/reset-demos")
    assert response.status_code == 200
    restored = {row["slug"]: row for row in response.json()}
    assert set(restored) == {CYBERNEURO, FLOWSTACK}
    for slug, project in restored.items():
        assert project["is_demo"] is True
        for field in ("name", "domain", "currency", "default_cpc", "pages_count", "queries_count"):
            assert project[field] == demos[slug][field]
    assert projects_by_slug(client) == {**restored, user["slug"]: user_state[0]}
    assert [get_json(client, user_path + suffix) for suffix in ("", "/summary", "/quarantine")] == user_state
    new_summary = get_json(client, f"/api/projects/{restored[CYBERNEURO]['id']}/summary")
    assert new_summary == {**reference_summary, "project_id": restored[CYBERNEURO]["id"]}

    assert client.post("/api/projects/reset-demos").status_code == 200
    with database.SessionLocal() as db:
        for model, expected in (
            (Project, 3), (PageMetricSnapshot, 35), (QueryPageMetric, 48),
            (ImportBatch, 3), (QuarantineRecord, 8),
        ):
            assert db.scalar(select(func.count()).select_from(model)) == expected


def test_reset_reports_reserved_slug_collision_without_changing_projects(demo_client):
    client = demo_client
    cyber = projects_by_slug(client)[CYBERNEURO]
    assert client.delete(f"/api/projects/{cyber['id']}").status_code == 204
    create_project(client, slug=CYBERNEURO)
    before = projects_by_slug(client)

    response = client.post("/api/projects/reset-demos")
    assert response.status_code == 409
    assert CYBERNEURO in response.json()["detail"]
    assert projects_by_slug(client) == before


@pytest.mark.parametrize(
    ("slug", "pages_count", "cluster_count", "quarantine_count", "statuses", "example_url"),
    [
        (CYBERNEURO, 18, 5, 4, (4, 4, 3, 4, 3), "/blog/ai-agents-b2b-sales-2024"),
        (FLOWSTACK, 16, 4, 3, (2, 2, 2, 6, 4), "/comparisons/flowstack-vs-notion"),
    ],
)
def test_all_reports_match_each_demo_project(
    demo_client, slug, pages_count, cluster_count, quarantine_count, statuses, example_url,
):
    client = demo_client
    project = projects_by_slug(client)[slug]
    prefix = f"/api/projects/{project['id']}"
    summary, pages, opportunities, clusters, plan = [
        get_json(client, f"{prefix}/{report}") for report in REPORTS
    ]
    assert summary["project_id"] == project["id"]
    assert summary["currency"] == project["currency"]
    assert summary["pages_count"] == len(pages) == pages_count
    assert summary["prev_clicks"] == sum(row["prev_clicks"] for row in pages)
    assert summary["curr_clicks"] == sum(row["curr_clicks"] for row in pages)
    assert summary["pages_by_status"] == dict(zip(
        ("critical_decay", "moderate_decay", "early_warning", "stable", "growing"), statuses,
    ))
    assert summary["ctr_opportunities_count"] == len(opportunities) == 3
    assert summary["cannibalization_clusters_count"] == len(clusters) == cluster_count
    assert summary["quarantined_rows_count"] == quarantine_count
    assert next(row for row in pages if row["url"] == example_url)["decay_status"] == "critical_decay"
    assert {cluster["resolution_strategy"] for cluster in clusters} == {
        "301_redirect_and_merge", "canonical_or_deoptimize", "internal_linking_reanchor",
    }
    for rows, key in (
        (pages, "decay_score"), (opportunities, "snippet_opportunity_score"),
        (clusters, "cannibalization_score"), (plan, "priority_score"),
    ):
        assert rows
        assert [row[key] for row in rows] == sorted((row[key] for row in rows), reverse=True)
    assert all(row["currency"] == project["currency"] for row in plan)
    for key in ("recoverable_clicks", "equivalent_ad_value", "projected_leads", "projected_revenue_impact"):
        assert summary[key] == pytest.approx(sum(row[key] for row in plan))


def test_decay_filters_combine_preserving_project_scores_and_match_titles(demo_client):
    client = demo_client
    project = projects_by_slug(client)[CYBERNEURO]
    path = f"/api/projects/{project['id']}/decay-pages"
    all_rows = get_json(client, path)
    by_url = {row["url"]: row for row in all_rows}

    url = "/blog/getcourse-n8n-automation"
    assert get_json(
        client, path, status="critical_decay", content_group="blog",
        search="АВТОМАТИЗАЦИЯ", min_decay_score=90,
    ) == [by_url[url]]
    assert get_json(client, path, search="/BLOG/GETCOURSE-N8N") == [by_url[url]]
    assert get_json(client, path, content_group="missing") == []
    assert get_json(client, path, min_decay_score=100) == []


@pytest.mark.parametrize("field", ["decay_score", "clicks_delta", "curr_clicks", "position_delta"])
@pytest.mark.parametrize("order", ["asc", "desc"])
def test_decay_sort_options_and_url_tiebreak(demo_client, field, order):
    client = demo_client
    project = projects_by_slug(client)[FLOWSTACK]
    rows = get_json(client, f"/api/projects/{project['id']}/decay-pages", sort_by=field, order=order)
    direction = 1 if order == "asc" else -1
    keys = [(direction * row[field], row["url"]) for row in rows]
    assert keys == sorted(keys)


def test_other_report_filters(demo_client):
    client = demo_client
    project = projects_by_slug(client)[FLOWSTACK]
    prefix = f"/api/projects/{project['id']}"
    opportunities = get_json(client, f"{prefix}/ctr-opportunities")
    result = get_json(client, f"{prefix}/ctr-opportunities", min_impressions=5400, max_position=3.6)
    assert result == opportunities[:2]
    assert get_json(client, f"{prefix}/ctr-opportunities", min_impressions=8500) == []

    clusters = get_json(client, f"{prefix}/cannibalization")
    target = next(row for row in clusters if row["normalized_query"] == "quarterly business review template")
    assert get_json(client, f"{prefix}/cannibalization", search="BUSINESS REVIEW") == [target]
    assert get_json(client, f"{prefix}/cannibalization", search="/DOCS/REPORTS/QBR") == [target]
    assert get_json(client, f"{prefix}/cannibalization", search="missing") == []

    plan = get_json(client, f"{prefix}/action-plan")
    cutoff = plan[2]["priority_score"]
    assert get_json(client, f"{prefix}/action-plan", min_priority=cutoff, limit=2) == plan[:2]
    assert get_json(client, f"{prefix}/action-plan", min_priority=cutoff) == [
        row for row in plan if row["priority_score"] >= cutoff
    ]
    assert get_json(client, f"{prefix}/action-plan", limit=0) == []


@pytest.mark.parametrize("slug", [CYBERNEURO, FLOWSTACK])
def test_patch_cpc_recomputes_ad_value_without_reimport(demo_client, slug):
    client = demo_client
    project = projects_by_slug(client)[slug]
    prefix = f"/api/projects/{project['id']}"
    previous = get_json(client, f"{prefix}/summary")
    old_plan = get_json(client, f"{prefix}/action-plan")
    assert previous["equivalent_ad_value"] > 0
    cpc = project["default_cpc"] * 2
    assert client.patch(prefix, json={"default_cpc": cpc}).status_code == 200

    new_plan = get_json(client, f"{prefix}/action-plan")
    assert new_plan == [
        {**row, "equivalent_ad_value": round(row["recoverable_clicks"] * cpc, 2)} for row in old_plan
    ]
    assert get_json(client, f"{prefix}/summary") == {
        **previous, "equivalent_ad_value": previous["equivalent_ad_value"] * 2,
    }


@pytest.mark.parametrize("report", REPORTS)
def test_audit_returns_404_for_unknown_project(demo_client, report):
    response = demo_client.get(f"/api/projects/999999/{report}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Project not found"}


@pytest.mark.parametrize(("report", "params"), [
    ("decay-pages", {"status": "expired"}),
    ("decay-pages", {"sort_by": "name"}),
    ("decay-pages", {"order": "up"}),
    ("decay-pages", {"min_decay_score": 101}),
    ("ctr-opportunities", {"min_impressions": -1}),
    ("ctr-opportunities", {"max_position": 0}),
    ("action-plan", {"min_priority": -0.1}),
    ("action-plan", {"limit": -1}),
])
def test_audit_validates_query_filters(demo_client, report, params):
    project = projects_by_slug(demo_client)[CYBERNEURO]
    response = demo_client.get(f"/api/projects/{project['id']}/{report}", params=params)
    assert response.status_code == 422
