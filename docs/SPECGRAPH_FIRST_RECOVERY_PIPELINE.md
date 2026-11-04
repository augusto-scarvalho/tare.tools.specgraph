# SpecGraph-first recovery pipeline

`specgraph resolve` is the canonical repository-search entry point for agents.
It keeps the blocking policy small while ensuring that useful traditional
search work is not discarded.

## Flow

```text
validated learned evidence
  -> current bytes match: HIT
  -> changed or missing: evict

structural SpecGraph lookup (bounded)
  -> HIT: return structural locations
  -> MISS | STALE | TIMEOUT | UNAVAILABLE: bounded literal scan
       -> HIT: return and index validated file pointers
       -> MISS | TIMEOUT | UNAVAILABLE: return the explicit terminal state
```

The final `status` says whether the query produced a result. `primary_status`
and `signals` preserve why recovery ran:

| Signal | Meaning | Automatic action |
|---|---|---|
| `HIT` | Structural or validated learned evidence answered the query | None |
| `MISS` | Structural lookup completed without a match | Run bounded literal recovery |
| `STALE` | The causal cache differs from current repository bytes | Use current-byte lookup; index the validated answer locally |
| `TIMEOUT` | A lookup exhausted its allocated budget | Do not retry it in the same request; spend the remaining budget on recovery |
| `UNAVAILABLE` | A lookup could not run or returned an invalid contract | Use recovery without inventing a structural result |
| `STALE_LEARNED_EVIDENCE` | A learned file hash no longer matches | Evict before reuse, then resolve normally |

## Derived state and telemetry

Both files live outside the repository by default, in a directory keyed by the
SHA-256 of the absolute repository path:

- Windows: `%LOCALAPPDATA%/tare.tools/specgraph/<repository-key>/`
- Linux/macOS: `$XDG_STATE_HOME/tare.tools/specgraph/<repository-key>/`, or
  `~/.local/state/tare.tools/specgraph/<repository-key>/` when unset

`TARE_SPECGRAPH_STATE_DIR` can override the location for hermetic tests and
managed deployments. The runtime never creates files in a target worktree by
default.

- `search-recovery-index.json` contains at most 256 query entries. Each entry
  stores bounded result metadata and a SHA-256 of its source file. A hit is
  returned only after the current file bytes reproduce that digest.
- `search-events.jsonl` is non-authoritative telemetry capped at 1 MiB plus one
  rotated segment. It stores a query digest, byte length, state, timings,
  result count and recovery action. It stores neither the raw query nor source
excerpts.

`specgraph telemetry --format json|yaml|table|md` aggregates the bounded log
into counts, recovery/reuse/staleness rates and p50/p95 latency without
revealing queries. The full event contract and the improvement loop are in
[`SEARCH_RECOVERY_TELEMETRY.md`](SEARCH_RECOVERY_TELEMETRY.md).

Telemetry failure never blocks retrieval. The only automatic mutations are
eviction of stale derived evidence and indexing of a result already observed
from current repository bytes. The pipeline never edits source, specifications,
ADRs, Git history, transcripts, or the authoritative causal graph.

## Ownership

- SpecGraph owns classification, bounded recovery, evidence validation and the
  disposable recovery index.
- Kernel owns the provider-neutral policy and the Claude, Codex and Antigravity
  envelope adapters.
- Reviewed aggregate reports stay in their owner repository, where the Library
  may index them. The Library is not called by the synchronous lookup path.
- Vendor `PostToolUse` payloads are not used. Codex and Claude expose tool
  results there, but Antigravity's documented contract exposes only the call
  and error. Keeping recovery inside `specgraph resolve` gives all providers
  the same behavior without reading mutable conversation transcripts.

## Deliberate limits

Recovery is a literal, read-only scan over configured SpecGraph paths, limited
to 5,000 files, 2 MiB per file, 100 results and a caller-supplied total timeout.
It is not a regex engine, daemon, semantic embedding store, general event bus or
automatic documentation writer.

## Opportunistic hook integration

The qualified `PreToolUse` search gate is the only current blocking use. Other
SpecGraph capabilities may become hook inputs, but only after their own data
contracts are useful without inventing authority or duplicating the agent loop.

| Candidate | Appropriate owner/event | Status |
|---|---|---|
| Neutral `search_paths` for README, changelog and ordinary docs | SpecGraph configuration and `resolve` | Implemented and negatively tested; retrieval does not change governance classification |
| Post-edit staleness/blast-radius notice | Kernel `PostToolUse`, advisory | Implemented through `specgraph staleness --changed-path`; reports stale scope without rebuilding the cache |
| Changed-file Reviewer Context Bundle | Kernel `Stop`, advisory | Held until real spec/code/test mappings make the bundle useful |
| Pre-edit impact preview | Kernel `PreToolUse`, advisory | Implemented through bounded `specgraph impact <path-or-symbol>` over a fresh initialized cache |
| Specification context injection | `specgraph ground` and Agent Runtime ingress | Implemented as a bounded projection plus a vendor-neutral ingress contract; not a vendor hook |
| Drift/orphan enforcement | CI or Git pre-commit | Not a vendor hook and must not make interactive agents bureaucratically fail |

### Neutral search-path prerequisite

The first Kernel dogfood placed ordinary documentation in `spec_paths` and
`adr_paths` so literal recovery could see it. That made retrieval work, but it
also caused the causal parser to count README, changelog and general docs as
formal governance artifacts. `doctor` consequently reported six invalid
criteria, 121 orphan symbols and zero coverage. This is a configuration-model
gap, not evidence that governance checks should be weakened.

The bounded `search_paths` field now:

- participate only in structural/literal retrieval discovery;
- remain invisible to spec, ADR, schema, API and coverage classification;
- reuse root confinement, exclusion and deduplication rules;
- preserve behavior for existing configuration files; and
- receive negative tests for escaping paths and classification leakage.

The Kernel dogfood can now be corrected and qualified before manifests are
copied to any additional repository. This slice still does not add automatic
cache sync, source edits, transcript parsing, or raw Library ingestion.

The exploratory `check-stale` value of `11.4 ms` on 2026-08-25 was measured
inside a remote PC process while the operator connected through 5G, Tailscale
and SSH. It is not a latency baseline and must not drive timeout tuning.
