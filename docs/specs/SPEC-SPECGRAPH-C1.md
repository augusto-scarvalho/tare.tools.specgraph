---
{
  "id": "SPEC-SPECGRAPH-C1",
  "title": "Python identity and structural resolution",
  "status": "ACCEPTED",
  "version": "1.0.0",
  "owner": "tare.tools.specgraph",
  "work_id": "SPECGRAPH-COMPLETION-C1",
  "program": "SPECGRAPH-COMPLETION-20260908",
  "decision_authority": "operator_instruction",
  "delivery_status": "NOT_QUALIFIED",
  "implements": [
    "REQ-SPECGRAPH-C1"
  ],
  "target_symbols": [],
  "acceptance_criteria": {
    "AC-01": "IDs preserve repo/path/lexical scope; homonyms and nested scopes do not merge.",
    "AC-02": "Absolute and relative imports and aliases resolve through the complete module/source root; ambiguity never picks the first basename.",
    "AC-03": "Supported local calls produce the correct dependency; an unused import does not become a function dependency.",
    "AC-04": "Each occurrence/relation declares its origin, range and resolution state; external/dynamic/unknown does not get a fictitious endpoint.",
    "AC-05": "Correct legacy APIs/IDs remain compatible; indexed code outside governed_paths does not become a blocking orphan."
  }
}
---

# SPEC-SPECGRAPH-C1: Python identity and structural resolution

## Intent and scope

Extract references with the native AST, resolve them within the module index, and separate syntactic certainty from a resolved target. DEPENDS_ON_STATIC remains a compatible projection; dynamic coverage is not promised.

## Contract and authority

Contract accepted for implementation by the operator's explicit instruction on 2026-09-08. Accepting the scope does not attest completion or simulate a tripartite deliberation. ADR-001 and ADR-059 remain in force. The criteria are in the frontmatter; delivery state and evidence belong to the Work.

## Dependencies and design

Work: `SPECGRAPH-COMPLETION-C1`. Prerequisites: C0. Decision: [ADR-SPECGRAPH-002](../adr/ADR-SPECGRAPH-002.md).

Target files: `src/specgraph/code_parser.py`, `src/specgraph/graph.py`, `src/specgraph/config.py`, `src/specgraph/resolver.py`, `tests/test_python_resolution.py`. Files that do not exist yet are planned deliverables.

## Verification

Planned qualification command, not an execution receipt:

```bash
python -m pytest tests/test_python_resolution.py tests/test_specgraph_core.py -q
```

Record the source/configuration, the result and the criteria exercised. A declared, an executed and an attested test are distinct facts; partial evidence does not satisfy the whole Work. Contract check: `python tools/check_completion_contracts.py`.

## Limits

No resumption of the frozen harness, no promotion of inference to Tier 1 and no universal latency promise. The qualification reports coverage and limitations.
