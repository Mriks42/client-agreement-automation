"""The emails the product sends: full transactional messages with a text and an HTML part, not
bare links (D11). Everything interpolated into HTML is escaped."""

from dataclasses import dataclass
from datetime import datetime
from html import escape


@dataclass(frozen=True)
class Composed:
    subject: str
    text: str
    html: str


def _date(d: datetime) -> str:
    return f"{d:%B} {d.day}, {d.year}"


def _html(
    *, preheader: str, heading: str, paras: list[str], button: tuple[str, str] | None, footer: str
) -> str:
    """paras are already-escaped HTML fragments."""
    body = "".join(
        f'<p style="margin:0 0 14px;font-size:15px;line-height:1.55;color:#1e293b">{p}</p>'
        for p in paras
    )
    cta = ""
    if button:
        label, url = button
        cta = (
            '<table role="presentation" cellspacing="0" cellpadding="0" style="margin:8px 0 22px">'
            '<tr><td style="border-radius:8px;background:#2f5bea">'
            f'<a href="{escape(url)}" style="display:inline-block;padding:12px 22px;'
            "font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;"
            f'border-radius:8px">{escape(label)}</a></td></tr></table>'
            '<p style="margin:0 0 14px;font-size:12px;line-height:1.5;color:#64748b">'
            "If the button doesn't work, copy this link into your browser:<br>"
            f'<a href="{escape(url)}" style="color:#2f5bea;word-break:break-all">'
            f"{escape(url)}</a></p>"
        )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        '<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,'
        "'Segoe UI',Roboto,Helvetica,Arial,sans-serif\">"
        f'<div style="display:none;max-height:0;overflow:hidden">{escape(preheader)}</div>'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        'style="background:#f1f5f9;padding:24px 12px"><tr><td align="center">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        'style="max-width:560px;background:#ffffff;border-radius:12px;padding:28px">'
        f'<tr><td><h1 style="margin:0 0 16px;font-size:20px;line-height:1.3;color:#0f172a">'
        f"{escape(heading)}</h1>{body}{cta}</td></tr></table>"
        '<p style="max-width:560px;margin:14px auto 0;font-size:12px;line-height:1.5;'
        f'color:#64748b">{footer}</p>'
        "</td></tr></table></body></html>"
    )


def signing_request(
    *,
    org_name: str,
    signer_name: str,
    agreement_name: str,
    url: str,
    expires_at: datetime,
    sender_name: str,
    reason: str,
    signed_before: list[str],
    product: str,
) -> Composed:
    """reason: 'invite', 'resend' or 'reminder'."""
    first = signer_name.split()[0] if signer_name.strip() else "there"
    if reason == "reminder":
        subject = f"Reminder: {org_name} is waiting for your signature on {agreement_name}"
        lead = f"{org_name} is still waiting for you to sign {agreement_name}."
    else:
        subject = f"{org_name} sent you {agreement_name} to sign"
        lead = f"{org_name} has sent you {agreement_name} to review and sign."
    before = ""
    if signed_before:
        before = f"{' and '.join(signed_before)} {'has' if len(signed_before) == 1 else 'have'} "
        before += "already signed. It's your turn."
    expires = _date(expires_at)
    steps = "It takes a couple of minutes, on your phone or computer. Nothing to install."
    private = (
        "This link is just for you, so please don't forward it. "
        f"Questions? Reply to this email to reach {sender_name} at {org_name}."
    )
    footer_text = f"Sent by {product} on behalf of {org_name}. The link works until {expires}."

    text_parts = [f"Hi {first},", lead]
    if before:
        text_parts.append(before)
    text_parts += [steps, f"Review and sign:\n{url}", private, "--", footer_text]
    text = "\n\n".join(text_parts) + "\n"

    paras = [escape(f"Hi {first},"), escape(lead)]
    if before:
        paras.append(escape(before))
    paras.append(escape(steps))
    html = _html(
        preheader=lead,
        heading=f"Please sign: {agreement_name}",
        paras=paras,
        button=("Review and sign", url),
        footer=escape(private) + "<br><br>" + escape(footer_text),
    )
    return Composed(subject, text, html)


def declined_notice(
    *,
    org_name: str,
    signer_name: str,
    role_label: str,
    agreement_name: str,
    file_title: str,
    reason: str,
    file_url: str,
    product: str,
) -> Composed:
    subject = f"{signer_name} declined to sign {agreement_name} ({file_title})"
    lead = f"{signer_name} ({role_label}) declined to sign {agreement_name} for {file_title}."
    text = (
        f"{lead}\n\nTheir reason:\n{reason}\n\n"
        f"Open the file to see what happened and start again if needed:\n{file_url}\n\n"
        f"--\n{product} for {org_name}\n"
    )
    html = _html(
        preheader=lead,
        heading="An agreement was declined",
        paras=[
            escape(lead),
            "Their reason:<br><em>" + escape(reason).replace("\n", "<br>") + "</em>",
        ],
        button=("Open the file", file_url),
        footer=escape(f"{product} for {org_name}"),
    )
    return Composed(subject, text, html)


