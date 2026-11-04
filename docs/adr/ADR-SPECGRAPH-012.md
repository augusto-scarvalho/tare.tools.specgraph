---
id: ADR-SPECGRAPH-012
title: Qualify the host response before context utility
status: ACCEPTED
date: 2026-09-08
governed_specs: [SPEC-SPECGRAPH-RESPONSE-CONTRACT]
---

# Decision

Five X1 responses hit the cap and produced no valid patch. The local
collector already documents empty content when reasoning_content consumes the budget.
The hypothesis requires measuring the fields, not attributing tokens by character
count or rejecting the scorer in general because of an output-contract failure.

Run a per-request ablation: omit enable_thinking in the control versus
false in the candidate, with the same cases, plain context, prompt and output cap.
Keep bounded raw responses, separate format validity from correctness,
refuse a truncated conclusion and compare real costs. Configuration observed in the
request does not prove the server applied it; the result includes that limitation.

The measurement function is reused by the runners, outside the automatic handler.
The earlier protocol and its rejection stay intact. The new admission, if it
happens, is only of the response profile on local reproductions. Full host
adoption and proof of sustained benefit remain in a separate Work.

The rejected JSON ablation stays as the primary result. An extension of
four proposals uses the SEARCH/REPLACE text format, avoiding the double
representation of code inside JSON. Its criterion is complete correctness in four
reproductions, without a savings comparison between different protocols.
