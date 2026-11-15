"""Free end-to-end check of a deployed app: no upload, so no Claude call.

Logs in to an existing account that has an approved agreement, opens a file with it, previews,
sends, signs as every party through copied links, waits for the signed PDF, and checks it: the
certificate pages, the stored SHA-256, no form fields left, and the signer's download by link.
Signers are plus-addresses of the sending inbox, so every email lands there (test data only).

    LIVE_CHECK_PASSWORD=... uv run python -m scripts.live_check \\
        --base https://kickoff-r713.onrender.com --email e2e+<stamp>@example.com \\
        --inbox kickoff.signing.test@gmail.com

Add --inspect-only to just check the account's completed files.
"""

import argparse
import asyncio
import hashlib
import os
import sys
import time
from typing import Any

import httpx

from app.services import pdf_engine


def log(*a: object) -> None:
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def fill(kind: str) -> Any:
    return True if kind == "checkbox" else "2026-11-20" if kind == "date" else "Acme Test Co"


def check_pdf(pdf: bytes, f: dict[str, Any]) -> None:
    assert pdf.startswith(b"%PDF"), "not a PDF"
    assert hashlib.sha256(pdf).hexdigest() == f["final_sha256"], "SHA-256 doesn't match the file"
    pages, agreement = pdf_engine.page_count(pdf), f["template"]["page_count"]
    assert pages > agreement, "no certificate page"
    ex = pdf_engine.extract(pdf)
    cert = " ".join(ln.text for p in ex.pages[agreement:] for ln in p.lines)
    assert "Certificate of completion" in cert and f["id"] in cert, "certificate content"
    for r in f["recipients"]:
        assert r["email"] in cert, f"{r['email']} isn't on the certificate"
    assert not any(s.kind == "form_field" for s in ex.slots), "form fields left in the PDF"
    log(f"  signed PDF ok: {len(pdf)} bytes, {agreement} + {pages - agreement} certificate pages")


async def inspect(c: httpx.AsyncClient) -> None:
    files = (await c.get("/api/files")).json()
    for s in (f for f in files if f["status"] == "completed"):
        f = (await c.get(f"/api/files/{s['id']}")).json()
        status = f"finalized {f['finalized_at']}, error {f['finalize_error']}"
        log(f"- {f['title']} ({f['id']}): {status}")
        if f["finalized_at"]:
            check_pdf((await c.get(f"/api/files/{f['id']}/signed.pdf")).content, f)


async def run(c: httpx.AsyncClient, base: str, inbox: str) -> None:
    stamp = int(time.time())
    user, _, domain = inbox.partition("@")
    templates = (await c.get("/api/templates")).json()
    tpl = next((t for t in templates if t["status"] == "active"), None)
    assert tpl, "this account has no approved agreement"
    t = (await c.get(f"/api/templates/{tpl['id']}")).json()
    people = [
        {
            "role": r["key"],
            "name": f"Test {r['label']}",
            "email": f"{user}+{r['key']}{stamp}@{domain}",
        }
        for r in sorted(t["roles"], key=lambda r: r["order"])
    ]
    new = {"template_id": t["id"], "values": {}, "recipients": people}
    r = await c.post("/api/files", json=new)
    assert r.status_code == 201, r.text
    f = r.json()
    values = {i["key"]: fill(i["type"]) for i in f["sender_inputs"]}
    body = {"revision": f["revision"], "values": values, "recipients": people}
    r = await c.put(f"/api/files/{f['id']}", json={**body, "title": f"Live check {stamp}"})
    assert r.status_code == 200, r.text
    f = r.json()
    assert (await c.get(f"/api/files/{f['id']}/preview.pdf")).status_code == 200
    r = await c.post(f"/api/files/{f['id']}/send", json={"revision": f["revision"]})
    assert r.status_code == 200, r.text
    log(f"sent {t['name']} v{t['version']} as file {f['id']}")

    async with httpx.AsyncClient(base_url=base, timeout=90) as signer:
        token = ""
        for i, rec in enumerate(f["recipients"]):
            for _ in range(30):
                cur = (await c.get(f"/api/files/{f['id']}")).json()
                if cur["recipients"][i]["status"] == "active":
                    break
                await asyncio.sleep(1)
            r = await c.post(f"/api/files/{f['id']}/recipients/{rec['id']}/link")
            assert r.status_code == 200, r.text
            token = r.json()["url"].rsplit("/", 1)[1]
            view = (await signer.get(f"/api/sign/{token}")).json()
            assert view["state"] == "ready", view["state"]
            assert (await signer.post(f"/api/sign/{token}/consent")).status_code == 204
            answers = {
                x["key"]: fill(x["type"])
                for x in view["fields"]
                if x["type"] not in ("signature", "initials", "date_signed")
            }
            sub = {
                "values": answers,
                "signature": {"kind": "typed", "text": rec["name"]},
                "initials": {"kind": "typed", "text": "TT"},
                "tz": "America/Phoenix",
            }
            r = await signer.post(f"/api/sign/{token}/submit", json=sub)
            assert r.status_code == 200, r.text
            log(f"{rec['role_label']} signed")

        started = time.time()
        for _ in range(150):
            f = (await c.get(f"/api/files/{f['id']}")).json()
            if f["finalized_at"] or f["finalize_error"]:
                break
            await asyncio.sleep(2)
        assert f["finalized_at"], f"not finalized: {f['finalize_error']}"
        log(f"signed PDF made {time.time() - started:.0f}s after the last signature")
        pdf = (await c.get(f"/api/files/{f['id']}/signed.pdf")).content
        check_pdf(pdf, f)
        assert (await signer.get(f"/api/sign/{token}/signed.pdf")).content == pdf
        log("  the signer's download by link is the same PDF")
    await asyncio.sleep(15)  # the email jobs
    f = (await c.get(f"/api/files/{f['id']}")).json()
    failed = [e["data"] for e in f["events"] if e["type"] == "email_failed"]
    assert not failed, failed
    log(f"emailed, no failures. File page: {base}/files/{f['id']}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--inbox", default="kickoff.signing.test@gmail.com")
    ap.add_argument("--inspect-only", action="store_true")
    args = ap.parse_args()
    password = os.environ.get("LIVE_CHECK_PASSWORD")
    if not password:
        sys.exit("Set LIVE_CHECK_PASSWORD to the account's password.")
    async with httpx.AsyncClient(base_url=args.base, timeout=90) as c:
        r = await c.post("/api/auth/login", json={"email": args.email, "password": password})
        assert r.status_code == 200, (r.status_code, r.text)
        await inspect(c)
        if not args.inspect_only:
            await run(c, args.base, args.inbox)
    log("PASS")


if __name__ == "__main__":
    asyncio.run(main())
