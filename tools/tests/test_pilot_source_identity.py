"""Check the source-envelope to native-tool boundary without inference."""
import hashlib
import json

import pytest
from tools.pilot_source_identity import source_identity

MediatedTools = pytest.importorskip(
    "compute_plane.mediated_tools", reason="Kernel integration requires its explicit installation").MediatedTools


def test_source_reference_dispatches_using_selected_alias(tmp_path):
    content = b"# source\n"
    (tmp_path / "code.py").write_bytes(content)
    repositories = {"library": str(tmp_path)}
    ref = source_identity(repositories, repository="library",
        canonical_repository="tare.tools.library", revision="frozen-revision",
        path="code.py", sha256=hashlib.sha256(content).hexdigest())
    # Exercise the serialized packet and real capability, not a fake alias map.
    decoded = json.loads(json.dumps(ref))
    tools = MediatedTools({"repositories": repositories, "role": "explorer", "writable": {}})
    result = tools.perform("read", {"repository": decoded["repository"], "path": decoded["path"]})
    assert result["sha256"] == decoded["sha256"]
    assert decoded["canonical_repository"] == "tare.tools.library"
    assert result["text"] == "1: # source"


def test_canonical_name_cannot_silently_become_a_dispatch_alias(tmp_path):
    with pytest.raises(ValueError, match="source_repository_not_selected"):
        source_identity({"library": str(tmp_path)}, repository="tare.tools.library",
            canonical_repository="tare.tools.library", revision="revision",
            path="code.py", sha256="0" * 64)
