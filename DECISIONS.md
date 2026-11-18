# Decisions

This is the record of what I decided to build, what I left out, and why. I wrote it before implementation began (Nov 11, 2026) and update it whenever a decision changes. The change log is at the bottom.

## The problem as I understand it

> "Every client we take on has to sign the same agreement before we can start work. Right now that is manual, and it slows down every file. I want something that automates getting that agreement signed on every file. Once it works for us, I want to sell it to every other company in our industry."

That request contains four requirements:

1. **Any agreement.** The product has to work on an agreement it has never seen. Astra will test it with the MIND client agreement, which I will not see in advance.
2. **Every file, automatically.** Each new file (one job or case for one client) should get the agreement pre-filled with that file's details. The agreement then has to be sent to the right people, signed, chased if it stalls, and returned as a completed, tamper-evident record. After that, the rest of the business needs to know that work can start.
3. **Sellable.** Other companies must be able to sign up, use their own agreement and branding, and connect it to whatever system they open files in, all without me.
4. **Runnable.** It has to be reliable, observable, and safe, with a human confirming the actions that matter.

**The hardest requirement is #1.** The format is unknown (text PDF, DOCX, or a scan), and so is the layout. I don't know how many parties sign or where each signs. Per-file blanks (client name, fee) look exactly like signer blanks (printed name, title, date) and like decorative underscores. And it is tested live, once, on a document I can't tune against. Most of the design below follows from taking that seriously.

## Assumptions

The brief says to make a reasonable decision when something is unclear and to write it down. These are those decisions.

