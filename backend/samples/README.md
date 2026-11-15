# samples/

Drop agreements here (PDF or DOCX) to preview the pipeline on them. Everything in this folder
except this README is git-ignored, so nothing you put here is ever committed. Use public templates
or test documents, not real client documents.

```bash
cd backend
uv run python -m scripts.demo_sign samples/<file>.pdf      # Claude labels the fields (~5-40¢)
uv run python -m scripts.demo_sign samples/<file>.pdf --offline   # keyword rules, free
open samples/out/                                          # <name>-review.pdf and <name>-signed.pdf
```

DOCX needs LibreOffice. If it isn't installed locally, run the same thing inside the production
image (which has it):

```bash
docker build -t agreement-app .. && docker run --rm --env-file .env -e APP_ENV=test \
  -e HOME=/tmp --user "$(id -u):$(id -g)" -v "$PWD/samples:/app/backend/samples" \
  agreement-app python -m scripts.demo_sign samples/<file>.docx
```

The review PDF shows each proposed field as a tinted box, one colour per signer and grey for
the values the firm fills in. The signed PDF has every field filled with obviously fake data
(Acme Test Co, Jane Client) and typed signatures. It is a preview, not the signing flow: no
emails, consent step or audit certificate.
