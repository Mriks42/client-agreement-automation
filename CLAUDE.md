# CLAUDE.md

Context for anyone working in this repo, human or AI. `DECISIONS.md` explains *why* things are the way they are. This file covers *what* the project is and *how* to work on it.

## Current status (update at the end of every work session)

_Last updated: Sun Nov 15, 2026 (afternoon), end of Day 4. Day 4 finished a day ahead of the revised schedule, so finalize moves up to Monday._

**Next session: Day 5 (Mon Nov 16), finalize.** This is the last piece of the graded path: the signed PDF with its certificate, sent to everyone.
- **`finalize_file` job.** `signing.submit` already enqueues it when the last signer signs (dedupe key `finalize_file:<file id>`). No handler is registered yet, so those jobs sit `pending` and run as soon as one is. Write it in `services/finalize.py` and register it in `worker/handlers.py`:
  1. Stamp every value: `signing.current_pdf` already does this (prepared PDF plus each signer's values, drawn marks loaded by `mark_images`).
  2. Append an audit certificate page (a new `pdf_engine` function). It shows the file id; the agreement name and version; the SHA-256 of the template PDF and of the prepared PDF (`file.prepared.sha256`); for each signer, their name, email, role, viewed, consented and signed times, IP, user agent and signature method (typed or drawn); and how to verify (compare SHA-256).
  3. Store the result as a blob. Set `final_blob_id`, `final_sha256` and `finalized_at` in one conditional update (`finalized_at: None`), so a rerun skips straight to the emails.
  4. Email the signed PDF to every signer and to the sender with `enqueue_email` (attachment by blob id, one dedupe key per address). Record a `finalized` event.
  5. UI: **Download signed agreement** on the file page and on the signer's "completed" page (by token). The banner already switches on `finalized_at`. If the job dies, show it on the file page with a **Try again** button (re-enqueue it).
- **Exit criterion:** on the live app, a fully signed file produces the signed PDF with a certificate page, every party receives it by email, and the file page downloads it.
- **Then, Monday evening: the blind-test round** (plan below).

**Blind-test round (Mon evening, about 2.5 h and $2–3 of credit).** The grade is one live run on an agreement we've never seen, so measure the whole path on six more unseen agreements, judged on the first run, and fix only what would break Anthony's path.
- **Before starting:** the owner tops up $5 (balance then about $7). Day 5 should be live, so every run ends in a signed PDF with its certificate.
- **The set.** Public templates only. Download them into `backend/samples/` (git-ignored); none has been seen before.
  1. Accounting engagement letter, Word: Journal of Accountancy's sample 1040 letter (`.doc`, with bracketed placeholders). Upload the `.doc` first to check the refusal and its next step, then save it as `.docx` and upload that. https://www.journalofaccountancy.com/wp-content/uploads/sites/3/2014/01/1040-engagement-letter-sample.doc
  2. Accounting engagement letter, PDF: the CPAi (AICPA insurance program) individual tax letter. This is a different signing style, where the firm may be the only signer. https://forms.cpai.com/pdfs/TAX_Individual_2016_FINAL.pdf
  3. A fillable PDF (AcroForm fields): one of WCG CPAs' fillable engagement letters. https://wcginc.com/tax-center/engagement/
  4. Consulting agreement, closest to an AI consultancy's: PlatoForms' 2-page Business Consulting Services Agreement (client and consultant signature blocks). https://templates.platoforms.com/pdf/business-consulting-services-agreement
  5. Long terms with few blanks: the AIGA Standard Form of Agreement for Design Services. It tests page count, latency and the appended signature page. https://aiga.org/resources/aiga-standard-form-of-agreement-for-design-services
  6. A scan: #4 printed to an image-only PDF (no text layer). It tests the scan fallback (an appended signature page, plus manual placement).
  - Backups if a download is gated: the AICPA amended-return letter, Wonder.legal's US consulting agreement, or a Google Docs export of any of the above.
- **Each document, on the live app with Claude:** upload, then note the analysis time and cost (`detection.llm` on the template). Count the edits needed before Approve. Open a file, preview, send, sign as the client on a phone viewport and then as the firm, and open the final PDF. Use fake details throughout, and plus-addresses of the sending inbox for the signers. For a quick look while triaging, use `scripts.demo_sign` (same cost).
- **Score:** signers and signing order right; signature blocks found in the document (or a page appended); blanks found out of the total; false positives; who fills each field (sender or signer) right; reviewer edits needed; anything wrong on the signing page or in the final PDF.
- **Pass bar:** each document reaches a signed PDF with correct signers and order, and needs at most 5 reviewer edits. **Blockers:** a crash, an analysis that never finishes, the wrong signing order, or a missing signature block that is hard to fix in the editor.
- **Triage:** fix blockers Monday night or first thing Tuesday, each with a test or an eval fixture. A prompt change needs an `evals.run` comparison (about 55¢ online; say so before running it). Everything else goes to DECISIONS.md as a known limitation. Nothing after the Tuesday 12:00 freeze.
- **Budget:** first pass about $1–1.50 (5–40¢ a document), reruns after fixes about $0.50–1, one online eval about 55¢. That's $2–3 in all, which leaves about $4 for grading, above the $2 floor.
- **Record** the results in the DECISIONS.md change log (as for the earlier blind tests) and in this status.

**Cuts applied (Nov 15):** the "if time allows" items, the verify page, logo and colour branding, bounce detection, and the template-versioning UI. Don't build them. The API and webhooks stay in scope, scheduled last. See DECISIONS.md "If behind schedule".

**Remaining schedule:**
- **Mon:** Day 5, finalize (above), then the blind-test round in the evening.
- **Tue morning:** fix blind-test blockers, then the API and webhooks if there's time, then polish.
- **Hard stops:** feature freeze Tue 12:00, final deploy verified by 17:00, last commit by 20:00 MST.

**Day 4 is done, and verified on the live app.**
- **Files** (`services/files.py`, `api/files.py`):
  - A file copies the approved template (version, PDF, roles, and fields with their redactions) when it's created.
  - The new-file form is `domain/fields.sender_inputs`: one input per shared key. Values are stored by key. There's one signer per role, held as embedded `recipients` in signing order.
  - The draft preview is the "prepared" PDF (sender values stamped), stored per `revision` with its SHA-256. Send must name the revision the sender previewed, then does an atomic `draft → sent`.
  - Empty required blanks are listed in the preview but don't block sending.
  - Also: delete a draft, Copy signing link, correct a name or email (correcting the current signer's email retires their old links and sends a new one), Resend (once a minute at most), and Void with a reason.
- **Signing** (`services/signing.py`, `api/signing.py`):
  - The state machine, routing and the signer's steps live here. A signature, the file's new status and the next signer's activation are one atomic update.
  - The public API (token in the path, no session) has view, consent, submit and decline. Each step is an event (`services/events.py`) with IP and user agent.
  - Tokens are 256-bit and stored only as SHA-256 (`recipients.token_hashes`). They're minted inside the `signing_email` job or by Copy signing link, so a raw token never sits in the database.
  - Reminders (day 2 and 5) are due-time jobs scheduled when a signer becomes current, and each checks the signer still hasn't signed. Expiry is a due-time job at 30 days that emails the sender. A dead signing email shows on the file with a way out.
  - "Date signed" uses the signer's own time zone. A signer's checkboxes are never required.
  - Signing paths are redacted in request logs and Sentry.
  - **Gotcha, documented in `is_current`:** never filter on `recipients.<i>.<field>` with null or not-null tests. MongoDB also reads that path as a field named "0" inside each element, so null always matches. Use `$elemMatch`.
- **Emails** (`email/messages.py`): invitation, reminder, resend, declined and expired, each with a text part and an HTML part. From the firm's name, Reply-To the sender.
- **Frontend:**
  - A Files list (now the home page after login), and the new-file form, which also edits a draft.
  - The draft page shows the exact pages, then Confirm and send.
  - The file page shows status, check-spam hint, signers with Copy link, Resend and Change email, the timeline and Void.
  - The signing page is mobile first: ESIGN consent, Next walks each field, typed or drawn signature, dates filled in, decline. Its overlay draws values with the same size and baseline rules as `pdf_engine._write`, so what the signer sees matches the PDF.
  - Drawn signatures are stamped on the line at the left.
- **Verified:**
  - **Local, headless Chrome:** the whole flow, with the client on an iPhone 13 viewport (typed signature) and the firm on desktop (drawn signature).
  - **Live, with Claude** (2-page fixture, 13 s analysis): sign up, approve, new file, preview, send, Copy signing link, client signs, firm countersigns from its copied link, "Signed: ready to start work". Both signing emails, sent to plus-addresses of the sending inbox, landed in its **Primary** tab. But mail an account sends itself is trusted by Gmail, so this proves delivery and formatting, not placement for an outside recipient. **Still to do: one send to an address on another account or provider** (the Day 1 email to an outside address went to spam).
  - Tests: 112 backend tests pass (cross-org 404 on every file endpoint, stale revisions, token storage, decline, void, reminders, expiry, a failed email), plus ruff, mypy, typecheck, lint and build. CI is green.
- **Known gaps, accepted:**
  - Finalize isn't built yet (Day 5).
  - Void doesn't email the current signer; their link says the agreement was withdrawn.
  - If the process dies in the instant between a signature's update and queueing the next signer's emails, that signer gets nothing until the sender uses Resend or Copy link.
  - A delivered-but-lost email isn't detected (bounce detection was cut). Copy link and Resend cover it.
  - `.doc` uploads are refused with a "save as .docx or PDF" message. Accepting them through LibreOffice would be a small change, if the blind test shows a need.

**Day 3 (templates), for reference:**
- Auth (D14): argon2id; server-side sessions stored as an HMAC of the cookie token; a per-email login throttle; cross-site `/api/` writes refused by `Origin`.
- `repo.Tenant` adds `org_id` to every query, and `transition()` is the atomic conditional update. `find_file_by_signing_token` is the one unscoped lookup (the token is the credential).
- Templates: `analyzing → needs_review → active`, with `analysis_failed` offering **Try again** or **Place the fields myself**. `revision` guards edits; `version` goes up when an approved template is edited. Approval needs a signature field for every signer.
- The review editor is desktop-first. Live with Claude, the Common Paper PSA (6 pages, 156 blanks) took 137 s and gave 152 fields, with both signature blocks found in the document.
- Templates can't be deleted. The login throttle and page cache are per-process. Test accounts named `e2e+<timestamp>@example.com` exist in the live DB.

**Day 2 (document pipeline), for reference:**
- The pipeline: `ingest` → `normalize_pdf` → `extract` → `detection/pipeline.analyze` (Claude, or heuristics on any failure) → `mapper` → an appended signature page if needed. `pdf_engine.stamp` writes values, and finalization reuses it. Preview any agreement with `uv run python -m scripts.demo_sign samples/<file>`.
- **Eval (11 synthetic fixtures):** online 81/81 blanks, signature blocks 11/11, 0 false positives. Offline (heuristics only) 80/81. Treat these as a regression floor.
- **Blind tests** (Common Paper PSA, a CalBar fee agreement, Bonterms PSA): signers and signing order were right every time. Still open: capitalised letterhead words with no brackets ("LAW FIRM") aren't detected.
- **Cost and latency scale with blanks:** about 5¢ and 10 s for 1 page, about 21¢ and 31 s for a 6-page letter, and about 41¢ and 144 s for 156 blanks. The per-call timeout is 300 s.

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
- **Anthropic API key** `kickoff`: Individual org, Default workspace, prepaid with auto-reload off. It expires **Feb 28, 2027**. The balance was about $2.60 on Nov 15, before three live Day 3 runs (about 5¢, 5¢ and 41¢) and the Day 4 live run (about 5¢), so it's now about **$2.05, just above the $2 floor**. The owner will add $5 before the blind-test round. Check it before submitting, and keep at least $2: at $0, detection silently falls back to heuristics. The key is in Render and in `backend/.env` (gitignored).
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
    repo.py              Tenant: the only way to query tenant collections (adds org_id; atomic transition())
    api/                 Routers: auth, templates, files, signing (public, token-auth), health, ops; v1 (API-key auth) later
    domain/              Pydantic models + state enums (fields.py: Field, Role, Rect)
    services/
      accounts.py        Orgs, users, argon2 passwords, HMAC-keyed sessions, login throttle
      storage.py         GridFS put/get/delete (the storage interface) and a small bytes LRU
      templates.py       Template lifecycle: upload, analyze job, validation, save, approve
      ingest.py          Upload validation, DOCX→PDF
      pdf_engine.py      Extract lines/slots, render PNGs, stamp fields, append pages (the only PyMuPDF import)
      detection/         schema.py, prompt.py, llm.py, heuristics.py, mapper.py, pipeline.py (analyze: the entry point)
      files.py           Files: create from an approved template, edit a draft, prepare (preview), send, copy link, resend, void
      signing.py         File/recipient state machine, routing, tokens, the signer's steps, signing email/expiry jobs
      events.py          Append-only audit events
      errors.py          NotFound / Conflict / Invalid, mapped to 404 / 409 / 422 by the routers
      finalize.py        Flatten values, audit certificate, SHA-256 (Day 5)
      email/             EmailSender protocol, gmail_api.py, console.py, messages.py (email text), outbox.py (enqueue_email)
      webhooks.py        Outbound webhooks (HMAC-SHA256)
    worker/              queue.py (claim/backoff/dead-letter, due-time jobs), handlers.py (the job registry)
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