def _check_copy(sha256: str) -> str:
    return (
        "To check that a copy hasn't been changed, compare its SHA-256 with this one "
        f"(the certificate explains how): {sha256}"
    )


def _people(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def signed_copy(
    *,
    org_name: str,
    signer_name: str,
    agreement_name: str,
    sender_name: str,
    parties: list[str],
    sha256: str,
    product: str,
) -> Composed:
    """To each signer when everyone has signed, with the signed PDF attached."""
    first = signer_name.split()[0] if signer_name.strip() else "there"
    subject = f"Signed: your copy of {agreement_name} with {org_name}"
    lead = f"Everyone has signed {agreement_name}: {_people(parties)}."
    attached = (
        "Your copy is attached. It ends with an audit certificate showing who signed, "
        "when, and how. Please keep it for your records."
    )
    contact = f"Questions? Reply to this email to reach {sender_name} at {org_name}."
    footer_text = f"Sent by {product} on behalf of {org_name}."
    check = _check_copy(sha256)
    text = "\n\n".join([f"Hi {first},", lead, attached, contact, check, "--", footer_text]) + "\n"
    html = _html(
        preheader=lead,
        heading=f"Signed: {agreement_name}",
        paras=[escape(f"Hi {first},"), escape(lead), escape(attached), escape(contact)],
        button=None,
        footer=escape(check) + "<br><br>" + escape(footer_text),
    )
    return Composed(subject, text, html)


def completed_notice(
    *,
    org_name: str,
    agreement_name: str,
    file_title: str,
    parties: list[str],
    sha256: str,
    file_url: str,
    product: str,
) -> Composed:
    """To the sender when everyone has signed, with the signed PDF attached."""
    subject = f"Signed: {file_title} ({agreement_name}). Ready to start work"
    lead = f"Everyone has signed {agreement_name} for {file_title}: {_people(parties)}."
    attached = (
        "The signed agreement is attached. It ends with an audit certificate showing who "
        "signed, when, and how. Each signer has been emailed their own copy, and it's also on "
        "the file page."
    )
    check = _check_copy(sha256)
    text = (
        f"{lead}\n\n{attached}\n\nOpen the file:\n{file_url}\n\n{check}\n\n"
        f"--\n{product} for {org_name}\n"
    )
    html = _html(
        preheader=lead,
        heading="Signed: ready to start work",
        paras=[escape(lead), escape(attached)],
        button=("Open the file", file_url),
        footer=escape(check) + "<br><br>" + escape(f"{product} for {org_name}"),
    )
    return Composed(subject, text, html)


def expired_notice(
    *,
    org_name: str,
    agreement_name: str,
    file_title: str,
    waiting_for: str,
    file_url: str,
    product: str,
) -> Composed:
    subject = f"{agreement_name} for {file_title} expired unsigned"
    lead = (
        f"The signing link for {agreement_name} ({file_title}) expired before {waiting_for} signed."
    )
    text = (
        f"{lead}\n\nTo try again, open a new file from the same agreement:\n{file_url}\n\n"
        f"--\n{product} for {org_name}\n"
    )
    html = _html(
        preheader=lead,
        heading="An agreement expired unsigned",
        paras=[escape(lead), "To try again, open a new file from the same agreement."],
        button=("Open the file", file_url),
        footer=escape(f"{product} for {org_name}"),
    )
    return Composed(subject, text, html)


def team_invite(
    *,
    org_name: str,
    invitee_name: str | None,
    inviter_name: str,
    url: str,
    expires_at: datetime,
    product: str,
) -> Composed:
    first = (invitee_name or "").split()[0] if (invitee_name or "").strip() else "there"
    subject = f"{inviter_name} invited you to {org_name} on {product}"
    lead = (
        f"{inviter_name} invited you to join {org_name} on {product}, where the team gets its "
        "client agreements signed on every file."
    )
    steps = "Choose a password to get your own login. You'll see the same agreements and files."
    private = "This link is just for you, so please don't forward it."
    footer_text = (
        f"The link works until {_date(expires_at)}. If you weren't expecting this, ignore it."
    )
    text = (
        "\n\n".join(
            [f"Hi {first},", lead, steps, f"Join {org_name}:\n{url}", private, "--", footer_text]
        )
        + "\n"
    )
    html = _html(
        preheader=lead,
        heading=f"Join {org_name} on {product}",
        paras=[escape(f"Hi {first},"), escape(lead), escape(steps)],
        button=(f"Join {org_name}", url),
        footer=escape(private) + "<br><br>" + escape(footer_text),
    )
    return Composed(subject, text, html)
