"""Document operations shared by the chat agent and the MCP server.

Every write re-reads the document and targets a paragraph id or heading from
that read, then returns the updated outline.
"""

from __future__ import annotations

import inspect
import json
import re
from typing import Any

from googleapiclient.errors import HttpError

from gdocs_agent.auth import AuthRequired
from gdocs_agent.docs_client import get_services
from gdocs_agent.document_map import (
    DocumentMap,
    MapError,
    clamp_delete_range,
    delete_paragraph_range,
    doc_url,
    find_text,
    paragraph_text_range,
    parse_document,
    parse_document_id,
    plan_insert,
    render_outline,
    resolve_paragraph,
)

_HEADINGS = {
    "title": "TITLE",
    "subtitle": "SUBTITLE",
    "normal": "NORMAL_TEXT",
    "body": "NORMAL_TEXT",
    "normal_text": "NORMAL_TEXT",
}
_ALIGNMENTS = {
    "left": "START",
    "start": "START",
    "center": "CENTER",
    "right": "END",
    "end": "END",
    "justify": "JUSTIFIED",
    "justified": "JUSTIFIED",
}
_BULLET_PRESETS = {
    "bullet": "BULLET_DISC_CIRCLE_SQUARE",
    "bulleted": "BULLET_DISC_CIRCLE_SQUARE",
    "disc": "BULLET_DISC_CIRCLE_SQUARE",
    "numbered": "NUMBERED_DECIMAL_ALPHA_ROMAN",
    "number": "NUMBERED_DECIMAL_ALPHA_ROMAN",
    "numbers": "NUMBERED_DECIMAL_ALPHA_ROMAN",
}
_MAX_MATCHES = 50


class ToolError(Exception):
    """A tool call cannot be completed."""


def list_documents(query: str = "", limit: int = 15) -> str:
    """List recent Google Docs, optionally filtering by a title substring."""
    size = max(1, min(int(limit or 15), 25))
    needle = (query or "").strip()
    drive_query = "mimeType = 'application/vnd.google-apps.document' and trashed = false"
    if needle:
        drive_query += f" and name contains '{_escape_query(needle)}'"
    _docs, drive = get_services()
    response = (
        drive.files()
        .list(
            q=drive_query,
            pageSize=size,
            orderBy="modifiedTime desc",
            fields="files(id, name, modifiedTime, webViewLink)",
        )
        .execute()
    )
    documents = []
    for item in response.get("files", []):
        file_id = item["id"]
        documents.append(
            {
                "id": file_id,
                "title": item.get("name", "Untitled"),
                "url": item.get("webViewLink") or doc_url(file_id),
                "modified": item.get("modifiedTime"),
            }
        )
    return _dump({"documents": documents})


def create_document(title: str) -> str:
    """Create an empty Google Doc and return its id and URL."""
    name = (title or "").strip()
    if not name:
        raise ToolError("A document title is required.")
    docs, _drive = get_services()
    created = docs.documents().create(body={"title": name}).execute()
    document_id = created["documentId"]
    return _dump(
        {
            "changed": f"Created {name!r}.",
            "id": document_id,
            "title": created.get("title", name),
            "url": doc_url(document_id),
        }
    )


def read_document(document: str) -> str:
    """Read a Google Doc URL or id and return a paragraph outline."""
    _docs, document_id, mapped = _load(document)
    return _dump(
        {
            "id": document_id,
            "title": mapped.title,
            "url": doc_url(document_id),
            "outline": render_outline(mapped),
        }
    )


def insert_text(
    document: str,
    text: str,
    target: str | None = None,
    where: str = "after",
) -> str:
    """Insert text at a paragraph, heading, or the start or end of the document.

    where is append, prepend, before, or after. append and prepend change the
    target paragraph. before and after add a new paragraph. target may be a
    paragraph id such as p3, heading text, or "document".
    """
    docs, document_id, mapped = _load(document)
    index, payload = plan_insert(mapped, text or "", target, where)
    _apply(docs, document_id, [{"insertText": {"location": {"index": index}, "text": payload}}])
    updated = _reload(docs, document_id)
    place = target or "document"
    return _result(updated, f"Inserted text {where} {place}.")


