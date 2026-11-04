---
{
  "id": "SPEC-SPECGRAPH-C6",
  "title": "TypeScript, Rust and an offline viewer",
  "status": "ACCEPTED",
  "version": "1.0.0",
  "owner": "tare.tools.specgraph",
  "work_id": "SPECGRAPH-COMPLETION-C6",
  "program": "SPECGRAPH-COMPLETION-20260908",
  "decision_authority": "operator_instruction",
  "delivery_status": "NOT_QUALIFIED",
  "implements": [
    "REQ-SPECGRAPH-C6"
  ],
  "target_symbols": [],
  "acceptance_criteria": {
    "AC-01": "TypeScript and Rust have Tree-sitter extractors and a contract per construct/language; the grammar is not presented as full semantic resolution.",
    "AC-02": "A supported installation does not require a C/Rust compiler; missing extras are diagnosed, with no silent fallback that invents coverage.",
    "AC-03": "A single offline HTML file without a CDN provides the map, filters, path/impact and sources, with a budget and safe escaping.",
    "AC-04": "CLI/MCP/SDK/UI represent the same generation and limits; code without a SPEC remains navigable.",
    "AC-05": "SCIP is an optional adapter with an explicit subset/version; no mandatory semantic enrichment enters the hot path."
  }
}
---

# SPEC-SPECGRAPH-C6: TypeScript, Rust and an offline viewer

## Intent and scope

Grammars through wheels and pure extractors; read-only UI, incremental by selection, with bounded datasets and a clickable origin. Completes the ratified phase; not optional for declaring the complete vision.

## Contract and authority

Contract accepted for implementation by the operator's explicit instruction on 2026-09-08. Accepting the scope does not attest completion or simulate a tripartite deliberation. ADR-001 and ADR-059 remain in force. The criteria are in the frontmatter; delivery state and evidence belong to the Work.

## Dependencies and design

Work: `SPECGRAPH-COMPLETION-C6`. Prerequisites: C5. Decision: [ADR-SPECGRAPH-003](../adr/ADR-SPECGRAPH-003.md).

Target files: `src/specgraph/extractors`, `src/specgraph/report.py`, `src/specgraph/scip_adapter.py`, `pyproject.toml`, `tests/test_multilanguage.py`, `tests/test_offline_report.py`. Files that do not exist yet are planned deliverables.

## Verification

Planned qualification command, not an execution receipt:

```bash
python -m pytest tests/test_multilanguage.py tests/test_offline_report.py -q
```

Record the source/configuration, the result and the criteria exercised. A declared, an executed and an attested test are distinct facts; partial evidence does not satisfy the whole Work. Contract check: `python tools/check_completion_contracts.py`.

## Limits

No resumption of the frozen harness, no promotion of inference to Tier 1 and no universal latency promise. The qualification reports coverage and limitations.
