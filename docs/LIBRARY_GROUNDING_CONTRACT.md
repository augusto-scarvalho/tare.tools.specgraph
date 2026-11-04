# Library grounding contract

`specgraph ground` is the single agent-facing projection of grounding metadata
for one repository. Its input is a sources manifest that the host generates
from Git; SpecGraph does not ingest, deduplicate, catalog or publish documents.

## Ownership

- Each repository owns its documents and ontology payloads, in its own tree. No
  shared catalog records pointers to them: `tare.tools.library` keeps only its
  own research and a derived retrieval index, and it is not a grounding
  authority.
- The host writes the sources manifest for the documents one Work references.
  `tare.tools.os` writes `SOURCES.json` into the run's train directory, reading
  each `repo:path` from Git (a satellite's at the gitlink the OS pins), and
  passes that file as `--library-manifest`. The flag and the v3 wire format keep
  their historical names.
- `tare.tools.specgraph` reads one explicitly supplied manifest and projects the
  entries mapped to one exact repository ID.
- Kernel/provider adapters invoke the CLI and transport its exact bytes.
- Agent Runtime accepts the immutable grounding envelope before provider
  execution. It does not import SpecGraph internals.

## Command

```text
specgraph ground \
  --repo tare.tools.kernel \
  --library-manifest "<TRAIN_DIR>/SOURCES.json" \
  --include-id "tare.tools.kernel:docs/SPEC-KERNEL-001.md" \
  --max-bytes 8192 \
  --format json
```

Manifest discovery is deliberately forbidden. The caller must choose both the
repository identity and the manifest path. Managed Work may additionally
require exact document IDs through repeatable `--include-id` arguments. An ID is
the repository-qualified `repo:path`; bare ADR numbers are not ecosystem-wide
identities.

Grounding remains metadata-only: a `READY` envelope does not acquire the owner
document's body or reconstruct missing acceptance criteria.
`ground --read-bodies --owner REPOSITORY=PATH` reads each selected body from the
owner's Git blob at the commit the manifest records and verifies its SHA-256; it
never reads the working file.

## Invariants

- read-only: no source document, manifest, cache, or worktree is changed;
- offline: no network, embedding, model, or daemon call;
- bounded: manifest input is capped at 16 MiB and output at 1-64 KiB;
- deterministic: stable ordering and compact UTF-8 JSON;
- byte-identifiable: output reports its exact byte count and manifest SHA-256;
- target-scoped: entries without the exact repository mapping are excluded;
- authority-aware: v3 entries require `authority_state/v1`; only
  `DECLARED_ACTIVE` and `UNMANAGED_ACTIVE` payloads may be selected, while
  unknown states and targeted `CONFLICT` or `EXCLUDED` entries fail closed;
- provenance-preserving: v3 payload identity equals its content SHA-256 and
  `source_paths` is a unique, UTF-8-byte-sorted list that includes the chosen
  transport path and retains each source's own authority state;
- work-scoped: when IDs are requested, every requested ID must map to the exact
  repository or the projection returns `INCOMPLETE_MAPPING`;
- revalidatable: `grounding_scope_sha256` identifies only the repository,
  requested IDs, and selected entry metadata. Unrelated manifest changes do not
  drift a running Work item; `--expected-scope-sha256` detects selected changes;
- fail-closed: duplicate selected document IDs or content payloads produce
  `AMBIGUOUS_SSOT` and no grounding entries;
- content-light: the projection includes identity, path, digest, title, type,
  and bounded acceptance criteria, but never reads full documents.

`READY`, `READY_TRUNCATED`, and `NOT_MAPPED` are usable outcomes. Source
unavailability, invalid contracts, incomplete required mappings, selected-scope
drift, and ambiguous canonical identity return a non-zero CLI exit code.
