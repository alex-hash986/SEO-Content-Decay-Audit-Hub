import csv
import io
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.main import app


@pytest.fixture
def demo_export(isolated_database):
    with TestClient(app) as client:
        demo = client.get("/api/projects").json()[0]
        yield client, demo, f"/api/projects/{demo['id']}"


def test_excel_download_sheets_styles_formulas_and_values(demo_export):
    client, demo, prefix = demo_export
    response = client.get(f"{prefix}/export/xlsx")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert response.headers["content-disposition"] == f'attachment; filename="seo_decay_audit_{demo["slug"]}.xlsx"'

    summary = client.get(f"{prefix}/summary").json()
    plan = client.get(f"{prefix}/action-plan").json()
    with io.BytesIO(response.content) as stream:
        book = load_workbook(stream)
        try:
            assert book.sheetnames == [
                "Executive Summary", "Content Decay & Action Plan",
                "CTR Opportunities", "Cannibalization & Quarantine",
            ]
            assert "Алексей Ханипов" in book.properties.creator
            assert all(sheet.max_row > 1 and sheet.freeze_panes == "A2" for sheet in book)
            for sheet in book:
                assert sheet["A1"].fill.fgColor.rgb == "FF1E40AF"
                assert sheet["A1"].font.bold and sheet["A1"].font.color.rgb == "FFFFFFFF"
                assert max(col.width for col in sheet.column_dimensions.values()) <= 55

            executive, action, ctr, combined = book.worksheets
            assert executive["B1"].value == demo["name"]
            assert executive["B2"].value == demo["domain"]
            assert executive["B5"].value == demo["default_cpc"]
            assert executive["B6"].number_format == "0.0%"
            assert executive["B10"].value == summary["prev_clicks"]
            assert executive["B12"].data_type == "f"
            assert "Content Decay & Action Plan" in executive["B18"].value
            assert action["O2"].alignment.wrap_text
            assert action["K2"].number_format == "#,##0"
            assert action["L2"].number_format == "#,##0.00"
            assert [action.cell(row, 2).value for row in range(2, action.max_row + 1)] == [
                item["url"] for item in plan
            ]
            assert sum(action.cell(row, 11).value for row in range(2, action.max_row + 1)) == summary["recoverable_clicks"]
            assert ctr["D2"].number_format == "0.0%"
            assert ctr.max_row == summary["ctr_opportunities_count"] + 1
            assert combined.cell(summary["cannibalization_clusters_count"] + 2, 1).value == "Data Quarantine"
            assert combined.max_row == (
                summary["quarantined_rows_count"] + summary["cannibalization_clusters_count"] + 2
            )
        finally:
            book.close()


def test_csv_download_uses_bom_semicolon_and_project_plan(demo_export):
    client, demo, prefix = demo_export
    response = client.get(f"{prefix}/export/csv")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == f'attachment; filename="seo_action_plan_{demo["slug"]}.csv"'
    assert response.content.startswith(b"\xef\xbb\xbfPriority Score;URL;")
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";"))
    plan = client.get(f"{prefix}/action-plan").json()
    assert [row["URL"] for row in rows] == [item["url"] for item in plan]
    assert float(rows[0]["Эквивалент бюджета"].replace(",", ".")) == plan[0]["equivalent_ad_value"]


def test_markdown_download_uses_project_totals_and_priorities(demo_export):
    client, demo, prefix = demo_export
    response = client.get(f"{prefix}/export/md")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert response.headers["content-disposition"] == f'attachment; filename="seo_content_brief_{demo["slug"]}.md"'
    assert response.text.startswith("# ТЗ для редакции")
    for section in ("Сводка потерь", "Топ-10 страниц", "Устранение каннибализации", "[ ]", "Прогноз выручки", demo["currency"]):
        assert section in response.text
    summary = client.get(f"{prefix}/summary").json()
    assert f'Возвращаемый трафик — {summary["recoverable_clicks"]} кликов' in response.text
    clusters = client.get(f"{prefix}/cannibalization").json()
    assert all(cluster["normalized_query"] in response.text for cluster in clusters)
    assert "\n### 10." in response.text
    assert "\n### 11." not in response.text


def test_empty_project_exports_and_unicode_filename(client):
    slug = "аудит-2025"
    created = client.post("/api/projects", json={"name": "Проверка", "domain": "example.org", "slug": slug})
    prefix = f"/api/projects/{created.json()['id']}"
    excel = client.get(f"{prefix}/export/xlsx")
    assert excel.status_code == 200
    assert f"filename*=UTF-8''{quote(f'seo_decay_audit_{slug}.xlsx')}" in excel.headers["content-disposition"]
    book = load_workbook(io.BytesIO(excel.content))
    try:
        assert len(book.worksheets) == 4
        assert book["Content Decay & Action Plan"].max_row == 1
        assert book["Executive Summary"]["B18"].value == 0
    finally:
        book.close()
    csv_response = client.get(f"{prefix}/export/csv")
    assert len(csv_response.content.decode("utf-8-sig").splitlines()) == 1
    assert "приоритетных страниц нет" in client.get(f"{prefix}/export/md").text


def test_imported_text_cannot_become_a_spreadsheet_formula(client):
    created = client.post("/api/projects", json={"name": "Export", "domain": "example.org"})
    prefix = f"/api/projects/{created.json()['id']}"
    imported = client.post(
        f"{prefix}/import/pages",
        files={"file": ("metrics.json", b'[{"url":"=1+1","page_title":"=2+2","curr_impressions":300,"curr_position":1}]')},
    )
    assert imported.status_code == 201
    book = load_workbook(io.BytesIO(client.get(f"{prefix}/export/xlsx").content))
    try:
        assert book.worksheets[1]["B2"].value == "=1+1"
        assert book.worksheets[1]["B2"].data_type == "s"
        assert book.worksheets[1]["C2"].data_type == "s"
    finally:
        book.close()
    row, = csv.DictReader(io.StringIO(client.get(f"{prefix}/export/csv").content.decode("utf-8-sig")), delimiter=";")
    assert row["URL"] == "'=1+1"


@pytest.mark.parametrize("extension", ["xlsx", "csv", "md"])
def test_export_unknown_project_returns_404(client, extension):
    assert client.get(f"/api/projects/999999/export/{extension}").status_code == 404


def test_spa_and_static_assets(client):
    index = client.get("/")
    assert index.status_code == 200
    assert index.headers["content-type"].startswith("text/html")
    assert "<title>SEO &amp; Content Decay Audit Hub</title>" in index.text
    assert 'id="project-select"' in index.text
    assert index.text.count('role="tabpanel"') == 5
    assert "/static/styles.css" in index.text and "/static/app.js" in index.text
    css = client.get("/static/styles.css")
    js = client.get("/static/app.js")
    assert css.status_code == js.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert "javascript" in js.headers["content-type"]
    assert "#1e40af" in css.text
    assert 'typeof Chart !== "undefined"' in js.text
