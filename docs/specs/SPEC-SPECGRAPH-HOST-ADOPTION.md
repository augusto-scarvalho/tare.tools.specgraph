---
{
  "id": "SPEC-SPECGRAPH-HOST-ADOPTION",
  "title": "Observed context adoption in real hosts",
  "status": "ACCEPTED",
  "version": "1.3.0",
  "owner": "tare.tools.specgraph",
  "work_id": "SPECGRAPH-FOLLOWUP-HOST-ADOPTION-20260908",
  "decision_authority": "operator_instruction",
  "delivery_status": "QUALIFIED_SCOPED",
  "acceptance_criteria": {
    "AC-01": "Before the authorized native trial, freeze the versions/commits of tare.tools.agent-runtime, Kernel, OS/TUI and SpecGraph, roots per repository, Work and criteria, explicit sources with hashes/ranges, the published snapshot, oracles, arms and verifiable budgets.",
    "AC-02": "Observe the on-demand lookup through the tare.tools.agent-runtime + Kernel + OS TUI path and prove the context in the runtime's real subsequent request to the provider; distinguish catalog/registration, invocation, adapter return and consumption. A fixture or manual callback does not qualify real adoption.",
    "AC-03": "Exercise the native task and run focused tests of resumption, roles/delegation and multiple roots with explicit Work/source/root/snapshot, preserving identity, dedup, limits and Stop without a loop; separate offline tests, real observation and gaps without requiring a Cartesian product of historical hosts.",
    "AC-04": "Run a small paired TUI comparison with and without context, with equivalent task/oracle/model/budgets; record correctness, tokens of all agents including input/cache/response, wall time, retries, interventions and bridge cost. Incomplete counters are unknown, not zero, and prevent a total-savings claim.",
    "AC-05": "Keep context on demand and automatic_roles empty, without automatic promotion; publish native coverage and limitations, preserving the historical Codex/Claude qualification and the four optional experiments. Aider/AGY are historical, not current completion criteria.",
    "AC-06": "The hook service's text lookup uses the shared lexical retriever on the published snapshot, preserves the exact type and identity of results and advisory provenance, without a live source/network/LLM/full scan; explicit selectors, budget and dedup per role/root remain conservative."
  }
}
---

# SPEC-SPECGRAPH-HOST-ADOPTION

The operator authorized moving on from coverage to quality, real hosts and aliases.
This contract bounds the followup; it does not retroactively reopen C7/C8 as incomplete
nor grant admission, attestation or qualification by mere documentary existence.
The canonical Work and its state belong to the OS/Backlog Graph.

## Current state and delivery boundaries

Normative state **ACCEPTED**, delivery **QUALIFIED_SCOPED** for the rescoped
contract. The v1.2.0 candidate remains in Git history. This acceptance does not
grant admission, nor reconcile or change the Work state in the OS/Backlog Graph.

The current path is `tare.tools.agent-runtime` (tactical loop) + Kernel (provider/process
ports and mediated capabilities) + OS (sessions, TUI, Work and settlement).
SpecGraph provides advisory, bounded context over a published snapshot, without
widening authority. Do not import, copy or resume `tare.tools.harness`.

| Boundary | Evidence and reach | Limit |
| --- | --- | --- |
| Windows / TUI / CLI, `explorer` role | Real pair with and without lookup, both correct per the oracle; the lookup reached the Runtime's next real request, with the projection hash rebuilt and the request anchor validated. | One investigation task, not qualification of every role or host. |
| Five roles and two protocols | Exercised in focused offline tests. | Not equivalent to five roles or two protocols observed live. |
| Multi-root, resumption and permissions | Exercised in offline tests, with identity, scope, dedup, budgets and Stop preserved. | Do not qualify these combinations in real sessions. |
| Injected callbacks | Offline integration evidence. | Not real delivery to the provider; automatic context delegation remains out of scope. |
| Aider / AGY and Codex / Claude | Protocols and historical qualification preserved below, with their original limits. | Not current native gates nor a new qualification. |

### Identity, sources and authority

The referenced Work and the conversation's operational binding are distinct identities.
Referencing this Work in a direct conversation does not turn it into a managed Work flow.
The descriptor and its criteria/hash apply only to the managed Work flow;
direct conversations keep their own binding, without implicit admission.

The `NativeProjectContext.lookup` → `specgraph.hook_context.context_hint` bridge
selects repository/root, role, snapshot and budget explicitly, with
cancellation and without presuming automatic callbacks. Role translations must be
explicit, not inferred. The OS Work package keeps the descriptor's criteria,
readable roots and an independent writable scope; it is not equivalent to a SpecGraph snapshot.
Sources selected by the host have identity, repository, path, hash and range;
unresolved refs remain explicit. Selection or return does not prove a read.
A missing, incompatible or stale snapshot, or unchecked coverage, must be declared,
without implicit reindexing; expansion by reading checks the source hash.

