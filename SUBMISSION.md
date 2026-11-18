# Kickoff: submission

- **Submission URL:** https://kickoff-r713.onrender.com
- **GitHub:** https://github.com/Mriks42/client-agreement-automation (public)
- **The full reasoning:** [DECISIONS.md](DECISIONS.md). **How it works:** [README.md](README.md)

## What I built

**Kickoff gets a firm's standard client agreement signed on every file.** The firm uploads its agreement once. AI proposes where every blank and signature goes and who fills each one, and a person checks and approves it. From then on, opening a file for a new client takes about a minute:
1. Fill in that client's details.
2. Look over the exact PDF.
3. Press send.

Kickoff then emails each signer in order, chases them (day 2 and day 5), and collects their signatures on a phone or computer. It produces a signed PDF with an audit certificate and SHA-256 fingerprints, emails it to everyone, and marks the file **"Signed: ready to start work"**.

**The hard part is an agreement it has never seen,** because that's how it's graded. Kickoff finds every place someone could write: underlines, `[brackets]`, empty boxes, form fields, and gaps after labels like "Date:". Claude labels each of those positions: what it is, who fills it, and who signs in what order. The model only labels positions Kickoff measured, so it can't invent a field, and nothing is used until a person approves it. If the AI fails, keyword rules take over. If a signer has nowhere to sign, a signature page is added. A scan gets a signature page plus manual placement in the editor.

**I measured it instead of hoping.** Kickoff was tested on twelve public agreements it had never seen, each judged on its first run. Nine went through the whole live app, from upload to signed PDF: accounting engagement letters (Word and PDF), consulting agreements, a 28-page design agreement, a fillable PDF form, a scan, and three software-subscription agreements like the one MIND would use. Every one of the nine reached a signed PDF with the right signers in the right order and at most 5 reviewer edits; most needed none. The first three (two services agreements and a legal fee agreement) tested detection earlier in the week, and the signers and order were right each time. The blockers they exposed were fixed, each with a test. The worst was that filling a placeholder in tightly set text deleted words on the neighbouring lines. The results are in the DECISIONS.md change log.

**It's built to be sold, not just demoed.** Any firm can sign up. Each firm's data is kept separate, and emails and the signing page show the firm's name, logo and colour. A firm's own systems can open files through an API and hear back through signed webhooks, and an AI assistant can do the same through an MCP server.

**For Astra and MIND:**
- Upload the MIND client agreement once.
- When a business customer signs up, open a file from the dashboard. Or MIND's backend opens it through the API, or an assistant does through MCP ("send our agreement to Jane at Acme").
- A person checks and sends it, the customer signs on their phone, Astra countersigns, and the `file.completed` webhook can switch on the customer's account.

## Where AI is used, and why not an agent

Claude is used in one place, once per agreement. When the firm uploads it, Claude labels each blank (what it is and who fills it) and the signers and their order, and a person checks and approves the result. Everything after that (sending, the signing order, reminders, the signed PDF and its certificate) is plain, predictable software, because a legal signing process must behave the same way every time and leave a record of exactly what happened. Agents can still drive Kickoff from outside, through the MCP server, but they can only prepare drafts. A person sends.

## Try it (about 5 minutes)

