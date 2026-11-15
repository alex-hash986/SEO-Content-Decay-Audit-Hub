import re
from urllib.parse import quote

from fastapi import APIRouter, Response

from app.api.projects import DbSession, project_or_404
from app.services.exporter import build_csv_action_plan, build_excel_report, build_markdown_brief


router = APIRouter(prefix="/api/projects/{project_id}/export", tags=["exports"])


def _download(content: bytes | str, media_type: str, filename: str) -> Response:
    fallback = re.sub(r"[^a-zA-Z0-9_.-]+", "_", filename)
    disposition = f'attachment; filename="{fallback}"'
    if fallback != filename:
        disposition += f"; filename*=UTF-8''{quote(filename, safe='')}"
    return Response(
        content, media_type=media_type, headers={"Content-Disposition": disposition, "Cache-Control": "no-store"},
    )


@router.get("/xlsx", response_class=Response)
def export_xlsx(project_id: int, db: DbSession) -> Response:
    project = project_or_404(db, project_id)
    return _download(
        build_excel_report(db, project), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        f"seo_decay_audit_{project.slug}.xlsx",
    )


@router.get("/csv", response_class=Response)
def export_csv(project_id: int, db: DbSession) -> Response:
    project = project_or_404(db, project_id)
    return _download(build_csv_action_plan(db, project), "text/csv", f"seo_action_plan_{project.slug}.csv")


@router.get("/md", response_class=Response)
def export_markdown(project_id: int, db: DbSession) -> Response:
    project = project_or_404(db, project_id)
    return _download(build_markdown_brief(db, project), "text/markdown", f"seo_content_brief_{project.slug}.md")
