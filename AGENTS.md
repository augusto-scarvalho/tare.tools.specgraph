# SpecGraph contributor instructions

SpecGraph owns code/specification analysis, indexed snapshots, bounded retrieval
and its read-only MCP gateway. Ground behavior in `docs/SPEC-SPECGRAPH-001.md`
and the relevant owner contract. The North Star and planned Work items do not
attest delivery. Source bindings and static dependencies are not execution proof.
One exact target may have several real source/criterion bindings. Duplicate
mapping identity is (source_ref, target_kind, target_id, sorted criteria), not
just (kind, target). Reuse the owner's validation; preserve legitimate bindings
when checking or adding mappings.

Historical inventories and their baseline hashes are immutable provenance, not
blanket freezes of later implementation. For each batch, record its initial Git
HEAD and current file hashes, and compare preservation against that batch start.
Use Git history/diffs and the owner's validation to distinguish approved changes
from earlier batches (which are not new violations) from changes in this batch.
Never blindly replace an expected hash with an observed one or erase history.
Explicitly frozen contracts/configuration still require their pinned hashes;
these pins are not superseded by a batch baseline.

The current tactical host is `tare.tools.agent-runtime`. Kernel owns provider
and process ports; OS owns settlement; Library owns its adopted research corpus
and a derived retrieval index. Change each owner in its own repository. The retired
`tare.tools.harness` is frozen and must not be imported, copied or resumed.

Keep tests offline, with synthetic or bounded fixtures. Normal validation must
work without operator storage, private records or old Git objects. Read
`docs/guides/REPOSITORY_CONTENT.md` before handling historical records; preserve
original bytes, hashes and failed outcomes. Live model qualification is opt-in
and requires separate operator authorization.

For material changes, add a concise `CHANGELOG.md` Unreleased entry without
rewriting released entries. Run `python tools/check_completion_contracts.py`
and relevant tests; complete CI runs `python -m pytest tests tools/tests` with
the pinned owner dependencies and native core. Use a reviewed PR for main.

Write code, comments, prompts and documentation in English; agents still reply
in the user's language. The Portuguese version for Brazilian readers lives only
in `README.pt-BR.md`: update it with its English source.