1. Open the submission URL and create a company account (use any company name).
2. Upload an agreement (PDF or Word). Analysis takes from about 10 seconds to 2 minutes.
3. Check the coloured boxes, then press **Approve**.
4. Go to **Files**, then **New file**. Enter the signers (Gmail plus-addresses such as `you+client@gmail.com` bring every email to one inbox) and the client's details. Then **Preview the agreement**, then **Confirm and send**.
5. Sign as the client from the email, ideally on a phone. Then countersign as the firm: press **Sign now** on the file page (when the firm's signer is you), or use the firm's email. The first email can land in Spam. **Copy signing link** on the file page always works too.
6. The file page then offers **Download signed agreement**. Anyone can check that copy at **/verify**.
7. Optional: in **Settings**, add your logo and colour, then send another file and see them on the email and the signing page. On that next **New file**, the **Client** box fills in the client you just sent to.

## Features I included, and why

| Feature | Why |
|---|---|
| **AI field detection with a one-time human review** (an editor to move, add or reassign fields) | Works on an agreement nobody has seen. A wrong signature field is worse than none, so a person approves once per agreement, not once per file. |
| **New-file form built from the agreement's own blanks**, then an exact preview and Confirm | Client details are typed once, and nothing goes out unseen. |
| **Saved clients**: every client you send to is saved, and picking one on New file fills their signer, company, address and contact details. They can be corrected or deleted on a Saved clients page | A repeat client's next file takes seconds, even on a different agreement. Dates, fees and the firm's own details are never filled from an old file, so nothing stale goes out. Plain keyword rules decide which fields match, not AI, so the same label always gets the same answer. |
| **Mobile-first signing**: consent to sign electronically, a guided "Next" through each field, a typed or drawn signature, automatic dates, decline with a reason | Clients sign on phones. The consent and the audit data follow US ESIGN/UETA. |
| **Signing in order** (client first, then the firm countersigns), with **Sign now** for the firm's own signer on the file page | Most client agreements need both parties, and the firm signs last, usually while already working in Kickoff. |
| **Reminders, 30-day expiry, resend, correct an email, copy link, void** | Chasing signatures is the manual work the client complained about. Copy link is the way out when an email lands in spam. |
| **Signed PDF with an audit certificate and SHA-256 fingerprints**, emailed to everyone | Proof of who signed, when, from where, and that the document hasn't changed since. |
| **A public verify page**: anyone drops in a signed PDF and sees whether it's the unchanged original | A client, auditor or court can check a copy in seconds without asking the firm. The browser computes the fingerprint, so the PDF never leaves their device, and the page shows only what the PDF itself shows. |
| **"Signed: ready to start work"** on each file, and a dashboard of every file with a summary (waiting on others, waiting on you, signed this week, average time to sign) and search | That's the outcome the client asked for: knowing when work can start. A firm with many files sees what needs it first. |
| **Self-serve signup with each company's data kept separate**; emails in the firm's name | "Sell it to every other company in our industry." |
| **The firm's logo and colour** on signing emails and the signing page (Settings, then Branding) | A client signs with a firm they recognise, not with an unfamiliar tool, which matters for a product Astra will resell. Text on the colour is always readable, and "Secured by Kickoff" stays. |
| **Team members**: invite colleagues by email, each with their own login, name and password (changed in Settings); remove them in one click | Any firm has more than one person. Each file shows who sent it, and a person who leaves loses access at once. |
| **Public API with API keys and idempotency, plus signed webhooks** with retries and a delivery log | "Every file" becomes automatic: the firm's CRM opens files and hears when they're signed. |
| **MCP server** for Claude and other AI assistants | Astra's world is AI assistants using tools through MCP. An assistant can prepare drafts, track signatures and fetch signed copies, but a person still sends. |
| **A durable job queue for everything slow or external**; every failure shown with a next step; monitoring | A slow AI or a hiccup at Gmail never breaks a click. Nothing fails silently. |
| **A detection eval and blind tests** | It turns "works on unseen agreements" into a measured result, and catches regressions. |

## Features I left out, and why

| Left out | Why |
|---|---|
| DocuSign or another e-signature provider | The product is *getting the agreement signed on every file*. Capturing a signature is the easy part. Provider fees would cut into Astra's resale margin, and the provider would still need to be told where the fields go. |
| Sending without a person, for files opened by the API or MCP | A person confirms every send, by design: AI output and automated triggers prepare, people approve. |
| Bounce detection | Copy signing link, the check-spam hint and Resend cover a lost email. |
| Billing, SSO and fine-grained roles | No pricing has been decided, and no buyer needs them yet. Every team member is an admin. |
| Native CRM connectors (HubSpot, Clio, ...) | The API, webhooks and MCP connect to any system, and Zapier or Make can use them today. |
| SMS codes and ID checks | Out of proportion for a client agreement. An emailed link with consent and an audit trail is the industry default. |
| Qualified (PAdES/eIDAS) signatures | US ESIGN/UETA is the baseline. Revisit for EU buyers. |
| OCR, rewriting the document, languages other than English | The client must sign exactly the document the firm uploaded. Scans still work through manual placement. |

## Assumptions where the brief was open

- "Our industry" means professional-services firms that open files: consultancies, agencies, legal and accounting firms. Nothing is specific to one of them.
- One standard agreement per company, uploaded as a PDF or Word file, in English.
- The firm opens the file, not the client. The signers are read from each agreement, usually the client and then the firm.
- Email delivers the signing links.

## Known limitations

- There's no "Forgot password?" yet. A colleague can remove and re-invite someone who forgets theirs; the next step is an emailed reset link.
- A long value in a short placeholder in mid-sentence (like `$[X], of which`) still shrinks to fit, because the document isn't re-typeset. On a line of its own, it runs on into the empty space.
- A two-underscore blank, like the `(__)` in "thirty (__) days", isn't found and is added in the editor. Three or more underscores are found, even in small type.
- Email comes from a dedicated Gmail account on the free tier. Production would use a transactional email provider on the firm's own domain.

The rest, with what production would need, is in DECISIONS.md under "Known limitations".

## Tools used

- **Claude Code** (Claude Opus 5.5): planning, writing the code, tests and documentation, code review, and running the blind tests.
- **Claude API** (`claude-opus-5-5`) inside the product, to label each blank in an agreement.
- **Playwright**, driving Chrome for the end-to-end checks (testing only, not part of the product).
- **Services:** Render (hosting), MongoDB Atlas (database and files), Gmail API (email), Sentry (errors), UptimeRobot (uptime), GitHub Actions (CI).
- **Main libraries:** FastAPI, Pydantic, PyMongo, uvicorn, httpx, argon2 (passwords), PyMuPDF, LibreOffice (Word conversion), React, TypeScript, Vite, Tailwind, TanStack Query, React Router, signature_pad, and the official MCP Python SDK.
- **Build and checks:** Docker, uv and npm; pytest, ruff, mypy and oxlint.

All test data is fake (Acme Test Co, Jane Client, plus-addresses of a dedicated test inbox).
