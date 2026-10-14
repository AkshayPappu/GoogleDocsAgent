"""Claude tool-use loop for Google Docs edits."""

from __future__ import annotations

import json
import os

import anthropic
from dotenv import load_dotenv

from gdocs_agent.tools import TOOL_SCHEMAS, dispatch

SYSTEM_PROMPT = """You edit the user's Google Docs through tools. You are a careful writing partner.

Rules:
- Read a document before changing it, unless you just created it or the latest tool result already includes its outline.
- Paragraph ids such as p3 come from the latest outline. They shift when paragraphs are inserted or deleted, so after every edit use the outline in that tool result.
- Prefer heading text when the user names a section. Prefer the smallest edit that does what they asked.
- Do not invent document contents. If several documents match, ask which one.
- After an edit, say what changed in plain language. Do not paste the raw outline unless the user asked to see the document.
"""

MAX_ROUNDS = 12
MAX_TOKENS = 16000
_HISTORY_LIMIT = 40


class Agent:
    def __init__(self) -> None:
        load_dotenv()
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your Claude API key."
            )
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5").strip() or "claude-sonnet-5-5"

    def run(self, messages: list[dict], user_text: str, emit) -> None:
        """Append the user turn, then stream text and tool status until Claude stops."""
        messages.append({"role": "user", "content": user_text})
        for _ in range(MAX_ROUNDS):
            _trim(messages)
            with self.client.messages.stream(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                tools=TOOL_SCHEMAS,
                messages=messages,
            ) as stream:
                for text in stream.text_stream:
                    emit("text", {"text": text})
                final = stream.get_final_message()
            messages.append({"role": "assistant", "content": _assistant_content(final)})
            if final.stop_reason == "tool_use":
                results = _tool_results(final, emit)
                if not results:
                    raise RuntimeError("Claude requested a tool but did not include a tool call.")
                messages.append({"role": "user", "content": results})
                continue
            if final.stop_reason == "pause_turn":
                continue
            if final.stop_reason == "max_tokens":
                emit(
                    "text",
                    {
                        "text": "\n\nThat reply hit the length limit. Ask me to continue with a smaller piece."
                    },
                )
            return
        emit(
            "text",
            {
                "text": "\n\nI stopped after several edits so I would not keep looping. Tell me how to continue."
            },
        )


def _tool_results(message, emit) -> list[dict]:
    results = []
    for block in message.content:
        if getattr(block, "type", None) != "tool_use":
            continue
        emit("tool", {"name": block.name, "status": "running"})
        result = dispatch(block.name, block.input)
        emit(
            "tool",
            {"name": block.name, "status": "done", "summary": summarize_result(result)},
        )
        item = {"type": "tool_result", "tool_use_id": block.id, "content": result}
        if _is_error(result):
            item["is_error"] = True
        results.append(item)
    return results


def _assistant_content(message) -> list[dict]:
    blocks = []
    for block in message.content:
        data = block.model_dump(mode="json", exclude_none=True)
        kind = data.get("type")
        if kind == "text":
            if data.get("text"):
                blocks.append({"type": "text", "text": data["text"]})
        elif kind == "tool_use":
            blocks.append(
                {
                    "type": "tool_use",
                    "id": data["id"],
                    "name": data["name"],
                    "input": data.get("input") or {},
                }
            )
        elif kind == "thinking":
            kept = {"type": "thinking", "thinking": data.get("thinking", "")}
            if data.get("signature"):
                kept["signature"] = data["signature"]
            blocks.append(kept)
        elif kind == "redacted_thinking":
            blocks.append({"type": "redacted_thinking", "data": data.get("data", "")})
        else:
            blocks.append(data)
    return blocks or [{"type": "text", "text": " "}]


def summarize_result(result: str) -> str:
    try:
        data = json.loads(result)
    except json.JSONDecodeError:
        return result[:240]
    if not isinstance(data, dict):
        return result[:240]
    if data.get("error"):
        return str(data["error"])[:240]
    if data.get("changed"):
        return str(data["changed"])[:240]
    if "documents" in data:
        return f"Found {len(data['documents'])} documents"
    if data.get("title") and data.get("outline"):
        return f"Read {data['title']}"
    if data.get("title"):
        return f"Created {data['title']}"
    return "Done"


def _is_error(result: str) -> bool:
    try:
        data = json.loads(result)
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict) and bool(data.get("error"))


def _trim(messages: list[dict]) -> None:
    if len(messages) <= _HISTORY_LIMIT:
        return
    trimmed = messages[-_HISTORY_LIMIT:]
    while trimmed:
        first = trimmed[0]
        content = first.get("content")
        if first.get("role") != "user":
            trimmed.pop(0)
            continue
        if (
            isinstance(content, list)
            and content
            and isinstance(content[0], dict)
            and content[0].get("type") == "tool_result"
        ):
            trimmed.pop(0)
            continue
        break
    messages[:] = trimmed
