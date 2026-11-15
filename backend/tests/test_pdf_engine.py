"""PDF engine: normalization and slot extraction on small, hand-built PDFs.

(Tests may build inputs with PyMuPDF; only app code is restricted to pdf_engine.py.)
"""

import pymupdf
import pytest

from app.services import pdf_engine as pe


def make_pdf(draw, pages: int = 1, **save) -> bytes:  # type: ignore[no-untyped-def]
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=612, height=792)
        draw(page)
    data = doc.tobytes(**save)
    doc.close()
    return data


def slots_of(data: bytes) -> list[pe.Slot]:
    return pe.extract(data).slots


# ---- normalize --------------------------------------------------------------------------------
def test_normalize_bakes_rotation_into_content() -> None:
    def draw(page: pymupdf.Page) -> None:
        page.insert_text((72, 100), "Signature: ________________", fontsize=11)
        page.set_rotation(90)

    out = pe.normalize_pdf(make_pdf(draw), max_pages=60)
    with pymupdf.open(stream=out.data, filetype="pdf") as doc:
        assert doc[0].rotation == 0
        assert (doc[0].rect.width, doc[0].rect.height) == (792, 612)


def test_normalize_rejects_password_protected_pdf() -> None:
    data = make_pdf(
        lambda p: p.insert_text((72, 72), "secret"),
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="test-user-pw",
        owner_pw="test-owner-pw",
    )
    with pytest.raises(pe.PdfError) as err:
        pe.normalize_pdf(data, max_pages=60)
    assert err.value.code == "encrypted"


def test_normalize_enforces_page_limit_and_rejects_garbage() -> None:
    with pytest.raises(pe.PdfError) as err:
        pe.normalize_pdf(make_pdf(lambda p: None, pages=3), max_pages=2)
    assert err.value.code == "too_many_pages"
    with pytest.raises(pe.PdfError) as err:
        pe.normalize_pdf(b"%PDF-1.7 this is not really a pdf", max_pages=60)
    assert err.value.code == "unreadable"


# ---- extract ----------------------------------------------------------------------------------
def test_underscore_blank_has_measured_box_and_label() -> None:
    def draw(page: pymupdf.Page) -> None:
        page.insert_text((72, 100), "Client legal name: ____________________ (as registered)")

    [slot] = slots_of(make_pdf(draw))
    label_width = pymupdf.get_text_length("Client legal name: ", fontsize=11)
    underscores = pymupdf.get_text_length("_" * 20, fontsize=11)
    assert slot.kind == "underscore"
    assert slot.before == "Client legal name:"
    assert slot.after == "(as registered)"
    assert slot.bbox.x0 == pytest.approx(72 + label_width, abs=0.5)
    assert slot.bbox.width == pytest.approx(underscores, abs=0.5)
    assert slot.baseline == pytest.approx(100, abs=0.5)


def test_two_blanks_on_one_line_split_the_label_text() -> None:
    def draw(page: pymupdf.Page) -> None:
        page.insert_text((72, 100), "Authorized signature: ____________ Date: ________")

    first, second = slots_of(make_pdf(draw))
    assert (first.before, first.after) == ("Authorized signature:", "Date:")
    assert (second.before, second.after) == ("Date:", "")


def test_drawn_line_is_a_blank_but_underlined_text_and_table_borders_are_not() -> None:
    def draw(page: pymupdf.Page) -> None:
        page.insert_text((72, 100), "Signature:")
        page.draw_line((130, 101.5), (330, 101.5))  # a drawn signature line
        page.insert_text((72, 160), "Important heading")
        w = pymupdf.get_text_length("Important heading", fontsize=11)
        page.draw_line((72, 162), (72 + w, 162))  # underline under text
        for y in (200, 220, 240):  # a bordered table
            page.draw_line((72, y), (400, y))
        for x in (72, 236, 400):
            page.draw_line((x, 200), (x, 240))
        page.insert_text((78, 214), "Workshop")
        page.insert_text((242, 214), "USD 2,000")

    [slot] = slots_of(make_pdf(draw))
    assert slot.kind == "drawn_line"
    assert slot.before == "Signature:"
    assert (slot.bbox.x0, slot.bbox.x1) == pytest.approx((130, 330))


