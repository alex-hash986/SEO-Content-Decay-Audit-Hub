from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from sqlalchemy import select

from app.api.projects import DbSession, project_or_404
from app.models import ImportBatch, QuarantineRecord
from app.schemas import DatasetType, ImportBatchRead, ImportPreset, QuarantineRecordRead
from app.services.importer import ImportFileError, import_dataset


router = APIRouter(prefix="/api/projects/{project_id}", tags=["imports"])
ImportUpload = Annotated[UploadFile, File()]


def upload_dataset(
    project_id: int,
    dataset_type: DatasetType,
    file: UploadFile,
    preset: ImportPreset,
    db: DbSession,
) -> ImportBatchRead:
    project_or_404(db, project_id)
    try:
        batch = import_dataset(
            db, project_id, dataset_type, preset, file.filename or "upload", file.file.read()
        )
    except ImportFileError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    response = ImportBatchRead.model_validate(batch)
    db.commit()
    return response


@router.post("/import/pages", status_code=status.HTTP_201_CREATED)
def import_pages(
    project_id: int, file: ImportUpload, db: DbSession, preset: ImportPreset = "standard",
) -> ImportBatchRead:
    return upload_dataset(project_id, "pages", file, preset, db)


@router.post("/import/queries", status_code=status.HTTP_201_CREATED)
def import_queries(
    project_id: int, file: ImportUpload, db: DbSession, preset: ImportPreset = "standard",
) -> ImportBatchRead:
    return upload_dataset(project_id, "queries", file, preset, db)


@router.get("/import/batches", response_model=list[ImportBatchRead])
def list_batches(project_id: int, db: DbSession) -> list[ImportBatch]:
    project_or_404(db, project_id)
    statement = (
        select(ImportBatch)
        .where(ImportBatch.project_id == project_id)
        .order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc())
    )
    return list(db.scalars(statement))


@router.get("/quarantine", response_model=list[QuarantineRecordRead])
def list_quarantine(
    project_id: int, db: DbSession, batch_id: int | None = None, dataset_type: DatasetType | None = None,
) -> list[QuarantineRecord]:
    project_or_404(db, project_id)
    statement = select(QuarantineRecord).where(QuarantineRecord.project_id == project_id)
    if batch_id is not None:
        statement = statement.where(QuarantineRecord.batch_id == batch_id)
    if dataset_type is not None:
        statement = statement.where(QuarantineRecord.dataset_type == dataset_type)
    return list(db.scalars(statement.order_by(QuarantineRecord.batch_id.desc(), QuarantineRecord.row_number)))