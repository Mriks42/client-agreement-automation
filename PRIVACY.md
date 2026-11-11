# Privacy Notice: Kickoff

_Last updated: 2026-11-11_

Kickoff helps firms get their client agreement signed on every new client file. This notice explains what data Kickoff processes, why, and who processes it.

> **Status:** this is an evaluation deployment built for a technical assessment. It must be used with **test data only**. Do not enter real people's personal information.

## Who this covers

- **Firm users:** people at a company that uses Kickoff to send agreements.
- **Signers:** people who receive an agreement from a firm and sign it.

## What we process

| Data | From whom | Why |
|---|---|---|
| Name, email address, password hash, sign-in sessions | Firm users | To provide accounts and keep them secure |
| Uploaded agreement documents and the per-file details a firm enters (for example, the client's name or fee) | Firm users | To prepare each agreement |
| Name, email address, typed or drawn signature, form field values | Signers | To complete the agreement |
| IP address, browser user agent, timestamps (viewed, consented, signed, declined), consent to sign electronically | Signers | To produce the audit trail that makes an electronic signature verifiable (US ESIGN Act / UETA) |
| Delivery status of emails we send | Both | To show a firm when a signing email bounced, so it can be corrected |

We do not sell data, use it for advertising, or use signer data to train AI models.

## Email and Google user data

Kickoff sends transactional emails (signing requests, reminders, completed copies) from a dedicated Gmail account using the Gmail API. Kickoff accesses only **that sending account**. It uses the access to:

- send these messages, and
- read delivery-failure notices in that account's inbox, so bounced signing requests can be flagged.

It does not access any other mailbox, and it does not use Gmail data for any other purpose. Kickoff's use and transfer of information received from Google APIs adheres to the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements.

## AI processing

When a firm uploads an agreement **template**, its content is sent once to Anthropic's Claude API to propose where fields and signatures go. A person at the firm reviews and approves the result. Signer data is not sent to the AI model.

## Service providers

| Provider | Purpose | Location |
|---|---|---|
| Render | Application hosting | United States |
| MongoDB Atlas (on AWS) | Database and document storage | United States (us-west-2) |
| Google (Gmail API) | Sending email | United States |
| Anthropic | AI field detection on uploaded templates | United States |
| Sentry | Error monitoring, configured not to collect personal data | United States |

## Retention and deletion

Completed agreements and their audit trails are kept for as long as the firm's account exists, because they are the firm's evidence of what was signed. A firm can ask for its account and data to be deleted. Signers can ask the firm that sent them the agreement, or contact us.

## Security

- All traffic is encrypted (HTTPS).
- Passwords are stored as one-way hashes.
- Signing links and API keys are stored only as hashes.
- Completed documents are fingerprinted with SHA-256, so any later alteration can be detected.

## Contact

kickoff.signing.test@gmail.com