| # | Assumption |
|---|---|
| A1 | "Our industry" means professional-services firms that open "files": consultancies, agencies, and legal or accounting practices. Nothing in the product is specific to one of these. |
| A2 | "The same agreement" means one standard agreement per company. The product supports several per company, with one marked as the default. |
| A3 | The agreement arrives as a **PDF or DOCX**, at most 20 MB and 60 pages. A Google Doc is exported to one of those first. |
| A4 | English-language agreements. **US ESIGN Act / UETA** is the compliance baseline. |
| A5 | The firm (a staff member, or the firm's own systems through the API) opens the file. The client does not start the process. |
| A6 | Signers are the client and, optionally, a firm countersigner. The actual parties are read from each agreement. |
| A7 | Email is the delivery channel for signing links. |
| A8 | One agreement per file. |
| A9 | Anthony plays both the firm admin and the signer, using his own inbox and test data. |

## Key decisions

### D1: Build signing myself, or use an e-signature provider
- **Options:**
  - (a) the DocuSign, Dropbox Sign, or BoldSign API
  - (b) self-host an open-source signer (DocuSeal, Documenso)
  - (c) native signing
- **Decision:** (c).
- **Why:**
  - The client's product is *getting the agreement signed on every file*. Capturing a signature is the easy, commodity part.
  - Provider sandboxes mark documents as test or not legally binding, which is the wrong first impression for a grader.
  - Per-envelope or per-seat provider fees come straight out of the margin when the client resells.
  - Providers still need to be told where the fields go, so the hard part (detection) has to be built either way.
  - Self-hosting means running and patching a second heavy service on a 512 MB instance, and it reduces my own work to glue.
  - Astra already runs its own e-signature tool (MIND Sign), so building in-house is clearly acceptable to them. A MIND Sign or DocuSign adapter would go in `services/signing.py`, which owns the signing steps. *(I planned a separate `SignatureProvider` interface for it; it wasn't needed for native signing, so it wasn't extracted.)*
- **Revisit when:** a buyer requires a specific provider, or needs a qualified signature (see D7).

### D2: How the product handles an agreement it has never seen
- **Options:**
  - (a) manual drag-and-drop field placement only
  - (b) fully automatic AI detection
  - (c) the AI proposes, a human reviews once, and the approved template is reused on every file
- **Decision:** (c), with a chain of fallbacks so the flow never dead-ends.
- **Why:**
  - (a) makes every customer do setup work.
  - (b) can be silently wrong, and a contract with a missing signature field is worse than useless.
  - (c) puts a human check where it matters, and that check happens once per agreement, not once per file.
- **How detection works:**
  1. **Normalize.** Reject password-protected PDFs. Convert DOCX to PDF. Bake page rotation into the content, so stored coordinates match what people see. Strip JavaScript and embedded files.
  2. **Extract.** Pull every text line and every candidate blank from the PDF, each with its real coordinates and a stable ID. Candidate blanks are:
     - underscore runs and dotted blanks (but not table-of-contents leaders);
     - drawn lines, which is how Word exports underlined tabs (but not text underlines or table borders), with any caption printed below them;
     - `[Bracketed]`, `{{curly}}`, `<<angle>>` and `«guillemet»` placeholders, including ones that wrap onto the next line;
     - empty space after a label such as "Signature:" or "By:";
     - checkbox glyphs, and the document's own PDF form fields.
  3. **Classify.** Claude reads the page images and the ID'd lines and blanks. It returns the signing parties and, for each blank, what the blank is and who fills it (the firm per file, or a specific signer). **The model never outputs coordinates.** It labels positions we measured, so it cannot invent a field that isn't on the page.
  4. **Validate.** Check the output with a Pydantic schema and against the slots we extracted, and retry once with the errors fed back. Slots that are still unlabelled get the heuristic label. Then run sanity checks: fields sit inside the page, fields don't overlap, and every signer role has a signature field.
  5. **Fall back.**
     - LLM failure, timeout, or refusal → keyword heuristics.
     - A role with no signature field → an appended signature page for that role.
     - A scanned document with no text layer → an appended signature page, plus manual placement.
  6. **Review.** A human sees every page with the proposed fields overlaid, edits them, and approves.
- **Proof:** an eval harness of deliberately varied synthetic agreements measures detection precision and recall before every prompt change. I also run a blind test on agreements I never tuned against.

### D3: How documents are represented
- **Options:**
  - (a) keep the original PDF and overlay fields on it
  - (b) convert to HTML and re-typeset
  - (c) regenerate each document from the DOCX
- **Decision:** (a). DOCX files are converted to PDF once, at upload, using LibreOffice and metric-compatible fonts.
- **Why:** the client must sign exactly the document the firm uploaded. Re-typesetting a legal document risks changing it.
- **Cost:** long values don't reflow. They shrink to fit, and the preview warns before sending.

### D4: What triggers "every file"
- **Options:**
  - (a) a "New file" form in the dashboard
  - (b) a REST API with outbound webhooks
  - (c) native integrations (HubSpot, Clio, Salesforce, ...)
  - (d) a public intake link that clients fill in themselves
- **Decision:** (a) and (b).
- **Why:**
  - (a) is how a small firm (and Anthony) uses the product.
  - (b) lets any system that opens files trigger the agreement and hear back when it's signed. That covers CRMs, practice-management tools, Zapier, and Make without me building one connector per vendor.
  - (c) is a long tail with no clear first target.
  - (d) inverts A5 and invites spam and identity problems.

### D5: Where a human confirms
- **Decision:** a human confirms at every consequential point, and nowhere else.
  - **Template approval is mandatory.** AI-proposed fields are never used until a person approves them.
  - **Sending from the UI** always shows a preview of the exact PDF that will go out, with a Confirm button.
  - **Files created through the API** wait as drafts, marked as opened through the API. A person at the firm checks the preview and sends them, as with any file. *(The planned auto-send switch is cut, so there's no separate "awaiting approval" status: a draft from the API is that state.)*
  - **Countersigning** by the firm stays a deliberate human act.
- **Why:** Astra explicitly values humans confirming before actions. Requiring confirmation on every reminder or status change would defeat the automation.

### D6: How signers are identified
- **Options:**
  - (a) an email link
  - (b) an email link plus a one-time code
  - (c) SMS one-time code
  - (d) ID or knowledge-based verification
- **Decision:** (a). The link carries a random 256-bit token that is stored only as a hash and expires. Before signing, the signer gives explicit ESIGN consent. IP, user agent, and timestamps for viewed, consented, and signed are recorded in the audit trail.
- **Why:** this is the industry-default level of assurance (DocuSign's default as well). A one-time code sent to the same inbox adds little. SMS and ID checks add a vendor, cost, and friction that an engagement letter doesn't justify.
- **Revisit when:** a buyer's agreements carry higher risk (for example, financial authority).

### D7: Tamper evidence
- **Options:**
  - (a) SHA-256 hashes, an audit certificate page, and a public verify page
  - (b) a PAdES digital signature on the PDF
- **Decision:** (a).
- **What it covers:** the completed PDF gets an appended certificate page. It lists the document ID, the hash of the original agreement, each signer's events, and the verification method. The final file's hash is stored, and anyone can upload a PDF to the verify page to check it. *(Nov 15: the verify page is cut for schedule. Nov 17: built after all. The browser computes the PDF's SHA-256, so the PDF never leaves the device, and only the hash is looked up.)*
- **Why not PAdES:**
  - With a self-signed certificate, Acrobat shows "signature validity unknown", which looks worse than having no signature.
  - A trusted (AATL) certificate costs money.
  - US ESIGN/UETA does not require one.
- **Revisit when:** selling to EU buyers who need eIDAS advanced or qualified signatures.

### D8: Stack and deployment shape
- **Decision:**
  - Astra's own stack: Python/FastAPI, React/TypeScript, MongoDB, Render.
  - Deployed as **one Docker web service** that serves the API and the built frontend from a single URL. Same origin, so there is no CORS.
  - Files are stored in MongoDB GridFS.
  - The database driver is PyMongo's async API (Motor is deprecated).
- **Why:**
  - It's the stack Astra's team can run and maintain on day one.
  - A single service is the simplest thing that can be reliable on the free tier.
  - GridFS avoids another vendor and another set of keys. The storage interface allows a move to S3 or R2 later.

### D9: Background work and retries
- **Options:**
  - (a) Celery with Redis
  - (b) APScheduler in memory
  - (c) a durable job queue stored in MongoDB
- **Decision:** (c), running inside the web process.
  - Jobs are claimed atomically.
  - Failures retry with exponential backoff.
  - After N attempts a job goes to a dead-letter state that is visible in the UI, with a Retry button.
  - Every handler is idempotent.
  - Every side effect goes through this queue, not through request handlers: emails, webhooks, LLM analysis, finalization, reminders, and expiry.
- **Why:**
  - (a) needs two more services, and the free tier can't run a separate worker.
  - (b) loses work on restart.
  - With (c), jobs carry a due time, so if the server sleeps or restarts, overdue jobs run when it comes back. Work gets delayed but never lost.
- **Revisit when:** volume justifies a separate worker service. The same code can run as one with `RUN_WORKER`.

### D10: Hosting
- **Decision:** Render free tier, with an external uptime monitor pinging `/healthz` every 10 minutes. The ping keeps the service awake, so there are no cold starts and the job worker keeps running. One always-on service fits inside Render's 750 free instance-hours a month.
- **Cost:** 0.1 CPU and 512 MB RAM, so DOCX conversion is slow. It runs one at a time with a timeout, and if it fails the user is told to upload a PDF instead.
- **Production path:** Render Starter ($7/mo) or above.

### D11: Email delivery
- **Options:**
  - (a) a transactional provider on a custom domain
  - (b) Gmail SMTP
  - (c) the Gmail REST API over HTTPS
  - (d) a free relay such as Brevo sending "from" a gmail.com address
- **Decision:** (c). Email is sent from a dedicated Gmail account, behind an `EmailSender` interface.
  - The sender display name is the firm's name.
  - Reply-To is the firm user's own address, so replies reach a person.
  - Bounces are detected by polling the sending inbox for delivery-failure notices and flagging the recipient on the file. *(Nov 15: cut for schedule. A signer who never receives the email is covered by the copy-link fallback and by resending to a corrected address.)*
  - The Google OAuth app is published to "In production", because refresh tokens in "Testing" mode expire after 7 days.
- **Why:**
  - (b) doesn't work: Render's free tier has blocked outbound SMTP ports since September 2025.
  - (d) fails sender authentication (DMARC alignment), so links land in spam.
  - (a) is the right production answer, but it needs a domain, and I chose not to buy one for the take-home.
- **Limits:** about 500 emails a day, and the sender address is a gmail.com account.
- **Revisit when:** onboarding the first paying customer. The interface makes the provider a one-file swap.
- **Observed risk:** the first email from the live server landed in Gmail's spam folder. The sender is a new account, and the links point to a free-hosting subdomain. Because a missed email must not stall a file, the product doesn't depend on email alone:
  - The file page has a **Copy signing link** fallback, recorded in the audit trail as "link shared by sender".
  - After sending, the app tells the sender to check spam if the email hasn't arrived.
  - Signing emails are full transactional messages with a text part and an HTML part, not bare links.

  The real fix is a custom domain for the app and a transactional email provider on that domain.

### D12: Which LLM, and how it's used
- **Decision:**
  - Claude Opus 5.5 (`claude-opus-5-5`), with adaptive thinking and effort set to `high`.
  - Structured outputs against a strict schema.
  - The PDF itself plus the extracted, ID'd lines and blanks as input.
  - Server-side refusal fallbacks enabled.
  - Any failure degrades to heuristics, never to an error screen.
- **Why:**
  - Detection runs **once per agreement**, not once per file. Accuracy is worth far more than the cost, which is well under $1 per agreement.
  - Detection is a single structured call. Nothing needs to act autonomously, so I don't use an agent.
- **Safety:** agreement text is treated as untrusted data. The model can only return labels for positions we extracted, a human reviews everything, and the model has no tools.

### D13: PDF library
- **Options:**
  - (a) PyMuPDF (AGPL)
  - (b) a permissive stack: pypdf, pdfplumber, pypdfium2, and reportlab
- **Decision:** (a), isolated in a single module.
- **Why:** PyMuPDF is the best available at extracting text positions, at *truly* removing placeholder text such as `[Client Name]` before writing the value, and at rendering pages. The permissive stack can only paint over placeholders, so the hidden text stays in the file.
- **Licensing:** the AGPL obligation is met because this repository is public. Before a closed-source commercial sale, either buy Artifex's commercial license or swap in option (b). That swap touches one file.

### D14: Authentication
- **Decision:**
  - My own: email and password (argon2 hashing) with an httpOnly, SameSite session cookie.
  - Every query is scoped to the user's company.
  - Hashed API keys for machine access.
- **Why:** it avoids another vendor, and the requirements are standard.
- **Revisit when:** a buyer requires SSO (see "Left out").

## What I'm including, and why

**The graded path (must work every time):**
1. **Self-serve company signup.** Every company is an isolated workspace, which is the minimum for selling to "every other company".
2. **Agreement upload with AI field detection and a review editor.** You can move, resize, add, or delete fields, set who fills each one, and approve. This is how an unseen agreement becomes usable in minutes (D2).
3. **New file.** The form is generated from the agreement's own per-file blanks, plus a name and email for each signer, then an exact preview and send. The client details go in once, and nobody retypes the agreement. If a signing email is slow or lands in spam, the sender can copy the signing link and share it another way. That action is recorded in the audit trail. *(Nov 17: plus saved clients. Each client is saved when a file is sent, and picking one on New file fills the client signer and the client's identity details.)*
4. **Signer experience.**
   - A branded email.
   - ESIGN consent.
   - Guided steps that take the signer from field to field.
   - A typed or drawn signature, with dates filled in automatically.
   - Decline with a reason.
   - A layout that works on a phone, because clients sign on phones.
5. **Sequential routing.** The client signs first and the firm countersigns, because most agreements need both parties. *(Nov 17: when it's their turn, the firm's own signer can also sign straight from the file page with **Sign now**, as well as from their email.)*
6. **Completion.**
   - The completed PDF has the values flattened in, an audit certificate page, and hashes.
   - It is emailed to every party and stays downloadable.
   - The file is marked **"Signed: ready to start work"**, which is the outcome the client actually asked for.
7. **Dashboard and per-file timeline.** Anyone can see where every file stands, and what happened when.
8. **Durable job queue for every email and LLM call.** Reliability on the graded path (D9).

**What makes it a product a company could buy and run today:**

9. **Automatic reminders** (day 2 and day 5 by default), **expiry** (30 days), **void**, and **resend to a corrected email**. Chasing signatures is the manual work the client is complaining about.
10. **Public REST API with API keys and idempotency keys**, and **signed outbound webhooks** with retries and a delivery log. This is how "every file" becomes truly automatic: the firm's own systems open files and hear back when they're signed (D4). *(Built Nov 17, in a lean form: API keys in Settings; `GET /api/v1/templates`, `POST /api/v1/files` with `Idempotency-Key` and `metadata`, `GET /api/v1/files/{id}` and its signed PDF; and one webhook endpoint per firm, with HMAC-signed events, retries, a delivery log with Retry, and a test event. Auto-send is cut (D5).)*
11. **Monitoring.** Error tracking (Sentry), a health endpoint, structured logs, a failed-jobs page with retry, and bounce detection. You can't run what you can't see. *(Bounce detection is cut; see the Nov 15 cuts below. The single failed-jobs page wasn't built either. Each failure shows where it happened, with its own way out: Try again on an analysis or a signed PDF, Resend or Copy link for an email, Retry for a webhook. The health check counts dead jobs, and UptimeRobot alerts on it.)*
12. **Per-company branding:** logo, colour, and sender name. Clients should see the firm's name, not mine, and the client wants to resell this. *(Logo and colour were cut on Nov 15 and built on Nov 17: Settings, then Branding.)*
13. **Verify page.** Anyone can check that a PDF is the unaltered signed original (D7). *(Cut on Nov 15; built on Nov 17 at `/verify`.)*
14. **Template versioning.** Each file stays pinned to the exact version it was sent with, so editing the agreement never changes what someone already signed. *(The pinning stays. Only the UI for browsing old versions is cut.)*
15. **Detection eval harness.** Synthetic agreements covering:
    - two parties, one party, and side-by-side signature blocks;
    - bracket placeholders, a DOCX, and initials on every page;
    - no signature block, and a scan;
    - drawn signature lines with captions, and label-only blocks with dot leaders;
    - a fillable PDF form.

    It turns "works on agreements it has never seen" from a hope into a measured number. CI runs it offline on every push, inside the production image so the DOCX fixture is included.
16. **An MCP server for AI assistants** *(added Nov 17)*. Claude Desktop, Claude Code or any MCP client can list agreements, prepare a file as a draft, see which files are waiting and on whom, and save a signed PDF. MCP is how AI assistants use outside tools, and Astra's own product (MIND) is built around it. So the firm's assistant can start the agreement on a new client without anyone opening Kickoff, apart from the one step that must stay human: checking the draft and sending it (D5). It sits on the public API (item 10) and adds nothing to the live app. It's a separate package (`mcp-server/`) depending on the official `mcp` Python SDK, so that dependency never enters the backend image.

**If time allows:** team invites, bulk file creation from CSV, in-app countersigning for the logged-in user, and automated browser end-to-end tests. *(All cut on Nov 15; see "If behind schedule" below. Team invites were then built on Nov 17, after the API, webhooks and MCP server were done: anyone in a company invites a colleague by email, they join with their own login, and a removed member's sessions and API keys stop working at once. In-app countersigning was built the same day as **Sign now**: the firm's own signer, logged in, signs from the file page.)*

## What I'm deliberately leaving out, and why

| Left out | Why |
|---|---|
| Third-party e-signature providers | See D1. An adapter would go in `services/signing.py`. |
| Billing and subscriptions (Stripe) | Astra is customer #1 and pricing isn't decided. Stripe Checkout is about a day of work once it is. |
| SSO/SAML and fine-grained roles | Enterprise features with no buyer yet. Every member of a company is an admin. |
| Native CRM or practice-management connectors | The API and webhooks cover them generically, and Zapier or Make can call them today. |
| SMS delivery, one-time codes, ID/KBA checks | See D6. Each adds a vendor and friction out of proportion to an engagement letter. |
| PAdES/qualified signatures, eIDAS | See D7. US ESIGN/UETA is the baseline. Revisit for EU buyers. |
| Editing, redlining, or negotiating the agreement | The client signs "the same agreement" every time. Negotiation is a different product. |
| Multi-document packets, payment collection at signing | One agreement per file (A8). Deposits are tempting but are a separate problem with their own compliance. |
| OCR for scanned agreements | Scans still work, through an appended signature page and manual placement. OCR adds weight and error for a rare case. |
| Text reflow for long values | Required by D3. Values shrink to fit, and the preview warns before sending. |
| Native mobile apps | The signing page is responsive, and signers shouldn't have to install anything. |
| Languages other than English | A4. |
| SOC 2, data residency, custom retention policies | Necessary to sell to larger firms, and listed as the path to production. They aren't buildable in a week. |

**If behind schedule, I cut in this order:** the "if time allows" items, then the verify page, then branding, then bounce detection, then the template-versioning UI. The API and webhooks go last, because they are the automation story. Reliability on the graded path is never cut.

**Cuts applied on Nov 15.** Day 2 finished two days late, so I applied the whole list above except the last item. What that means in the product:

| Cut | What remains |
|---|---|
| Team invites, bulk CSV, in-app countersigning, browser end-to-end tests | Each company had one login until Nov 17, when team invites were built. The firm countersigns from its own signing email, like any signer, or (since Nov 17) with Sign now on the file page. Each step of the graded path gets a manual run on the live app instead of a browser test. |
| Verify page | The certificate page and the stored SHA-256 hashes (D7). *(Built on Nov 17.)* |
| Logo and colour branding | The firm's name, from signup, on emails and the signing page. *(Built on Nov 17.)* |
| Bounce detection | Copy signing link, the check-spam prompt, and resend to a corrected email (D11). |
| Template-versioning UI | Every file keeps a snapshot of the template version it was sent with. Editing an approved template creates a new version that needs approval again. You can't browse old versions. |

The API and webhooks stayed in scope and were built on Tuesday morning, Nov 17, in a lean form (item 10). Auto-send was cut (D5).

## Known limitations and the path to production

- **Email** comes from a gmail.com address, with a limit of about 500 a day (D11). For production, move to a transactional provider on the firm's own domain, with SPF, DKIM, and DMARC.
- **Hosting** is on the free tier: 0.1 CPU and 512 MB, kept awake by an external ping (D10). For production, use Render Starter or above, with the worker split into its own service.
- **PyMuPDF is AGPL** (D13). Buy a commercial license or swap libraries before any closed-source sale.
- **Storage** is GridFS on Atlas M0, which has a 512 MB cap. For production, use S3 or R2 behind the existing storage interface, with Atlas backups.
- **The single process** runs both the API and the worker. Jobs are claimed atomically, so scaling out horizontally is safe once a second instance exists.
- **Fonts in the PDF** are the built-in Helvetica and Times, which only cover Latin-1. Curly quotes, dashes and letters such as "Ł" are written in their plain forms (O'Brien, Lukasz), and other scripts (Chinese, Arabic, Cyrillic) appear as "?". For production, embed a Unicode font such as Noto Sans and measure text with it on the signing page too.
- **The certificate can't contain its own hash.** The signed PDF's SHA-256 is stored with the file, shown on the file page and printed in every delivery email, and the certificate says where to find it. A PAdES signature (D7) would make the PDF self-verifying.
- **The next step for detection is a self-checking setup agent.** After Claude labels the blanks, an agent would fill a test copy of the agreement with sample values, look at the rendered pages, and fix the problems it sees: values printed too small, short blanks it missed, a signer with no signature box. Then it would hand the result to the person to approve, as now. It isn't built yet, for three reasons: it would change the step the graded test depends on, it would cost 2–4 times as much per agreement, and it needs paid eval runs first to show that it helps more than it hurts.

Found in the blind tests (Nov 17); none stops an agreement from being signed:

- **A long value in a short placeholder in mid-sentence still prints small.** The firm's values now run on into empty space to their right, so a date replacing "[Date]" on a line of its own prints at full size. But a value can't push printed text along, so one followed on the same line by words or punctuation ("$[X], of which…", "ended [Date]:") still shrinks to fit (D3), down to 4 pt. The reviewer can widen the field in the editor, or the firm can shorten the value. Signers' values always keep to their box, because the signing page shows them at the size the PDF will use.
- **Two-underscore blanks aren't found.** *(Updated Nov 17: blanks of three or more underscores are now found from 8 pt wide, which covers "___" initials and option marks such as "___ Exclusive / ___ Nonexclusive".)* A run of two underscores, such as "(__) days" echoing a number written out before it, still isn't treated as a blank. The reviewer can add one in the editor.
- **Optional signers belong to the template.** A spouse or representative who signs only on some files has to be removed in the editor. One action removes the role and its fields. Otherwise the firm keeps a second version of the agreement.
- **PDF dropdowns** become free-text inputs on the new-file form, so their option lists aren't offered.
- **Scans** have no text layer, so nothing printed on them can be removed. A value placed over printed hint text in a box is written on top of it.
- **The API and webhooks are lean.**
  - One webhook endpoint per firm.
  - A webhook carries the file as it is when the delivery is made, not when the event happened. `created_at` gives the event's time.
  - The endpoint's host is checked against private addresses when it's saved and before each delivery. The request then resolves it again, which leaves a small DNS-rebinding window. For production, pin the checked address for the connection.
  - An API key acts for the person who created it, and there's no rate limit per key.
  - An event that's recorded just before the process dies may never be announced (the same gap as the next signer's email, Day 4).
- **The MCP server runs locally** (stdio). Each user installs it with `uvx` and their API key. For production, also serve it as a remote MCP endpoint on the app, so web-based assistants can connect by URL with OAuth instead of a pasted key.
- **Role names vary between runs.** On one run of the same agreement, Claude named the roles after their placeholders ("[Client Name]") instead of "Client". The reviewer can rename a role in the editor.

## AI tools and services used

- **Claude Code** (model: Claude Opus 5.5) for planning, writing code, tests, and documentation, and for code review.
- **Claude API** (`claude-opus-5-5`) inside the product, for agreement field detection.
- **Services:** Render (hosting), MongoDB Atlas (database), Gmail API (email), Sentry (error tracking), UptimeRobot (uptime and keep-awake), GitHub.
- **Key libraries:** FastAPI, Pydantic, PyMongo, uvicorn, httpx, argon2 (passwords), PyMuPDF, LibreOffice (DOCX conversion), React, TypeScript, Vite, Tailwind, TanStack Query, React Router, signature_pad.
- **Build and checks:** Docker, uv and npm; pytest, ruff, mypy and oxlint.
- **Playwright**, driving Chrome for the end-to-end checks and blind tests (testing only, not part of the product).
- **The official MCP Python SDK** for the Kickoff MCP server (item 16).

## Change log

| Date | Change |
|---|---|
| 2026-11-11 | Initial decisions written before implementation. Hosting set to Render free tier and email to the Gmail API, after confirming that Render's free tier blocks SMTP. |
| 2026-11-11 | Pinned PyMuPDF to 1.x. Version 2.x hard-requires layout/LLM add-ons (onnxruntime, numpy, networkx), which added about 250 MB to the image for nothing we use. The image dropped from 2.3 GB to 1.0 GB. |
| 2026-11-11 | CI runs MongoDB 7.0 while production runs Atlas 8.0. GitHub's runners moved to Linux 6.19+ kernels, and MongoDB 8.0+ refuses to start on them (an upstream allocator issue, SERVER-121912, still open as SERVER-136848). We use no 8.0-only features, and local dev and Atlas still exercise 8.0. Revisit when the upstream fix ships. |
| 2026-11-11 | First deploy is live, and email from production is verified, so Day 1 is done. The delivery test landed in spam, so I added a copy-link fallback and a check-spam prompt to the graded path (D11). |
| 2026-11-14 | The document pipeline is built (D2), so Day 2 is done, two days late. Extraction covers more kinds of blank than planned: drawn lines, dotted blanks, label gaps, wrapped placeholders and PDF form fields. Each is common in Word and PDF exports, and missing one means a missing field. I added a `date` field type for sender-filled dates. The eval grew to 10 fixtures, and the heuristics alone score 98.5% on them offline. |
| 2026-11-15 | First Claude eval: 68/68 blanks and signature blocks on 10/10 fixtures, at about 4–6¢ and about 10 s per agreement. That's well under the "under $1" budget in D12. |
| 2026-11-15 | I ran a blind test on a public template, the Common Paper Professional Services Agreement cover page. It found both signature blocks correctly, but exposed extraction gaps: `[XX]` placeholders, `[ ]` and `( )` option boxes, long instruction placeholders, and table rules taken for blanks. Those are fixed, with a new fixture and tests. Cost and latency grow with the number of blanks, at roughly 1 s and 0.25¢ each, so a 150-blank template takes about 2.5 minutes and about 40¢. That's still under D12's "under $1". The LLM timeout is now 300 s, and the analyze job must hold its lease longer than that. |
| 2026-11-15 | I ran two more blind tests, judged on the first run. On a State Bar of California fee agreement (6 pages, PDF), every blank was found and its unusual signature layout came out right. On the Bonterms PSA cover page (DOCX), the signature blocks were right but it missed company, email and postal blanks that have only empty space after the label. Fixes: the label-gap rule now covers party-detail labels, and an instruction bracket next to a blank is removed when the blank is filled. I also added stamping, which writes values into the PDF (`pdf_engine.stamp`; values shrink to fit per D3, and placeholder text is removed). It was planned for finalization, but I pulled it forward so a full preview could be shown with `scripts/demo_sign.py`. |
| 2026-11-15 | Applied the "If behind schedule" cuts, because Day 2 finished two days late. Cut: the "if time allows" items, the verify page, logo and colour branding, bounce detection, and the template-versioning UI. Hashes, the certificate page, per-file version snapshots, copy-link, and resend all stay. The API and webhooks stay in scope, scheduled last. Details are in the table under "If behind schedule". |
| 2026-11-15 | Day 3 (templates) is done and verified on the live app: signup, upload, AI analysis, review, edit and approve. Choices made along the way: sessions are stored server-side, so logout really ends them, and the database holds only an HMAC of each token keyed with `SESSION_SECRET` (D14). DOCX conversion runs during the upload request rather than in the job, so an unusable file gets its error and next step immediately; it takes about 10 s on the live instance. Analysis runs in a job with a 10-minute lease, and the worker hands interrupted jobs back on shutdown, so a deploy doesn't strand one. Editing an approved template creates a new version that needs approval again. The model's notes to the reviewer have internal slot ids rewritten as "a blank on page N". |
| 2026-11-15 | Day 4 (files and signing) is done and verified on the live app, one day ahead of the revised schedule. Choices made along the way. A file's recipients are stored inside the file, so a signature, the file's new status and the next signer's turn are one atomic update. Signing emails don't go through the generic email job: their own job mints the signing token as it sends, so a usable link is never stored anywhere, only its SHA-256 (D6). Copy signing link mints another; correcting an email retires the old ones. Reminders (day 2 and day 5) and expiry (day 30) are jobs due at those times, scheduled when a signer's turn starts, and each checks the signer still hasn't signed before sending. Empty blanks don't block sending: the preview lists them and the person confirming decides, because detection marks many optional rows as required. A signer's checkboxes are never required, because option groups expect some boxes left empty. "Date signed" uses the signer's own time zone. Signing links are removed from logs and error reports. |
| 2026-11-15 | Day 5 (finalize) is done and verified on the live app, a day ahead of the revised schedule. When the last signer signs, a job stamps every value on the PDF the sender previewed and sent, bakes any leftover form fields into the page so nothing can be filled in afterwards, and appends an audit certificate. The certificate has the document details; SHA-256 hashes of the upload, of the approved agreement and of the PDF as sent; each signer's viewed, consented and signed times, IP, browser and signature method; the full audit trail; and how to check a copy. One conditional update stores the result, so a rerun keeps the first PDF and the signed artifact never changes. Every signer and the sender then get it by email, one deduplicated job per address. The sender can download it from the file page and each signer from their link. If making it fails, the file page says so and offers Try again. A signed copy that can't be emailed shows on the timeline, with the download as the way out. Found while building it: the built-in PDF fonts draw anything outside Latin-1 as a dot, so a phone's curly apostrophe (O’Brien) was stamped as "O·Brien". Values are now written in their plain forms (see Known limitations). On the live app, the two completed files that were waiting (one signed end to end by the owner with real emails) were finalized within seconds of the deploy, and a new file on an approved agreement had its signed PDF 2 seconds after the last signature. The client's signed copy reached an outside Gmail inbox (Inbox, not spam), and the SHA-256 in the email matched the one on the file page. |
| 2026-11-17 | I ran a blind-test round on six public agreements I hadn't seen, each judged on its first run on the live app: upload, review, file, send, the client signing on a phone, the firm on a desktop, then the signed PDF and certificate. All six reached a signed PDF with the right signers in the right order, and none needed more than 5 reviewer edits. Journal of Accountancy 1040 letter (Word): the .doc was refused with "save it as .docx or PDF"; the .docx got 28/28 blanks and 3 signers, plus a signature page for the firm, which has no line of its own; 0 edits. CPAi tax letter (10-page PDF): 30/30 blanks; client, spouse and firm; 0 edits. WCG 1040 form (6-page PDF): 20/20 blanks, two client signers, and the firm correctly left as a non-signer; 1 edit, removing the optional representative. PlatoForms consulting agreement (fillable PDF with 23 form fields, including dropdowns and signature boxes): 23/23, 0 edits. AIGA design services agreement (28 pages): both signature blocks and every core term found in 46 s, but not the very short "___" blanks for initials and option marks; 0 edits for the base agreement. A scan of the consulting agreement: the signers were read from the page images, a signature page was added, and 5 fields were placed by hand in the editor. Analysis took 9–46 s for the PDFs and 104 s for the Word file. Each signed PDF was ready 2–9 s after the last signature, and no email failed. Two blockers, both on the CPAi letter, are fixed, with tests. Filling a placeholder also removed words on the lines above and below it (26 words, such as "professional" under "[CPA Firm]"), so the signed agreement's wording changed: in tightly set text, the extraction boxes overlap the next line, and redaction removes every glyph it touches. "$[X]" for the fee was read as a ticked box, so the fee was written over the printed "[X]". A rerun of the CPAi letter on the live app lost no words and removed every "[X]". The other findings are under Known limitations. |
| 2026-11-17 | Polish after the blind tests. A long value the firm fills in now runs on into the empty space to its right instead of shrinking, up to the next word, form field or value on its line. A date replacing "[Date]" was printing at 4 pt in three of the six agreements. Only the firm's values do this, because a signer's are drawn on the signing page at the size the PDF will use. The added signature page writes curly quotes and apostrophes in plain form, so a title no longer appears as "?[Sample Letter]?". The logged-out landing page no longer shows the developer "System status" box. `/api/health` stays for monitoring. |
| 2026-11-17 | Built the public API and webhooks (item 10), in a lean form, before the 12:00 freeze. Settings has API keys, shown once and stored as SHA-256 like signing tokens, and the webhook endpoint with its signing secret, a test event and a delivery log with Retry. `/api/v1` lists approved agreements with their roles and fields, opens a file, reads it and downloads its signed PDF. `Idempotency-Key` makes retries safe, and `metadata` is echoed in every webhook. A file opened through the API is a draft that a person checks and sends (D5), so I cut auto-send instead of adding an "awaiting approval" status. Webhooks are queued from the audit events (sent, each signature, declined, voided, expired, and file.completed once the signed PDF exists). Each is signed `t=<unix>,v1=<HMAC-SHA256 of "t.body">` and retried 8 times over about an hour and a half. They're logged with the jobs, not the events, so they never appear in a signed certificate. Endpoints must be public https addresses: private, loopback, link-local and cloud-metadata addresses are refused, and redirects aren't followed. Also refined the Nov 17 small-text fix: a value shrinks within its box first, down to 8 pt, and runs on to the right only below that, so a date on a blank line stays on the line. |
| 2026-11-17 | A second blind-test round on three public software-subscription agreements, the kind MIND (Astra AI's product) would likely use, each judged on its first run on the live app. Common Paper Cloud Service Agreement with the AI terms option (Word, 13 pages, about 170 fields including the training-data and AI acceptable-use options): Customer then Provider, both signature tables mapped cell by cell, one placeholder missed ("[fill in state, province, and/or county]" for the chosen courts, which the AI flagged in its notes), 112 s including the Word conversion. Common Paper Order Form (Word, 3 pages): every blank found, 52 s. A fillable SaaS subscription agreement with a 4-row pricing table, plan dropdowns and signature boxes (PDF, 43 form fields): all 43 mapped, the client's data-processing checkbox left for the client, 33 s. All three reached a signed PDF with 0 reviewer edits, no words were lost around 132 filled placeholders, and no email failed. No blockers, so no fixes. |
| 2026-11-17 | Added the Kickoff MCP server (item 16), plus `GET /api/v1/files` with a status filter so an assistant can answer "what's still waiting?". The server is a separate package over the public API, with five tools: list_agreements, open_file (a draft only), list_files, get_file, and download_signed_agreement (checks the SHA-256 on record). Errors come back as Kickoff's own sentences, a `reference` makes retries safe through the Idempotency-Key, and it installs with `uvx` straight from the repository. Verified with a real MCP client over stdio against the live app, with the server installed from GitHub exactly as the README says: a draft was prepared (a repeat returned the same file), sent by a person from the app, signed by both parties, listed as waiting and then completed, and the signed PDF was saved with a matching SHA-256. |
| 2026-11-17 | Built team members, cut on Nov 15, because the API, webhooks, MCP server and second blind-test round were done well before the feature freeze. Anyone in a company can invite a colleague by email from Settings. The invitation works like a signing link: its token is minted in the email job (or by Copy invite link) and only its SHA-256 is stored. It expires after 7 days and can be revoked. Joining creates the person's own login in the inviting company, bound to the invited email, and marks the invitation used in one conditional update, so a link works once. Files they send go out in their name and with their Reply-To. Removing someone deletes their login, ends their sessions and revokes the API keys they made. You can't remove yourself. Every member is still an admin (roles stay out). Tested for joining, reuse, expiry, revocation, removal, other companies and token storage, and checked in Chrome with the invitee joining on a phone viewport. |
| 2026-11-17 | Built Sign now (in-app countersigning, cut on Nov 15). When it's the logged-in person's turn to sign (their email is the signer's), the file page and the Files list say "Waiting for you", and Sign now opens their signing page directly instead of through their email. It's only for that person, so the certificate's record of who signed stays true, and it says the page was opened from the file while logged in. Nothing was removed: the "it's your turn" email still goes out, and its link still works. Tested for the wrong person, the wrong turn and another company, and checked in Chrome locally and on the live app, where the certificate records it. |
| 2026-11-17 | Built saved clients, an address book that fills itself in. When a file is sent, the client signer's name and email and the values of the agreement's client identity fields (company, contact name, email, phone, address, title) are saved for the company, one entry per client email; sending again updates it, and a detail an agreement doesn't ask for is kept, not erased. On New file, an optional **Client** search at the top fills the client signer and those fields. It's hidden until the company has sent a file, so a first-time user sees the form exactly as before. Which fields match is decided by plain keyword rules on each label, not by AI, so the same label always gets the same answer: dates, fees, descriptions, deliverables and other job details are never filled, and neither is anything mentioning the consultant, provider, firm, vendor, designer or another signer's role name. On the agreement a client was last sent, the exact values come back key for key; on a different agreement, fields are matched by category. Saving happens after the send's atomic update and can't make a send fail. Tested for matching across two agreements, firm-side fields, job fields, updates, and other companies (404), and checked in Chrome on a desktop and a phone viewport. Later the same day, a Saved clients page (linked from Settings and from the Client box) was added to correct or delete an entry. Correcting one drops the values saved key for key, so the next file fills from the corrected details everywhere. |
| 2026-11-17 | Built the verify page (D7, item 13), cut on Nov 15. Anyone opens `/verify` without logging in and chooses a signed PDF. The browser computes its SHA-256 with Web Crypto, so the PDF never leaves the device, and asks `GET /api/verify/{sha256}`. That's a lookup by the signed PDF's stored hash across every company, the fourth lookup not scoped by company: only someone holding that exact PDF knows the hash, and the answer shows only what the PDF itself shows (the agreement, the file title, the firm, the completion date and the signers' names, never their emails). Anything else, including a copy with one byte changed or the PDF as it was sent before signing, gets "Not a match: this copy was changed, or wasn't signed with Kickoff." The file page and new delivery emails link to it. Already-signed PDFs and their certificates are unchanged (signed artifacts are immutable). |
| 2026-11-17 | Built the firm's logo and colour (item 12), cut on Nov 15. In Settings, then Branding, a firm uploads a PNG or JPG of at most 512 KB, recognised by its first bytes whatever its name or claimed type, and stored in GridFS. It's served publicly at a URL containing its own id, so a new logo gets a new URL, the old one stops working, and each can be cached for a year. The firm can also choose a colour, as a validated hex value. Text on the colour is white, near-black or black, whichever first reaches 4.5:1 contrast (one always does), and links on white fall back to near-black when the colour is too light to read there. Signing emails (invitation, reminder, resend) and the signed copy sent to each signer show the logo at the top and the button in the colour. The signing page shows the logo in its header and its buttons in the colour. "Secured by Kickoff" stays, and without branding every email and page is byte-for-byte as before (tested). Emails to the firm itself are unchanged. |
| 2026-11-17 | Very short blanks are now found. An underscore run counted as a blank only from 15 pt wide, so three or four underscores in small type, used for initials and option marks such as "___ Exclusive / ___ Nonexclusive", were missed: about 20 on the 28-page AIGA design agreement in the blind test. Runs of three or more underscores now count from 8 pt. Dot runs keep 15 pt, because short ones are ellipses far more often than blanks, and two underscores still don't count. A new eval fixture (`short_blanks`: option marks, a rate and both parties' initials in 9 pt Times, each 13.5 pt wide) found none of its 5 short blanks with the old rule and all 5 with the new one. The offline eval was unchanged on the existing fixtures (71/72 without the Word fixture) with 0 false positives. A free sweep of the blind-test PDFs found 21 new blanks, all real and all in the AIGA agreement, and nothing new in the other 9. With the owner's approval, the online eval with Claude (about 48¢) scored 82/82 blanks and 11/11 signature blocks with 0 false positives, the same 100% as the Nov 15 baseline; Claude read the option marks as checkboxes and gave each party its own initials. On two fixtures with no blanks at all (a scan and an agreement with no signature block), Claude also listed the provider named in the text as a signer, which the Nov 15 run didn't. Extraction on those fixtures is identical, so that's the model's own variation, and on the scan it's arguably right. The reviewer can remove a role in the editor. Re-run on the live app with Claude (approved by the owner), the AIGA agreement now had all 21 of its short blanks, each labelled sensibly, in 48 s: the percentages as values the firm fills in, the supplement initials split between client and designer, and the media and exclusive/nonexclusive options as checkboxes. |
| 2026-11-17 | Polished the logged-out landing page and gave Kickoff a logo mark, after the owner judged the page too bare. The mark is a white page with a folded corner and a check on the product blue, used on the landing, login and verify pages, in the app's top bar (the mark alone on a phone, where that bar is tight) and as the browser-tab icon. The signing page header stays the firm's own space. Beside the headline is a drawing of the product built in HTML and SVG, not a screenshot: an agreement with the client's details filled in, both parties' signatures, "Signed: ready to start work" and an audit-certificate tag, using test names only. A raw screenshot of the live app looked unfinished. Under the buttons, three lines say what makes it trustworthy (the audit certificate and SHA-256, ESIGN/UETA consent, a person approving the AI), and a footer links to the verify page, the API docs and GitHub. Sign-up and the rest of the graded path didn't change. |
