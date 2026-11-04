---
{
  "id": "SPEC-SPECGRAPH-X5",
  "title": "Candidate tests and proof debt",
  "status": "PROPOSED",
  "version": "1.0.0",
  "owner": "tare.tools.specgraph",
  "work_id": "SPECGRAPH-COMPLETION-X5",
  "program": "SPECGRAPH-COMPLETION-20260908",
  "decision_authority": "operator_instruction",
  "delivery_status": "NOT_QUALIFIED",
  "implements": [
    "REQ-SPECGRAPH-X5"
  ],
  "target_symbols": [],
  "acceptance_criteria": {
    "AC-01": "Selection distinguishes a static reference from executed/attested coverage and makes blocks without proof explicit.",
    "AC-02": "Known mutations compare the selection with the full suite; omitting a relevant test suspends selective qualification.",
    "AC-03": "Incomplete coverage/config/env requires a fallback; a heuristic set is not a global minimum and does not waive mandatory tests.",
    "AC-04": "Measure the capture and execution cost; exploratory target of a median <=30% of the suite time without reducing correctness."
  }
}
---

# SPEC-SPECGRAPH-X5: Candidate tests and proof debt

## Intent and scope

Dependencies per block inspired by Testmon; the executor owns execution and attestation. SpecGraph proposes a small experiment with reasons.

## Contract and authority

Experiment proposal; the outcome can be a reasoned rejection. Accepting the scope does not attest completion or simulate a tripartite deliberation. ADR-001 and ADR-059 remain in force. The criteria are in the frontmatter; delivery state and evidence belong to the Work.

## Dependencies and design

Work: `SPECGRAPH-COMPLETION-X5`. Prerequisites: C5. Decision: [ADR-SPECGRAPH-004](../adr/ADR-SPECGRAPH-004.md).

Target files: `src/specgraph/evidence.py`, `tests/test_test_selection.py`. Files that do not exist yet are planned deliverables.

## Using the experiment

### Bounded operational observation

`recommend_from_receipt` also returns `applicability`: `CANDIDATE` only means
that none of the verified structural invalidations was found;
`FALLBACK` reports their codes. The field always keeps
`execution_authorized: false`, `supports_success: false` and the transitive
closure `UNKNOWN`. An earlier capture can guide the experiment before the new
target runs; it does not approve that target and does not replace C5. The main
result stays `FULL_SUITE` without the C5 composition already required.

The pytest hook observes the same requested command, without running a second
suite. It records the inventory, failures per nodeid and setup/call/teardown times.
The ceiling is 1 MiB of JSON, 4,096 nodeids and 32,768 test/block pairs. When it is
exceeded, the hook stops accumulating and emits `truncated: true`, `complete: false`,
with a reason; this does not change the pytest result and is not a valid capture. A
green output does not show that the selection omitted nothing.

The consuming executor remains responsible for the opt-in, the original command,
custody, deadlines and disposal of bulky data, full costs, the summary for
review, and the decision. Observation mode does not widen authority, is not an
additional real delivery and does not demonstrate net operational savings.

In the repository directory (PowerShell; local source instead of an old installation):

Capture and comparison require pytest in the project's Python (`pip install -e ".[dev]"`
in this repository). The recommendation still returns a fallback when pytest is not installed.

```powershell
$env:PYTHONPATH = 'src'
$receipt = Join-Path $env:TEMP 'x5-local-receipt.json'
python -m specgraph.cli capture-tests $receipt
python -m specgraph.cli recommend-from-trace $receipt src/specgraph/evidence.py:132 --mandatory tests/test_evidence_lineage.py::test_junit_hash_and_self_report_without_external_verifier_are_insufficient
python -m specgraph.cli compare-tests $receipt tools/fixtures/x5/x5-mutations.json (Join-Path $env:TEMP 'x5-comparison.json')
```

The capture uses `pytest tests tools/tests` by default (or the repeatable `--test-path`), a collection hook for nodeids and a protocol hook for Python lines in setup/call/teardown, with failures taken from reports; it does not parse stdout. The JSON records hashes of the local Python/config/lock files, the Python/pytest environment, commands/times and the lines executed per test. `recommend-from-trace` accepts paths or `path:line`; it returns candidates with reasons, blocks without proof, mandatory tests, a runnable full-suite command and fallback reasons. The changed paths must list **all** changes; unlisted changes, config, env, a line without execution and unknown dependencies require the full suite. `recommend-tests` with static impact remains available, but a manual `complete:true` never enables selective execution. Static references are distinct from local observations, which are not attested C5 evidence: `evaluate_evidence` requires an external verifier and an attestation; X5 does not sign and does not promote Tier 1. Even `dependency_closure: EXTERNALLY_VERIFIED` in the JSON is untrusted text. Imports during collection, shared fixtures, execution in threads/subprocesses, native code, non-Python data and transitive dependencies have no guaranteed closure; never infer a global minimum.

