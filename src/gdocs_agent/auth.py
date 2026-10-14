"""Desktop OAuth for the Google Docs and Drive APIs."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.readonly",
]

CREDENTIALS_HELP = (
    "Missing credentials.json. In Google Cloud Console, enable the Google Docs API "
    "and Google Drive API, create an OAuth client of type Desktop app, and save the "
    "downloaded JSON as credentials.json in the project folder."
)

_lock = threading.Lock()
_credentials: Credentials | None = None


class AuthRequired(Exception):
    """The user has not connected a Google account."""


def _project_root() -> Path:
    root = Path(__file__).resolve().parents[2]
    if (root / "pyproject.toml").exists():
        return root
    return Path.cwd()


def _resolve(name: str) -> Path:
    path = Path(name)
    if path.is_absolute():
        return path
    return _project_root() / path


def credentials_path() -> Path:
    return _resolve(os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json"))


def token_path() -> Path:
    return _resolve(os.environ.get("GOOGLE_TOKEN_FILE", "token.json"))


def _load_token() -> Credentials | None:
    path = token_path()
    if not path.exists():
        return None
    return Credentials.from_authorized_user_file(str(path), SCOPES)


def _save(creds: Credentials) -> None:
    token_path().write_text(creds.to_json(), encoding="utf-8")


def _login() -> Credentials:
    path = credentials_path()
    if not path.exists():
        raise AuthRequired(CREDENTIALS_HELP)
    flow = InstalledAppFlow.from_client_secrets_file(str(path), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    _save(creds)
    return creds


def get_credentials(interactive: bool = False) -> Credentials:
    """Return usable user credentials, refreshing or opening a browser if asked."""
    global _credentials
    with _lock:
        creds = _credentials or _load_token()
        if creds and creds.valid:
            _credentials = creds
            return creds
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                _save(creds)
                _credentials = creds
                return creds
            except RefreshError:
                creds = None
                _credentials = None
        if not interactive:
            if not credentials_path().exists():
                raise AuthRequired(CREDENTIALS_HELP)
            raise AuthRequired(
                "Google is not connected. Use Connect Google Docs in the chat, "
                "or run again and allow the browser prompt."
            )
    creds = _login()
    with _lock:
        _credentials = creds
        return creds


def connected() -> bool:
    try:
        get_credentials(interactive=False)
    except AuthRequired:
        return False
    return True
