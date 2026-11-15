"""Upload, list, and fetch documents."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Sequence
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from edu_rag.api.deps import OwnerDep, SessionDep
from edu_rag.celery_app import INGEST_TASK
from edu_rag.celery_app import app as celery_app
from edu_rag.config import settings
from edu_rag.models import Document

logger = logging.getLogger(__name__)

router = APIRouter()


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    status: str
    page_count: int | None
    error: str | None
    doc_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@router.post("/documents", response_model=DocumentOut, status_code=201)
async def upload_document(
    response: Response,
    file: Annotated[UploadFile, File()],
    session: SessionDep,
    owner_id: OwnerDep,
    title: Annotated[str | None, Form()] = None,
) -> Document:
    """Store the bytes and queue the ingest task. Identical bytes reuse a row."""
    data = await _read_upload(file)
    digest = hashlib.sha256(data).hexdigest()
    filename = _filename(file.filename)
    doc_metadata = {
        "filename": filename,
        "content_type": file.content_type or "application/octet-stream",
    }

    document = await session.scalar(
        select(Document).where(
            Document.owner_id == owner_id,
            Document.sha256 == digest,
        )
    )
    if document is not None and document.status != "failed":
        response.status_code = 200
        return document

    if document is not None:
        document.content = data
        document.status = "uploaded"
        document.error = None
        document.doc_metadata = doc_metadata
        document.title = title or document.title
    else:
        document = Document(
            owner_id=owner_id,
            sha256=digest,
            title=title or PurePosixPath(filename).stem or "Untitled",
            doc_metadata=doc_metadata,
            content=data,
            status="uploaded",
        )
        session.add(document)

    await session.commit()
    await session.refresh(document)

    try:
        await asyncio.to_thread(
            celery_app.send_task, INGEST_TASK, args=[str(document.id)]
        )
    except Exception as exc:
        logger.exception("could not queue %s", document.id)
        document.status = "failed"
        document.error = f"could not queue: {exc}"
        await session.commit()
        raise HTTPException(503, "could not queue the document") from exc

    return document


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(
    session: SessionDep,
    owner_id: OwnerDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Sequence[Document]:
    stmt = (
        select(Document)
        .where(Document.owner_id == owner_id)
        .order_by(Document.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return (await session.scalars(stmt)).all()


@router.get("/documents/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID,
    session: SessionDep,
    owner_id: OwnerDep,
) -> Document:
    document = await session.get(Document, document_id)
    if document is None or document.owner_id != owner_id:
        raise HTTPException(404, "document not found")
    return document


async def _read_upload(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > settings.max_upload_bytes:
            raise HTTPException(413, "file is too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _filename(name: str | None) -> str:
    # Browsers send C:\fakepath\...; normalize before taking the basename.
    return (name or "upload").replace("\\", "/").rsplit("/", 1)[-1]
