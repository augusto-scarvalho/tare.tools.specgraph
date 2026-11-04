"""Source metadata for context pilots; dispatch identity is explicitly selected.

This constructs metadata only. Callers still pin and verify source bytes and
revisions; the canonical repository name does not grant tool authority.
"""


def source_identity(repositories, *, repository, canonical_repository, revision, path, sha256):
    if repository not in repositories:
        raise ValueError("source_repository_not_selected")
    return {
        "repository": repository,
        "canonical_repository": canonical_repository,
        "revision": revision,
        "path": path,
        "sha256": sha256,
    }