def replace_text(
    document: str,
    find: str,
    replace: str,
    target: str | None = None,
    match_case: bool = True,
    replace_all: bool = True,
) -> str:
    """Replace text in a document. Limit to a paragraph id or heading with target.

    replace_all replaces every match. When target is omitted and replace_all is
    true, this uses one document-wide replacement.
    """
    if not find:
        raise ToolError("The text to find is empty.")
    docs, document_id, mapped = _load(document)
    if replace_all and not (target or "").strip():
        response = _apply(
            docs,
            document_id,
            [
                {
                    "replaceAllText": {
                        "containsText": {"text": find, "matchCase": bool(match_case)},
                        "replaceText": replace or "",
                    }
                }
            ],
        )
        changed = 0
        for reply in response.get("replies", []):
            if "replaceAllText" in reply:
                changed = reply["replaceAllText"].get("occurrencesChanged", 0)
        updated = _reload(docs, document_id)
        if not changed:
            return _result(updated, f"No matches for {find!r}.")
        return _result(updated, f"Replaced {changed} match(es) of {find!r}.")

    block = resolve_paragraph(mapped, target) if (target or "").strip() else None
    ranges = find_text(mapped, find, bool(match_case), block)
    if not replace_all:
        ranges = ranges[:1]
    _guard_match_count(ranges, find)
    requests = _replacement_requests(ranges, replace or "", mapped.end_index)
    if not requests:
        return _result(mapped, f"No matches for {find!r}.")
    _apply(docs, document_id, requests)
    updated = _reload(docs, document_id)
    scope = f" in {target}" if target else ""
    return _result(updated, f"Replaced {len(ranges)} match(es) of {find!r}{scope}.")


def delete_text(
    document: str,
    target: str | None = None,
    text: str | None = None,
    match_case: bool = True,
    delete_all: bool = False,
) -> str:
    """Delete a paragraph, or delete a snippet of text.

    Pass a paragraph id or heading as target with no text to remove that
    paragraph. Pass text to delete that snippet, optionally limited to target.
    """
    docs, document_id, mapped = _load(document)
    if (target or "").strip() and not text:
        block = resolve_paragraph(mapped, target or "")
        span = delete_paragraph_range(mapped, block)
        if span is None:
            return _result(mapped, f"{block.id} is already empty.")
        _apply(
            docs,
            document_id,
            [{"deleteContentRange": {"range": {"startIndex": span[0], "endIndex": span[1]}}}],
        )
        updated = _reload(docs, document_id)
        return _result(updated, f"Deleted paragraph {block.id}.")
    if not text:
        raise ToolError("Provide a paragraph id or heading, or the text to delete.")
    block = resolve_paragraph(mapped, target) if (target or "").strip() else None
    ranges = find_text(mapped, text, bool(match_case), block)
    if not delete_all:
        ranges = ranges[:1]
    _guard_match_count(ranges, text)
    requests = []
    for start, end in sorted(ranges, reverse=True):
        span = clamp_delete_range(start, end, mapped.end_index)
        if span is None:
            continue
        requests.append(
            {"deleteContentRange": {"range": {"startIndex": span[0], "endIndex": span[1]}}}
        )
    if not requests:
        return _result(mapped, f"No matches for {text!r}.")
    _apply(docs, document_id, requests)
    updated = _reload(docs, document_id)
    return _result(updated, f"Deleted {len(requests)} match(es) of {text!r}.")


