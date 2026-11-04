---
{
  "id": "SPEC-SPECGRAPH-NATIVE-EDIT",
  "title": "Native editing with published context and observed cost",
  "status": "ACCEPTED",
  "version": "1.1.0",
  "owner": "tare.tools.specgraph",
  "work_id": "SPECGRAPH-FOLLOWUP-HOST-ADOPTION-20260908",
  "decision_authority": "operator_instruction",
  "acceptance_criteria": {
    "AC-01": "Freeze sources, implementation, versions, oracles and protocol before inference; use two historical defects in two rounds and two arms, without selecting results or repeating failures.",
    "AC-02": "The installed Aider must execute /run, consume the packet in the real request and apply the edit with its diff editor; the observer validates scope but never writes the fix. Check the produced file, exact identity and regressions.",
    "AC-03": "Compare the complete file and an explicitly selected function in the snapshot, with the same instruction, editor, model and 2048-token ceiling; measure context, query, process, verification, real counters and every attempt.",
    "AC-04": "Admit a gain only with four correct fixes in the focused arm, no loss of correctness, fewer tokens and wall time no greater in each round; missing counters, drift, retry or intervention prevent promotion.",
    "AC-05": "Keep automatic_roles empty and full adoption partial; publication distinguishes on-demand /run, native editing, external validation, callbacks, resumption, subagents and multi-root sessions. Two trial directories do not qualify a multi-root session."
  }
}
---

# Native editing: a slice of the hosts Work

This protocol advances the existing host-adoption Work. The two defects and
oracles are the same as in the earlier qualification; the agent knows the cases and
this sample is neither blind nor a demonstration of sustained gain.

The `full` arm delivers the snapshot file; `focused` delivers only the function
explicitly selected by ID, with lines and the file SHA. Both use the
same compact envelope of root, Work, source, snapshot and path. It is an exact structural
query; it is not an evaluation of natural-language discovery nor a
rehabilitation of the rejected X1 selector. The slice does not promise to solve tasks that
require omitted dependencies. The packet records that limitation.

The host starts with no files added to the conversation. `/load` executes `/run`, which
queries the published index; the content is observed in the request to the provider.
The editable file exists in the isolated copy. Aider itself authorizes and applies
the block via `--yes-always`, with Git, lint, automatic tests, command suggestions
and commits disabled per invocation. The validation before the response is delivered
accepts only an exact replacement inside the authorized function. The accepted
response goes to the host unchanged. No external executor applies the patch.

`--line-endings lf` preserves the bytes of LF sources. The native history limit
is 65536 tokens, above the request limited to 128 KiB, to avoid automatic
summarization inside this slice. Any additional attempt is counted and
blocked by the observer. These options were defined in the offline preflight,
before the first inference, and do not change global settings.

Limits: eight real calls in total, one per trial, zero retries, qwen38 already
loaded, `enable_thinking=false`, temperature zero, 2048 output tokens,
128 KiB request, 1 MiB response, packet up to 32 KiB, query up to 1000 ms,
150 seconds per trial. A separate offline preflight uses a fixed response and the
installed Aider, without inference; it does not take part in the utility results.

The oracle runs externally after the native edit. It also compares the on-disk result
with the permitted replacement and refuses additional changes in the copy.
Token accounting uses only provider values; bytes do not estimate
reasoning tokens. Preparation and indexing costs are kept separate from the pairs.

Decision: [ADR-SPECGRAPH-013](../adr/ADR-SPECGRAPH-013.md).

## Fix after the study, without new inference

The eight v1.0 trials were closed and rejected: four valid proposals
used the copy's absolute path and the literal comparator refused their delivery.
In v1.1 the validation resolves the path against the isolated root and requires the identity
of the single authorized file, including symlink resolution. The text of the
response delivered to the host stays intact. The replacement remains exact and
limited to the function. Version v1.0 and its results are archived.

The fix is exercised by the installed Aider with four captured responses,
in offline replay; only the root of the path is relocated to the new copy.
The SEARCH/REPLACE blocks are compared byte for byte with the originals. The oracle
runs on the file produced by the host. This replay is not new inference,
does not correct the original results and does not demonstrate paired savings.
