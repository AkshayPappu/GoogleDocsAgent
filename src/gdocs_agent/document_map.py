"""Turn a Google Doc into a short outline and resolve edit targets.

Google Docs addresses characters with UTF-16 indexes that move after every
write. Callers re-read the document and pass a paragraph id or heading from
that outline. This module maps those handles back to indexes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_DOC_URL = re.compile(r"/document/d/([a-zA-Z0-9-_]+)")
_ID_PARAM = re.compile(r"[?&]id=([a-zA-Z0-9-_]+)")
_BARE_ID = re.compile(r"^[a-zA-Z0-9-_]{20,}$")
_BLOCK_ID = re.compile(r"^(?:[pt]|toc)\d+$", re.IGNORECASE)

_HEADING_STYLES = {"TITLE", "SUBTITLE"}
_NUMBERED_GLYPHS = {
    "DECIMAL",
    "ZERO_DECIMAL",
    "UPPER_ALPHA",
    "ALPHA",
    "UPPER_ROMAN",
    "ROMAN",
}

_WHERE = {"append", "prepend", "before", "after"}
_WHERE_ALIASES = {
    "end": "after",
    "start": "before",
    "inside": "append",
    "beginning": "prepend",
}


class MapError(Exception):
    """The requested target does not match the current document."""


@dataclass
class TextRun:
    start_index: int
    end_index: int
    content: str


@dataclass
class Block:
    id: str
    kind: str
    start_index: int
    end_index: int
    text: str = ""
    raw_text: str = ""
    style: str | None = None
    bullet: str | None = None
    runs: list[TextRun] = field(default_factory=list)
    rows: int | None = None
    columns: int | None = None
    preview: str = ""


@dataclass
class DocumentMap:
    document_id: str
    title: str
    blocks: list[Block]
    end_index: int

    def paragraphs(self) -> list[Block]:
        return [block for block in self.blocks if block.kind == "paragraph"]


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def doc_url(document_id: str) -> str:
    return f"https://docs.google.com/document/d/{document_id}/edit"


def parse_document_id(value: str) -> str:
    text = (value or "").strip()
    match = _DOC_URL.search(text) or _ID_PARAM.search(text)
    if match:
        return match.group(1)
    if _BARE_ID.fullmatch(text):
        return text
    raise MapError(
        "Pass a Google Doc URL or document id. Use list_documents to find one."
    )


def parse_document(document: dict) -> DocumentMap:
    content = document.get("body", {}).get("content", [])
    lists = document.get("lists", {})
    blocks: list[Block] = []
    counts = {"p": 0, "t": 0, "toc": 0}
    end_index = 1
    for element in content:
        end_index = max(end_index, int(element.get("endIndex", 1)))
        if "paragraph" in element:
            counts["p"] += 1
            blocks.append(_paragraph_block(f"p{counts['p']}", element, lists))
        elif "table" in element:
            counts["t"] += 1
            blocks.append(_table_block(f"t{counts['t']}", element))
        elif "tableOfContents" in element:
            counts["toc"] += 1
            blocks.append(
                Block(
                    id=f"toc{counts['toc']}",
                    kind="toc",
                    start_index=int(element.get("startIndex", 0)),
                    end_index=int(element.get("endIndex", 0)),
                )
            )
    return DocumentMap(
        document_id=document.get("documentId", ""),
        title=document.get("title", "Untitled"),
        blocks=blocks,
        end_index=end_index,
    )


def render_outline(doc: DocumentMap, limit: int = 120) -> str:
    lines = [
        f"Title: {doc.title}",
        f"ID: {doc.document_id}",
        f"URL: {doc_url(doc.document_id)}",
        "",
    ]
    for block in doc.blocks[:limit]:
        if block.kind == "paragraph":
            snippet = block.text.replace("\n", " ").strip()
            if len(snippet) > 240:
                snippet = snippet[:240] + "…"
            lines.append(f"{block.id}{_tags(block)} {snippet}".rstrip())
        elif block.kind == "table":
            lines.append(f"{block.id} [TABLE {block.rows}x{block.columns}]")
            if block.preview:
                lines.append(block.preview)
        else:
            lines.append(f"{block.id} [TABLE OF CONTENTS]")
    hidden = len(doc.blocks) - limit
    if hidden > 0:
        lines.append(
            f"… {hidden} more blocks not shown. Read a heading to narrow the view."
        )
    if len(doc.blocks) == 0:
        lines.append("(empty document)")
    return "\n".join(lines)


def resolve_paragraph(doc: DocumentMap, target: str) -> Block:
    text = (target or "").strip()
    if not text:
        raise MapError("A paragraph id or heading is required.")
    if _BLOCK_ID.fullmatch(text):
        for block in doc.blocks:
            if block.id.lower() != text.lower():
                continue
            if block.kind != "paragraph":
                raise MapError(
                    f"{block.id} is a {block.kind}, not a paragraph. Choose a p id from the outline."
                )
            return block
        raise MapError(
            f"No block with id {text}. Read the document again and use an id from that outline."
        )

    headings = [block for block in doc.paragraphs() if _is_heading(block)]
    needle = text.casefold()
    exact = [block for block in headings if block.text.strip().casefold() == needle]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise MapError(
            f"More than one heading matches {text!r}: {_id_list(exact)}. Use a paragraph id."
        )
    contains = [block for block in headings if needle in block.text.casefold()]
    if len(contains) == 1:
        return contains[0]
    if len(contains) > 1:
        raise MapError(
            f"Several headings contain {text!r}: {_id_list(contains)}. Use a paragraph id."
        )
    available = _id_list(headings) or "none"
    raise MapError(f"No heading matches {text!r}. Headings: {available}.")


def plan_insert(
    doc: DocumentMap,
    text: str,
    target: str | None,
    where: str | None,
) -> tuple[int, str]:
    """Return the Docs index and the exact string to insert."""
    if not text:
        raise MapError("Text to insert is empty.")
    where_name = _normalize_where(where)
    target_name = (target or "").strip()
    document_level = target_name.lower() in {"", "document", "doc", "start", "end"}
    if target_name.lower() == "end":
        where_name = "after"
    elif target_name.lower() == "start":
        where_name = "before"

    paragraphs = doc.paragraphs()
    if not paragraphs:
        raise MapError("This document has no body paragraph to edit.")

    if document_level:
        if where_name in {"after", "append"}:
            block = paragraphs[-1]
            if _is_blank(block):
                return block.start_index, text.rstrip("\n")
            if where_name == "append":
                return _append_index(block, doc.end_index), text
            return _insert_after(block, doc.end_index, text)
        block = paragraphs[0]
        if where_name == "prepend":
            return block.start_index, text
        return block.start_index, _as_new_paragraph(text)

    block = resolve_paragraph(doc, target_name)
    if where_name == "append":
        return _append_index(block, doc.end_index), text
    if where_name == "prepend":
        return block.start_index, text
    if where_name == "before":
        return block.start_index, _as_new_paragraph(text)
    return _insert_after(block, doc.end_index, text)


def find_text(
    doc: DocumentMap,
    needle: str,
    match_case: bool = True,
    block: Block | None = None,
) -> list[tuple[int, int]]:
    if not needle:
        raise MapError("Search text is empty.")
    blocks = [block] if block is not None else doc.paragraphs()
    found: list[tuple[int, int]] = []
    for item in blocks:
        found.extend(_find_in_block(item, needle, match_case))
    return found


def delete_paragraph_range(doc: DocumentMap, block: Block) -> tuple[int, int] | None:
    if block.kind != "paragraph":
        raise MapError("Only a paragraph can be deleted this way.")
    end = block.end_index
    if end >= doc.end_index:
        end = doc.end_index - 1
    if block.start_index >= end:
        return None
    return block.start_index, end


def paragraph_text_range(doc: DocumentMap, block: Block) -> tuple[int, int] | None:
    end = block.end_index
    if block.raw_text.endswith("\n"):
        end -= 1
    if end > doc.end_index:
        end = doc.end_index
    if block.start_index >= end:
        return None
    return block.start_index, end


def clamp_delete_range(
    start: int, end: int, segment_end: int
) -> tuple[int, int] | None:
    """Keep a delete range from removing the document's final newline."""
    if end > segment_end:
        end = segment_end
    if end >= segment_end:
        end = segment_end - 1
    if start >= end:
        return None
    return start, end