def format_text(
    document: str,
    target: str | None = None,
    text: str | None = None,
    bold: bool | None = None,
    italic: bool | None = None,
    font: str | None = None,
    size: float | None = None,
    color: str | None = None,
    match_case: bool = True,
) -> str:
    """Change bold, italic, font, size, or color for a paragraph or an exact snippet."""
    style, fields = _text_style(bold, italic, font, size, color)
    docs, document_id, mapped = _load(document)
    snippet = text or ""
    if snippet:
        block = resolve_paragraph(mapped, target) if (target or "").strip() else None
        ranges = find_text(mapped, snippet, bool(match_case), block)
        _guard_match_count(ranges, snippet)
    elif (target or "").strip():
        block = resolve_paragraph(mapped, target or "")
        span = paragraph_text_range(mapped, block)
        ranges = [span] if span else []
    else:
        raise ToolError("Provide a paragraph id, heading, or the exact text to format.")
    requests = []
    for start, end in ranges:
        if end > mapped.end_index:
            end = mapped.end_index
        if start >= end:
            continue
        requests.append(
            {
                "updateTextStyle": {
                    "range": {"startIndex": start, "endIndex": end},
                    "textStyle": style,
                    "fields": fields,
                }
            }
        )
    if not requests:
        return _result(mapped, "Nothing matched, so no style was changed.")
    _apply(docs, document_id, requests)
    updated = _reload(docs, document_id)
    return _result(updated, f"Updated text style on {len(requests)} range(s).")


def format_paragraph(
    document: str,
    target: str,
    heading: str | None = None,
    alignment: str | None = None,
    space_above: float | None = None,
    space_below: float | None = None,
    bullets: str | None = None,
) -> str:
    """Set a paragraph's heading style, alignment, spacing, or list style.

    bullets is "bullet", "numbered", or "none". heading is title, subtitle,
    heading 1-6, or normal.
    """
    docs, document_id, mapped = _load(document)
    block = resolve_paragraph(mapped, target)
    requests = []
    style: dict[str, Any] = {}
    fields = []
    if heading:
        style["namedStyleType"] = _heading_style(heading)
        fields.append("namedStyleType")
    if alignment:
        style["alignment"] = _alignment(alignment)
        fields.append("alignment")
    if space_above is not None:
        style["spaceAbove"] = {"magnitude": float(space_above), "unit": "PT"}
        fields.append("spaceAbove")
    if space_below is not None:
        style["spaceBelow"] = {"magnitude": float(space_below), "unit": "PT"}
        fields.append("spaceBelow")
    if fields:
        requests.append(
            {
                "updateParagraphStyle": {
                    "range": {"startIndex": block.start_index, "endIndex": block.end_index},
                    "paragraphStyle": style,
                    "fields": ",".join(fields),
                }
            }
        )
    bullet_request = _bullet_request(block, bullets)
    if bullet_request:
        requests.append(bullet_request)
    if not requests:
        raise ToolError("Set a heading, alignment, spacing, or bullets.")
    _apply(docs, document_id, requests)
    updated = _reload(docs, document_id)
    return _result(updated, f"Updated paragraph {block.id}.")


