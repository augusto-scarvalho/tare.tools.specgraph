---
{
  "id": "ADR-SPECGRAPH-013",
  "title": "Qualify application by the native editor before widening hooks",
  "status": "ACCEPTED",
  "date": "2026-09-08",
  "scope": "tare.tools.specgraph",
  "decision_authority": "operator_instruction",
  "governed_specs": ["SPEC-SPECGRAPH-NATIVE-EDIT"],
  "tripartite_ratification_claimed": false
}
---

# ADR-SPECGRAPH-013

The earlier SEARCH/REPLACE contract was qualified with Aider ask and external
application. To observe the next limit, Aider diff receives context through its
native command and applies the block to the copy. The observer keeps the response and
checks scope before delivery; the host's editor produces the final file.

The experimental context uses an exact structural read of the snapshot, in compact
text, compared with the complete file in the same format. This separates the cost
of selection from the cost of large JSON envelopes. Function selection assumes
an explicit location and does not demonstrate the quality of text search.

Each pair uses the same delivery and editing mechanism. No earlier result
is reclassified. No gain or a correctness failure keeps on-demand context
and the Work partial. The implementation lives in qualification tools;
it does not create an agent loop, a fictitious callback or global configuration.
