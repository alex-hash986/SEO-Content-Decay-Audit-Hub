import json
from datetime import date

import pytest
from sqlalchemy import select

from app import database
from app.models import PageMetricSnapshot, QueryPageMetric
from app.services import importer


@pytest.fixture
def project_id(client):
    response = client.post("/api/projects", json={"name": "Import project", "domain": "example.org"})
    assert response.status_code == 201
    return response.json()["id"]


def upload(client, project_id, dataset, content, *, filename="report.json", preset="standard"):
    if isinstance(content, list):
        content = json.dumps(content, ensure_ascii=False).encode("utf-8")
    return client.post(
        f"/api/projects/{project_id}/import/{dataset}",
        params={"preset": preset},
        files={"file": (filename, content)},
    )


def assert_counts(response, *, total, valid, quarantined):
    assert response.status_code == 201, response.text
    batch = response.json()
    assert (batch["total_rows"], batch["valid_rows"], batch["quarantined_rows"]) == (
        total, valid, quarantined,
    )
    assert batch["created_at"].endswith("Z")
    return batch


@pytest.mark.parametrize("encoding", ["utf-8-sig", "cp1251"])
def test_yandex_csv_semicolon(client, project_id, encoding):
    content = (
        "АДРЕС СТРАНИЦЫ;Клики (стало);Показы (стало);CTR (%);Средняя позиция (стало);"
        "Клики (было);Показы (было);Средняя позиция (было)\r\n"
        " /blog/rag-guide ;1\u00a0250;10 000;12,5%;3,45;600;8 000;5\r\n"
    ).encode(encoding)
    response = upload(
        client, project_id, "pages", content, filename="pages.CSV", preset="yandex_webmaster",
    )
    batch = assert_counts(response, total=1, valid=1, quarantined=0)
    assert (batch["project_id"], batch["filename"], batch["preset"], batch["dataset_type"]) == (
        project_id, "pages.CSV", "yandex_webmaster", "pages",
    )

    with database.SessionLocal() as db:
        page = db.scalars(select(PageMetricSnapshot)).one()
        assert page.url == "/blog/rag-guide"
        assert (page.curr_clicks, page.curr_impressions) == (1250, 10000)
        assert page.curr_ctr == pytest.approx(0.125)
        assert page.curr_position == pytest.approx(3.45)
        assert page.prev_ctr == pytest.approx(600 / 8000)
        assert page.prev_position == 5


def test_gsc_queries_csv_percent_and_empty_ctr(client, project_id):
    content = (
        b'"TOP QUERIES","Page","Last 3 months clicks","Impressions","CTR","Position"\n'
        b'"widgets, blue",/shop,54,1000,5.4%,3\n'
        b'widgets green,/shop,25,100,,4\n'
    )
    response = upload(client, project_id, "queries", content, filename="query-export", preset="gsc")
    assert_counts(response, total=2, valid=2, quarantined=0)
    with database.SessionLocal() as db:
        queries = {row.query: row for row in db.scalars(select(QueryPageMetric))}
        blue = queries["widgets, blue"]
        green = queries["widgets green"]
        assert (blue.query, blue.url) == ("widgets, blue", "/shop")
        assert blue.curr_ctr == pytest.approx(0.054)
        assert blue.curr_position == 3
        assert (blue.prev_clicks, blue.prev_impressions, blue.intent) == (0, 0, "informational")
        assert green.curr_ctr == pytest.approx(0.25)


def test_gsc_pages_position_fallback_and_fractional_ctr():
    mapping = importer.column_mapping("pages", "gsc")
    values = importer.normalize_row(importer.mapped_values({
        "Страница": "/search",
        "Clicks (current)": "42",
        "Impressions (current)": "100",
        "CTR (current)": "0,42",
        "Position (current)": "12.3",
        "Previous CTR": "4,5%",
    }, mapping), "pages")
    assert values["curr_ctr"] == pytest.approx(0.42)
    assert values["prev_ctr"] == pytest.approx(0.045)
    assert values["prev_position"] == values["curr_position"] == pytest.approx(12.3)


