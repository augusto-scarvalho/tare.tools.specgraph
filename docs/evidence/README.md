# Historical evidence archive

Historical payloads, protocols, receipts and decisions are preserved byte for byte in the pinned Git revision recorded in [ARCHIVE.json](ARCHIVE.json). The manifest records every original path, byte count, Git blob and SHA-256. This removes operational snapshots from the active checkout; it does not rewrite Git history or grant new qualification authority.

Maintained regressions use bounded fixtures under `tests/fixtures/historical`, with exact provenance. X5 campaign inputs live under `tools/fixtures/x5`. Historical rejection decisions remain rejections, and X5 release selection still requires independent C5 attestation.

Ordinary CI validates current code and bounded fixtures without these old Git
objects. Historical replay is a separate explicit operation in the private archive.
With its pinned history available, replay an original offline verifier:

```text
python tools/evidence_archive.py 2026-09-08-x1
```

The helper materializes that directory outside the checkout, verifies its complete inventory and every SHA-256, then runs the unchanged historical verifier. The small parser/helper support set is restored from the same pinned commit and verified independently; current checkout modules cannot silently replace historical imports. It never fetches or calls a model. A shallow clone must explicitly fetch the pinned commit before replay. `--output PATH` materializes without executing the verifier.

Any earlier `docs/evidence/...` reference names an archived path under the manifest's commit. Read individual bytes with `git show COMMIT:PATH`; browse the pinned links in the maintained guides. Original evidence is historical, with the limits recorded at its creation.
