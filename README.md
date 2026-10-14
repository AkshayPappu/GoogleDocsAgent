# Google Docs chat agent

A local chat that edits your Google Docs. Claude decides what to change. Python performs the edit. The same tools are available to Cursor or Claude Desktop over MCP.

## What you set up once

### Google

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project and enable **Google Docs API** and **Google Drive API**.
2. On the OAuth consent screen, choose **External**, leave the app in **Testing**, and add your Gmail as a test user. Testing tokens expire after 7 days until the app is published.
3. Create an OAuth client of type **Desktop app** and download the JSON.
4. Save that file as `credentials.json` in this folder.

The app requests:

- `https://www.googleapis.com/auth/documents` — read and edit doc content
- `https://www.googleapis.com/auth/drive.readonly` — find a doc by title

The first connection opens a browser and writes `token.json`. A service account cannot see your personal docs unless every file is shared with it.

### Claude

1. Create an API key at [console.anthropic.com](https://console.anthropic.com/) and enable billing.
2. Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`.
3. `ANTHROPIC_MODEL` defaults to `claude-sonnet-5-5`.

`.env`, `credentials.json`, and `token.json` are gitignored.

## Run the chat

Python 3.10 or newer. On this machine, use the 3.12 launcher:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env
uvicorn gdocs_agent.api:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 and choose **Connect Google Docs**. Paste a doc link, or ask it to list recent docs.

## MCP

The web chat does not need this. To use the same tools from Cursor or Claude Desktop, point the client at:

```text
C:\Users\aksha\GoogleDocsAgent\.venv\Scripts\python.exe -m gdocs_agent.mcp_server
```

Run it with the working directory set to this folder so it can find `credentials.json` and `token.json`.
