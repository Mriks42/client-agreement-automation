# CLAUDE.md

Context for anyone working in this repo, human or AI. `DECISIONS.md` explains *why* things are the way they are. This file covers *what* the project is and *how* to work on it.

> **Status:** Day 1 scaffold is in place (health, job queue, email, fixtures). Keep the commands below accurate as features land.

## What this is

A multi-tenant web app that gets a firm's standard client agreement signed on every **file**, where a file is one job or case for one client. A firm uploads its agreement once. The AI proposes where each blank and signature goes, a human approves that, and from then on every new file:

1. pre-fills the agreement with that file's details,
2. sends it to each signer in order,
3. chases anyone who hasn't signed,
4. produces a signed PDF with an audit certificate,
5. tells the firm's other systems (via webhook) that work can start.

**Customer #1:** Astra AI, a small AI consulting firm. **Target market:** any professional-services firm that opens "files".

**How it is graded:** Anthony (Astra) opens the submission URL, uploads an agreement we have never seen (the MIND client agreement), and gets it signed from start to finish. **Anything that would break that path takes priority over everything else.**

## Hard constraints

- **Deadline: Wed Nov 18, 2026 00:00 America/Phoenix.** Any commit after that disqualifies the submission. Features freeze at Tue Nov 17 12:00. Final push by Tue 20:00.
- **Test data only.** Never put real people's names, emails, addresses or signatures in fixtures, seeds, tests, logs or screenshots. Use obviously fake data such as `jane.client@example.com` or "Acme Test Co".
- The scope is fixed in `DECISIONS.md`. Adding or dropping a feature means updating `DECISIONS.md` in the same commit.

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.13, FastAPI, Pydantic v2, pydantic-settings, **PyMongo async (`AsyncMongoClient`)**. Do not use Motor; it is deprecated. |
| Database / files | MongoDB Atlas M0. Binaries (uploads, rendered pages, signed PDFs) live in GridFS. |
| PDF | PyMuPDF **1.x** (pinned `<2`: 2.x hard-depends on ~250 MB of ML packages we don't use), imported **only** in `backend/app/services/pdf_engine.py` (AGPL; see D13). DOCX→PDF uses LibreOffice headless as a subprocess (~14 s on the free tier's 0.1 CPU). |
| LLM | Anthropic Python SDK, `claude-opus-5-5` |
| Email | Gmail REST API over HTTPS from a dedicated account, behind the `EmailSender` protocol. Use the `console` backend in dev and tests. |
| Frontend | React + TypeScript + Vite + Tailwind, TanStack Query, React Router, `signature_pad` |
| Hosting | One Docker web service on Render (free tier) that serves the API under `/api` and the built SPA at `/` |
| Monitoring | Sentry, `/healthz`, structured JSON logs, failed-jobs page |
| Package managers | `uv` (backend), `npm` (frontend) |

## Repo layout

```
backend/
  app/
    main.py              FastAPI app; lifespan starts the job worker; serves frontend/dist
    config.py            Settings (env vars below)
    db.py                Mongo client, indexes, GridFS buckets
    api/                 Routers: auth, templates, files, signing (public, token-auth), v1 (API-key auth), verify, health
    domain/              Pydantic models + state enums
    services/
      ingest.py          Upload validation, DOCX→PDF
      pdf_engine.py      Extract lines/slots, render PNGs, stamp fields, append pages (the only PyMuPDF import)
      detection/         schema.py, prompt.py, llm.py, heuristics.py, mapper.py
      signing.py         File/recipient state machine, routing, atomic transitions
      finalize.py        Flatten values, audit certificate, SHA-256
      email/             EmailSender protocol, gmail_api.py, console.py, templates/
      webhooks.py        Outbound webhooks (HMAC-SHA256)
    worker/              queue.py (claim/backoff/dead-letter), handlers.py, scheduler.py
  tests/
  evals/                 fixtures/ (synthetic agreements), expected/ (labels), make_fixtures.py, run.py
  scripts/               gmail_auth.py (mints the Gmail refresh token), seed_demo.py
frontend/
  src/                   pages/, components/, api/ (typed client), lib/
Dockerfile               Multi-stage: node build → python:3.13-slim + libreoffice-writer-nogui + fonts
render.yaml              Render Blueprint
```

## Commands

```bash
# Local Mongo (there is no local mongod; use Docker)
docker run -d --name mongo -p 27017:27017 mongo:8.0

# Backend
cd backend
uv sync
uv run uvicorn app.main:create_app --factory --reload --port 8000
uv run pytest                          # unit + integration (needs local Mongo)
uv run ruff check . && uv run ruff format --check .
uv run mypy app

# Detection evals. Online mode calls the Claude API and costs money; ask before running it.
uv run python -m evals.make_fixtures   # regenerate the synthetic agreements
uv run python -m evals.run --offline   # heuristics only, free
uv run python -m evals.run             # full pipeline against claude-opus-5-5

# One-time: mint the Gmail API refresh token for the dedicated sender account
uv run python -m scripts.gmail_auth

# Frontend (Vite on :5173, proxies /api → :8000)
cd frontend
npm install
npm run dev
npm run build && npm run lint && npm run typecheck

# Production image, run locally
docker build -t agreement-app .
docker run --env-file backend/.env -p 8000:8000 agreement-app
```