def test_standard_json_metadata_and_defaults(client, project_id):
    rows = [
        {
            "URL": " https://example.org/a ", "page_title": " Page A ",
            "content_group": "blog", "published_at": "2024-02-29", "last_updated_at": "2025-01-01",
            "curr_clicks": 45, "curr_impressions": 1000, "curr_ctr": 0.045, "curr_position": 6,
        },
        {"url": "/b", "curr_clicks": 1},
    ]
    response = upload(client, project_id, "pages", rows, filename="export.data")
    assert_counts(response, total=2, valid=2, quarantined=0)
    with database.SessionLocal() as db:
        pages = {row.url: row for row in db.scalars(select(PageMetricSnapshot))}
        page = pages["https://example.org/a"]
        default_page = pages["/b"]
        assert (page.url, page.page_title, page.content_group) == ("https://example.org/a", "Page A", "blog")
        assert page.published_at == date(2024, 2, 29)
        assert page.last_updated_at == date(2025, 1, 1)
        assert (page.curr_ctr, page.prev_position) == (0.045, 6)
        assert default_page.content_group == "general"
        assert (default_page.curr_ctr, default_page.prev_ctr, default_page.curr_position) == (0, 0, 0)


@pytest.mark.parametrize("dataset,model", [("pages", PageMetricSnapshot), ("queries", QueryPageMetric)])
def test_upsert_last_valid_row_wins_and_project_isolation(client, project_id, dataset, model):
    key = {"url": "/article"}
    if dataset == "queries":
        key["query"] = "decay"
    initial = {**key, "curr_clicks": 1, "curr_impressions": 10}
    first = assert_counts(upload(client, project_id, dataset, [initial]), total=1, valid=1, quarantined=0)

    other_id = client.post("/api/projects", json={"name": "Other", "domain": "other.org"}).json()["id"]
    assert upload(client, other_id, dataset, [initial]).status_code == 201
    rows = [
        {**initial, "curr_clicks": 2},
        {**initial, "curr_clicks": 3},
        {**initial, "curr_clicks": -1},
    ]
    if dataset == "queries":
        rows.append({**initial, "query": "second query"})
    second = assert_counts(
        upload(client, project_id, dataset, rows), total=len(rows), valid=len(rows) - 1, quarantined=1,
    )
    with database.SessionLocal() as db:
        stored = db.scalars(select(model).order_by(model.id)).all()
        assert [row.curr_clicks for row in stored] == ([3, 1] if dataset == "pages" else [3, 1, 1])
        assert stored[0].project_id == project_id
        assert stored[0].curr_ctr == pytest.approx(0.3)
        assert stored[1].project_id == other_id

    response = client.get(f"/api/projects/{project_id}/import/batches")
    assert response.status_code == 200
    assert response.json() == [second, first]
    card = client.get(f"/api/projects/{project_id}").json()
    assert (card["pages_count"], card["queries_count"]) == ((1, 0) if dataset == "pages" else (0, 2))


BAD_PAGES = [
    ("url", ""),
    ("url", "x" * 1025),
    ("curr_clicks", -1),
    ("prev_clicks", -1),
    ("curr_impressions", -1),
    ("prev_impressions", -1),
    ("curr_clicks", 101),
    ("prev_clicks", 101),
    ("curr_ctr", "101%"),
    ("prev_ctr", "-0.01"),
    ("curr_ctr", 5),
    ("curr_position", 0),
    ("prev_position", 201),
    ("prev_position", "NaN"),
    ("curr_impressions", "abc"),
    ("curr_clicks", "2,5"),
    ("curr_clicks", True),
    ("prev_clicks", "2147483648"),
    ("published_at", "2025-02-29"),
    ("last_updated_at", "20250101"),
]


