# Kickoff: get your client agreement signed on every file

A firm uploads its standard client agreement once. AI finds where every blank and signature goes, and a person confirms it. From then on, every new file sends a pre-filled agreement to the right people, chases them until they sign, and returns a signed, tamper-evident PDF. It then tells the firm's other systems that work can start.

- **Live app:** https://kickoff-r713.onrender.com
- **Why it's built this way:** [DECISIONS.md](DECISIONS.md)
- **Working on the code:** [CLAUDE.md](CLAUDE.md)

> Status: Day 1 scaffold (health checks, job queue, email delivery, test fixtures). The product features land over the week; see the plan in DECISIONS.md.

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

```bash
cd backend && uv run pytest                               # tests (needs the local Mongo)
cd backend && uv run python -m evals.make_fixtures        # regenerate synthetic agreements
```

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
