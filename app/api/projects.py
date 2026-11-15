import re
import unicodedata
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import PageMetricSnapshot, Project, QueryPageMetric
from app.schemas import ProjectCreate, ProjectRead, ProjectUpdate
from app.services.demo_seeder import DemoSlugConflict, seed_demo_projects


router = APIRouter(prefix="/api/projects", tags=["projects"])
DbSession = Annotated[Session, Depends(get_db)]

pages_count = (
    select(func.count())
    .where(PageMetricSnapshot.project_id == Project.id)
    .scalar_subquery()
)
queries_count = (
    select(func.count())
    .where(QueryPageMetric.project_id == Project.id)
    .scalar_subquery()
)
projects_with_counts = select(Project, pages_count, queries_count)


def slug_from_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", name).lower()
    slug = re.sub(r"[\W_]+", "-", normalized).strip("-")
    return slug[:120].rstrip("-") or "project"


def project_response(project: Project, pages: int, queries: int) -> ProjectRead:
    return ProjectRead.model_validate(project).model_copy(
        update={"pages_count": pages, "queries_count": queries}
    )


def project_or_404(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("", response_model=list[ProjectRead])
def list_projects(db: DbSession) -> list[ProjectRead]:
    rows = db.execute(projects_with_counts.order_by(Project.id))
    return [project_response(project, pages, queries) for project, pages, queries in rows]


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: DbSession) -> ProjectRead:
    slug = payload.slug if payload.slug is not None else slug_from_name(payload.name)
    project = Project(**payload.model_dump(exclude={"slug"}), slug=slug)
    db.add(project)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if db.scalar(select(Project.id).where(Project.slug == slug)) is not None:
            raise HTTPException(status_code=409, detail="Project slug already exists") from error
        raise
    return ProjectRead.model_validate(project)


@router.post("/reset-demos")
def reset_demos(db: DbSession) -> list[ProjectRead]:
    try:
        projects = seed_demo_projects(db, force_reset=True)
    except DemoSlugConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    response = [
        project_response(project, len(project.page_snapshots), len(project.query_metrics))
        for project in projects
    ]
    db.commit()
    return response


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: int, db: DbSession) -> ProjectRead:
    row = db.execute(projects_with_counts.where(Project.id == project_id)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project_response(*row)


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(project_id: int, payload: ProjectUpdate, db: DbSession) -> ProjectRead:
    project = project_or_404(db, project_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    db.commit()
    return get_project(project_id, db)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: int, db: DbSession) -> Response:
    db.delete(project_or_404(db, project_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)