### Inspecting the native evidence

Inspect the `outcome` and the `journal` of the run/session in the OS, including
`capability_observations.context`. Correlate the conversation binding and the referenced
Work, session/turn/tool call, repository/root, role, snapshot, query,
status and hash/bytes of the return with the Runtime's real subsequent request. Check
the rebuilt projection and the request anchor in that request, not just catalog,
registration, invocation or adapter return. Inclusion demonstrates delivery for
inference, not understanding by the model; correctness depends on the task oracle.

The legacy `hook doctor` observers diagnose the historical hooks path;
they do not replace the native artifacts nor prove inclusion in the Runtime request.
Injected callbacks and manual offline preflight do not prove it either.

Prompts, detailed counters, pins/commits, hashes and dated run records
stay in private custody linked to the Work in the OS, not in this repository. Preserve
originals and rejected results; the documentation distributes the contract and the limits,
not the operational payloads. The private summary of the pair supports this decision, without
granting authority or admission.

### Adoption and measurement decision

The real pair demonstrated correctness and bounded delivery, **not sustained savings**.
The fixed order, the warm cache and the initial cost of preparing the index prevent
generalization. Distinguish provider-reported usage from admission estimates;
hidden attempts, monetary cost and exclusive attribution of tokens to tools
remain unknown. A missing counter is neither zero nor a complete measurement.

Keep context optional, on demand, **off by default**, with
`automatic_roles: []`. No favorable result promotes automation. Do not promise
65% savings, superiority over Graphify, global adoption or production
qualification. The four optional experiments and the historical Codex/Claude qualification
remain in their previous states and limits, without activation or requalification.

### Contract and integration validation

Reuse `tools/check_completion_contracts.py`, the focused SpecGraph
hooks/context tests, the Kernel native context tests and the OS Work/Runtime/TUI tests,
plus the SpecGraph CI with pinned dependencies and native core. Do not create
a parallel verifier nor run the whole OS suite on the laptop.

Preserve the six criteria, AC-06 literally, the historical protocols and earlier
CHANGELOG entries. Documentary validation does not replace the
integration checks. Model trials remain opt-in, with explicit authorization and
budget; these offline checks do not require repeating the real pair already observed.

## Preserved historical protocols — not current native gates

The historical local Codex/Claude qualification, its limits and original results
are not changed. The Aider/AGY protocols below record the earlier scope;
their budgets and tools are not installation/CI input nor an execution
requirement in this revision. The partial state described at the end is historical, not a
new qualification or backlog reconciliation.

Before the first inference, `tools/qualify_host_adoption.py prepare` fixes a
`protocol.json` and its SHA-256, four tasks with expected answers not sent
to the model, sources selected by full commit and the snapshot hash. An
exact copy of the engine freezes the tested implementation; later alias
changes are outside that observation and are not announced as a qualified binary.

The initial Aider 0.86.2 bridge is `/load` → `/run`: the host itself runs the context
command and incorporates its output into the conversation. The local observer proves the
presence of the package in Aider's real request to the provider, without storing other
parties' prompts or credentials. This is explicit on-demand native adoption, not automatic
PreToolUse delivery. The installed AGY has print/stream-json CLI, resumption,
multi-root and MCP; the availability of these options does not prove a hook or a local route.

Initial budgets of the local Aider pair: one model request per trial, response limited to 512 tokens,
90 seconds per trial, request up to 64 KiB, response up to 1 MiB, SpecGraph context
up to 4096 bytes and lookup up to 1000 ms. Up to two rounds, four roles and two arms
per round. Execution is opt-in via `--execute`; `probe` and `prepare` do not infer.
That pair uses only an identified model already loaded on the authorized local endpoint;
do not change models, configure hosts globally or dispatch Works.

The AGY observation is a separate protocol, authorized by the operator through an already
authenticated CLI, with one invocation and a deadline of 45 seconds in the host/60 in the process.
The CLI does not expose a cap per model call or tokens; this limitation is explicit,
and all reported steps/counters go into the receipt. A read-only file
consumed by a native tool qualifies that adoption mode, without qualifying
automatic callbacks. An explicit selection of a new project creates host conversation
state, but does not change the global hooks or MCP configuration.

Results of bounded tasks or repository copies do not prove production
adoption, native resumption or subagents. In the historical scope, the Work remained
partial while AC-02 and AC-03 were not fully demonstrated; this does not
replace the rescoped decision above nor update the backlog. Do not promise
65% savings, superiority over Graphify or coverage of all hosts.

Decision: [ADR-SPECGRAPH-009](../adr/ADR-SPECGRAPH-009.md).
