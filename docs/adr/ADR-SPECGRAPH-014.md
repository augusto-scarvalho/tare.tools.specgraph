# ADR-SPECGRAPH-014 — Measure new repairs before automating context

Status: ACCEPTED — operator instruction, 2026-09-08.
Work: SPECGRAPH-FOLLOWUP-HOST-ADOPTION-20260908.
Contract: [SPEC-SPECGRAPH-NEW-TASK-PILOT](../specs/SPEC-SPECGRAPH-NEW-TASK-PILOT.md).

The earlier study reused already known defects and its initial version
refused valid absolute paths. The offline replay fixed that transport
limit, but brought no new evidence of benefit on real tasks.

Run four calls on two defects still present, with behavioral
oracles defined before inference and a clock around the whole
execution. Reuse `qualify_native_host` to transport/apply edits;
keep a separate protocol, without reinterpreting the eight earlier results.
The execution input is reserved only once to prevent accidental partial
re-execution. Record every failure, missing counter and separate cost.

Do not require the investigator to implement a known solution before measuring:
the baseline must fail the new requirements and pass the preservations.
After the calls, apply regressions to the real product and review the patches.

Consequence: a large function can almost cancel the context reduction. That
result is informative and does not authorize changing the selector after observing the
arms. Four samples from the same module are not enough to enable global hooks,
qualify other roles/hosts or promise superiority over Graphify.
