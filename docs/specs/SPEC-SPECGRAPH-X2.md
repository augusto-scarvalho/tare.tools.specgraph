---
{
  "id": "SPEC-SPECGRAPH-X2",
  "title": "Scoped absence and answer coverage",
  "status": "ACCEPTED",
  "version": "1.0.0",
  "owner": "tare.tools.specgraph",
  "work_id": "SPECGRAPH-COMPLETION-X2",
  "program": "SPECGRAPH-COMPLETION-20260908",
  "decision_authority": "operator_instruction",
  "delivery_status": "NOT_QUALIFIED",
  "implements": [
    "REQ-SPECGRAPH-X2"
  ],
  "target_symbols": [],
  "acceptance_criteria": {
    "AC-01": "An empty answer identifies the snapshot, scope, method/constructs, exclusions, unresolved items and budget.",
    "AC-02": "A reference outside the index or an unresolved dispatch produces UNKNOWN/PARTIAL, never a universal proof of absence.",
    "AC-03": "Full syntactic coverage is not confused with semantic completeness; the hook checks the substitute's real coverage."
  }
}
---

# SPEC-SPECGRAPH-X2: Scoped absence and answer coverage

## Intent and scope

Mandatory core contract with an ergonomics experiment; static relations only assert what the supported method observed.

## Contract and authority

Contract accepted for implementation by the operator's explicit instruction on 2026-09-08. Accepting the scope does not attest completion or simulate a tripartite deliberation. ADR-001 and ADR-059 remain in force. The criteria are in the frontmatter; delivery state and evidence belong to the Work.

## Dependencies and design

Work: `SPECGRAPH-COMPLETION-X2`. Prerequisites: C4. Decision: [ADR-SPECGRAPH-003](../adr/ADR-SPECGRAPH-003.md).

Target files: `src/specgraph/storage.py`, `src/specgraph/query.py`, `tests/test_query_coverage.py`. Files that do not exist yet are planned deliverables.

## Verification

Planned qualification command, not an execution receipt:

```bash
python -m pytest tests/test_query_coverage.py -q
```

Record the source/configuration, the result and the criteria exercised. A declared, an executed and an attested test are distinct facts; partial evidence does not satisfy the whole Work. Contract check: `python tools/check_completion_contracts.py`.

## Bounded application of AC-03 on the current path

The original wording of AC-03 in the frontmatter stays intact. On the current
path Agent Runtime → Kernel `NativeProjectContext.lookup` → SpecGraph `context_hint`,
the SpecGraph producer evaluates the facts of the static references actually
delivered, not sufficiency for any question nor completeness of execution.
`substitute_assessment` distinguishes `STATIC_REFERENCES_DELIVERED` from
`RECOVERY_REQUIRED`; the first only means that the selected references were
delivered and resolved by the published method. It does not prove runtime behavior,
universal absence, exhaustive selection for open questions or universal semantic
coverage. HIT and full syntactic coverage do not replace this assessment.

Every role receives references with ranges, state, reason and known target,
plus recovery reasons tied to the symbol and to the reference's index in the
report's snapshot. A truncated selection, omitted references, undelivered units,
partial coverage and an observed dirty state prevent a sufficient conclusion.
The first eight references per symbol are complete units; the rest are
counted. The first eight reasons come with a count and a truncation indicator.
The assessment and the witnesses take part in the digest and the serialized
limit; identity and mandatory criteria are still preserved or refused with
`BUDGET_REQUIRED`. The path stays snapshot-only, with no scans, network or models.

Kernel `NativeProjectContext.lookup` only carries the report and preserves
its identity boundaries and `absence_proven=false`; it does not decide sufficiency.
The historical snapshot-only test keeps its local policy, explicitly identified
as producer/transport proof, not a production decision.

The additional proof uses the real `ExplorationSources.collect("src/lib.py:caller", focus=())`,
with indexed fixtures that are still readable. By default the collector recovers
the selected ranges with a published hash, both for the resolved direct call and
for the unknown callback, without consulting `substitute_assessment`. After a real
change without reindexing, it records `exploration_source_changed` in `missing`,
does not publish the diverging source in `sources`, and `verify` rejects the
previously collected source. This proves default recovery and hash refusal in the
existing module, not a selective gate, automatic elision, savings or a demonstrated
integration of the host/Runtime loop. Expansion is limited to at most four symbols
with a hash, ranges of up to 200 lines, a serialized source budget and cancellation
control; it may deliver no sources.
The lookup stays snapshot-only; recovery is a separate read operation.
The provenance receipt binds each case to the root and to the hashes of the modules
actually loaded, not to the delimiter. The test accepts the two known numbering
contracts (`|` and `: `), always with content/range derived from the source/AST
and bytes checked against the real envelope. Hashes are provenance, not a whitelist
or a skip condition. The default selection depends on the import environment;
`SPECGRAPH_X2_KERNEL_ROOT` allows an explicit selection and refuses a silent mix.
Contested historical receipts remain historical, separated by run and
checkout, without promoting the qualification state.
The `.github/workflows/ci.yml` workflow installs Kernel and Runtime at pinned
revisions, separately from the dependencies declared in pyproject. This configuration
does not attest a successful CI run nor identify the imports of a local check.
The qualification separates the observed local check from the checked external
receipt of explicit reference selection; neither of them demonstrates CI. Private
custody records are not a CI dependency.
The qualification summary records the historical reviews and the most recent
documentary REVISE, plus the selection reported by the host: OpenAI GPT 6.1 Sol high
and an Anthropic auditor. This does not attest internal reasoning nor approval of this
revision; independent re-audit and application remain the host's responsibility.

The historical name `hook_context.py` is kept for compatibility of the API called
by the native consumer, not as a resumption of retired hooks/hosts. No adaptation
of the AC-03 text is needed: the responsibility for assessing the substitute stays
with the SpecGraph owner, separate from the Kernel pass-through.
Historical host/hook evidence is not current proof of this path. The bounded
AC-03 qualification record was removed from the tree (it is kept in Git history);
the whole Work remains `NOT_QUALIFIED`, including unattested dependencies and review.

## Limits

No resumption of the frozen harness, no promotion of inference to Tier 1 and no universal latency promise. The qualification reports coverage and limitations.