TOOL_SCHEMAS = [
    {
        "name": "list_documents",
        "description": "List the user's recent Google Docs. Optionally filter by a title substring.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Title substring. Empty lists recent docs."},
                "limit": {"type": "integer", "description": "How many docs to return, up to 25."},
            },
        },
    },
    {
        "name": "create_document",
        "description": "Create a new empty Google Doc with the given title.",
        "input_schema": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
    },
    {
        "name": "read_document",
        "description": (
            "Read a Google Doc and return an outline. Paragraph ids such as p3 "
            "refer to this outline only and change after inserts or deletes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "document": {"type": "string", "description": "Document URL or id."}
            },
            "required": ["document"],
        },
    },
    {
        "name": "insert_text",
        "description": (
            "Insert text. target is a paragraph id (p3), heading text, or "
            "'document'. where is append (end of that paragraph), prepend, "
            "before (new paragraph before), or after (new paragraph after). "
            "Newlines inside the text start new paragraphs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "document": {"type": "string"},
                "text": {"type": "string"},
                "target": {"type": "string"},
                "where": {
                    "type": "string",
                    "enum": ["append", "prepend", "before", "after"],
                },
            },
            "required": ["document", "text"],
        },
    },
    {
        "name": "replace_text",
        "description": (
            "Replace text. Omit target to search the whole document. Set target "
            "to a paragraph id or heading to limit the search. replace_all "
            "defaults to true."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "document": {"type": "string"},
                "find": {"type": "string"},
                "replace": {"type": "string"},
                "target": {"type": "string"},
                "match_case": {"type": "boolean"},
                "replace_all": {"type": "boolean"},
            },
            "required": ["document", "find", "replace"],
        },
    },
    {
        "name": "delete_text",
        "description": (
            "Delete a whole paragraph by passing target and no text, or delete "
            "an exact snippet by passing text. delete_all defaults to false."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "document": {"type": "string"},
                "target": {"type": "string"},
                "text": {"type": "string"},
                "match_case": {"type": "boolean"},
                "delete_all": {"type": "boolean"},
            },
            "required": ["document"],
        },
    },
    {
        "name": "format_text",
        "description": (
            "Apply bold, italic, font family, size in points, or hex color. "
            "Pass target to style a whole paragraph, or text to style an exact snippet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "document": {"type": "string"},
                "target": {"type": "string"},
                "text": {"type": "string"},
                "bold": {"type": "boolean"},
                "italic": {"type": "boolean"},
                "font": {"type": "string"},
                "size": {"type": "number"},
                "color": {"type": "string", "description": "Hex color, for example #1A1A1A."},
                "match_case": {"type": "boolean"},
            },
            "required": ["document"],
        },
    },
    {
        "name": "format_paragraph",
        "description": (
            "Set heading level, alignment, spacing in points, or bullets on one "
            "paragraph. bullets is bullet, numbered, or none. heading is title, "
            "subtitle, heading 1 through heading 6, or normal."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "document": {"type": "string"},
                "target": {"type": "string"},
                "heading": {"type": "string"},
                "alignment": {
                    "type": "string",
                    "enum": ["left", "center", "right", "justify"],
                },
                "space_above": {"type": "number"},
                "space_below": {"type": "number"},
                "bullets": {"type": "string", "enum": ["bullet", "numbered", "none"]},
            },
            "required": ["document", "target"],
        },
    },
]

TOOL_FUNCTIONS = {
    "list_documents": list_documents,
    "create_document": create_document,
    "read_document": read_document,
    "insert_text": insert_text,
    "replace_text": replace_text,
    "delete_text": delete_text,
    "format_text": format_text,
    "format_paragraph": format_paragraph,
}


def dispatch(name: str, arguments: dict | None) -> str:
    """Run one tool and return a JSON string the model can read."""
    function = TOOL_FUNCTIONS.get(name)
    if function is None:
        return _dump({"error": f"Unknown tool {name}."})
    payload = arguments or {}
    if not isinstance(payload, dict):
        return _dump({"error": f"Arguments for {name} must be an object."})
    accepted = {
        key: value
        for key, value in payload.items()
        if key in inspect.signature(function).parameters
    }
    try:
        return function(**accepted)
    except (ToolError, MapError, AuthRequired) as exc:
        return _dump({"error": str(exc)})
    except HttpError as exc:
        detail = str(exc)
        if len(detail) > 500:
            detail = detail[:500]
        return _dump({"error": f"Google API error: {detail}"})
    except TypeError as exc:
        return _dump({"error": f"Bad arguments for {name}: {exc}"})
    except Exception as exc:  # noqa: BLE001 - tool errors go back to the model
        return _dump({"error": f"{type(exc).__name__}: {exc}"})


def _load(document: str):
    docs, _drive = get_services()
    document_id = parse_document_id(document)
    return docs, document_id, _reload(docs, document_id)


def _reload(docs, document_id: str) -> DocumentMap:
    raw = docs.documents().get(documentId=document_id).execute()
    return parse_document(raw)


