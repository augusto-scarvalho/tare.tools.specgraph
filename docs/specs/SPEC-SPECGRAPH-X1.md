---
{
  "id": "SPEC-SPECGRAPH-X1",
  "title": "Context selection by usefulness and risk",
  "status": "ACCEPTED",
  "version": "1.1.1",
  "owner": "tare.tools.specgraph",
  "work_id": "SPECGRAPH-COMPLETION-X1",
  "program": "SPECGRAPH-COMPLETION-20260908",
  "decision_authority": "operator_instruction",
  "delivery_status": "LOCAL_EXPERIMENT_COMPLETE_SCORER_REJECTED",
  "implements": [
    "REQ-SPECGRAPH-X1"
  ],
  "target_symbols": [],
  "acceptance_criteria": {
    "AC-01": "A deterministic scorer selects complete units under the budget and mandatory constraints, with auditable reasons.",
    "AC-02": "A paired experiment compares a contract+dependencies baseline with the same budget and measures success/total cost.",
    "AC-03": "Without a gain in two rounds, or with an additional warm cost >20ms, reject the scorer and keep the simple selector."
  }
}
---

# SPEC-SPECGRAPH-X1: Context selection by usefulness and risk

## Intent and scope

Exploratory hypothesis based on H-18 and the Aider selective map; a heuristic is not a statistically proven information gain.

## Contract and authority

Experiment proposal; the outcome can be a reasoned rejection. Accepting the scope does not attest completion or simulate a tripartite deliberation. ADR-001 and ADR-059 remain in force. The criteria are in the frontmatter; delivery state and evidence belong to the Work.

## Dependencies and design

Work: `SPECGRAPH-COMPLETION-X1`. Prerequisites: C4. Decision: [ADR-SPECGRAPH-003](../adr/ADR-SPECGRAPH-003.md).

Target files for this round: `tools/context_selection_experiment.py`,
`tools/qualify_context_selection.py`, `tools/aider_headless.py` and their tests.
The production selector remains simple. The exact version frozen before the
measurements is at `docs/evidence/2026-09-08-x1/SPEC-X1-v1.1.frozen.md`.

## Verification

Planned qualification command, not an execution receipt:

```bash
python -m pytest tests/test_context_selection.py -q
```

Record the source/configuration, the result and the criteria exercised. A declared, an executed and an attested test are distinct facts; partial evidence does not satisfy the whole Work. Contract check: `python tools/check_completion_contracts.py`.

## Limits

No resumption of the frozen harness, no promotion of inference to Tier 1 and no universal latency promise. The qualification reports coverage and limitations.


## Authorized continuation protocol

The operator's instruction to pursue X1 together with host adoption admits this
bounded experiment, without automatically widening the hooks. The prototype
selects whole units from a caller-supplied snapshot: identity, criteria and the
target unit are mandatory; neighboring functions, dependencies and tests are
optional. Fixed weights per role, explicit distance and declared risk have
auditable reasons in a receipt kept separate from the model's context. No
inference turns proximity into authority or proof of execution.

Baseline: contract, target and optional units in the declared order. Candidate:
the same units, budget and source, with weights per role and a usefulness
threshold. At most 64 units, 1 MiB of serialized input and a 16 KiB packet; no
live source reads, network or model inside the selector. Mandatory requirements
that exceed the budget refuse the packet; optional units are accepted or
omitted whole. Declared dependencies accompany the selected unit.

Before the first qualification inference, freeze the tasks, criteria, oracles,
sources, snapshot, prototype and runner. Two real historical fixes are
reproduced in isolated copies of the repositories, with two alternating rounds
per arm. Local native Aider proposes an exact replacement; the runner applies
it only to the authorized target file and runs the frozen falsifiers. Oracles
and the historical solution are not given to the model. It is a bounded
implementation and verification cycle; it does not prove resumption/subagents
or autonomous production editing by the host. The four roles have deterministic
tests; the model measurement for this first decision covers the implementer.

Limits: eight trials, one call per trial, up to 2048 response tokens, 150
seconds per trial, no model retry and no human repair of the candidate. Count
reported tokens, host time, emission, verification and preparation/indexing
cost separately. Missing counters prevent any claimed gain. The decision
requires every candidate to pass, without reducing the baseline's successes,
with fewer tokens and no increase in total time in both rounds, and an
additional warm selector cost of 20 ms or less. Otherwise reject this scorer
for adoption and keep the simple selector. Do not change the criteria after
observing responses.

The Work classification records a reasoned acceptance or rejection of the
experiment. The host adoption Work separately keeps every criterion that was
not exercised. Protocol and decision: `docs/evidence/2026-09-08-x1/`.

This round's prototype lives in `tools/context_selection_experiment.py`, with
the falsifiers in `tests/test_context_selection.py`. It stays outside the
automatic handler; the experiment does not change `src/specgraph/context.py`.
