---
{
  "id": "SPEC-SPECGRAPH-NEW-TASK-PILOT",
  "title": "Local utility on two new hook defects",
  "status": "ACCEPTED",
  "version": "1.0.0",
  "owner": "tare.tools.specgraph",
  "work_id": "SPECGRAPH-FOLLOWUP-HOST-ADOPTION-20260908",
  "decision_authority": "operator_instruction",
  "acceptance_criteria": {
    "AC-01": "Reproduce two defects still present on main a6822b49dcb462775ed3ec22974bb15dab856d6e: dirty_paths identity, and a missing symbol accompanied by a valid SPEC. Pin sources, behavioral oracles, implementation, Work and versions before four calls.",
    "AC-02": "Absolute, relative, dot/parent-component and duplicate dirty_paths of the same identity produce equivalent POSIX relative paths, context and digest; an escape from the root returns a bounded OUTSIDE_SCOPE. Preserve snapshot queries, limits and notification silence.",
    "AC-03": "A missing explicit symbol_id returns MISS even with a valid spec_id or selected files. Preserve a valid HIT, SPEC-only intent, MISS for a nonexistent SPEC and OUTSIDE_SCOPE for a symbol of another file.",
    "AC-04": "Compare full/focused through the real Aider diff with explicit /run, the same instruction/model/cap, one call per cell, zero retries. Measure reported tokens, packet, query, process, verification and an external clock including pre/post checks.",
    "AC-05": "A local gain requires four correct results, fewer tokens and total time no greater for focused in each pair, without drift, retry or intervention. Four correlated runs do not demonstrate a sustained gain or a global cost while the investigator's counters are missing. Keep automatic_roles empty."
  }
}
---

# New-task pilot

Authorized by the operator when accepting the next step after the native delivery.
The defects were found by the investigator by querying the index and reading
the code; the oracles reproduce failures on main before any fix.
The model receives the requirements, but neither the oracle nor a known solution.
This is neither blind selection nor natural-language discovery by the model.

Both defects are in `context_hint`, in the same file. That correlation and
the small difference between the size of the function and of the file reduce the reach
of the study. Do not select other cases to produce larger savings.
Each task uses the same original source, without incorporating the other's fix.

Fixed order: dirty-path full/focused; missing-symbol focused/full. One sample
per cell, four real calls in total, qwen38 already loaded, Aider diff,
2048 output tokens, temperature zero, thinking disabled, zero retries,
150 s per run, request up to 128 KiB, response up to 1 MiB, packet up to 32 KiB,
query up to 1000 ms. A separate offline preflight, without inference or score.
Reuse the editor, browser guard and path validation of the native
qualification. The observer validates, but only Aider writes the fix into the copy.

The external clock starts before the run's checks and stops after the
provider verification and the persistence of the native receipt. Preparation, index
and preflight are separate costs. The investigator's tokens and engineering time
are not available to the meter; claiming global savings is forbidden.
Compare each pair, without estimating tokens from bytes or selectively repeating failures.

After the trials, review the proposals, fix the product and run the two
regression batteries combined. Later changes belong to the product
delivery and never change the frozen result of the comparison.

Decision: [ADR-SPECGRAPH-014](../adr/ADR-SPECGRAPH-014.md).
