# Vendored runtime inventory

Baseline: `d85f5f224981c6edd4bcd41d856c61593b13abf4`.
The complete pre-patch file checksum list is stored in
`patches/novel-translator-vendor-baseline.sha256`.

## Retain through direct-call validation

The following files are the current compatibility closure and remain intact
until the process-internal implementation has passed its behavior tests:

- `main.py` and `app/cli_main.py` (the CLI compatibility entry point and its
  complete import closure)
- `app/book_io.py`, `app/config.py`, `app/models.py`, `app/snapshots.py`
- `app/review.py`, `app/manual.py`, `app/placeholders.py`
- `app/terminology.py`, `app/quality.py` (transitive dependencies of review
  writeback and translation reset)
- `prompts/novel_translation_system.md` and `LICENSE`

## Deferred cleanup candidates

The current pipeline has no call sites for the upstream translation and
workflow-only features. After direct Python calls are verified, inspect and
remove the corresponding CLI commands and these modules when they are no
longer in the retained import closure:

- `app/analysis.py`
- `app/context.py`
- `app/delivery.py`
- `app/feedback.py`
- `app/memory.py`
- `app/persona.py`
- `app/runs.py`
- `app/task_control.py`
- `app/translation_outline.py`
- `app/translator.py`
- `app/work_records.py`
- `app/workspace.py`

This inventory deliberately does not delete those files in stage two because
`app/cli_main.py` imports them at startup.
