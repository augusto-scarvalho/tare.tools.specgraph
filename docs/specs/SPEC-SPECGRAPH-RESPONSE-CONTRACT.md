---
id: SPEC-SPECGRAPH-RESPONSE-CONTRACT
title: Complete-response contract before comparing context
status: ACCEPTED
version: 1.1.0
owner: tare.tools.specgraph
work_id: SPECGRAPH-RESPONSE-CONTRACT-20260908
acceptance_criteria:
  AC-05: In a separate pilot of four attempts, qualify the SEARCH/REPLACE format only with four complete and correct patches; do not reclassify the JSON ablation or claim a paired gain.
  AC-01: Separate the final response, reasoning and output limit; a truncated response or ambiguous JSON is never a valid fix.
  AC-02: Observe the requested profile in the native Aider transport and validate only the replacement in the target and its falsifiers.
  AC-03: Freeze two cases, two profiles and two rounds with the same context and 2048 tokens; publish all results and a bounded decision.
  AC-04: Preserve the earlier X1, global settings and on-demand hooks; admit the local profile only if all four candidates pass without a cost regression in both rounds.
---

# Complete response as a precondition of measurement

The operator authorized the recommendation to stabilize the contract and measurement before
qualifying the full host cycle. It is a continuation of its own; X1 remains
concluded with a rejection. ADR-SPECGRAPH-012 records the decision.

Two historical cases reused from X1: exact-file-selection and
receipt-write-release. They are not an independent benchmark or blind cases. Same
oracles and sources, isolated copies, simple context limited to 16 KiB in both
arms, the same JSON path/old/new instruction, native Aider ask and an executor
limited to the target. The only configuration difference between profiles is sending
chat_template_kwargs.enable_thinking=false in direct-answer; provider-default
omits that parameter. No server change, model swap or global
parameters. Local model qwen38 already loaded, temperature zero, 2048-token ceiling.

Freeze protocol, scripts, tests, SPEC, tasks, sources, oracles, Aider version
and instance identity before inference. Eight trials (2x2x2), one call
per trial, zero retries, up to 150 s per trial. Alternate the profile order between
rounds. A missing configuration in the transport prevents forwarding the request.
HTTP response limited to 1 MiB; keep the body and hash for offline inspection.
Record bytes of content and reasoning_content, finish_reason, reported
counters and times. Bytes are not token estimates; a missing split of tokens
between fields is UNKNOWN. Cache remains a subset of the input.

Only finish_reason=stop, strict JSON content without duplicate keys, a valid
path/old/new object, a single replacement inside the target and passing falsifiers
may count as success. Valid JSON does not demonstrate that the code is correct.
Browser and analytics guards stay in all arms.

Admit direct-answer only for these local repairs if the four candidates
pass, with complete counters, preserved identity and tokens/total time
no higher than the control in each round. Any failure requires a rejection or an
explicitly incomplete result; do not raise the ceiling, modify the prompt, repeat a
trial or swap a source after observing responses. No conclusion promotes
automatic hooks, retroactively recovers X1 or closes complete host adoption.

Qualification: classification/limit tests, a client serialization probe
without a model, eight native trials and offline recomputation of the receipts. The authority
is a bounded run authorized by the operator; not a Tier 1 attestation.


## Bounded extension after the JSON ablation

The original ablation was concluded and rejected: direct profile 3/4 correct, control
0/4. Markdown fences and incorrect JSON escapes prevented usable responses.
SPEC 1.0 and the original scripts are preserved in
`docs/evidence/2026-09-08-response-contract/ablation/frozen/`.

To complete the response contract, run a separate pilot of four
SEARCH/REPLACE proposals: the same two cases and two rounds, only simple
context and enable_thinking=false, 2048 ceiling, one call and zero retries per trial.
It is not a paired comparison with the eight earlier trials. Accept only one
complete block, exact path, a unique substring inside the target, finish_reason=stop
and passing regressions in all four cases. The code never goes through heuristic
JSON repair or escape interpretation; delimited Markdown framing is
allowed, without outside prose. The same byte, instance and analytics limits
remain. Aider stays in ask mode and the executor applies the patch in the isolated copy:
format qualification does not prove autonomous editing by the host.
