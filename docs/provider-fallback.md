# Provider fallback workflow

The chapter pipeline uses the configured primary translator (Gemini by default,
or local OpenCode) and treats an explicit
provider content-filter response as a recoverable failure. It sends large source
windows first (`--primary-batch-max-chars`, default `4000`). A blocked window is
split recursively. The smallest still-blocked segment is sent to the configured
fallback provider; the pipeline never rewrites or disguises the source to retry
the same provider.

## OpenCode backend

OpenCode can be selected independently for review and translation. It is invoked
through the local `opencode run --format json` CLI and does not require changes
to Novel Translator:

```bash
export REVIEWER_BACKEND=opencode
export TRANSLATION_PRIMARY_PROVIDER=opencode
export OPENCODE_REVIEWER_MODEL='provider/model'   # optional
export OPENCODE_TRANSLATOR_MODEL='provider/model' # optional
```

The startup preflight executes one real JSON request for the reviewer and each
selected translator provider. A missing CLI, failed request, or malformed JSON
stops the pipeline before the workspace is initialized.

## LM Studio fallback

Start LM Studio with an OpenAI-compatible server, then set the optional
environment variables before running:

```bash
export MURASAKI_BASE_URL=http://127.0.0.1:1234/v1
export MURASAKI_MODEL=murasaki-14b-v0.2
```

Run the normal chapter pipeline:

```bash
.venv/bin/python scripts/book_pipeline.py \
  --book BOOK_ID \
  --name BOOK_NAME \
  --review-mode chapter \
  --primary-batch-max-chars 4000 \
  --fallback-provider murasaki-local \
  --apply --autonomous --finalize
```

The repository's direct provider adapter performs targeted translation and
writes the selected results into the existing Novel Translator manifest. Novel
Translator itself does not need a provider or CLI code change. The repository
records paragraph provenance in
`data/translation-provenance.json` and provider diagnostics in
`data/provider-diagnostics.json`. Chapter review receives the provenance but
judges the completed chapter uniformly.

The adapter accepts only the required JSON envelope. It rejects freeform output,
truncated responses (`finish_reason=length`), duplicate or repeated content,
whole-paragraph copies from previous context, and suspiciously oversized
translations before writing the manifest. Local fallback requests also receive
a source-size-based `max_tokens` cap so a malformed response cannot consume the
full context window.

The Gemini bridge records a short raw provider response excerpt when it detects
`content_filter`, allowing the failure to be distinguished from network,
output-format, and timeout errors.
