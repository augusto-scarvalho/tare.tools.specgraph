# Explain a selected architectural dependency

The Runtime's existing AST boundary test already enforces the ADR-057 import
boundary. A general witness framework is unnecessary for that protection.
X4 adds one optional diagnostic function over SpecGraph's existing directed,
bounded path query: a concrete `file:line -> dependency -> selected rule`.
It adds no traversal engine, policy registry, layer language, solver or execution
gate. Existing queries and the Runtime's enforcement remain unchanged.

```python
from specgraph import architecture_witness

result = architecture_witness(
    "/path/to/indexed/project",
    "src/core.py:execute", "src/adapters.py:settle",
    contract_id="ADR-057:runtime-core-isolation",
    adr_ref="tare.tools.os:docs/adr/ADR-057_AGENT_RUNTIME_BOUNDED_CONTEXT_AND_REPOSITORY.md",
    state_dir="/path/to/explicit/index-state",
    max_hops=5,
)
```

The caller explicitly chooses a forbidden source-to-target dependency and
supplies the contract and ADR references. Those strings are bounded, opaque
references; the function neither opens nor authenticates them. This is a
conditional explanation under the selected rule, not a new policy decision.

`architecture_witness.status` is `SUPPORTED` only when the existing outgoing
`DEPENDS_ON_STATIC` path has complete supported provenance: resolved Python AST
edges, materialized Python definitions, valid definition/reference ranges and
the matching published source/snapshot identity. The path uses the existing
BFS. Minimality is scoped to that relation/direction and complete traversal
evidence; bounded omissions cannot establish a global shortest path.

The complete path stays in `results`: exact symbol ids, definition files/ranges,
source hashes, reference lines/UTF-8 columns and resolution origins/statuses.
A reference line locates the static use, not necessarily the import statement.
The diagnostic preserves published-observation freshness; it does not read or
attest to the current worktree or runtime behavior.

No path, unresolved/ambiguous selectors, legacy/inferred edges, absent source
provenance, partial extraction or traversal cutoffs produce `INCONCLUSIVE`,
never a declaration that the architecture is clean. Existing outer query status
is retained; the nested witness status is the diagnostic result.

All results are advisory (`authority=NONE`). There are no execution permits,
strict blocks, automatic graph cuts or claims that a suggested cut preserves
behavior. No repair is simulated or applied.

## Whole-response limits

The function accepts the existing query budgets: 1..1,000 nodes, 0..10 hops and
1,024..65,536 UTF-8 bytes (default 8,192). The entire response, including the
contract annotation, uses `serialize_query` and its final newline. A complete
chain or required metadata that cannot fit raises `ValueError`; no partial chain
is presented as a full explanation. Contract/ADR references are nonempty, at most
256 UTF-8 bytes each, with no control characters.

The diagnostic reads the existing SQLite snapshot, checks that its second
provenance read identifies the same publication, and performs no source scan,
graph rebuild, current source/ADR read, model/network call or write. It does not
expand K=1 index retention or create persistent diagnostic history.

## Real contract and bounded qualification

The real contract is OS
[ADR-057](https://github.com/augusto-scarvalho/tare.tools.os/blob/258ee22d1a915b6fda09ff941e369e6d48d98dc4/docs/adr/ADR-057_AGENT_RUNTIME_BOUNDED_CONTEXT_AND_REPOSITORY.md):
the Runtime core consumes injected ports and must not import private OS/Kernel
implementations. The existing enforcement oracle is
[the Runtime boundary test](https://github.com/augusto-scarvalho/tare.tools.agent-runtime/blob/612bcd0197cfced92026ace2d84a25f6727f14ad/tests/test_boundaries.py)
at the actual selected satellite revision. Its seven boundary tests passed
without changing owner source.

The operator activated this optional X4 experiment on 2026-10-19. Ten fixed
offline cases compared the ordinary path output with the annotated diagnostic
over the identical snapshot and rule. These were seeded fixtures, not current
ecosystem architectural defects.

| Case | Ordinary path | Witness |
| --- | --- | --- |
| Direct forbidden dependency | HIT | SUPPORTED, one edge |
| Transitive, with longer alternative and cycle | HIT | SUPPORTED, shortest two-edge path |
| No supported path | UNKNOWN | INCONCLUSIVE |
| Ambiguous target | AMBIGUOUS | INCONCLUSIVE |
| Unresolved external target | UNKNOWN | INCONCLUSIVE |
| Legacy/inferred edge | HIT | INCONCLUSIVE |
| Hop cutoff | UNKNOWN | INCONCLUSIVE |
| Missing/invalid source provenance | HIT | INCONCLUSIVE |
| Whole response cannot fit | REFUSED | REFUSED |
| Forbidden import without symbol use | UNKNOWN | INCONCLUSIVE; existing import oracle detects it |

The observed value is explicit contract/ADR context plus conservative provenance
adjudication: two ordinary `HIT` paths with unsupported provenance are not
promoted to supported findings. Both supported cases retain the path oracle's
exact chain and source locations. The annotation added 611 serialized bytes to
each supported sample; no context-byte or quota saving is claimed. Human
decision time, model-delivery quality and production incident reduction were
not measured.

The unused-import case is an important limit: SpecGraph indexes used symbol
dependencies, while the existing AST guard also detects unused forbidden
imports. Keep that test as the enforcement oracle. The experiment supports
only the small opt-in explanation; broader architecture automation is rejected.
There is no hidden requirement to build a solver or run a paid benchmark.

Reproduce the supported/negative cases with:

```text
python tools/check_completion_contracts.py
python -m pytest tests/test_architecture_contracts.py tests/test_project_queries.py tests/test_query_coverage.py tools/tests/test_completion_contracts.py -q
```

The raw ten-case observation, pinned contract hashes, finite public-TUI native
custody and failed probe outcomes remain in the operator's private
`_archives/specgraph-x4-20261019`, capped at 64 MiB. Temporary corpora are removed
or retained explicitly as failed evidence. After green integration/installed
verification, private records are eligible for cleanup after 2026-11-19 when no
recovery depends on them. This maintained guide, code and offline regression
cases retain the decision; no collector or new database is installed.
