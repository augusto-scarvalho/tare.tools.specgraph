# Repository content and validation

The tree contains code, tests, contracts (specifications and ADRs), the
ontology and usage documentation. Dated operational records (pilot runs,
studies, raw audit conversations, qualification receipts) are not part of the
tree. Earlier evidence directories stay in Git history; their original paths,
byte counts, Git blobs and SHA-256 values are listed in
[docs/evidence/ARCHIVE.json](../evidence/ARCHIVE.json), and the
[evidence README](../evidence/README.md) explains how to read them. These
references preserve provenance; they grant no publication or execution
authority.

`.gitignore` keeps local copies of such records (for example `docs/studies/`
or `docs/archive/`) out of commits. Never rewrite an original receipt to make it
portable, and never overwrite uncommitted work when updating a checkout.

## Source validation

Install the optional dependencies and the Kernel and Agent Runtime revisions
pinned in `.github/workflows/ci.yml`, then run
`python tools/check_completion_contracts.py` and
`python -m pytest tests tools/tests -q`. Ordinary CI exercises current code and
the bounded, byte-identified regression inputs in `tests/fixtures/historical`.
It needs no private storage, historical transcripts or old Git objects. The
native core is built and tested in CI.

Historical replay is a separate, explicit offline operation. With the pinned
commit available, `python tools/evidence_archive.py 2026-09-08-x1` replays an
unchanged verifier; `--help` lists the other accepted group names. The helper
never fetches anything or calls a model. Missing history is not a successful
historical verification, and the original rejections and qualification limits
stay unchanged.

The agent host is `tare.tools.agent-runtime`. Aider and AGY protocols in
historical contracts describe the scopes observed at the time and stay
historical. The pilot and qualification runners in `tools/` are explicit
opt-in experiments, not installation or CI entry points. If one is used
deliberately, `TARE_LOCAL_MODELS` selects the operator's model endpoint; the
default is loopback. Setup downloads no models.
