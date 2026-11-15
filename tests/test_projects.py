import pytest
from sqlalchemy import func, select

from app import database
from app.models import ImportBatch, PageMetricSnapshot, QuarantineRecord, QueryPageMetric


PROJECT = {"name": "Example Site", "domain": "example.com"}


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "SEO & Content Decay Audit Hub",
        "version": "0.1.0",
    }


def test_project_crud(client):
    assert client.get("/api/projects").json() == []

    response = client.post("/api/projects", json={**PROJECT, "description": "Initial audit"})
    assert response.status_code == 201
    created = response.json()
    assert created["slug"] == "example-site"
    assert created["default_cpc"] == 1.80
    assert created["conversion_rate"] == 0.025
    assert created["avg_lead_value"] == 150.0
    assert created["currency"] == "USD"
    assert created["is_demo"] is False
    assert created["created_at"].endswith("Z")
    assert (created["pages_count"], created["queries_count"]) == (0, 0)

    path = f"/api/projects/{created['id']}"
    assert client.get("/api/projects").json() == [created]
    response = client.get(path)
    assert response.status_code == 200
    assert response.json() == created

    changes = {"name": "Renamed Site", "description": None, "conversion_rate": 0, "currency": "EUR"}
    response = client.patch(path, json=changes)
    assert response.status_code == 200
    assert response.json() == {**created, **changes}

    response = client.delete(path)
    assert response.status_code == 204
    assert response.content == b""
    assert client.get("/api/projects").json() == []
    assert client.get(path).status_code == 404
    assert client.patch(path, json={"name": "Missing"}).status_code == 404
    assert client.delete(path).status_code == 404


@pytest.mark.parametrize("slug", [{}, {"slug": "example-site"}])
def test_duplicate_slug(client, slug):
    assert client.post("/api/projects", json=PROJECT).status_code == 201
    response = client.post("/api/projects", json={**PROJECT, **slug})
    assert response.status_code == 409
    assert len(client.get("/api/projects").json()) == 1
    assert client.post("/api/projects", json={**PROJECT, "slug": "second-site"}).status_code == 201


@pytest.mark.parametrize("field,value", [
    ("default_cpc", -1),
    ("conversion_rate", -0.1),
    ("conversion_rate", 1.1),
    ("avg_lead_value", -1),
    ("domain", None),
])
def test_invalid_project_parameters(client, field, value):
    response = client.post("/api/projects", json={**PROJECT, field: value})
    assert response.status_code == 422
    assert client.get("/api/projects").json() == []

    created = client.post("/api/projects", json=PROJECT).json()
    path = f"/api/projects/{created['id']}"
    assert client.patch(path, json={field: value}).status_code == 422
    assert client.get(path).json() == created


def test_project_counts_and_cascade_delete(client):
    project_id = client.post("/api/projects", json=PROJECT).json()["id"]
    second_id = client.post("/api/projects", json={**PROJECT, "slug": "second-site"}).json()["id"]
    url = "https://example.com/"
    with database.SessionLocal.begin() as db:
        db.add_all([
            PageMetricSnapshot(project_id=project_id, url=url),
            PageMetricSnapshot(project_id=second_id, url=url),
            QueryPageMetric(project_id=project_id, url=url, query="first"),
            QueryPageMetric(project_id=project_id, url=url, query="second"),
        ])
        batch = ImportBatch(project_id=project_id, dataset_type="pages", preset="standard", filename="pages.csv")
        db.add(QuarantineRecord(
            batch=batch,
            project_id=project_id,
            dataset_type="pages",
            row_number=2,
            raw_payload={"url": ""},
            error_reason="Missing URL",
        ))

    projects = client.get("/api/projects").json()
    assert [(row["id"], row["pages_count"], row["queries_count"]) for row in projects] == [
        (project_id, 1, 2),
        (second_id, 1, 0),
    ]
    assert client.delete(f"/api/projects/{project_id}").status_code == 204

    with database.SessionLocal() as db:
        assert db.scalars(select(PageMetricSnapshot.project_id)).all() == [second_id]
        for model in (QueryPageMetric, ImportBatch, QuarantineRecord):
            assert db.scalar(select(func.count()).select_from(model)) == 0