# Book data access

`translator.core.book_store` is the runtime boundary for book JSON mutations.

- `JsonDocument.snapshot()` returns a detached value and content revision. `replace`
  requires that revision; a stale snapshot raises `VersionConflict` without writes.
- `update` holds the file lock across read/mutate/publish. `patch` merges only supplied
  fields, suitable for progress checkpoints (do not pass an old full progress object).
- `BookRepository.update_paragraphs` merges only selected IDs. Provider calls run
  outside the transaction and pass the pre-call manifest as a paragraph baseline.
- `glossary_transaction` publishes the authority and derived translator projection
  together; an exception restores both files. Knowledge extraction uses the same
  file transaction for glossary, projection, memory, candidates and conflicts.
- `file_transaction` integrates legacy vendor mutations with the same lock and
  rollback boundary. Acquire multiple locks in canonical path order. Locks are
  reentrant within a thread; Linux also uses flock for cooperating processes.

Do not hold transactions over HTTP/model calls. Atomic file replacement is not a
read-modify-write transaction. All writers must participate in this protocol.
Multi-file rollback handles exceptions in a live process; power-loss atomicity
requires a journal/database and is not claimed here. Queue dispatch is still a
single-process service; run one job-manager process per output root.

## Execution contexts

`BookExecutionContext` binds a book ID, manifest, workspace, vendor root, config
file and translation policy to a detached config snapshot. Create it once per
job. Its translator factory always injects the book glossary and the resolved
config-relative policy. Its reviewer passes the same snapshot through chunking,
dual-review threads, retries and provider construction. Custom callbacks remain
supported; they are responsible for their own configuration.

`JobManager(config=..., config_path=...)` supports independent embedded instances.
The module-level manager remains a compatibility/default web entry point, not a
required dependency for constructing adapters. With no explicit config override,
a manager captures current config when a job begins; changes affect subsequent
jobs, not a running review's later chunks. Relative policy overrides are resolved
against the config file, not the process working directory or vendor directory.

## CI fault interleavings

Run `python -m pytest -q -m interleaving` and, in `frontend/`,
`npm run test:interleaving`. CI runs each suite three times; backend runs vary
`PYTHONHASHSEED` and retain JUnit reports for each Python version, including failed
runs. Worker/provider fixtures use bounded Events and release them in finally
blocks; frontend tests use controlled Promises instead of network timing.

Coverage includes selected/unrelated paragraph edits during translation, edits
at the publish boundary, cancellation followed by provider failure and next-job
dispatch, replacement while queued, enqueue/batch/retry while replacing, failed
replacement rollback (exact hashes), static directory/symlink boundaries, and
late successful/failed saves or retranslations after book/chapter changes or
navigation away and back. Version-conflict and glossary projection rollback tests
run in the same focused backend gate.
