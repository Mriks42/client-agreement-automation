"""Saved clients: saved when a file is sent, and picked on New file to fill the client signer and
the client's identity details, never job-specific or firm-side fields."""

from typing import Any

import httpx
from fastapi import FastAPI

from app.services.clients import identity_inputs
from tests.conftest import signup
from tests.test_templates import edit_body, upload, worker

JANE = {"role": "client", "name": "Jane Client", "email": "Jane.Client@example.com"}
FIRM = {"role": "provider", "name": "Test Owner", "email": "owner@example.com"}


async def approved(app: FastAPI, client: httpx.AsyncClient, name: str) -> dict[str, Any]:
    t = await upload(client, name)
    await worker(app).drain()
    t = (await client.get(f"/api/templates/{t['id']}")).json()
    assert t["status"] == "needs_review", t["status"]
    r = await client.post(f"/api/templates/{t['id']}/approve", json={"revision": t["revision"]})
    assert r.status_code == 200, r.text
    return r.json()


async def send(
    client: httpx.AsyncClient, t: dict[str, Any], values: dict[str, Any], people: list[Any]
) -> dict[str, Any]:
    r = await client.post(
        "/api/files", json={"template_id": t["id"], "values": values, "recipients": people}
    )
    assert r.status_code == 201, r.text
    f = r.json()
    r = await client.post(f"/api/files/{f['id']}/send", json={"revision": f["revision"]})
    assert r.status_code == 200, r.text
    return r.json()


async def fill(client: httpx.AsyncClient, client_id: str, t: dict[str, Any]) -> dict[str, Any]:
    r = await client.get(f"/api/clients/{client_id}/fill", params={"template_id": t["id"]})
    assert r.status_code == 200, r.text
    return r.json()