The comparator applies each mutation `{name,path,find,replace}` (unique find) in a temporary copy; it runs the candidate nodeids **separately** from the full-suite targets, checks that the imported package comes from the mutant worktree, collects real failures per nodeid and suspends qualification if the full suite fails outside the selection. It measures capture, selection and runs; the 30% target is exploratory and can be rejected. Do not run the selection as a substitute for the full suite without independent attestation/closure. Results and limits are in `docs/evidence/x5-experiment.md` (archived; see `docs/evidence/ARCHIVE.json`).

## Verification

Planned qualification command, not an execution receipt:

```bash
python -m pytest tests/test_test_selection.py -q
```

Record the source/configuration, the result and the criteria exercised. A declared, an executed and an attested test are distinct facts; partial evidence does not satisfy the whole Work. Contract check: `python tools/check_completion_contracts.py`.

## Limits

No resumption of the frozen harness, no promotion of inference to Tier 1 and no universal latency promise. The qualification reports coverage and limitations.

## C5 composition under the executor's custody

The current Python API is `build_x5_scope(root, changed, receipt, selected, mandatory)` and
`recommend_from_receipt(changed, receipt, root=root, mandatory=mandatory,
c5_evidence=record, c5_current=current, attestation_verifier=verifier)`.
The set is the union of the local trace, static impact and mandatory tests; it is not a global minimum.
The claim contains only `x5_scope_sha256`, the SHA-256 of the scope's canonical JSON
(`sort_keys=True`, `ensure_ascii=False`, `separators=(',', ':')`, `allow_nan=False`),
never an inline scope. The binding covers the capture, project, target, lines, selection and mandatory tests.

Only a trusted callable in a Python composition, under the executor's custody,
can verify the attestation. Strings or JSON saying VERIFIED/complete grant no
authority. The executor observes the target's real run, checks the Kernel receipt,
the exit code and the cleanup of the contained process, and binds the exact subject
to the observed artifacts. The verifier must reject any other claim or state: returning
VERIFIED unconditionally is not enough. The CLI stays FULL_SUITE by default.

C5 requires qualified EXECUTED_COVERAGE, a snapshot whose repository equals
`root.resolve().as_posix()`, the contract's source/criterion, and complete config,
environment and dependencies with hashes of real files. The test inventory and the
symbols/lines must come from the target's executed capture; check the package origin,
the file correspondence and the line limits. `c5_current` must reread the filesystem,
configuration, `_environment()` and artifacts, not copy the record. Recheck everything
immediately before running the selected command. Missing proof, a changed
artifact/target, a coverage gap or a binding divergence requires FULL_SUITE.

`tests/test_x5_executor_integration.py` bounds the proof to a known temporary pure-Python
fixture with four tests: a runtime call, a collection-time call
(reached statically), an independent mandatory test and an unrelated one. The baseline
capture, the equivalent mutation and the target's real capture are distinct facts. This
qualification of the mechanism does not prove the dependency closure of arbitrary projects nor adoption.

## Current technical decision: 2026-10-14

Reject broad adoption of the strategy in this cycle. Keep the recommendation and the
explicit scope gate, with the full suite whenever proof is missing. Do not call this
operational savings nor completion of the Work. The 2026-10-01 and 2026-10-08 deadlines were
historical; the decision is late and must not be backdated.

The campaigns of
[09-28](https://github.com/augusto-scarvalho/tare.tools.specgraph/actions/runs/36455921004)
and [10-12](https://github.com/augusto-scarvalho/tare.tools.specgraph/actions/runs/38290061006)
use the same 26 real commits across 16 modules and three controls, with zero confirmed
omissions for the combined Windows/Linux policy. Repeating the cases does not increase
coverage. Full costs per delivery still do not demonstrate a gain; the cost
of the qualification itself exceeded the comparison suites.

## Cost and explicit campaign

Separate observed cost from projection: isolated selective time is not savings per
delivery. Use `tools.x5_campaign.delivery_cost` to account for capture, selection,
execution, recapture, invalidation, retry, correction and audit; missing data produce
UNKNOWN, not zero and not a presumed gain. A comparison requires a comparable baseline.

Normal CI, including the weekly run, and release selection remain intact. Only the
X5 campaign is opt-in: `workflow_dispatch` with the boolean input `x5_campaign`, default
false. Running it and preserving the X5 artifacts require that event and input true;
the 150-minute timeout applies only in that case, 15 minutes otherwise. Explicit request:

```bash
gh workflow run ci.yml -f x5_campaign=true
```

Negative results remain in the artifacts. Repeating this 29-case campaign
measures stability, without widening the coverage of distinct changes.
