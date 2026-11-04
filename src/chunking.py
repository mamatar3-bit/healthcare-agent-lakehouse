"""Markdown-aware chunking for policy documents (pure Python, unit-tested)."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path


def _split_long(text: str, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks, current = [], ""
    for s in sentences:
        if current and len(current) + len(s) + 1 > max_chars:
            chunks.append(current.strip())
            current = current[-overlap:] + " " + s if overlap else s
        else:
            current = f"{current} {s}" if current else s
    if current.strip():
        chunks.append(current.strip())
    return chunks


def chunk_markdown(doc_name: str, markdown: str, max_chars: int = 1200, overlap: int = 150) -> list[dict]:
    """One chunk per '## ' section (long sections split on sentences). Each chunk carries the
    document title and section heading so it reads well on its own in retrieval."""
    title_match = re.search(r"^# (.+)$", markdown, flags=re.M)
    title = title_match.group(1).strip() if title_match else doc_name
    parts = re.split(r"^## ", markdown, flags=re.M)
    preamble, sections = parts[0], parts[1:]

    rows: list[dict] = []
    intro = re.sub(r"^# .+$", "", preamble, flags=re.M).strip()
    pieces = ([("Overview", intro)] if intro else []) + [
        (s.split("\n", 1)[0].strip(), s.split("\n", 1)[1].strip() if "\n" in s else "") for s in sections
    ]
    for section, body in pieces:
        if not body:
            continue
        for i, part in enumerate(_split_long(" ".join(body.split()), max_chars, overlap)):
            content = f"{title} — {section}\n\n{part}"
            chunk_id = hashlib.sha1(f"{doc_name}|{section}|{i}".encode()).hexdigest()[:16]
            rows.append({"chunk_id": chunk_id, "doc_name": doc_name, "doc_title": title,
                         "section": section, "chunk_index": i, "content": content})
    return rows


def chunk_folder(folder: str | Path) -> list[dict]:
    rows = []
    for path in sorted(Path(folder).glob("*.md")):
        rows.extend(chunk_markdown(path.stem, path.read_text(encoding="utf-8")))
    return rows
