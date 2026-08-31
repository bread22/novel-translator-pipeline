# Vendored runtime inventory

Baseline: `d85f5f224981c6edd4bcd41d856c61593b13abf4`.
The complete pre-patch file checksum list is stored in
`patches/novel-translator-vendor-baseline.sha256`.

## Retained after direct-call validation

The direct-call tests passed before this cleanup. The final retained closure is
now limited to the six operations:

- `app/__init__.py`
- `app/book_io.py`, `app/config.py`, `app/models.py`, `app/snapshots.py`
- `app/review.py`, `app/manual.py`, `app/placeholders.py`
- `prompts/novel_translation_system.md` and `LICENSE`

## Removed after direct-call validation

The CLI and upstream workflow-only modules were removed only after the direct
operation and full pipeline tests passed:

- `main.py`, `app/cli_main.py`
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
- vendor-only documentation, build metadata, skills, and tests
