# CLAUDE.md

Context for anyone working in this repo, human or AI. `DECISIONS.md` explains *why* things are the way they are. This file covers *what* the project is and *how* to work on it.

## Current status (update at the end of every work session)

_Last updated: Tue Nov 17, 2026, about 02:30 MST. The blind-test round is done. All six unseen agreements reached a signed PDF on the live app, and the two blockers it found are fixed and deployed._

**Next session (before the 12:00 freeze):**
1. ~~The polish list~~: done, see below.
2. ~~The API and webhooks~~: built and verified live, see "API and webhooks" below.
3. Final deploy, verified by 17:00: run `scripts.live_check`, then turn off auto-deploy.
- **Credit:** the owner topped up $5, so the balance was about $7. The round ran 7 Claude analyses (the six agreements, plus one CPAi rerun). From past per-document costs that's an estimated $1–2; the live API doesn't expose `detection.llm`. Read the console balance and keep at least $2.

**Day 5 (finalize) is done, and verified on the live app.**
- **The job** (`services/finalize.py`, registered in `worker/handlers.py`; 5 attempts, a 10-minute lease):
  1. Stamps every value on the prepared PDF (`signing.stamp_signed`, the uncached half of `current_pdf`).
  2. `pdf_engine.finalize_pdf` bakes leftover form fields into the page (nothing left to fill in) and appends the certificate. It has the document details; SHA-256 of the upload, of the approved agreement and of the PDF as sent; per signer the email, role, link, viewed, consented (with disclosure version), signed, signature method, IP and user agent; the audit trail; and how to check a copy (`shasum -a 256` or `certutil`). Times are in UTC. Long sections flow onto more pages, and a signer's block never splits.
  3. One conditional update (`finalized_at: None`) stores `final_blob_id`, `final_sha256` and `finalized_at`. A losing or repeated run deletes its own blob and keeps the first.
  4. Emails go to every signer (from the firm, Reply-To the sender) and to the sender (with a file link). One job per address, deduplicated, and a signer who is also the sender gets one email. Then a `finalized` event.
- **The certificate can't hold its own hash.** `final_sha256` is shown on the file page and printed in every delivery email, and the certificate says so.
- **Failures have a way out.** A dead job sets `finalize_error`: the file page shows it with **Try again** (`POST /api/files/{id}/finalize` resets the same job). A signed copy that can't be emailed records `email_failed` (reason `signed_copy`) on the file, and the page lists it with the download as the next step. `on_email_dead` does this for any queued email whose payload has `ref` and `org_id`.
- **Downloads:** `GET /api/files/{id}/signed.pdf` (session, tenant-scoped) and `GET /api/sign/{token}/signed.pdf` (signer state `completed`). The signer's view has `signed_pdf_ready`. Both pages poll until the PDF exists (the signer's page for about 3 minutes at most).
- **Fixed along the way:** the built-in PDF fonts only cover Latin-1, so a phone's curly apostrophe (O’Brien) was stamped as "O·Brien". `pdf_engine.latin1()` now writes plain forms for stamps and the certificate. Other scripts still show as "?" (DECISIONS.md, Known limitations).
- **Verified:**
  - Tests: 120 backend tests pass (8 new: the whole flow, rerun idempotency, two racing runs, Try again, failed signed-copy emails, cross-org 404, certificate pagination, Latin-1). Also ruff, mypy, typecheck, lint and build. CI is green.
  - **Local, headless Chrome:** both parties signed (client on an iPhone 13 viewport), the signer's page and the file page offered **Download signed agreement**, and both downloads matched the SHA-256 shown on the page.
  - **Live:** the two completed files that were waiting were finalized within seconds of the deploy. That includes the file the owner signed end to end on Nov 15, whose certificate shows both real drawn signatures, IPs and browsers. A new file on the approved agreement had its signed PDF 2–5 s after the last signature, the signer's download by link was the same file, the file page in a browser showed the download with a matching hash, and Gmail accepted every email (0 dead jobs). The signed copy reached an outside Gmail Inbox (see above).
