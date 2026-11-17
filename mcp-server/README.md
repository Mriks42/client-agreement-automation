# Kickoff MCP server

Use Kickoff from an AI assistant: Claude Desktop, Claude Code, or any other app that speaks [MCP](https://modelcontextprotocol.io). For example:

> **You:** Send our client agreement to Jane Client at Acme Test Co, start date December 1.
> **Claude:** I've prepared it as a draft. Check the preview and press **Confirm and send** here: https://kickoff-r713.onrender.com/files/…
>
> **You:** Which agreements are still waiting for a signature?
> **Claude:** Two. Acme Test Co is waiting for Jane Client (sent Tuesday). Globex is waiting for you to countersign.
>
> **You:** Save the signed Acme agreement.
> **Claude:** Saved to ~/Downloads/Acme Test Co (signed).pdf. Its SHA-256 matches the one Kickoff has on record.

The assistant **prepares drafts but can't send them.** A person at the firm always checks the exact PDF and presses Confirm and send in Kickoff, the same as for any file.

## Tools

| Tool | What it does |
|---|---|
| `list_agreements` | The firm's approved agreements, their signers in order, and the details a file fills in |
| `open_file` | Prepares a file as a **draft** with the client's details, and returns the link to check and send it. A `reference` (e.g. a CRM deal id) makes retries safe |
| `list_files` | Files waiting for a signature (or drafts, completed, all), with who each one is waiting for |
| `get_file` | One file in detail: each signer's status and signing time |
| `download_signed_agreement` | Saves the signed PDF, with its audit certificate, on this computer, after checking its SHA-256 against Kickoff's record |

The server is a thin layer over Kickoff's public API (`/api/v1`) and acts with a Kickoff API key.

## Set it up

1. **Create an API key in Kickoff:** Settings, then API keys, then Create key. Copy it; it's shown once.
2. **Install [uv](https://docs.astral.sh/uv/)** if you don't have it (`brew install uv` on a Mac).
3. **Add the server to your assistant.**

**Claude Desktop:** Settings, then Developer, then Edit Config. Add this to `claude_desktop_config.json` and restart Claude Desktop:

```json
{
  "mcpServers": {
    "kickoff": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/Mriks42/client-agreement-automation#subdirectory=mcp-server",
        "kickoff-mcp"
      ],
      "env": { "KICKOFF_API_KEY": "kk_live_..." }
    }
  }
}
```

**Claude Code:**

```bash
claude mcp add kickoff --env KICKOFF_API_KEY=kk_live_... -- \
  uvx --from "git+https://github.com/Mriks42/client-agreement-automation#subdirectory=mcp-server" kickoff-mcp
```

Optional settings, also passed as `env`:

| Variable | Default | Meaning |
|---|---|---|
| `KICKOFF_URL` | `https://kickoff-r713.onrender.com` | Another Kickoff deployment |
| `KICKOFF_DOWNLOAD_DIR` | `~/Downloads` | Where signed agreements are saved |

## Develop

```bash
cd mcp-server
uv sync
uv run pytest -q                       # the tools, against a fake Kickoff API
uv run ruff check . && uv run mypy kickoff_mcp
KICKOFF_API_KEY=kk_live_... uv run kickoff-mcp   # run it on stdio, as an assistant would
```