**Deploy:** pushing to `main` triggers a Render auto-deploy from `render.yaml`. Turn auto-deploy off after the final push on Tue Nov 17.

## Environment variables

Defined in `backend/app/config.py`. Never commit `.env`; keep `.env.example` current instead.

| Var | Purpose |
|---|---|
| `APP_ENV` | `dev` \| `test` \| `prod` |
| `APP_BASE_URL` | Public URL used in email links |
| `MONGODB_URI`, `MONGODB_DB` | Database |
| `SESSION_SECRET` | Signs session cookies |
| `ANTHROPIC_API_KEY` | Claude API |
| `LLM_MODEL` (default `claude-opus-5-5`), `LLM_EFFORT` (default `high`) | Detection model settings |
| `EMAIL_BACKEND` | `gmail_api` \| `console` |
| `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`, `GMAIL_SENDER` | Gmail API sender |
| `SENTRY_DSN` | Error reporting (optional in dev) |
| `RUN_WORKER` | `true` starts the in-process job worker |
| `MAX_UPLOAD_MB` (20), `MAX_PAGES` (60), `LIBREOFFICE_BIN` | Ingest limits |

## Domain terms

- **Org**: a customer company (tenant). Every other document carries `org_id`.
- **Template**: an uploaded agreement normalized to PDF, plus its approved **fields** and **roles**. Templates are versioned, and editing an active one creates a new version.
- **Field**: a rectangle on a page, stored in **PDF points with a top-left origin**. Each field has a `type` (`signature`, `initials`, `date_signed`, `name`, `text`, `checkbox`), a `filled_by` (`sender` or a role key), a `key`, a label, `required`, and a `source` (`ai`, `heuristic`, or `manual`).
- **Role**: a signing party in the agreement, for example `client` or `firm`. Roles have an order for sequential routing.
- **File**: one job or case for one client. It holds a snapshot of the template version, the sender-filled values, the recipients, the status, and the hashes.
- **Recipient**: a person assigned to a role on a file. Each recipient has its own token, status, and audit data.
- **Event**: an append-only audit record (sent, viewed, consented, signed, declined, reminded, bounced, ...).
- **Job**: a durable background task (send email, deliver webhook, analyze template, finalize, remind, expire).

**File status:** `draft → awaiting_approval → sent → partially_signed → completed`. The side exits are `declined`, `expired`, and `voided`. A recipient can additionally be `bounced`.

## Rules

**Correctness and safety**
- **Tenant isolation.** Every query on tenant data filters by `org_id`. Go through repository helpers that require it, and never write a bare `find({"_id": ...})` on tenant collections. Tests must cover cross-org access returning 404.
- **No side effects inside request handlers.** Emails, webhooks, LLM calls, finalization and reminders are enqueued as jobs. Every job handler must be idempotent, because it can run more than once.
- **State transitions are atomic.** Use `find_one_and_update` with the expected current state in the filter. Never read, modify and write state in separate steps.
- **Humans approve AI output.** A template's AI-proposed fields cannot be used until a user approves them. A UI send always goes through the preview-and-confirm step. Files created via the API wait in `awaiting_approval` unless the org enabled auto-send.
- **Uploaded documents are untrusted input.** Their text goes to the LLM as data, not instructions. The LLM only returns schema-validated labels for slot IDs we extracted, never coordinates or actions. Validate file type by magic bytes and enforce size and page limits.
- **Signed artifacts are immutable.** Never modify a completed PDF or an event. Corrections create new events.
- **Secrets and PII.** Never log signing tokens, API keys, passwords, refresh tokens or signature images. Store signing tokens and API keys as SHA-256 hashes only.
- **Every failure needs a way out.** It must show up in the UI with a next step, such as retry, upload as PDF, place fields manually, or resend to a different email.

**Claude API** (load the `claude-api` skill before writing or changing LLM code)
- Model `claude-opus-5-5` (configurable through `LLM_MODEL`), `thinking: {type: "adaptive"}`, and `output_config.effort` **set explicitly**: the default is `medium`, and we use `high`.
- Get JSON through structured outputs (`output_config.format` / `client.messages.parse`). Opus 5.5 returns a 400 on forced `tool_choice` (`any`/`tool`), on `temperature`/`top_p`/`top_k`, and on assistant prefill, so none of those are allowed.
- Always check `stop_reason` (`refusal`, `max_tokens`) before using content. Enable server-side refusal fallbacks. On any failure, degrade to `detection/heuristics.py`. Detection must never block the user.
- Send PDFs as base64 `document` blocks. Never silently truncate documents.
- Every prompt change gets an `evals.run` comparison before merge.

**Workflow**
- Run `uv run pytest`, `ruff`, `mypy`, `npm run typecheck`, and `npm run build` before pushing. They must also pass in CI.
- Keep commits small and focused, with conventional messages (`feat:`, `fix:`, `chore:`, `docs:`, `test:`).
- Prefer boring, well-known libraries. Before adding a new dependency or external service, justify it in `DECISIONS.md`.
- When behind schedule, cut in the order given in `DECISIONS.md` § "If behind". Never cut reliability on the graded path.
