from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.services import templates as svc
from app.worker.handlers import build_registry
from app.worker.queue import Worker
from tests.conftest import signup

FIXTURES = Path(__file__).parent.parent / "evals" / "fixtures"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def worker(app: FastAPI) -> Worker:
    # Heuristics only: tests never call the model.
    return Worker(app.state.db.db, build_registry(), services={**app.state.services})


async def upload(client: httpx.AsyncClient, name: str = "two_party_services.pdf") -> dict[str, Any]:
    r = await client.post(
        "/api/templates", files={"file": (name, fixture(name), "application/pdf")}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def analyzed(app: FastAPI, client: httpx.AsyncClient) -> dict[str, Any]:
    t = await upload(client)
    assert await worker(app).drain() == 1
    r = await client.get(f"/api/templates/{t['id']}")
    assert r.json()["status"] == "needs_review"
    return r.json()


def edit_body(t: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return {"revision": t["revision"], "roles": t["roles"], "fields": t["fields"], **changes}


async def test_upload_requires_login(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/templates", files={"file": ("a.pdf", fixture("engagement.docx"), "application/pdf")}
    )
    assert r.status_code == 401


async def test_upload_rejects_what_it_cannot_use(client: httpx.AsyncClient) -> None:
    await signup(client)
    r = await client.post(
        "/api/templates", files={"file": ("notes.pdf", b"just some text", "application/pdf")}
    )
    assert r.status_code == 422
    assert "PDF or Word" in r.json()["detail"]


async def test_upload_analyze_review_approve(app: FastAPI, client: httpx.AsyncClient) -> None:
    await signup(client)
    t = await upload(client)
    assert t["status"] == "analyzing" and t["page_count"] >= 1
    assert t["name"] == "two party services"
    job = await app.state.db.db.jobs.find_one({"type": svc.ANALYZE_TEMPLATE})
    assert job["payload"]["template_id"] == t["id"]

    # Pages can be shown while the analysis runs.
    png = await client.get(f"/api/templates/{t['id']}/pages/1.png")
    assert png.status_code == 200 and png.content.startswith(b"\x89PNG")
    assert (await client.get(f"/api/templates/{t['id']}/pages/99.png")).status_code == 404

    w = worker(app)
    claimed = await w.claim()
    assert claimed is not None
    assert (claimed["locked_until"] - claimed["updated_at"]).total_seconds() >= 600
    await w.run_one(claimed)

    r = await client.get(f"/api/templates/{t['id']}")
    t = r.json()
    assert t["status"] == "needs_review"
    assert t["detector"] == "heuristic" and t["fallback_reason"]
    assert {r["key"] for r in t["roles"]} and t["fields"]
    assert t["problems"] == []  # every signer has somewhere to sign
    assert "redact" not in t["fields"][0]

    listed = (await client.get("/api/templates")).json()
    assert [x["id"] for x in listed] == [t["id"]]
    assert listed[0]["field_count"] == len(t["fields"])

    # Edit a field: move it and rename it.
    fields = [dict(f) for f in t["fields"]]
    f0 = fields[0]
    f0["label"] = "Renamed by reviewer"
    f0["rect"] = {**f0["rect"], "x0": f0["rect"]["x0"] + 5, "x1": f0["rect"]["x1"] + 5}
    r = await client.put(f"/api/templates/{t['id']}", json=edit_body(t, fields=fields))
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["revision"] == t["revision"] + 1
    assert saved["fields"][0]["label"] == "Renamed by reviewer"

    # A stale tab can't overwrite it, or approve what it didn't see.
    stale = await client.put(f"/api/templates/{t['id']}", json=edit_body(t))
    assert stale.status_code == 409
    stale_ok = await client.post(
        f"/api/templates/{t['id']}/approve", json={"revision": t["revision"]}
    )
    assert stale_ok.status_code == 409

    r = await client.post(f"/api/templates/{t['id']}/approve", json={"revision": saved["revision"]})
    assert r.status_code == 200, r.text
    approved = r.json()
    assert approved["status"] == "active" and approved["approved_at"] and approved["version"] == 1
    again = await client.post(
        f"/api/templates/{t['id']}/approve", json={"revision": approved["revision"]}
    )
    assert again.status_code == 409


async def test_editing_an_approved_template_makes_a_new_version(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    await signup(client)
    t = await analyzed(app, client)
    t = (
        await client.post(f"/api/templates/{t['id']}/approve", json={"revision": t["revision"]})
    ).json()
    r = await client.put(f"/api/templates/{t['id']}", json=edit_body(t, name="Renamed"))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "needs_review" and body["version"] == 2
    assert body["name"] == "Renamed" and body["approved_at"] is None


async def test_save_rejects_invalid_layouts(app: FastAPI, client: httpx.AsyncClient) -> None:
    await signup(client)
    t = await analyzed(app, client)
    base = t["fields"][0]
    sig = next(f for f in t["fields"] if f["type"] == "signature")
    cases = [
        [{**base, "filled_by": "nobody"}],
        [{**sig, "filled_by": "sender"}],
        [{**base, "rect": {"x0": 10, "y0": 10, "x1": 9000, "y1": 30}}],
        [{**base, "page": 42}],
        [base, base],  # duplicate id
    ]
    for fields in cases:
        r = await client.put(f"/api/templates/{t['id']}", json=edit_body(t, fields=fields))
        assert r.status_code == 422, fields
        assert r.json()["detail"]["problems"]
    roles = [*t["roles"], {**t["roles"][0]}]
    r = await client.put(f"/api/templates/{t['id']}", json=edit_body(t, roles=roles))
    assert r.status_code == 422


async def test_approval_needs_a_signature_for_every_signer(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    await signup(client)
    t = await analyzed(app, client)
    unsigned = [f for f in t["fields"] if f["type"] != "signature"]
    r = await client.put(f"/api/templates/{t['id']}", json=edit_body(t, fields=unsigned))
    assert r.status_code == 200
    saved = r.json()
    assert saved["problems"] and "nowhere to sign" in saved["problems"][0]
    r = await client.post(f"/api/templates/{t['id']}/approve", json={"revision": saved["revision"]})
    assert r.status_code == 422


async def test_new_fields_and_roles_from_the_editor(
    app: FastAPI, client: httpx.AsyncClient, db: Any
) -> None:
    await signup(client)
    t = await analyzed(app, client)
    roles = [*t["roles"], {"key": "witness", "label": "Witness", "kind": "other", "order": 9}]
    added = {
        "id": "fld_manual_1",
        "key": "",
        "label": "Witness signature",
        "type": "signature",
        "filled_by": "witness",
        "page": 1,
        "rect": {"x0": 72, "y0": 600, "x1": 260, "y1": 630},
        "required": True,
        "source": "manual",
        # A client can't make the server remove page content.
        "redact": [{"x0": 0, "y0": 0, "x1": 600, "y1": 800}],
    }
    r = await client.put(
        f"/api/templates/{t['id']}", json=edit_body(t, roles=roles, fields=[*t["fields"], added])
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [r["order"] for r in body["roles"]] == list(range(1, len(roles) + 1))
    stored = await db.templates.find_one({})
    new = next(f for f in stored["fields"] if f["id"] == "fld_manual_1")
    assert new["redact"] == [] and new["key"] == "fld_manual_1"


async def test_placeholder_removal_survives_edits(
    app: FastAPI, client: httpx.AsyncClient, db: Any
) -> None:
    await signup(client)
    t = await upload(client, "bracket_placeholders.pdf")
    await worker(app).drain()
    before = await db.templates.find_one({})
    redact = {f["id"]: f["redact"] for f in before["fields"]}
    assert any(redact.values())  # [CLIENT NAME]-style text to remove when filling
    t = (await client.get(f"/api/templates/{t['id']}")).json()
    fields = [{**f, "redact": []} for f in t["fields"]]
    r = await client.put(f"/api/templates/{t['id']}", json=edit_body(t, fields=fields))
    assert r.status_code == 200
    after = await db.templates.find_one({})
    assert {f["id"]: f["redact"] for f in after["fields"]} == redact


async def test_failed_analysis_offers_retry_and_manual_setup(
    app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await signup(client)

    async def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("extraction crashed")

    monkeypatch.setattr(svc.pipeline, "analyze", broken)
    t = await upload(client)
    db = app.state.db.db
    w = worker(app)
    await w.drain()
    await db.jobs.update_many({"status": "pending"}, {"$set": {"run_at": svc.utcnow()}})
    await w.drain()  # second and last attempt
    t = (await client.get(f"/api/templates/{t['id']}")).json()
    assert t["status"] == "analysis_failed" and t["error"]
    assert "crashed" not in t["error"]  # internal detail stays internal

    r = await client.post(f"/api/templates/{t['id']}/retry-analysis")
    assert r.status_code == 200 and r.json()["status"] == "analyzing"
    monkeypatch.undo()
    await w.drain()
    assert (await client.get(f"/api/templates/{t['id']}")).json()["status"] == "needs_review"
    # Only a failed analysis can be retried or set up by hand.
    assert (await client.post(f"/api/templates/{t['id']}/retry-analysis")).status_code == 409
    assert (await client.post(f"/api/templates/{t['id']}/manual")).status_code == 409


async def test_manual_setup_after_failure(app: FastAPI, client: httpx.AsyncClient, db: Any) -> None:
    await signup(client)
    t = await upload(client)
    await db.templates.update_one({}, {"$set": {"status": "analysis_failed"}})
    r = await client.post(f"/api/templates/{t['id']}/manual")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "needs_review" and body["fields"] == []
    assert body["roles"][0]["key"] == "client" and body["problems"]


async def test_a_repeated_or_stale_analysis_job_does_nothing(
    app: FastAPI, client: httpx.AsyncClient, db: Any
) -> None:
    await signup(client)
    t = await analyzed(app, client)
    before = await db.templates.find_one({})
    payload = {"template_id": t["id"], "org_id": str(before["org_id"]), "attempt": 1}
    await svc.run_analysis(db, app.state.db.blobs, None, payload)
    after = await db.templates.find_one({})
    assert after["revision"] == before["revision"]


async def test_other_companies_cannot_see_or_touch_a_template(
    app: FastAPI, client: httpx.AsyncClient, second_client: httpx.AsyncClient
) -> None:
    await signup(client)
    t = await analyzed(app, client)
    await signup(second_client, email="other@example.com", org_name="Other Test Co")

    tid = t["id"]
    assert (await second_client.get("/api/templates")).json() == []
    for method, path, body in [
        ("GET", f"/api/templates/{tid}", None),
        ("GET", f"/api/templates/{tid}/pages/1.png", None),
        ("GET", f"/api/templates/{tid}/pdf", None),
        ("PUT", f"/api/templates/{tid}", edit_body(t)),
        ("POST", f"/api/templates/{tid}/approve", {"revision": t["revision"]}),
        ("POST", f"/api/templates/{tid}/retry-analysis", None),
        ("POST", f"/api/templates/{tid}/manual", None),
    ]:
        r = await second_client.request(method, path, json=body)
        assert r.status_code == 404, (method, path, r.status_code)
    # And the owner still can.
    assert (await client.get(f"/api/templates/{tid}")).status_code == 200
    assert (await client.get("/api/templates/not-an-id")).status_code == 404


def test_notes_never_show_internal_ids() -> None:
    assert (
        svc.readable_note("p1-s4 was treated as a decorative separator line.")
        == "A blank on page 1 was treated as a decorative separator line."
    )
    assert svc.readable_note("Slots p2-s10 and p2-s11 look optional; see p3-l7.") == (
        "Blanks on page 2 look optional; see a line on page 3."
    )
    # Seen on the live app with the Common Paper PSA:
    assert (
        svc.readable_note(
            "Checkboxes p2-s3, p2-s5, p3-s1 and p3-s4 appear pre-ticked ([ x ]) in the template."
        )
        == "Checkboxes on pages 2 and 3 appear pre-ticked ([ x ]) in the template."
    )
    assert (
        svc.readable_note("Rules above the signature rows (p2-s15/16, p6-s3/4) look decorative.")
        == "Rules above the signature rows (blanks on pages 2 and 6) look decorative."
    )
    assert svc.readable_note("Only Customer Covered Claims (p3-s7) is a slot.") == (
        "Only Customer Covered Claims (a blank on page 3) is a slot."
    )
    assert svc.readable_note("Check the fee clause.") == "Check the fee clause."