def _paragraph_block(block_id: str, element: dict, lists: dict) -> Block:
    paragraph = element["paragraph"]
    runs = _runs(paragraph)
    raw = "".join(element_text(item) for item in paragraph.get("elements", []))
    return Block(
        id=block_id,
        kind="paragraph",
        start_index=int(element.get("startIndex", 0)),
        end_index=int(element.get("endIndex", 0)),
        text=_display_text(raw),
        raw_text=raw,
        style=paragraph.get("paragraphStyle", {}).get("namedStyleType"),
        bullet=_bullet_kind(paragraph, lists),
        runs=runs,
    )


def element_text(element: dict) -> str:
    if "textRun" in element:
        return element["textRun"].get("content", "")
    return ""


def _runs(paragraph: dict) -> list[TextRun]:
    runs: list[TextRun] = []
    for element in paragraph.get("elements", []):
        start = element.get("startIndex")
        end = element.get("endIndex")
        if start is None or end is None:
            continue
        if "textRun" in element:
            content = element["textRun"].get("content", "")
        else:
            content = "\uFFFC" * max(0, int(end) - int(start))
        runs.append(TextRun(int(start), int(end), content))
    return runs


def _table_block(block_id: str, element: dict) -> Block:
    table = element["table"]
    rows = table.get("rows") or len(table.get("tableRows", []))
    columns = table.get("columns") or 0
    preview_lines = []
    for row in table.get("tableRows", []):
        cells = []
        for cell in row.get("tableCells", []):
            parts = []
            for child in cell.get("content", []):
                if "paragraph" in child:
                    raw = "".join(
                        element_text(item)
                        for item in child["paragraph"].get("elements", [])
                    )
                    shown = _display_text(raw).strip()
                    if shown:
                        parts.append(shown)
            cells.append(" ".join(parts))
        if not columns:
            columns = len(cells)
        preview_lines.append(" | ".join(cells))
    preview = "\n".join(line for line in preview_lines if line.strip())
    if len(preview) > 300:
        preview = preview[:300] + "…"
    return Block(
        id=block_id,
        kind="table",
        start_index=int(element.get("startIndex", 0)),
        end_index=int(element.get("endIndex", 0)),
        rows=rows,
        columns=columns,
        preview=preview,
    )


