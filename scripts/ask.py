#!/usr/bin/env python3
"""Stream one answer from the Edu-RAG API into the terminal.

Usage:
    python scripts/ask.py --owner "$OWNER" "What is attention?"
    python scripts/ask.py --owner "$OWNER" --document-id <uuid> "What is attention?"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from typing import Any

import httpx


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="+", help="the question to ask")
    parser.add_argument(
        "--api",
        default="http://localhost:8000",
        help="base URL of the API (default: %(default)s)",
    )
    parser.add_argument(
        "--owner",
        default=os.environ.get("OWNER_ID"),
        help="X-Owner-Id header (default: $OWNER_ID, or a generated UUID)",
    )
    parser.add_argument(
        "--document-id",
        action="append",
        default=[],
        metavar="UUID",
        help="restrict retrieval to this document (repeatable)",
    )
    return parser.parse_args()


def _owner(value: str | None) -> str:
    if value:
        return value
    generated = str(uuid.uuid4())
    print(f"owner: {generated}", file=sys.stderr)
    return generated


def _citation(citation: dict[str, Any]) -> str:
    page = citation.get("page_start")
    if page is None:
        where = ""
    elif citation.get("page_end") in (None, page):
        where = f"page {page}"
    else:
        where = f"pages {page}-{citation['page_end']}"
    headings = " > ".join(citation.get("headings") or [])
    label = " · ".join(part for part in (where, headings) if part)
    text = citation.get("text", "").strip()
    return f"[{citation['index']}] {label}\n{text}"


def _handle(event: str, data: dict[str, Any]) -> bool:
    """Print one event; return False when the stream should stop."""
    if event == "generation":
        print(f"generation: {data['id']}", file=sys.stderr)
    elif event == "delta":
        print(data.get("text", ""), end="", flush=True)
    elif event == "citations":
        print()
        citations = data.get("citations") or []
        if citations:
            print("\nSources:")
            for citation in citations:
                print()
                print(_citation(citation))
    elif event == "error":
        print(f"\nerror: {data.get('message', 'unknown error')}", file=sys.stderr)
        return False
    return True


def main() -> int:
    args = _arguments()
    owner = _owner(args.owner)
    body: dict[str, Any] = {"question": " ".join(args.question)}
    if args.document_id:
        body["document_ids"] = args.document_id

    try:
        with httpx.Client(timeout=None) as client:
            with client.stream(
                "POST",
                f"{args.api.rstrip('/')}/generations",
                headers={"X-Owner-Id": owner},
                json=body,
            ) as response:
                if response.status_code != 200:
                    print(response.read().decode("utf-8", "replace"), file=sys.stderr)
                    return 1

                event: str | None = None
                lines: list[str] = []
                for line in response.iter_lines():
                    if line == "":
                        if event is not None:
                            payload = json.loads("\n".join(lines))
                            if not _handle(event, payload):
                                return 1
                        event, lines = None, []
                    elif line.startswith("event:"):
                        event = line.removeprefix("event:").strip()
                    elif line.startswith("data:"):
                        lines.append(line.removeprefix("data:").lstrip())
    except httpx.HTTPError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
