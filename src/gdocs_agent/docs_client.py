"""Google Docs and Drive service builders."""

from __future__ import annotations

from googleapiclient.discovery import build

from gdocs_agent.auth import get_credentials

INTERACTIVE = False

_services = None
_service_token: str | None = None


def get_services(interactive: bool | None = None):
    """Return cached Docs and Drive clients for the current user token."""
    global _services, _service_token
    if interactive is None:
        interactive = INTERACTIVE
    creds = get_credentials(interactive=interactive)
    token = creds.token
    if _services is None or token != _service_token:
        docs = build("docs", "v1", credentials=creds)
        drive = build("drive", "v3", credentials=creds)
        _services = (docs, drive)
        _service_token = token
    return _services