def _bullet_kind(paragraph: dict, lists: dict) -> str | None:
    bullet = paragraph.get("bullet")
    if not bullet:
        return None
    levels = (
        lists.get(bullet.get("listId", ""), {})
        .get("listProperties", {})
        .get("nestingLevels", [])
    )
    nesting = int(bullet.get("nestingLevel", 0) or 0)
    level = levels[nesting] if nesting < len(levels) else (levels[0] if levels else {})
    if level.get("glyphSymbol"):
        return "bullet"
    glyph = level.get("glyphType")
    if glyph in _NUMBERED_GLYPHS:
        return "numbered"
    return "bullet"


def _display_text(raw: str) -> str:
    text = raw.replace("\uFFFC", "")
    if text.endswith("\n"):
        text = text[:-1]
    return text


def _tags(block: Block) -> str:
    tags = []
    if block.style and block.style != "NORMAL_TEXT":
        tags.append(block.style)
    if block.bullet == "bullet":
        tags.append("BULLET")
    elif block.bullet == "numbered":
        tags.append("NUMBERED")
    if not tags:
        return ""
    return " [" + " ".join(tags) + "]"


def _is_heading(block: Block) -> bool:
    style = block.style or ""
    return style in _HEADING_STYLES or style.startswith("HEADING_")


def _id_list(blocks: list[Block]) -> str:
    return ", ".join(f"{block.id} {block.text}".strip() for block in blocks[:8])


def _normalize_where(where: str | None) -> str:
    name = (where or "after").strip().lower()
    name = _WHERE_ALIASES.get(name, name)
    if name not in _WHERE:
        raise MapError("where must be append, prepend, before, or after.")
    return name


def _is_blank(block: Block) -> bool:
    return block.raw_text.replace("\n", "").strip() == ""


def _as_new_paragraph(text: str) -> str:
    return text.rstrip("\n") + "\n"


def _append_index(block: Block, segment_end: int) -> int:
    if block.raw_text.endswith("\n"):
        return block.end_index - 1
    if block.end_index >= segment_end:
        return max(block.start_index, segment_end - 1)
    return block.end_index


def _insert_after(block: Block, segment_end: int, text: str) -> tuple[int, str]:
    body = text.rstrip("\n")
    if block.end_index >= segment_end:
        return block.end_index - 1, "\n" + body
    return block.end_index, body + "\n"


def _find_in_block(block: Block, needle: str, match_case: bool) -> list[tuple[int, int]]:
    full = "".join(run.content for run in block.runs)
    if not full:
        return []
    if not match_case and len(full.lower()) == len(full) and len(needle.lower()) == len(needle):
        haystack, query, query_len = full.lower(), needle.lower(), len(needle)
    else:
        haystack, query, query_len = full, needle, len(needle)
    spans: list[tuple[int, int]] = []
    cursor = 0
    while True:
        found = haystack.find(query, cursor)
        if found < 0:
            break
        start = _py_to_docs(block.runs, found)
        end = _py_to_docs(block.runs, found + query_len)
        spans.append((start, end))
        cursor = found + query_len
    return spans


def _py_to_docs(runs: list[TextRun], py_offset: int) -> int:
    consumed = 0
    for run in runs:
        span = len(run.content)
        if py_offset <= consumed + span:
            local = py_offset - consumed
            return run.start_index + utf16_len(run.content[:local])
        consumed += span
    last = runs[-1]
    return last.start_index + utf16_len(last.content)