def test_quarantine_reasons_original_rows_and_filters(client, project_id):
    prefix = f"/api/projects/{project_id}"
    baseline = {"url": "/valid", "prev_impressions": 100, "curr_impressions": 100}
    rows = [baseline, *({**baseline, field: value} for field, value in BAD_PAGES)]
    pages = assert_counts(
        upload(client, project_id, "pages", rows),
        total=len(rows), valid=1, quarantined=len(BAD_PAGES),
    )
    invalid_queries = [{"url": "/query", "query": " "}, {"url": None, "query": "term"}]
    queries = assert_counts(
        upload(client, project_id, "queries", invalid_queries), total=2, valid=0, quarantined=2,
    )

    response = client.get(f"{prefix}/quarantine", params={"dataset_type": "pages", "batch_id": pages["id"]})
    assert response.status_code == 200
    records = response.json()
    assert len(records) == len(BAD_PAGES)
    for index, (record, (field, _)) in enumerate(zip(records, BAD_PAGES), start=2):
        assert record["row_number"] == index
        assert record["raw_payload"] == rows[index - 1]
        assert field in record["error_reason"]
        assert (record["batch_id"], record["project_id"], record["dataset_type"]) == (
            pages["id"], project_id, "pages",
        )

    responses = client.get(f"{prefix}/quarantine", params={"dataset_type": "queries"}).json()
    assert [row["raw_payload"] for row in responses] == invalid_queries
    assert [row["row_number"] for row in responses] == [1, 2]
    assert all(row["batch_id"] == queries["id"] for row in responses)
    assert client.get(f"{prefix}/quarantine", params={"dataset_type": "queries", "batch_id": pages["id"]}).json() == []
    assert len(client.get(f"{prefix}/quarantine").json()) == len(BAD_PAGES) + 2

    second_id = client.post("/api/projects", json={"name": "Second", "domain": "second.org"}).json()["id"]
    assert client.get(f"/api/projects/{second_id}/quarantine", params={"batch_id": pages["id"]}).json() == []
    assert client.get(f"/api/projects/{second_id}/import/batches").json() == []


def test_csv_quarantine_keeps_header_spelling_and_multiline_record_number(client, project_id):
    content = b'URL;PAGE_TITLE;CURR_CLICKS\n/good;"first line\nsecond line";10\n/bad;Original;-1\n'
    response = upload(client, project_id, "pages", content, filename="pages.csv")
    assert_counts(response, total=2, valid=1, quarantined=1)
    record, = client.get(f"/api/projects/{project_id}/quarantine").json()
    assert record["row_number"] == 2
    assert record["raw_payload"] == {"URL": "/bad", "PAGE_TITLE": "Original", "CURR_CLICKS": "-1"}


@pytest.mark.parametrize(("filename", "content", "detail"), [
    ("empty.csv", b"", "empty"),
    ("empty.json", b"[]", "no data rows"),
    ("empty.csv", b"url,curr_clicks\n", "no data rows"),
    ("data.json", b'[{"url": "/valid"},', "Invalid JSON"),
    ("data.json", b'{"url": "/valid"}', "array of objects"),
    ("data.json", b'[1]', "array of objects"),
    ("data.json", b'[{"url": "/x", "curr_clicks": NaN}]', "Invalid JSON"),
    ("data.json", br'[{"url": "\ud800"}]', "Invalid JSON"),
    ("data.csv", b"/no-header,1\n", "header"),
    ("data.csv", b'url,curr_clicks\n/valid,1\n"/broken,2', "Invalid CSV"),
])
def test_unreadable_file_returns_422_without_partial_import(client, project_id, filename, content, detail):
    response = upload(client, project_id, "pages", content, filename=filename)
    assert response.status_code == 422
    assert detail in response.json()["detail"]
    assert client.get(f"/api/projects/{project_id}/import/batches").json() == []
    assert client.get(f"/api/projects/{project_id}").json()["pages_count"] == 0


def test_request_validation_and_missing_project(client, project_id):
    rows = [{"url": "/a"}]
    assert upload(client, project_id, "pages", rows, preset="unknown").status_code == 422
    assert upload(client, 99999, "pages", rows).status_code == 404
    assert upload(client, 99999, "queries", rows).status_code == 404
    assert client.get("/api/projects/99999/import/batches").status_code == 404
    assert client.get("/api/projects/99999/quarantine").status_code == 404
    assert client.get(f"/api/projects/{project_id}/quarantine?dataset_type=other").status_code == 422


def test_database_failure_rolls_back_entire_import(client, project_id, monkeypatch):
    upsert = importer.upsert_metrics

    def fail_after_upsert(db, dataset_type, records):
        upsert(db, dataset_type, records)
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(importer, "upsert_metrics", fail_after_upsert)
    with pytest.raises(RuntimeError, match="simulated write failure"):
        upload(client, project_id, "pages", [{"url": "/valid"}, {"url": ""}])

    assert client.get(f"/api/projects/{project_id}/import/batches").json() == []
    assert client.get(f"/api/projects/{project_id}/quarantine").json() == []
    assert client.get(f"/api/projects/{project_id}").json()["pages_count"] == 0