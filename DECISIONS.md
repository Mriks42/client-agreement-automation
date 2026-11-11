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
  - (c) native signing behind a `SignatureProvider` interface
- **Decision:** (c).
- **Why:**
  - The client's product is *getting the agreement signed on every file*. Capturing a signature is the easy, commodity part.
  - Provider sandboxes mark documents as test or not legally binding, which is the wrong first impression for a grader.
  - Per-envelope or per-seat provider fees come straight out of the margin when the client resells.
  - Providers still need to be told where the fields go, so the hard part (detection) has to be built either way.
  - Self-hosting means running and patching a second heavy service on a 512 MB instance, and it reduces my own work to glue.
  - Astra already runs its own e-signature tool (MIND Sign), so building in-house is clearly acceptable to them. The interface is where a MIND Sign or DocuSign adapter would go.
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
  1. **Normalize.** Reject encrypted PDFs. Convert DOCX to PDF.
  2. **Extract.** Pull every text line and every candidate blank from the PDF, each with its real coordinates and a stable ID. Candidate blanks are underscore runs, `[Bracketed]` or `{{curly}}` placeholders, and lines such as "Signature:" or "By:".
  3. **Classify.** Claude reads the page images and the ID'd lines and blanks. It returns the signing parties and, for each blank, what the blank is and who fills it (the firm per file, or a specific signer). **The model never outputs coordinates.** It labels positions we measured, so it cannot invent a field that isn't on the page.
  4. **Validate.** Check the output with a Pydantic schema and retry once with the errors fed back. Then run sanity checks: fields sit inside the page, fields don't overlap, and every signer role has a signature field.
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
  - **Files created through the API** wait in "awaiting approval" by default. A company can switch on auto-send once it trusts its template.
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
- **What it covers:** the completed PDF gets an appended certificate page. It lists the document ID, the hash of the original agreement, each signer's events, and the verification method. The final file's hash is stored, and anyone can upload a PDF to the verify page to check it.
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
  - Bounces are detected by polling the sending inbox for delivery-failure notices and flagging the recipient on the file.
  - The Google OAuth app is published to "In production", because refresh tokens in "Testing" mode expire after 7 days.
- **Why:**
  - (b) doesn't work: Render's free tier has blocked outbound SMTP ports since September 2025.
  - (d) fails sender authentication (DMARC alignment), so links land in spam.
  - (a) is the right production answer, but it needs a domain, and I chose not to buy one for the take-home.
- **Limits:** about 500 emails a day, and the sender address is a gmail.com account.
- **Revisit when:** onboarding the first paying customer. The interface makes the provider a one-file swap.

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
3. **New file.** The form is generated from the agreement's own per-file blanks, plus a name and email for each signer, then an exact preview and send. The client details go in once, and nobody retypes the agreement.
4. **Signer experience.**
   - A branded email.
   - ESIGN consent.
   - Guided steps that take the signer from field to field.
   - A typed or drawn signature, with dates filled in automatically.
   - Decline with a reason.
   - A layout that works on a phone, because clients sign on phones.
5. **Sequential routing.** The client signs first and the firm countersigns, because most agreements need both parties.
6. **Completion.**
   - The completed PDF has the values flattened in, an audit certificate page, and hashes.
   - It is emailed to every party and stays downloadable.
   - The file is marked **"Signed: ready to start work"**, which is the outcome the client actually asked for.
7. **Dashboard and per-file timeline.** Anyone can see where every file stands, and what happened when.
8. **Durable job queue for every email and LLM call.** Reliability on the graded path (D9).

**What makes it a product a company could buy and run today:**

9. **Automatic reminders** (day 2 and day 5 by default), **expiry** (30 days), **void**, and **resend to a corrected email**. Chasing signatures is the manual work the client is complaining about.
10. **Public REST API with API keys and idempotency keys**, and **signed outbound webhooks** with retries and a delivery log. This is how "every file" becomes truly automatic: the firm's own systems open files and hear back when they're signed (D4).
11. **Monitoring.** Error tracking (Sentry), a health endpoint, structured logs, a failed-jobs page with retry, and bounce detection. You can't run what you can't see.
12. **Per-company branding:** logo, colour, and sender name. Clients should see the firm's name, not mine, and the client wants to resell this.
13. **Verify page.** Anyone can check that a PDF is the unaltered signed original (D7).
14. **Template versioning.** Each file stays pinned to the exact version it was sent with, so editing the agreement never changes what someone already signed.
15. **Detection eval harness.** Synthetic agreements covering two parties, one party, side-by-side signature blocks, bracket placeholders, a DOCX, initials on every page, no signature block, and a scan. It turns "works on agreements it has never seen" from a hope into a measured number.

**If time allows:** team invites, bulk file creation from CSV, in-app countersigning for the logged-in user, and automated browser end-to-end tests.

## What I'm deliberately leaving out, and why

| Left out | Why |
|---|---|
| Third-party e-signature providers | See D1. The `SignatureProvider` interface is the hook for later. |
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

## Known limitations and the path to production

- **Email** comes from a gmail.com address, with a limit of about 500 a day (D11). For production, move to a transactional provider on the firm's own domain, with SPF, DKIM, and DMARC.
- **Hosting** is on the free tier: 0.1 CPU and 512 MB, kept awake by an external ping (D10). For production, use Render Starter or above, with the worker split into its own service.
- **PyMuPDF is AGPL** (D13). Buy a commercial license or swap libraries before any closed-source sale.
- **Storage** is GridFS on Atlas M0, which has a 512 MB cap. For production, use S3 or R2 behind the existing storage interface, with Atlas backups.
- **The single process** runs both the API and the worker. Jobs are claimed atomically, so scaling out horizontally is safe once a second instance exists.

## AI tools and services used

- **Claude Code** (model: Claude Opus 5.5) for planning, writing code, tests, and documentation, and for code review.
- **Claude API** (`claude-opus-5-5`) inside the product, for agreement field detection.
- **Services:** Render (hosting), MongoDB Atlas (database), Gmail API (email), Sentry (error tracking), UptimeRobot (uptime and keep-awake), GitHub.
- **Key libraries:** FastAPI, Pydantic, PyMongo, PyMuPDF, LibreOffice (DOCX conversion), React, Vite, Tailwind, TanStack Query, signature_pad.
- *Add any other assistants or tools used before submitting.*

## Change log

| Date | Change |
|---|---|
| 2026-11-11 | Initial decisions written before implementation. Hosting set to Render free tier and email to the Gmail API, after confirming that Render's free tier blocks SMTP. |
| 2026-11-11 | Pinned PyMuPDF to 1.x. Version 2.x hard-requires layout/LLM add-ons (onnxruntime, numpy, networkx), which added about 250 MB to the image for nothing we use. The image dropped from 2.3 GB to 1.0 GB. |
| 2026-11-11 | CI runs MongoDB 7.0 while production runs Atlas 8.0. GitHub's runners moved to Linux 6.19+ kernels, and MongoDB 8.0+ refuses to start on them (an upstream allocator issue, SERVER-121912, still open as SERVER-136848). We use no 8.0-only features, and local dev and Atlas still exercise 8.0. Revisit when the upstream fix ships. |
