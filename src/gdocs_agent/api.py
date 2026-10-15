"""Local chat service for the floating overlay."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from gdocs_agent.agent import Agent
from gdocs_agent.auth import AuthRequired, connected, credentials_path, get_credentials

load_dotenv()

app = FastAPI(title="Google Docs chat")
_STATIC = Path(__file__).resolve().parent / "static"
_sessions: dict[str, list] = {}
_session_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()
_agent: Agent | None = None
_agent_lock = threading.Lock()


class ChatIn(BaseModel):
    message: str = Field(min_length=1)
    session_id: str | None = None


def _session_lock(session_id: str) -> threading.Lock:
    with _guard:
        lock = _session_locks.get(session_id)
        if lock is None:
            lock = threading.Lock()
            _session_locks[session_id] = lock
        return lock


def _get_agent() -> Agent:
    global _agent
    with _agent_lock:
        if _agent is None:
            _agent = Agent()
        return _agent


@app.get("/")
def index():
    return FileResponse(_STATIC / "index.html")


@app.get("/overlay.css")
def overlay_css():
    from gdocs_agent.overlay_layout import stylesheet

    return Response(stylesheet(), media_type="text/css")


@app.get("/api/auth/status")
def auth_status():
    present = credentials_path().exists()
    if connected():
        return {"connected": True, "credentials_present": present, "detail": None}
    detail = None
    if not present:
        detail = (
            "Save your OAuth Desktop client JSON as credentials.json in the project folder."
        )
    return {"connected": False, "credentials_present": present, "detail": detail}


@app.post("/api/auth/login")
def auth_login():
    try:
        get_credentials(interactive=True)
    except AuthRequired as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - show the OAuth failure in the page
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"connected": True}


@app.post("/api/chat")
async def chat(body: ChatIn):
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is empty.")
    session_id = body.session_id or str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def emit(event: str, data: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (event, data))

    def work() -> None:
        try:
            emit("session", {"session_id": session_id})
            agent = _get_agent()
            with _session_lock(session_id):
                history = _sessions.setdefault(session_id, [])
                agent.run(history, message, emit)
        except Exception as exc:  # noqa: BLE001 - streamed to the chat page
            emit("error", {"message": str(exc)})
        finally:
            emit("done", {})

    async def events():
        task = asyncio.create_task(asyncio.to_thread(work))
        try:
            while True:
                event, data = await queue.get()
                payload = json.dumps(data, ensure_ascii=False)
                yield f"event: {event}\ndata: {payload}\n\n"
                if event == "done":
                    break
        finally:
            await task

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def main() -> None:
    """Open the floating chat above the other windows."""
    from gdocs_agent.overlay import main as run_overlay

    run_overlay()
