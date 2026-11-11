"""Mint a long-lived Gmail API refresh token for the dedicated sender account.

One-time setup (see README "Email setup"):
  1. Google Cloud console → new project → enable "Gmail API".
  2. OAuth consent screen (Google Auth Platform → Audience): user type External, then
     PUBLISH APP so the status is "In production". In "Testing" status Google expires
     refresh tokens after 7 days, which would silently break sending mid-evaluation.
  3. Credentials → Create credentials → OAuth client ID → Application type "Desktop app".
  4. Run:  uv run python -m scripts.gmail_auth --client-id ... --client-secret ...
     Sign in AS THE DEDICATED SENDER ACCOUNT. Google will warn that the app is unverified;
     choose Advanced → continue (it is your own app).
  5. Put the printed values into backend/.env (dev) and the Render dashboard (prod).

Scopes: gmail.send to send; gmail.readonly so the bounce poller can read delivery-failure
notices in the sender's inbox. Use --send-only if you don't want bounce detection.
"""

import argparse
import os
import sys

SEND = "https://www.googleapis.com/auth/gmail.send"
READ = "https://www.googleapis.com/auth/gmail.readonly"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--client-id", default=os.environ.get("GMAIL_CLIENT_ID"))
    parser.add_argument("--client-secret", default=os.environ.get("GMAIL_CLIENT_SECRET"))
    parser.add_argument("--send-only", action="store_true", help="skip the read scope")
    args = parser.parse_args()
    if not args.client_id or not args.client_secret:
        parser.error("--client-id and --client-secret are required (or set GMAIL_CLIENT_* env)")

    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = [SEND] if args.send_only else [SEND, READ]
    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": args.client_id,
                "client_secret": args.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        scopes=scopes,
    )
    # prompt=consent + offline access guarantees Google returns a refresh token.
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        print(
            "Google did not return a refresh token. Revoke the app's access at "
            "https://myaccount.google.com/permissions and run this again.",
            file=sys.stderr,
        )
        return 1

    print("\nAdd these to backend/.env and to the Render environment:\n")
    print("EMAIL_BACKEND=gmail_api")
    print(f"GMAIL_CLIENT_ID={args.client_id}")
    print("GMAIL_CLIENT_SECRET=<the client secret you passed in>")
    print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")
    print("GMAIL_SENDER=<the dedicated account's address>")
    print(f"\nScopes granted: {', '.join(creds.scopes or scopes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
