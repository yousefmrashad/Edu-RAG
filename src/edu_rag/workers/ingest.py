"""Turn a queued document into embedded chunks.

The document row is the queue: the API stores the PDF and sets
``status = 'uploaded'``, then hands the id to Celery. This task is the only
writer of ``chunks`` and the only thing that moves a document to ``ready``.
"""

import logging
import uuid
from collections.abc import Iterator
from functools import lru_cache
from io import BytesIO
from typing import cast

from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.transforms.chunker.doc_chunk import DocChunk
from docling_core.transforms.chunker.hybrid_chunker import HybridChunker

from edu_rag.celery_app import app
from edu_rag.clients.tei import SyncTeiEmbedder
from edu_rag.config import settings
from edu_rag.db import get_sync_session
from edu_rag.models import Chunk, Document
from edu_rag.tokenizer import ChunkerTokenizer, count_tokens

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _converter() -> DocumentConverter:
    """PDF converter with RapidOCR on the Paddle backend.

    Docling resolves the PP-OCR version from the installed rapidocr, which
    picks PP-OCRv6 for every language it serves. Build once per worker child.
    """
    options = PdfPipelineOptions()
    options.do_ocr = True
    options.ocr_options = RapidOcrOptions(backend="paddle", lang=["en"])
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )


@lru_cache(maxsize=1)
def _chunker() -> HybridChunker:
    """Chunk on the embedder's own tokenizer, so the window is real tokens."""
    return HybridChunker(tokenizer=ChunkerTokenizer(max_tokens=settings.chunk_size))


def _pages(chunk: DocChunk) -> tuple[int | None, int | None]:
    pages = [prov.page_no for item in chunk.meta.doc_items for prov in item.prov]
    return (min(pages), max(pages)) if pages else (None, None)


def _rows(
    document_id: uuid.UUID, chunks: list[DocChunk], vectors: list[list[float]]
) -> Iterator[Chunk]:
    for ordinal, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
        start, end = _pages(chunk)
        yield Chunk(
            document_id=document_id,
            ordinal=ordinal,
            text=chunk.text,
            headings=chunk.meta.headings or [],
            page_start=start,
            page_end=end,
            embedding=vector,
        )


def _embed(texts: list[str]) -> list[list[float]]:
    embedder = SyncTeiEmbedder(
        settings.embedder_url,
        embedding_dim=settings.embedding_dim,
        instruction=settings.query_instruction,
        timeout=settings.http_timeout,
        count_tokens=count_tokens,
    )
    try:
        return embedder.embed_documents(texts)
    finally:
        embedder.close()


@app.task(name="edu_rag.workers.ingest.ingest_document")
def ingest_document(document_id: str) -> None:
    """Parse, chunk, embed and store one document. Safe to call twice."""
    with get_sync_session()() as session:
        document = session.get(Document, uuid.UUID(document_id))
        if (
            document is None
            or document.status != "uploaded"
            or document.content is None
        ):
            logger.info("skipping %s: not queued", document_id)
            return

        document.status = "processing"
        session.commit()

        try:
            result = _converter().convert(
                DocumentStream(name="document.pdf", stream=BytesIO(document.content))
            )
            # HybridChunker yields DocChunk; the ABC widens it to BaseChunk.
            chunks = cast(list[DocChunk], list(_chunker().chunk(result.document)))
            vectors = _embed([chunk.text for chunk in chunks])

            session.add_all(_rows(document.id, chunks, vectors))

            document.status = "ready"
            document.content = None
            document.page_count = len(result.document.pages)
            session.commit()
        except Exception as exc:
            logger.exception("ingest failed for %s", document_id)
            session.rollback()
            document.status = "failed"
            document.error = str(exc)
            session.commit()
            raise