async def test_saved_on_send_and_matched_across_agreements(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    await signup(client)
    services = await approved(app, client, "two_party_services.pdf")
    brackets = await approved(app, client, "bracket_placeholders.pdf")
    keys = {i["key"] for i in services["sender_inputs"]}
    assert {"client_legal_name", "client_address", "project_fee_usd"} <= keys

    values = {
        "client_legal_name": "Acme Test Co",
        "client_address": "1 Test Street, Testville",
        "effective_date": "2026-11-20",
        "project_fee_usd": "5,000",
        "project_description": "A test website",
    }
    r = await client.post(
        "/api/files",
        json={"template_id": services["id"], "values": values, "recipients": [JANE, FIRM]},
    )
    assert r.status_code == 201
    assert (await client.get("/api/clients")).json() == []  # a draft isn't saved

    await send(client, services, values, [JANE, FIRM])
    found = (await client.get("/api/clients", params={"q": "acme"})).json()
    assert len(found) == 1
    saved = found[0]
    assert saved["company"] == "Acme Test Co"
    assert saved["contact_name"] == "Jane Client"
    assert saved["email"] == "jane.client@example.com"
    assert saved["address"] == "1 Test Street, Testville"
    assert (await client.get("/api/clients", params={"q": "JANE"})).json()[0]["id"] == saved["id"]
    assert (await client.get("/api/clients", params={"q": "nobody"})).json() == []

    # Another agreement: matched by label category. Its job fields stay empty.
    other = await fill(client, saved["id"], brackets)
    assert other["recipient"] == {
        "role": "client",
        "name": "Jane Client",
        "email": "jane.client@example.com",
    }
    assert other["values"] == {
        "client_name": "Acme Test Co",
        "client_address": "1 Test Street, Testville",
    }

    # The same agreement: the exact keys come back, and still no dates, fees or descriptions.
    same = await fill(client, saved["id"], services)
    assert same["values"] == {
        "client_legal_name": "Acme Test Co",
        "client_address": "1 Test Street, Testville",
    }

    # Sending again updates the same entry; a detail left empty doesn't erase the saved one.
    await send(
        client,
        brackets,
        {"client_name": "Acme Test Co", "monthly_fee": "900"},
        [
            {**JANE, "name": "Jane Q. Client"},
            {"role": "company", **{k: FIRM[k] for k in ("name", "email")}},
        ],
    )
    all_clients = (await client.get("/api/clients")).json()
    assert len(all_clients) == 1
    assert all_clients[0]["contact_name"] == "Jane Q. Client"
    assert all_clients[0]["address"] == "1 Test Street, Testville"
    assert all_clients[0]["files_sent"] == 2


async def test_firm_side_fields_are_never_filled(app: FastAPI, client: httpx.AsyncClient) -> None:
    await signup(client)
    t = await approved(app, client, "two_party_services.pdf")
    base = next(f for f in t["fields"] if f["key"] == "client_address")
    extra = []
    for n, label in enumerate(["Provider address", "Consultant phone", "Firm email"]):
        rect = {**base["rect"], "y0": base["rect"]["y0"] + 30 * (n + 1)}
        rect["y1"] = rect["y0"] + 14
        extra.append(
            {**base, "id": f"x{n}", "key": f"firm_{n}", "label": label, "rect": rect, "redact": []}
        )
    r = await client.put(f"/api/templates/{t['id']}", json=edit_body(t, fields=t["fields"] + extra))
    assert r.status_code == 200, r.text
    t = r.json()
    t = (
        await client.post(f"/api/templates/{t['id']}/approve", json={"revision": t["revision"]})
    ).json()
    values = {"client_legal_name": "Acme Test Co", "client_address": "1 Test Street"}
    firm_values = {"firm_0": "9 Firm Road", "firm_1": "555-0100", "firm_2": "firm@example.com"}
    await send(client, t, {**values, **firm_values}, [JANE, FIRM])
    saved = (await client.get("/api/clients")).json()[0]
    assert saved["phone"] == "" and saved["address"] == "1 Test Street"
    assert (await fill(client, saved["id"], t))["values"] == values


def test_label_rules() -> None:
    roles = [
        {"key": "client", "label": "Client", "kind": "client", "order": 1},
        {"key": "agency", "label": "Agency", "kind": "firm", "order": 2},
    ]

    def field(n: int, label: str, type: str = "text") -> dict[str, Any]:
        rect = {"x0": 50, "y0": 50 + 20 * n, "x1": 200, "y1": 64 + 20 * n}
        return {"key": f"k{n}", "label": label, "type": type, "filled_by": "sender", "page": 1,
                "rect": rect, "required": True}  # fmt: skip

    labels = {
        "Client company name": "company",
        "Client": "company",
        "Contact person": "contact_name",
        "Dear": "contact_name",
        "Client e-mail address": "email",
        "Client phone": "phone",
        "Client address": "address",
        "Client title": "title",
        "Agency address": None,  # the firm role's own label
        "Consultant name": None,
        "Provider phone": None,
        "Vendor email": None,
        "Designer name": None,
        "Project fee": None,
        "Description of services": None,
        "Deliverables": None,
        "Client industry": None,
    }
    fields = [field(n, label) for n, label in enumerate(labels)]
    fields.append(field(99, "Client start", "date"))
    fields.append(field(98, "Client name", "checkbox"))
    got = {i["label"]: cat for i, cat in identity_inputs(fields, roles)}
    assert got == {label: cat for label, cat in labels.items() if cat}

    # Shared words between the two parties' names don't exclude the client's own fields.
    roles = [
        {"key": "party_a", "label": "Party A", "kind": "client", "order": 1},
        {"key": "party_b", "label": "Party B", "kind": "firm", "order": 2},
    ]
    got = {i["label"]: c for i, c in identity_inputs([field(1, "Party A name")], roles)}
    assert got == {"Party A name": "company"}


async def test_other_companies_get_404(
    app: FastAPI, client: httpx.AsyncClient, second_client: httpx.AsyncClient
) -> None:
    await signup(client)
    t = await approved(app, client, "two_party_services.pdf")
    await send(client, t, {"client_legal_name": "Acme Test Co"}, [JANE, FIRM])
    mine = (await client.get("/api/clients")).json()[0]

    await signup(second_client, email="other@example.com", org_name="Other Test Co")
    theirs = await approved(app, second_client, "two_party_services.pdf")
    assert (await second_client.get("/api/clients")).json() == []
    r = await second_client.get(
        f"/api/clients/{mine['id']}/fill", params={"template_id": theirs["id"]}
    )
    assert r.status_code == 404
    assert (await second_client.delete(f"/api/clients/{mine['id']}")).status_code == 404
    # Nor can the first company fill against the other company's agreement.
    r = await client.get(f"/api/clients/{mine['id']}/fill", params={"template_id": theirs["id"]})
    assert r.status_code == 404
    assert (await client.get("/api/clients/not-an-id/fill?template_id=x")).status_code == 404

    assert (await client.delete(f"/api/clients/{mine['id']}")).status_code == 204
    assert (await client.get("/api/clients")).json() == []