- **Free live smoke test, for Tuesday's final deploy:** `LIVE_CHECK_PASSWORD=<the test-suite password> uv run python -m scripts.live_check --base https://kickoff-r713.onrender.com --email e2e+1794741626993@example.com`. That account holds the approved "two party services" agreement and the completed live files. It runs a whole file on the approved agreement with no upload (so no Claude call) and checks the signed PDF. Add `--inspect-only` to just check the completed files.

**Blind-test round (Nov 17): done.** Six public agreements, none seen before, each judged on its first run on the live app with Claude. Each run: upload, review, file, preview, send. The client signed on an iPhone 13 viewport (typed), the firm on a desktop (drawn). Then the signed PDF and certificate were opened and checked: the hash, no form fields left, the signer's download identical, and no failed emails. A scratch Playwright script drove the installed Chrome; it isn't committed. The agreements are in `backend/samples/blind/` (git-ignored). The test firm was `e2e+blind1794904039@example.com`; its password wasn't recorded.

| # | Agreement | Analysis | Signers, in order | Blanks found | Edits | Result |
|---|---|---|---|---|---|---|
| 1 | Journal of Accountancy 1040 letter, Word. The `.doc` was refused with its next step, then the `.docx` was uploaded | 104 s (14 s of it conversion) | Client, spouse, firm (a signature page was added: the letter has no firm line) | 28/28, 0 false | 0 | Signed PDF in 3 s |
| 2 | CPAi individual tax letter, 10-page PDF | 35 s | Client, spouse, firm | 30/30, 0 false | 0 | **2 blockers, fixed** (below). The rerun was clean |
| 3 | WCG 1040 engagement form, 6-page PDF | 25 s | Taxpayer, spouse (the firm correctly doesn't sign) | 20/20, 0 false | 1: remove the optional representative | Signed |
| 4 | PlatoForms consulting agreement, fillable PDF (23 form fields: dropdowns, signature boxes) | 20 s | Client, consultant | 23/23, 0 false | 0 | Signed, no form fields left |
| 5 | AIGA design services agreement, 28 pages | 46 s | Client, designer | Every core term and both signature blocks; about 20 short "___" option and initials blanks missed | 0 (2 more per supplement used) | Signed PDF in 9 s |
| 6 | Scan of #4 (images only) | 9 s | Client, consultant (read from the page images) | 0/23, as expected; a signature page was added | 5, placed by hand in the editor | Signed |

- **Pass bar met on all six.** Every email was accepted, and every signed PDF was ready 2–9 s after the last signature.
- **Blockers fixed** (commit `5cc3912`, tests in `test_pdf_engine.py` and `test_detection.py`):
  - Filling a placeholder removed words on the lines above and below it: 26 words on the CPAi letter, so the signed wording changed. In tightly set text the extraction boxes overlap the next line, and redaction removes every glyph it touches. Removal now clears only `pdf_engine._ink_band`, a band from the baseline to about x-height that's never larger than the old box. A sweep of about 300 placeholders in 10 agreements found 0 words lost and 0 placeholder text left.
  - "$[X]" for the fee was read as a ticked box, so the fee was written over the printed "[X]". A bracket box labelled as a value is now removed like a placeholder (`mapper._rect`), and one labelled as a checkbox keeps its brackets.
- **Fixed afterwards in the polish** (see the polish list): tiny values in short placeholders, unless text follows them on the same line; "?" around a curly-quoted title on the added signature page.
- **Not fixed** (DECISIONS.md, Known limitations): a value in a mid-sentence placeholder still shrinks; blanks under 15 pt aren't found; optional signers belong to the template; dropdown options aren't offered; a scan's hint text stays under values; role names can echo placeholders ("[Client Name]").
- Tests: 127 backend tests pass (7 new), plus ruff and mypy. The offline eval is unchanged at 71/72 without the Docker-only Word fixture.

**Cuts applied (Nov 15):** the "if time allows" items, the verify page, logo and colour branding, bounce detection, and the template-versioning UI. Don't build them. The API and webhooks stay in scope, scheduled last. See DECISIONS.md "If behind schedule".

**Remaining schedule:**
- **Tue morning, before the 12:00 freeze:** the polish list, then the API and webhooks or their cut.
- **Polish list: done** (Nov 17, about 03:15). Values the firm fills in run on into the empty space to their right instead of printing tiny (`pdf_engine._right_limit`, sender values only, through `Stamp.room`). The added signature page writes curly quotes in plain form. The landing page no longer has the "System status" box. 129 backend tests pass.
- **Hard stops:** feature freeze Tue 12:00, final deploy verified by 17:00, last commit by 20:00 MST.

**API and webhooks (Nov 17, about 03:45): built, lean** (DECISIONS.md item 10, D5).
- **Settings page** (`/settings`, `api/settings.py`): API keys (create shown once, revoke), the webhook endpoint (signing secret, new secret, test event, remove) and the delivery log (the last 25 `deliver_webhook` jobs, with Retry on one that gave up). It also has a quick-start for developers, and the full reference is at `/api/docs`.
- **Public API** (`api/v1.py`, `Authorization: Bearer kk_live_...`): `GET /api/v1/templates`, `POST /api/v1/files` (`Idempotency-Key` header, `metadata`; it makes a draft marked as from the API, and the draft page says so), `GET /api/v1/files/{id}`, `GET /api/v1/files/{id}/signed.pdf`. Keys are found by SHA-256 (`repo.find_api_key`, the second unscoped lookup) and act for their creator.
- **Webhooks** (`services/webhooks.py`): `events.record` calls `webhooks.on_event`, which queues one `deliver_webhook` job per event per endpoint (deduplicated) if the firm has an endpoint. The event types are file.sent, signer.signed, file.declined, file.voided, file.expired, and file.completed (on `finalized`), plus `ping` for the test. The body is `{id, type, created_at, data: {file (api_view.file_view), signer?, reason?}}`, signed `X-Kickoff-Signature: t=..,v1=HMAC-SHA256(secret, "t.body")`. Delivery makes 8 attempts with backoff, and the result's status code is kept on the job. Addresses go through `check_url`: https only and public addresses only in prod (private allowed in dev and test), no redirects, re-checked on delivery.
- **Verified:** 135 backend tests (6 new in `test_api.py`: the key lifecycle, a file through the API with idempotency, cross-org 404s, signed and retried webhooks over a whole signing flow, the delivery log and Retry, URL safety), plus ruff, mypy, typecheck, lint and build. **Locally in Chrome:** signup, approval, a key and webhook created in Settings, the test event delivered, a file opened through the API with the draft page showing "Opened through the API with the key “CRM” (matter_id: 1042)", sent, signed. A local receiver got ping, file.sent, two signer.signed and file.completed, every signature verified, and the signed PDF downloaded with the key matched its SHA-256.
  - **Live (no Claude call):** a key was created in Settings, and a private address (10.0.0.5) was refused. A file opened through the API (a repeat with the same Idempotency-Key returned the same file) was sent after the preview and signed by both parties. The signed PDF downloaded with the key matched its SHA-256, and `metadata` came back. The webhook pointed at the app's own GET-only `/healthz`, so no test data went to a third party: ping, file.sent, both signer.signed and file.completed were each delivered, signed, refused with 405 and queued to retry. The webhook was then removed and the keys revoked (401). Once, the second signer's view returned non-JSON right after the first signature; it didn't happen again, and roughly 20 other live signer views were fine. If it recurs, check Render's logs for a 502.

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
  - **Live, by the owner, with real emails:** the client signed from the invitation in an outside Gmail inbox, and the firm countersigned from its own invitation email; the file completed. It showed that the AI's field labels read oddly to a signer ("Client signer title"), so the signing page now shows plain labels ("Title") with a hint.
  - **Live, with Claude** (2-page fixture, 13 s analysis): sign up, approve, new file, preview, send, Copy signing link, client signs, firm countersigns from its copied link, "Signed: ready to start work". Both signing emails, sent to plus-addresses of the sending inbox, landed in its Primary tab. Then the owner sent a file to an outside personal Gmail account: it landed in the **Inbox, not spam**. It showed as from "Acme Test Co", with Reply-To the firm user, passed Gmail's sender checks (mailed-by and signed-by gmail.com), and the file page switched to "Emailed to …" on its own. **Not yet checked: Outlook or Microsoft 365 inboxes.** Copy signing link and the check-spam hint cover those.
  - Tests: 112 backend tests pass (cross-org 404 on every file endpoint, stale revisions, token storage, decline, void, reminders, expiry, a failed email), plus ruff, mypy, typecheck, lint and build. CI is green.
- **Known gaps, accepted:**
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
- **Anthropic API key** `kickoff`: Individual org, Default workspace, prepaid with auto-reload off. It expires **Feb 28, 2027**. The balance was about $2.60 on Nov 15, before three live Day 3 runs (about 5¢, 5¢ and 41¢) and the Day 4 live run (about 5¢), so it's now about **$2.05, just above the $2 floor** (Day 5 used none). The owner will add $5 before the blind-test round. Check it before submitting, and keep at least $2: at $0, detection silently falls back to heuristics. The key is in Render and in `backend/.env` (gitignored).
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
    api/                 Routers: auth, templates, files, signing (public, token-auth), health, ops, settings (API keys, webhook), v1 (public API, API-key auth)
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
      finalize.py        The finalize_file job: signed PDF, audit certificate, SHA-256, everyone's copy
      email/             EmailSender protocol, gmail_api.py, console.py, messages.py (email text), outbox.py (enqueue_email)
      webhooks.py        Outbound webhooks: endpoint settings, URL safety check, HMAC signing, the deliver_webhook job
      apikeys.py         API keys (SHA-256 stored, shown once), authenticate for /api/v1
      api_view.py        A file as the API and webhooks show it
    worker/              queue.py (claim/backoff/dead-letter, due-time jobs), handlers.py (the job registry)
  tests/
  evals/                 fixtures/ (synthetic agreements), expected/ (labels), make_fixtures.py, run.py
  scripts/               gmail_auth.py (mints the Gmail refresh token), demo_sign.py (preview detection + a fake-signed PDF), live_check.py (free end-to-end check of a deployed app), seed_demo.py
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

# Free end-to-end check of a deployed app (no upload, so no Claude call): a whole file on an
# approved agreement, signed by every party, then the signed PDF and its certificate checked.
LIVE_CHECK_PASSWORD=... uv run python -m scripts.live_check --base <url> --email <account> [--inspect-only]

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

**File status:** `draft → sent → partially_signed → completed` (a file opened through the API is a draft marked `api`; auto-send and its `awaiting_approval` status are cut, D5). The side exits are `declined`, `expired`, and `voided`. A recipient can additionally be `bounced`.

## Rules

**Correctness and safety**
- **Tenant isolation.** Every query on tenant data filters by `org_id`. Go through repository helpers that require it, and never write a bare `find({"_id": ...})` on tenant collections. Tests must cover cross-org access returning 404.
- **No side effects inside request handlers.** Emails, webhooks, LLM calls, finalization and reminders are enqueued as jobs. Every job handler must be idempotent, because it can run more than once.
- **State transitions are atomic.** Use `find_one_and_update` with the expected current state in the filter. Never read, modify and write state in separate steps.
- **Humans approve AI output.** A template's AI-proposed fields cannot be used until a user approves them. A UI send always goes through the preview-and-confirm step. Files created via the API wait as drafts for a person to check and send (auto-send is cut).
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
