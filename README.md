# Kickoff: get your client agreement signed on every file

A firm uploads its standard client agreement once. AI finds where every blank and signature goes, and a person confirms it. From then on, every new file sends a pre-filled agreement to the right people, chases them until they sign, and returns a signed, tamper-evident PDF. It then tells the firm's other systems that work can start.

- **Live app:** https://kickoff-r713.onrender.com
- **Why it's built this way:** [DECISIONS.md](DECISIONS.md)
- **Working on the code:** [CLAUDE.md](CLAUDE.md)

## What it does

1. **Upload your agreement once,** as a PDF or Word (.docx) file. Claude proposes every blank and signature line, and who fills each one: the firm, or which signer. A person checks the fields in the editor, adjusts them if needed, and approves. A scanned agreement gets a signature page, and fields can be placed by hand.
2. **Open a file for each client.** Fill in that client's details, look over the exact PDF, and send it.
3. **Signers sign in order,** from an emailed link, on a phone or a computer. They agree to sign electronically, fill in their parts, and type or draw a signature. Reminders go out on day 2 and day 5, and links expire after 30 days. The firm can copy a signing link, resend, correct an email address, or void the file.
4. **Everyone gets the signed PDF** with an audit certificate showing who signed, when, from where, and the SHA-256 fingerprints. The file shows "Signed: ready to start work".
5. **Other systems can join in.** With an API key, a CRM or practice manager can open files. Signed webhooks report when each file is sent, signed and complete.

## Try it

1. Open the [live app](https://kickoff-r713.onrender.com) and create an account (any company name).
2. Upload an agreement. Analysis takes from about 10 seconds to 2 minutes, depending on its length.
3. Check the proposed fields and press **Approve**.
4. **Files → New file:** enter the signers and the client's details, then **Preview the agreement → Confirm and send**.
5. Each signer gets an email in the firm's name. The first one may land in spam, so check there. You can also press **Copy signing link** on the file page and send the link yourself.
6. When everyone has signed, the file page offers **Download signed agreement**.

## API and webhooks

In **Settings**, create an API key and set a webhook address. The full reference is at [`/api/docs`](https://kickoff-r713.onrender.com/api/docs).

```bash
# Your approved agreements, with each signer's role and the details a file fills in
curl https://kickoff-r713.onrender.com/api/v1/templates -H "Authorization: Bearer $KICKOFF_KEY"

# Open a file. It waits as a draft until someone at the firm checks the preview and sends it.
curl https://kickoff-r713.onrender.com/api/v1/files \
  -H "Authorization: Bearer $KICKOFF_KEY" -H "Idempotency-Key: matter-1042" \
  -H "Content-Type: application/json" \
  -d '{"template_id": "<id>", "signers": [{"role": "client", "name": "Jane Client", "email": "jane@example.com"}],
       "values": {"client_name": "Acme Test Co"}, "metadata": {"matter_id": "1042"}}'
```

- **Webhook events:** `file.sent`, `signer.signed`, `file.completed` (the signed PDF is ready), `file.declined`, `file.voided` and `file.expired`. Each event carries the file and your `metadata`.
- **Checking a webhook came from Kickoff:** each delivery has `X-Kickoff-Signature: t=<unix time>,v1=<hex>`. Compute an HMAC-SHA256 of `t + "." + raw body` with your signing secret, compare it with `v1`, and reject anything more than 5 minutes old.
- **Failures:** a failed delivery is retried for about an hour and a half. Settings shows a log of every delivery, with **Retry** on any that gave up.

## Status

Everything above is live. Before release I ran a blind test on six public agreements the system had never seen; the results are in the DECISIONS.md change log. Known limitations, and what production would need, are under "Known limitations" in [DECISIONS.md](DECISIONS.md).

## Run it locally

You need Docker, [uv](https://docs.astral.sh/uv/), and Node 22 or newer.

```bash
docker run -d --name mongo -p 27017:27017 mongo:8.0        # database

cd backend
cp .env.example .env                                      # defaults work for local dev
uv sync
uv run uvicorn app.main:create_app --factory --reload --port 8000

# in another terminal
cd frontend
npm install
npm run dev                                               # http://localhost:5173
```

In dev, emails are printed to the backend log instead of being sent (`EMAIL_BACKEND=console`).

Word (.docx) uploads need LibreOffice installed locally. The Docker image includes it (`docker build -t agreement-app . && docker run --env-file backend/.env -p 8000:8000 agreement-app`).

```bash
cd backend && uv run pytest                               # tests (needs the local Mongo)
cd backend && uv run python -m evals.run --offline        # detection eval, keyword rules only (free)
cd backend && uv run python -m evals.run                  # detection eval with Claude (costs about 55 cents)
cd frontend && npm run typecheck && npm run lint && npm run build
```

To check a deployed app end to end without a Claude call, use `scripts/live_check.py`. It runs a whole file on an approved agreement and checks the signed PDF (see [CLAUDE.md](CLAUDE.md)).

## Deploy (Render, free tier)

1. **Database:** create a free MongoDB Atlas M0 cluster and a database user. Under Network Access, allow `0.0.0.0/0` (Render's free tier has no static IPs). Copy the connection string.
2. **Email:** follow "Email setup" below.
3. **Render:** choose New → Blueprint and select this repo. `render.yaml` creates the service. Fill in the secrets it asks for: `MONGODB_URI`, `ANTHROPIC_API_KEY`, the `GMAIL_*` values, and `SENTRY_DSN`.
4. **Keep-awake:** add a free UptimeRobot HTTP monitor on `https://<your-app>.onrender.com/api/health` every 5 minutes. It keeps the free instance from sleeping, and it alerts you if anything degrades.
5. **Verify:**
   - `GET /api/health` should report every check `ok`.
   - Send yourself a test email from the deployed service:
     ```bash
     curl -X POST https://<your-app>.onrender.com/api/ops/test-email \
       -H "x-ops-token: <OPS_TOKEN from the Render dashboard>" \
       -H "content-type: application/json" -d '{"to": "you@example.com"}'
     ```

## Email setup (Gmail API, one-time, about 10 minutes)

Render's free tier blocks SMTP, so the app sends through the Gmail REST API over HTTPS from a dedicated Gmail account. The reasoning is in DECISIONS.md, D11.

1. Create (or reuse) a **dedicated** Gmail account for sending, and turn on 2-step verification.
2. In the [Google Cloud console](https://console.cloud.google.com/), create a project and enable the **Gmail API**.
3. Open **Google Auth Platform**:
   - Under **Branding**, set the app name, support email and developer contact.
     - Google won't let you publish without a home page and a privacy policy. Use the repo URL plus [PRIVACY.md](PRIVACY.md) and [TERMS.md](TERMS.md).
     - Add `github.com` under Authorized domains.
   - Under **Audience**, choose External, then **Publish app** so the status reads "In production". This matters: in "Testing" status, Google expires the refresh token after 7 days.
4. Under **Clients**, choose Create client → **Desktop app**, and copy the client ID and secret.
5. Run the following, using your own address for the test email:
   ```bash
   cd backend
   uv run python -m scripts.gmail_auth --send-test you@example.com
   ```
   - It prompts for the client ID and secret. The secret is hidden and stays out of your shell history.
   - A browser opens. Pick the **dedicated** account. Google warns that the app is unverified; choose Advanced → Continue, since it's your own app.
   - The script prints which account it connected, and sends you a test email.
6. Copy the printed `GMAIL_*` values into `backend/.env` (to send for real locally) and into Render.
