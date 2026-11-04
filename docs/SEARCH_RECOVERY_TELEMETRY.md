# Search recovery telemetry and learning handoff

## Ownership and context

- Owner: `tare.tools.specgraph` maintainers
- Created: 2026-08-25
- Status: implemented, ready for engineering review
- Contract version: telemetry v1

This document specifies how repository-search failures become evidence for
improving SpecGraph without making telemetry authoritative, leaking source
content, or adding a semantic service to the hook hot path.

## Intent

The system must answer two different questions:

1. Can the agent find the information now, under a bounded latency budget?
2. What recurring gaps should be fixed so future agents need less traditional
   search?

`specgraph resolve` answers the first synchronously. Its bounded telemetry and
validated recovery index support the second. Removing telemetry would preserve
correctness but erase the evidence needed to reduce miss, stale and timeout
rates over time.

## Event contract

Each completed resolution appends one JSON object to `search-events.jsonl`:

```json
{
  "version": 1,
  "at": "2026-08-25T00:00:00+00:00",
  "query_sha256": "<64 lowercase hex characters>",
  "query_bytes": 17,
  "status": "HIT",
  "source": "traditional",
  "primary_status": "MISS",
  "signals": ["MISS"],
  "recovery_action": "INDEXED_FALLBACK_EVIDENCE",
  "count": 2,
  "duration_ms": 83.21
}
```

The log never stores the raw query, excerpts, file paths, prompts, model output,
conversation IDs or transcript paths. `query_sha256` only correlates repeated
local events; it is not a globally stable semantic identifier.

### Enumerations

| Field | Values | Meaning |
|---|---|---|
| `status` | `HIT`, `MISS` | Whether the complete resolution produced evidence |
| `source` | `specgraph`, `traditional`, `learned`, `none` | Which layer produced the final answer |
| `primary_status` | `HIT`, `MISS`, `STALE`, `TIMEOUT`, `UNAVAILABLE` | Why structural resolution succeeded or recovery was needed |
| `signals` | Primary states plus `STALE_LEARNED_EVIDENCE` and `FALLBACK_*` | Non-lossy diagnostic trail |
| `recovery_action` | `NONE`, `REUSED_VALIDATED`, `INDEXED_CURRENT_EVIDENCE`, `INDEXED_FALLBACK_EVIDENCE`, `EVICTED_STALE` | Bounded mutation of derived state |

## Storage, retention and integrity

Runtime state lives outside the worktree; see
[`SPECGRAPH_FIRST_RECOVERY_PIPELINE.md`](SPECGRAPH_FIRST_RECOVERY_PIPELINE.md).
The active telemetry segment is capped at 1 MiB. Rotation keeps one prior
segment, so local retention is at most approximately 2 MiB. Malformed lines are
ignored by aggregation and telemetry write failures never alter lookup results.

The recovery index is capped at 256 query entries. It may store the local query
and bounded result metadata because it must serve future lookup, but every
result is bound to the SHA-256 of the current source file. Changed, missing or
out-of-root files are rejected before reuse.

## Automatic actions

The state classifier, not a model, chooses automatic actions:

| Observation | Action in the same request | Persistent effect |
|---|---|---|
| Structural `HIT` on fresh bytes | Return result | None |
| `MISS` | Run bounded literal scan | Index fallback evidence only if found |
| Causal cache `STALE` | Search current bytes | Index the current validated answer locally |
| `TIMEOUT` | Do not retry the failed stage | Spend only remaining budget on fallback |
| `UNAVAILABLE` | Do not invent a result | Use bounded fallback |
| Learned file digest changed | Evict before return | Relearn only from a new current-byte hit |

No automatic action edits code, specifications, ADRs, documentation, Git
history, the causal cache or a Library corpus.

## Measurement

Run:

```powershell
specgraph telemetry --format table
specgraph telemetry --format json
specgraph telemetry --format md
```

The summary exposes:

- event and state counts;
- traditional recovery rate;
- validated learned-reuse rate;
- stale, timeout and unavailable rates;
- action counts;
- latency p50, p95 and maximum.

Interpretation is comparative, not threshold-driven:

- rising traditional recovery with stable workload suggests a structural
  coverage or configuration gap;
- rising stale rate suggests cache synchronization is lagging repository churn;
- rising timeout rate suggests the structural budget or parser cost regressed;
- rising validated reuse with falling fallback rate shows recovered work is
  being amortized;
- frequent stale-evidence eviction means recovered locations are volatile and
  should probably become first-class structural entities instead of aliases.

No production threshold is hard-coded yet. A threshold becomes policy only
after a measured baseline and an explicit decision.

## SpecGraph improvement loop

1. Collect local bounded events during ordinary agent work.
2. Inspect `specgraph telemetry` for a repeated state or latency regression.
3. Inspect the local recovery index only on the affected machine when exact
   query-to-location evidence is required.
4. Convert the recurring gap into a minimal parser, configuration or benchmark
   hypothesis.
5. Add a falsifier that reproduces the structural miss without relying on the
   learned index.
6. Implement the smallest correction and compare state rates on the same
   workload.
7. Remove compatibility learning when the structural layer covers the case.

This makes telemetry an input to engineering judgment, not a self-authorizing
code-generation loop.

## Relationship with `tare.tools.library`

The synchronous resolver does **not** use the Library indexing pipeline.
That separation is deliberate:

- Library's current `tools/indexer/embed_corpus.py` indexes Markdown chunks into
  a SQLite vector store and namespaces results by embedding model and
  provenance.
- Heavy embedding work is governed for execution on a configured compute
  node; adding it to a pre-tool hook would violate the latency and thin-client
  contracts.
- Semantic similarity cannot replace the exact path, line and source-file hash
  validation required before a blocking hook suppresses traditional search.
- Raw runtime events are operational evidence, not canonical architectural
  knowledge, and must not silently enter the Library SSOT.

Library can consume an explicitly reviewed aggregate report at the slow path:

```text
specgraph telemetry --format md
  -> maintainer review and interpretation
  -> accepted experiment/report artifact
  -> tools.ingest in tare.tools.library
  -> tools.build_manifest
  -> normal incremental Library indexing
```

Only the reviewed report is eligible. The raw JSONL, recovery index, queries,
source excerpts and per-call events are excluded. Library then serves research,
cross-project synthesis and historical comparison; it never becomes a runtime
dependency of `specgraph resolve`.

## Test plan and acceptance criteria

- `HIT` skips fallback.
- `MISS`, `TIMEOUT` and `UNAVAILABLE` admit fallback.
- global and learned `STALE` states remain observable.
- changed evidence is never reused.
- successful fallback becomes reusable evidence.
- source scope, file size, file count, result count and total time are bounded.
- telemetry contains no raw query.
- summary math is deterministic for empty and populated logs.
- default state location is outside the repository.
- Claude, Codex and Antigravity surface the same canonical denial.

## Pending decisions

- Whether aggregate reports should be ingested into Library on a fixed cadence
  or only when an engineering investigation begins.
- Whether multiple machines need a privacy-preserving merge format. There is no
  collector or network transport until that need is measured.
- Which baseline window justifies alert thresholds for miss, stale and timeout
  rates.
