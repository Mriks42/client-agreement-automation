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
