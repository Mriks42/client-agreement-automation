"""Connect the dedicated sender Gmail account: mints a long-lived Gmail API refresh token.

One-time setup (see README "Email setup"):
  1. Google Cloud console → new project → enable "Gmail API".
  2. Google Auth Platform → Branding (app name, support email, home page, privacy policy) →
     Audience: External, then PUBLISH APP so the status is "In production". In "Testing" status
     Google expires refresh tokens after 7 days, which would silently break sending.
  3. Clients → Create client → Application type "Desktop app". Copy the ID and secret.
  4. Run:  uv run python -m scripts.gmail_auth --send-test you@example.com
     It prompts for the client ID and secret (the secret is not echoed or kept in shell
     history). A browser opens: pick THE DEDICATED SENDER ACCOUNT. Google warns the app is
     unverified; choose Advanced → continue (it is your own app).
  5. Put the printed values into the Render dashboard (and backend/.env to send for real
     locally).

Scopes: gmail.send to send; gmail.readonly so the bounce poller can read delivery-failure
notices in the sender's own inbox. Use --send-only to skip bounce detection.
"""

import argparse
import asyncio
import getpass
import os
import sys

SEND = "https://www.googleapis.com/auth/gmail.send"
READ = "https://www.googleapis.com/auth/gmail.readonly"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--client-id", default=os.environ.get("GMAIL_CLIENT_ID"))
    parser.add_argument("--send-only", action="store_true", help="skip the read scope")
    parser.add_argument(
        "--send-test", metavar="TO", help="after connecting, send a test email to this address"
    )
    args = parser.parse_args()

    client_id = args.client_id or input("Client ID: ").strip()
    client_secret = (
        os.environ.get("GMAIL_CLIENT_SECRET") or getpass.getpass("Client secret (hidden): ").strip()
    )
    if not client_id or not client_secret:
        parser.error("client ID and secret are required")

    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = [SEND] if args.send_only else [SEND, READ]
    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        scopes=scopes,
    )
    print("\nA browser window will open. Choose the DEDICATED SENDER account, not your own.\n")
    # select_account: always show the account picker. consent + offline: always return a
    # refresh token, even if this account authorised the app before.
    creds = flow.run_local_server(
        port=0, access_type="offline", prompt="select_account consent", open_browser=True
    )
    if not creds.refresh_token:
        print(
            "Google did not return a refresh token. Revoke the app's access at "
            "https://myaccount.google.com/permissions and run this again.",
            file=sys.stderr,
        )
        return 1

    sender = _connected_address(creds.token) if READ in (creds.scopes or scopes) else None
    if sender:
        print(f"Connected account: {sender}")

    if args.send_test:
        from app.services.email import GmailApiSender, OutgoingEmail

        address = sender or input("Sender address (the account you just chose): ").strip()

        async def _send() -> str | None:
            gmail = GmailApiSender(client_id, client_secret, creds.refresh_token, address)
            result = await gmail.send(
                OutgoingEmail(
                    to=[args.send_test],
                    subject="Kickoff email setup test",
                    text="If you can read this, the Gmail API sender is working.",
                    from_name="Kickoff",
                )
            )
            return result.provider_message_id

        print(f"Test email sent to {args.send_test} (Gmail id {asyncio.run(_send())})")

    print("\nPut these into Render (Environment) and, optionally, backend/.env:\n")
    print("EMAIL_BACKEND=gmail_api")
    print(f"GMAIL_CLIENT_ID={client_id}")
    print("GMAIL_CLIENT_SECRET=<the client secret you entered>")
    print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")
    print(f"GMAIL_SENDER={sender or '<the dedicated account address>'}")
    print(f"\nScopes granted: {', '.join(creds.scopes or scopes)}")
    print("Treat the refresh token like a password: don't paste it into chats or commit it.")
    return 0


def _connected_address(access_token: str) -> str | None:
    import httpx

    try:
        resp = httpx.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        return resp.json().get("emailAddress") if resp.status_code == 200 else None
    except httpx.HTTPError:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
