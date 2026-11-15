# CLAUDE.md

Context for anyone working in this repo, human or AI. `DECISIONS.md` explains *why* things are the way they are. This file covers *what* the project is and *how* to work on it.

## Current status (update at the end of every work session)

_Last updated: Sun Nov 15, 2026 (early morning), end of Day 2. Day 2 ran two days late (planned for Thu Nov 12)._

**Next session: Day 3, templates.** This is the first half of the graded path: Anthony signs up, uploads the MIND agreement, reviews the proposed fields and approves them.
- **Backend:**
  1. **Auth (D14).** `orgs` and `users` collections; signup, login and logout; argon2 hashing (`argon2-cffi` is already a dependency); an httpOnly SameSite session cookie signed with `SESSION_SECRET`; a `require_user` dependency; repository helpers that require `org_id`; tests that cross-org access returns 404.
  2. **Template upload.** `POST /api/templates` (multipart) runs `services/ingest.ingest`, stores the original and the normalized PDF in GridFS, creates the template with status `analyzing`, and enqueues an `analyze_template` job.
  3. **The `analyze_template` job.** It calls `detection.pipeline.analyze`. It stores the returned `pdf`, which may include an appended signature page, along with the roles, fields, warnings and `detector`/`fallback_reason`, then sets status `needs_review`. On failure it sets `analysis_failed` and gives a next step (place fields manually, or upload a PDF). The job needs a lease of **at least 10 minutes**. The worker's `LEASE` is a global 5 minutes and the LLM budget is up to 330 s, so add a per-`JobType` lease to `worker/queue.py`.
  4. **Other template endpoints.**
     - `GET` the list.
     - `GET /{id}` returns status, fields, roles and warnings.
     - `GET /{id}/pages/{n}.png` uses `pdf_engine.render_page_png`.
     - `PUT` saves edited fields and roles.
     - `POST /{id}/approve` makes an atomic `needs_review → active` transition.
- **Frontend:**
  - Signup and login pages, and a templates list.
  - An upload page that polls while analysis runs and says it "usually takes 1–3 minutes".
  - **The review editor:**
    - page images with the fields overlaid, scaled from PDF points;
    - click a field to edit its type, who fills it, its label and whether it's required;
    - drag to move or resize, add, and delete fields;
    - show the AI's warnings;
    - an Approve button.
- **Exit criterion:** on the live app, sign up, upload an agreement (for example `backend/samples/commonpaper-psa-cover.pdf`), see the proposed fields, edit one, and approve.

**Decision the owner still has to make:** whether to apply DECISIONS.md "If behind" cuts now. I recommend yes, in the documented order: the "if time allows" items, then the verify page, branding, bounce detection, and the template-versioning UI. Record the decision in DECISIONS.md.

**Remaining schedule** (it replaces the original day plan after the slip):
- **Sun:** Day 3, templates (above).
- **Mon:** files and signing.
  - The new-file form, built from the template's sender fields.
  - A preview of the exact PDF to be sent, using `pdf_engine.stamp`, then Send.
  - Recipients and signing tokens, sequential routing, emails plus the copy-link fallback (D11).
  - The signing page: consent, guided fields, `signature_pad`, decline.
  - Reminders.
- **Tue morning:** finalize. Stamp every value, add the audit certificate page and SHA-256 hashes, email the signed PDF to every party, and mark the file "Signed: ready to start work". Then polish, and the API and webhooks if there's time.
- **Hard stops:** feature freeze Tue 12:00, final deploy verified by 17:00, last commit by 20:00 MST.

**Day 2 is done.**
- **Document pipeline** (library code; Day 3 puts it behind an endpoint and a job):
  1. `services/ingest.py` checks magic bytes and size limits, and converts DOCX to PDF with LibreOffice.
  2. `pdf_engine.normalize_pdf` bakes in page rotation and strips JavaScript and attachments.
  3. `pdf_engine.extract` finds lines and candidate blanks with measured boxes.
  4. `detection/pipeline.analyze` labels them with Claude, or with heuristics if Claude fails.
  5. `detection/mapper` builds field rectangles and checks bounds and overlaps.
  6. Any role with nowhere to sign gets an appended signature page.
  7. `pdf_engine.stamp` writes values into the PDF: placeholders removed, values shrunk to fit, typed signatures, ticks. Finalization will reuse it.
- **Preview tool:** `uv run python -m scripts.demo_sign samples/<file>` writes a review PDF and a fake-signed PDF to `backend/samples/out/`. `backend/samples/` is git-ignored apart from its README.
- **Eval:** 11 synthetic fixtures.
  - **Online (Claude, `claude-opus-5-5`, effort high): 81/81 blanks, signature blocks on 11/11, 0 false positives.**
  - Offline (heuristics only), full set in the production image: 80/81 blanks (98.8%), signature blocks on 11/11.
  - The fixtures are cleaner than real agreements. Treat these numbers as a regression floor.
- **Blind tests on three unseen public agreements:** the Common Paper PSA cover page (6-page DOCX), a State Bar of California fee agreement (6-page PDF) and the Bonterms PSA cover page (1-page DOCX).
  - Every signature block, signer and signing order was right on the first run.
  - The misses were per-client blanks: `[XX]`, option boxes, long instruction placeholders, "Company:"-style label gaps, a blank after a tick box, and instructions that wrap to the next line. All are now fixed and pinned by tests.
  - Still open, deliberately: letterhead words in capitals with no brackets ("LAW FIRM", "ADDRESS") aren't detected; the reviewer adds them by hand.