def test_caption_below_a_bare_line_names_it() -> None:
    def draw(page: pymupdf.Page) -> None:
        page.draw_line((72, 300), (300, 300))
        page.insert_text((72, 311), "Client signature", fontsize=8)

    [slot] = slots_of(make_pdf(draw))
    assert slot.line_id is None
    assert slot.caption == "Client signature"
    assert slot.anchor == "Client signature"


def test_placeholders_including_one_that_wraps_but_not_legal_brackets() -> None:
    def draw(page: pymupdf.Page) -> None:
        page.insert_text((72, 100), "Notices go to [CLIENT\nADDRESS] and {{client_email}}.")
        page.insert_text((72, 160), "As held in Case [1] and in [a], see clause 3 [sic].")

    slots = slots_of(make_pdf(draw))
    assert [s.text for s in slots] == ["[CLIENT ADDRESS]", "{{client_email}}"]
    wrapped = slots[0]
    assert len(wrapped.redact) == 2  # both halves get removed before the value is written


def test_toc_dot_leaders_are_not_blanks_but_dotted_blanks_are() -> None:
    def draw(page: pymupdf.Page) -> None:
        page.insert_text((72, 100), "1. Services " + "." * 60 + " 3")
        page.insert_text((72, 140), "Matter: " + "." * 60)

    [slot] = slots_of(make_pdf(draw))
    assert slot.kind == "dots"
    assert slot.before == "Matter:"


def test_label_with_empty_space_after_it_becomes_a_blank() -> None:
    def draw(page: pymupdf.Page) -> None:
        page.insert_text((72, 100), "By:")
        page.insert_text((330, 100), "By:")
        page.insert_text((72, 140), "Payment is due by: the end of the month")  # not a gap

    left, right = slots_of(make_pdf(draw))
    assert left.kind == right.kind == "label_gap"
    assert left.bbox.x1 < 330  # stops before the other column
    assert right.bbox.x1 <= 612 - 36


def test_form_field_replaces_the_line_drawn_under_it() -> None:
    def draw(page: pymupdf.Page) -> None:
        page.insert_text((72, 100), "Client signature:")
        page.draw_line((170, 101.5), (370, 101.5))
        w = pymupdf.Widget()
        w.field_name = "client_sig"
        w.field_type = pymupdf.PDF_WIDGET_TYPE_SIGNATURE
        w.rect = pymupdf.Rect(170, 84, 370, 102)
        page.add_widget(w)

    [slot] = slots_of(make_pdf(draw))
    assert slot.kind == "form_field"
    assert slot.detail == "Signature form field"
    assert slot.before == "Client signature:"


def test_scanned_page_has_no_text_layer() -> None:
    def draw(page: pymupdf.Page) -> None:
        pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 50, 50), False)
        pix.clear_with(200)
        page.insert_image(page.rect, pixmap=pix)

    ex = pe.extract(make_pdf(draw))
    assert not ex.has_text_layer
    assert ex.slots == []


# ---- write ------------------------------------------------------------------------------------
def test_append_signature_page_adds_a_block_per_signer() -> None:
    data = make_pdf(lambda p: p.insert_text((72, 72), "Terms"))
    out, fields = pe.append_signature_page(
        data, [("client", "Client"), ("firm", "Acme Test Co")], title="Terms of Business"
    )
    assert pe.page_count(out) == 2
    assert {(f.role_key, f.type) for f in fields} == {
        (r, t) for r in ("client", "firm") for t in ("signature", "name", "date_signed")
    }
    for f in fields:
        assert f.page == 2
        assert 0 <= f.rect.x0 < f.rect.x1 <= 612 and 0 <= f.rect.y0 < f.rect.y1 <= 792
    text = pe.extract(out).pages[1].lines
    assert any("Terms of Business" in ln.text for ln in text)


def test_append_signature_page_overflows_onto_more_pages() -> None:
    data = make_pdf(lambda p: None)
    signers = [(f"party_{i}", f"Party {i}") for i in range(8)]
    out, fields = pe.append_signature_page(data, signers, title="")
    assert pe.page_count(out) == 3
    assert max(f.rect.y1 for f in fields) <= 792 - 72


def test_render_page_png() -> None:
    png = pe.render_page_png(make_pdf(lambda p: p.insert_text((72, 72), "Hi")), 1, dpi=40)
    assert png.startswith(b"\x89PNG")
    with pytest.raises(pe.PdfError):
        pe.render_page_png(make_pdf(lambda p: None), 2)
