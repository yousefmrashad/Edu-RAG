"""Turn a queued document into embedded chunks.

The document row is the queue: the API stores the file and sets
``status = 'uploaded'``, then hands the id to Celery. This task is the only
writer of ``chunks`` and the only thing that moves a document to ``ready``.

The API records the upload's ``filename`` and ``content_type`` in
``doc_metadata``. The worker needs them because docling infers the format from
the stream name for text formats, which carry no magic bytes to sniff.
"""

import logging
import mimetypes
import uuid
from collections.abc import Iterator
from functools import lru_cache
from io import BytesIO
from pathlib import PurePosixPath
from typing import cast

from docling.datamodel.base_models import (
    DocumentStream,
    FormatToExtensions,
    InputFormat,
    MimeTypeToFormat,
)
from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
from docling.document_converter import (
    DocumentConverter,
    ImageFormatOption,
    PdfFormatOption,
)
from docling_core.transforms.chunker.doc_chunk import DocChunk
from docling_core.transforms.chunker.hybrid_chunker import HybridChunker

from edu_rag.celery_app import app
from edu_rag.clients.tei import SyncTeiEmbedder
from edu_rag.config import settings
from edu_rag.db import get_sync_session
from edu_rag.models import Chunk, Document
from edu_rag.tokenizer import ChunkerTokenizer, count_tokens

logger = logging.getLogger(__name__)

# Backends that need something the image does not install: LibreOffice for the
# legacy binary Office formats, odfdo for OpenDocument, arelle for XBRL, and a
# Whisper stack for audio and video. Everything else ships with docling's
# `standard` extra.
UNSUPPORTED_FORMATS = frozenset(
    {
        InputFormat.DOC,
        InputFormat.RTF,
        InputFormat.PPT,
        InputFormat.XLS,
        InputFormat.ODT,
        InputFormat.ODS,
        InputFormat.ODP,
        InputFormat.XML_XBRL,
        InputFormat.AUDIO,
        InputFormat.VIDEO,
    }
)
ALLOWED_FORMATS = [fmt for fmt in InputFormat if fmt not in UNSUPPORTED_FORMATS]


@lru_cache(maxsize=1)
def _converter() -> DocumentConverter:
    """Converter for every installed format, with OCR on the Paddle backend.

    Docling resolves the PP-OCR version from the installed rapidocr, which
    picks PP-OCRv6 for every language it serves. Images share the PDF pipeline,
    so they need the same options to OCR with Paddle instead of the default
    backend. Build once per worker child.
    """
    options = PdfPipelineOptions()
    options.do_ocr = True
    options.ocr_options = RapidOcrOptions(backend="paddle", lang=["en"])
    return DocumentConverter(
        allowed_formats=ALLOWED_FORMATS,
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=options),
        },
    )


@lru_cache(maxsize=1)
def _chunker() -> HybridChunker:
    """Chunk on the embedder's own tokenizer, so the window is real tokens."""
    return HybridChunker(tokenizer=ChunkerTokenizer(max_tokens=settings.chunk_size))


def _suffix(content_type: str) -> str:
    """The extension docling needs to tell text formats apart.

    Byte-sniffable formats do not care, but Markdown, HTML, CSV and plain text
    are all just text; without a matching suffix the converter falls back to
    PDF and fails.
    """
    mime = content_type.split(";", 1)[0].strip().lower()
    if not mime or mime in {"application/octet-stream", "binary/octet-stream"}:
        return ""
    formats = MimeTypeToFormat.get(mime)
    if formats:
        # An image's first extension is .jpg regardless of the actual type.
        if formats[0] is InputFormat.IMAGE:
            return mimetypes.guess_extension(mime) or ""
        extensions = FormatToExtensions.get(formats[0])
        if extensions:
            return f".{extensions[0]}"
    return mimetypes.guess_extension(mime) or ""


def _stream_name(document: Document) -> str:
    """A filename for the stream, preferring the upload's own name."""
    meta = document.doc_metadata or {}
    # Browsers send C:\fakepath\...; normalize before taking the basename.
    filename = (meta.get("filename") or "").replace("\\", "/").rsplit("/", 1)[-1]
    path = PurePosixPath(filename)
    stem = path.stem or PurePosixPath(document.title).stem or "document"
    suffix = path.suffix or _suffix(meta.get("content_type") or "")
    return f"{stem}{suffix}"


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
                DocumentStream(
                    name=_stream_name(document), stream=BytesIO(document.content)
                )
            )
            # HybridChunker yields DocChunk; the ABC widens it to BaseChunk.
            chunks = cast(list[DocChunk], list(_chunker().chunk(result.document)))
            vectors = _embed([chunk.text for chunk in chunks])

            session.add_all(_rows(document.id, chunks, vectors))

            document.status = "ready"
            document.content = None
            # DOCX, Markdown, HTML and friends have no pages; 0 would be a lie.
            document.page_count = len(result.document.pages) or None
            session.commit()
        except Exception as exc:
            logger.exception("ingest failed for %s", document_id)
            session.rollback()
            document.status = "failed"
            document.error = str(exc)
            session.commit()
            raise
