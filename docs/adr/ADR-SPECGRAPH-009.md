---
{
  "id": "ADR-SPECGRAPH-009",
  "title": "Host delivery observed separately from registration and automation",
  "status": "ACCEPTED",
  "date": "2026-09-08",
  "scope": "tare.tools.specgraph",
  "decision_authority": "operator_instruction",
  "tripartite_ratification_claimed": false,
  "governed_specs": ["SPEC-SPECGRAPH-HOST-ADOPTION"]
}
---

# ADR-SPECGRAPH-009

The manual codec demonstrated formatting, but not execution/delivery by the host.
Installation, registration, invoked command, returned package, consumed package and
measured benefit are distinct observations. We will use the bridge the host actually
supports and name its nature explicitly.

In the installed Aider, `/run` can execute a command and add its output to the
messages; `/load` executes that command before `--message-file`. This bridge
allows on-demand context without inventing automatic hooks, modifying the host or
using lint errors as an information channel. Qualification uses per-invocation
configuration, an isolated directory, disabled commits and an HTTP observer on
loopback that forwards the real request to the authorized local model. The observer
records hashes, package delivery, task response and reported counters;
it does not replace the host/model with a fixture or fabricate tokens.

Retrieval in the hooks service shares the lexical mechanism of the published
query. Document results and symbols keep distinct identities;
relevance and declared links do not become proof of execution or authority.
No source search, network or models are added to the synchronous handler.

AGY CLI print/MCP is available, but it is not enough to claim support for hooks or
a local inference route. Without evidence of that combination, record the specific
missing capability and keep the Work partial. Do not change globals or resume the
frozen harness to obtain a demonstration.

A gain can only be announced from pairs with success and comparable total costs.
Unreported cache stays unknown; bytes do not replace tokens. Keep
`automatic_roles: []` while the mechanism adds time or does not demonstrate
a sustained gain. Earlier Codex/Claude runs and four optional experiments keep
their historical scopes.

