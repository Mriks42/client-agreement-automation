"""The firm's logo and colour on signing emails and the signing page; nothing changes without
them."""

from typing import Any

import httpx
from fastapi import FastAPI

from app.services import branding
from app.services.email import messages
from tests.conftest import signup
from tests.test_files import PNG, last_link, sent
from tests.test_templates import worker

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


async def upload(client: httpx.AsyncClient, data: bytes, name: str = "logo.png") -> httpx.Response:
    return await client.post(
        "/api/settings/branding/logo", files={"file": (name, data, "image/png")}
    )


async def test_logo_and_colour_settings(
    client: httpx.AsyncClient, second_client: httpx.AsyncClient
) -> None:
    await signup(client)
    assert (await client.get("/api/settings/branding")).json() == {
        "logo_url": None,
        "color": None,
        "ink": "#ffffff",
        "text": "#2f5bea",
    }

    # Checked by its first bytes, whatever its name or claimed type.
    for bad in (b"GIF89a" + b"\x00" * 20, b"<svg></svg>", PNG[:4]):
        r = await upload(client, bad)
        assert r.status_code == 422 and "PNG or JPG" in r.text
    r = await upload(client, PNG[:8] + b"\x00" * branding.MAX_LOGO)
    assert r.status_code == 422 and "512 KB" in r.text

    r = await upload(client, PNG)
    assert r.status_code == 200, r.text
    first = r.json()["logo_url"]
    got = await second_client.get(first)  # public: no login
    assert got.status_code == 200 and got.content == PNG
    assert got.headers["content-type"] == "image/png"
    assert "immutable" in got.headers["cache-control"]

    # A new logo gets a new URL; the old one stops working.
    second = (await upload(client, JPEG, "logo.jpg")).json()["logo_url"]
    assert second != first
    assert (await second_client.get(first)).status_code == 404
    assert (await second_client.get(second)).headers["content-type"] == "image/jpeg"

    # Another company's id in the path doesn't find it.
    await signup(second_client, email="other@example.com", org_name="Other Test Co")
    other_org = (await second_client.get("/api/auth/me")).json()["org"]["id"]
    blob = second.rsplit("/", 1)[1]
    assert (await second_client.get(f"/api/branding/{other_org}/logo/{blob}")).status_code == 404
    assert (await second_client.get("/api/settings/branding")).json()["logo_url"] is None

    # Colours: validated, and the text on them is whichever of white or near-black reads better.
    assert (await client.put("/api/settings/branding", json={"color": "#12345"})).status_code == 422
    assert (await client.put("/api/settings/branding", json={"color": "red"})).status_code == 422
    dark = (await client.put("/api/settings/branding", json={"color": "#1E3A8A"})).json()
    assert dark["color"] == "#1e3a8a" and dark["ink"] == "#ffffff" and dark["text"] == "#1e3a8a"
    light = (await client.put("/api/settings/branding", json={"color": "ffd400"})).json()
    assert light["color"] == "#ffd400" and light["ink"] == "#0f172a" and light["text"] == "#0f172a"

    await client.delete("/api/settings/branding/logo")
    cleared = (await client.put("/api/settings/branding", json={"color": None})).json()
    assert cleared == {"logo_url": None, "color": None, "ink": "#ffffff", "text": "#2f5bea"}
    assert (await second_client.get(second)).status_code == 404


def test_readable_text_on_any_colour() -> None:
    for color in ("#000000", "#ffffff", "#2f5bea", "#ffd400", "#777777", "#00ff00", "#8b0000"):
        assert branding.contrast(color, branding.ink(color)) >= 4.5, color
        text = branding.text_on_white(color)
        assert branding.contrast(text, "#ffffff") >= 4.5, color


def test_emails_without_branding_are_unchanged() -> None:
    def compose(brand: dict[str, Any] | None) -> str:
        return messages._html(
            preheader="p", heading="h", paras=["x"], button=("Go", "https://e.test"), footer="f",
            brand=brand, logo_alt="Acme Test Co",
        )  # fmt: skip

    plain = compose(None)
    assert compose(branding.public(None)) == plain
    assert compose(branding.public({"_id": "x", "name": "Acme Test Co"})) == plain
    assert "<img" not in plain and "background:#2f5bea" in plain and "color:#ffffff" in plain


async def test_signing_email_and_page_show_the_brand(
    app: FastAPI, client: httpx.AsyncClient, outbox: Any
) -> None:
    await signup(client)
    _, f = await sent(app, client)
    await worker(app).drain()
    client_email = f["recipients"][0]["email"]
    plain = next(m for m in outbox.outbox if m.to == [client_email])
    assert "<img" not in plain.html and "background:#2f5bea" in plain.html
    token = last_link(outbox, client_email)
    signer = httpx.AsyncClient(transport=client._transport, base_url="http://test")
    view = (await signer.get(f"/api/sign/{token}")).json()
    assert view["branding"] == {
        "logo_url": None,
        "color": None,
        "ink": "#ffffff",
        "text": "#2f5bea",
    }

    logo = (await upload(client, PNG)).json()["logo_url"]
    await client.put("/api/settings/branding", json={"color": "#7c2d12"})
    r = await client.patch(
        f"/api/files/{f['id']}/recipients/{f['recipients'][0]['id']}", json={"resend": True}
    )
    assert r.status_code == 200, r.text
    await worker(app).drain()
    branded = [m for m in outbox.outbox if m.to == [client_email]][-1]
    assert f'src="http://localhost:5173{logo}"' in branded.html
    assert "background:#7c2d12" in branded.html and 'alt="Acme Test Co"' in branded.html
    assert "Secured" not in branded.text  # the email footer is unchanged: "Sent by Kickoff ..."
    assert "Sent by Kickoff on behalf of Acme Test Co" in branded.text

    view = (await signer.get(f"/api/sign/{token}")).json()
    assert view["branding"]["logo_url"] == logo and view["branding"]["color"] == "#7c2d12"
    assert view["branding"]["ink"] == "#ffffff"