- **Cost and latency scale with the number of blanks:** about 5¢ and 10 s for a 1-page agreement, about 21¢ and 31 s for a 6-page letter, and about 41¢ and **144 s** for a 156-blank template. The per-call timeout is 300 s.
- **CI** runs the offline eval in two places: the backend job, which skips the DOCX fixture, and inside the Docker image, which runs the full set.

**Infrastructure (Day 1, unchanged):**
- **Live app:** https://kickoff-r713.onrender.com
  - Render web service `kickoff`, free plan, Oregon, created from `render.yaml`. It auto-deploys on every push to `main`.
  - `GET /api/health` reports database, email, worker and LLM as ok. The LLM check only confirms a key is configured; the online eval is the real test.
- **Database:** MongoDB Atlas M0 `Cluster0` (AWS us-west-2, MongoDB 8.0). The IP access list includes `0.0.0.0/0` for Render.
- **Email:** Gmail API from `kickoff.signing.test@gmail.com`. The Google Cloud project is `kickoff-email`, and its OAuth app is **In production**, so the refresh token doesn't expire. Email from the live server is verified working. The first test email landed in spam, which is why a copy-link fallback is planned (DECISIONS.md D11).
- **Monitoring:** UptimeRobot checks `/healthz` every 5 minutes. That keeps the free instance awake and emails the owner on downtime.
- **CI:** GitHub Actions is green (backend, frontend, Docker build and boot). CI uses MongoDB 7.0; see the comment in `ci.yml`.
- **Built on Day 1:** the FastAPI app factory, the durable job queue, the email layer, health and ops endpoints, and the frontend shell.
- **Secrets** live only in the Render dashboard and the owner's private notes. Never in the repo, and never in chat.

**Later reminders:**
- **Anthropic API key** `kickoff`: Individual org, Default workspace, prepaid with auto-reload off. It expires **Feb 28, 2027**. The balance was about $2.60 on Nov 15. Check it before submitting, and keep at least $2: at $0, detection silently falls back to heuristics. The key is in Render and in `backend/.env` (gitignored).
- The workspace's 750 free Render hours are shared with two other services; suspend those if grading runs into December.
- Optional, before submitting: rotate the Atlas DB user's password, and update `MONGODB_URI` in Render to match.

## Start of a work session (local dev)

```bash
open -a Docker                      # start Docker Desktop; wait ~20 s until `docker info` works
docker start mongo 2>/dev/null || docker run -d --name mongo -p 27017:27017 mongo:8.0
cd backend && uv sync && uv run pytest -q            # sanity check: all green
uv run uvicorn app.main:create_app --factory --reload --port 8000   # API on :8000
# second terminal:
cd frontend && npm install && npm run dev            # UI on http://localhost:5173
```

At the end of a session: `docker stop mongo` (optional), then update "Current status" above and commit.

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

- **Deadline: Wed Nov 18, 2026 00:00 America/Phoenix.** Any commit after that disqualifies the submission. Features freeze at Tue Nov 17 12:00. **Final deploy, verified live, by Tue 17:00**, because Render has infrastructure maintenance from Nov 17 19:00 MST. Final commit and push by Tue 20:00.
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
    domain/              Pydantic models + state enums (fields.py: Field, Role, Rect)
    services/
      ingest.py          Upload validation, DOCX→PDF
      pdf_engine.py      Extract lines/slots, render PNGs, stamp fields, append pages (the only PyMuPDF import)
      detection/         schema.py, prompt.py, llm.py, heuristics.py, mapper.py, pipeline.py (analyze: the entry point)
      signing.py         File/recipient state machine, routing, atomic transitions
      finalize.py        Flatten values, audit certificate, SHA-256
      email/             EmailSender protocol, gmail_api.py, console.py, templates/
      webhooks.py        Outbound webhooks (HMAC-SHA256)
    worker/              queue.py (claim/backoff/dead-letter), handlers.py, scheduler.py
  tests/
  evals/                 fixtures/ (synthetic agreements), expected/ (labels), make_fixtures.py, run.py
  scripts/               gmail_auth.py (mints the Gmail refresh token), demo_sign.py (preview detection + a fake-signed PDF), seed_demo.py
  samples/               drop agreements here for demo_sign; git-ignored except the README
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
uv run python -m evals.run --baseline evals/out/<earlier>.json   # compare two runs
# The DOCX fixture needs LibreOffice, so run the full offline set inside the image:
docker build -t agreement-app .. && docker run --rm -e APP_ENV=test agreement-app python -m evals.run --offline

# Preview on any agreement: writes samples/out/<name>-review.pdf (proposed fields) and -signed.pdf
# (filled with fake data, typed signatures). Online mode costs about 5–40¢ per document.
uv run python -m scripts.demo_sign samples/<file>.pdf [--offline]

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
- **Field**: a rectangle on a page, stored in **PDF points with a top-left origin**. Pages are 1-based, and coordinates refer to the normalized PDF, with rotation already baked in. Each field has:
  - a `type`: `signature`, `initials`, `date_signed`, `name`, `text`, `date` (a sender-filled date such as an effective date), or `checkbox`;
  - a `filled_by`: `sender` or a role key;
  - a `key`: fields that share a key and `filled_by` share one value, so each `[CLIENT NAME]` is typed once and initials are adopted once;
  - a label, `required`, `source` (`ai`, `heuristic`, or `manual`), and `redact` (placeholder text removed before the value is written).
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
