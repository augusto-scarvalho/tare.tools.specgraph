# Optional context deltas (X3)

`ContextPackager.generate_spec_snapshot` and `generate_spec_delta` are explicit
SDK experiments. Each call recomputes the complete current selection; a delta
changes transmission, not indexing cost. Existing text context, reviewer bundles,
automatic hooks and Agent Runtime delivery retain their existing behavior.

```python
from specgraph import ContextPackager

previous = ContextPackager.generate_spec_snapshot(
    "SPEC-EXAMPLE", "/path/to/project", work_id="WORK-EXAMPLE"
)
# After a source edit, while the client still holds this exact context:
change = ContextPackager.generate_spec_delta(
    "SPEC-EXAMPLE", previous["handle"], "/path/to/project",
    work_id="WORK-EXAMPLE"
)
```

Serialize the whole returned packet as UTF-8 JSON with sorted keys,
`ensure_ascii=False`, compact `(',', ':')` separators and `allow_nan=False`.
The default complete response limit is 65,536 bytes; accepted limits are
1,024 through 65,536 bytes. Units are indivisible. Oversized obligations refuse
explicitly rather than being truncated.

The handle contains repository, Work, specification, generation, timestamps and
unit ids/digests. It carries no old source. Its limits are 16 KiB, 128 units and
one hour, with a caller-supplied shorter TTL and injectable clock for checks.
It is an untrusted cache hint, not an authorization or authenticity credential.
There is no server registry, persisted source history or index-retention change.

For a `delta`, the client checks `base_snapshot` against its held snapshot,
removes every `removed` id, replaces complete `upserts`, applies `unit_order`,
and takes the new `snapshot` and `handle`. That reconstructs the fresh full
packet, including changed criteria, verification links, source ranges and removals.
The caller must hold the matching context; the SDK cannot attest to client state.
Filesystem reads are not an atomic transaction against concurrent editors.

Malformed, expired, future-dated or mismatched root/Work/specification handles
return `refresh` with complete current units. A delta at least as large as the
full envelope also refreshes. At an exact byte boundary, added refresh metadata
may displace the optional next handle; all units and provenance stay intact,
and the next request must obtain full context again. Initial selections exceeding
handle limits likewise return complete context with `handle=None`.

Consumers must preserve the model's existing prompt prefix. Applying a delta to
a client data structure does not authorize rewriting old conversation messages.
Only an explicitly matching consumer may append this new context. No automatic
runtime or prompt-cache adoption was enabled by X3.

## Bounded comparison and decision

The operator activated X3 on 2026-10-18. The corpus and eight edit categories
were fixed before measurement: one specification, three criteria, twelve linked
implementations and twelve verifying tests. Each case starts fresh. The full
and incremental paths see the identical edited source; all emitted metadata
and the refreshed handle count toward response bytes.

Windows/Python 3.12.10 observations:

| Edit | Full bytes | Returned bytes | Response |
| --- | ---: | ---: | --- |
| One implementation body | 28,293 | 5,245 | delta |
| One acceptance criterion | 28,279 | 12,188 | delta |
| Removed verifying test | 26,866 | 11,626 | delta |
| Removed implementation | 27,464 | 11,969 | delta |
| Shifted source ranges | 28,278 | 11,742 | delta |
| Removed verification binding | 26,911 | 11,662 | delta |
| Unrelated source edit | 28,273 | 4,637 | delta |
| Broad selected rewrite | 28,306 | 28,324 | full refresh |

All eight reconstructions exactly matched independent full recomputation.
The eight deterministic context tasks recovered the same acceptance criteria,
implementation ASTs/ranges and verifying-test links. A deliberately omitted
changed unit makes the comparison fail immediately. These are context lookup
tasks, not model-generated repairs or adjudicated ordinary Work deliveries.

The median whole-response ratio was **43.19%** of full context, above the
exploratory **20%** target. A raw candidate delta exceeded full context in **1/8
cases (12.5%)**, below the separate 30% rejection threshold; that case refreshed.
The actual refresh was 18 bytes larger because it explains the fallback. The
single localized implementation edit reached 18.54%; it does not substitute
for the aggregate result. The bounded experiment is complete, with a decision
to **reject automatic adoption and keep the complete-context baseline**. The
explicit SDK surface remains experimental; no broader rollout is pending.

No model calls were used to collect comparison data. Bytes are not invoice
tokens or cache savings. Wall times include complete recomputation on both
paths and are descriptive. No broad agent-quality, compute-saving or concurrent
source-snapshot claim follows from these observations.

## Reproduce and retain

```text
python tools/check_completion_contracts.py
python -m pytest tests/test_context_delta.py tests/test_bundle_fidelity.py tools/tests/test_context_delta_qualification.py -q
python tools/qualify_context_delta.py --output /fresh/path/result.json
```

The opt-in runner writes one receipt capped at 64 KiB and removes its temporary
corpus. An existing output is refused. Ordinary offline CI checks the same
corpus/parity and an omitted-edit falsifier, with no operator storage, network
or providers. Timing differences and temporary-root lengths may change byte
counts slightly between hosts; original measured outcomes are retained.

Raw failed boundary reproduction, final receipt, finite TUI implementation
custody and the prespecified plan remain in the operator's private
`_archives/specgraph-x3-20261018`, capped at 64 MiB. After green integration and
installed verification, it is eligible for cleanup after 2026-11-18 when no
recovery depends on it. This maintained guide, the source and the reproducible
checks retain the decision; no ongoing telemetry collector was added.