def _apply(docs, document_id: str, requests: list[dict]) -> dict:
    if not requests:
        raise ToolError("Nothing to change.")
    return (
        docs.documents()
        .batchUpdate(documentId=document_id, body={"requests": requests})
        .execute()
    )


def _result(mapped: DocumentMap, changed: str) -> str:
    return _dump({"changed": changed, "outline": render_outline(mapped)})


def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _escape_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _guard_match_count(ranges: list[tuple[int, int]], needle: str) -> None:
    if len(ranges) > _MAX_MATCHES:
        raise ToolError(
            f"Found {len(ranges)} matches for {needle!r}. Narrow the target or the search text."
        )


def _replacement_requests(
    ranges: list[tuple[int, int]], replacement: str, segment_end: int
) -> list[dict]:
    requests = []
    for start, end in sorted(ranges, reverse=True):
        span = clamp_delete_range(start, end, segment_end)
        if span is None:
            continue
        requests.append(
            {
                "deleteContentRange": {
                    "range": {"startIndex": span[0], "endIndex": span[1]}
                }
            }
        )
        if replacement:
            requests.append(
                {"insertText": {"location": {"index": span[0]}, "text": replacement}}
            )
    return requests


def _text_style(bold, italic, font, size, color) -> tuple[dict, str]:
    style: dict[str, Any] = {}
    fields = []
    if bold is not None:
        style["bold"] = bool(bold)
        fields.append("bold")
    if italic is not None:
        style["italic"] = bool(italic)
        fields.append("italic")
    if font:
        style["weightedFontFamily"] = {"fontFamily": str(font)}
        fields.append("weightedFontFamily")
    if size is not None:
        points = float(size)
        if points <= 0:
            raise ToolError("Font size must be greater than 0.")
        style["fontSize"] = {"magnitude": points, "unit": "PT"}
        fields.append("fontSize")
    if color:
        style["foregroundColor"] = _color(str(color))
        fields.append("foregroundColor")
    if not fields:
        raise ToolError("Set bold, italic, font, size, or color.")
    return style, ",".join(fields)


def _color(value: str) -> dict:
    hex_color = value.strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{3}", hex_color):
        hex_color = "".join(character * 2 for character in hex_color)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", hex_color):
        raise ToolError("Color must be a hex code such as #1A1A1A.")
    red = int(hex_color[0:2], 16) / 255
    green = int(hex_color[2:4], 16) / 255
    blue = int(hex_color[4:6], 16) / 255
    return {"color": {"rgbColor": {"red": red, "green": green, "blue": blue}}}


def _heading_style(value: str) -> str:
    key = re.sub(r"[\s-]+", "_", value.strip().lower())
    if key in _HEADINGS:
        return _HEADINGS[key]
    match = re.fullmatch(r"(?:heading|h)_?([1-6])", key)
    if match:
        return f"HEADING_{match.group(1)}"
    if key.upper() in {"TITLE", "SUBTITLE", "NORMAL_TEXT"} or re.fullmatch(
        r"HEADING_[1-6]", key.upper()
    ):
        return key.upper()
    raise ToolError("Heading must be title, subtitle, heading 1-6, or normal.")


def _alignment(value: str) -> str:
    name = _ALIGNMENTS.get(value.strip().lower())
    if not name:
        raise ToolError("Alignment must be left, center, right, or justify.")
    return name


def _bullet_request(block, bullets: str | None) -> dict | None:
    if not bullets:
        return None
    choice = bullets.strip().lower()
    span = {"startIndex": block.start_index, "endIndex": block.end_index}
    if choice == "none":
        return {"deleteParagraphBullets": {"range": span}}
    preset = _BULLET_PRESETS.get(choice)
    if not preset:
        raise ToolError("Bullets must be bullet, numbered, or none.")
    return {"createParagraphBullets": {"range": span, "bulletPreset": preset}}
