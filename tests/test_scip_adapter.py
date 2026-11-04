import copy

import pytest

from specgraph.scip_adapter import import_scip_json


def fixture(root):
    return {"metadata": {"version": 0, "projectRoot": root.as_uri(), "toolInfo": {"name": "fixture", "version": "0.6"}},
            "documents": [{"relativePath": "src/a.ts", "positionEncoding": 2, "language": "typescript",
                           "symbols": [{"symbol": "local 1", "documentation": ["A local declaration"]}],
                           "occurrences": [{"symbol": "local 1", "range": [0, 3, 7], "symbolRoles": 1},
                                           {"symbol": "local 1", "range": [2, 0, 3, 1], "symbolRoles": 0}]}]}


def test_protocol_version_and_column_encoding_are_preserved(tmp_path):
    result = import_scip_json(fixture(tmp_path), tmp_path)
    assert result["protocol_version"] == 0
    assert result["indexer"]["version"] == "0.6"  # Tool release is not protocol version.
    assert result["occurrences"][0]["position_encoding"] == "UTF16CodeUnitOffsetFromLineStart"
    assert result["occurrences"][0]["range"] == [0, 3, 0, 7]
    assert result["occurrences"][0]["is_definition"] is True
    assert result["occurrences"][1]["is_definition"] is False
    assert result["index_mutated"] is False
    assert not list(tmp_path.iterdir())


def test_local_identifiers_remain_document_qualified(tmp_path):
    payload = fixture(tmp_path)
    other = copy.deepcopy(payload["documents"][0])
    other["relativePath"] = "src/b.ts"
    payload["documents"].append(other)
    result = import_scip_json(payload, tmp_path)
    assert {item["document_scope"] for item in result["occurrences"]} == {"src/a.ts", "src/b.ts"}


@pytest.mark.parametrize("path", ["../escape.ts", "/root.ts", "a//b.ts", "C:/foreign.ts", "a/./b.ts", "a\\b.ts"])
def test_noncanonical_paths_are_rejected(tmp_path, path):
    payload = fixture(tmp_path)
    payload["documents"][0]["relativePath"] = path
    with pytest.raises(ValueError, match="relativePath"):
        import_scip_json(payload, tmp_path)


def test_unknown_version_root_and_encoding_are_rejected(tmp_path):
    payload = fixture(tmp_path)
    payload["metadata"]["version"] = "0.6"
    with pytest.raises(ValueError, match="ProtocolVersion"):
        import_scip_json(payload, tmp_path)
    payload = fixture(tmp_path)
    payload["metadata"]["projectRoot"] = tmp_path.parent.as_uri()
    with pytest.raises(ValueError, match="root"):
        import_scip_json(payload, tmp_path)
    payload = fixture(tmp_path)
    del payload["documents"][0]["positionEncoding"]
    with pytest.raises(ValueError, match="positionEncoding"):
        import_scip_json(payload, tmp_path)


def test_budget_and_invalid_ranges_fail_without_partial_import(tmp_path):
    with pytest.raises(ValueError, match="max_occurrences"):
        import_scip_json(fixture(tmp_path), tmp_path, max_occurrences=1)
    payload = fixture(tmp_path)
    payload["documents"][0]["occurrences"][0]["range"] = [1, 7, 2]
    with pytest.raises(ValueError, match="before"):
        import_scip_json(payload, tmp_path)


def test_unsupported_enrichment_is_reported(tmp_path):
    payload = fixture(tmp_path)
    payload["documents"][0]["symbols"][0]["relationships"] = [{"symbol": "external"}]
    payload["externalSymbols"] = [{"symbol": "external"}]
    result = import_scip_json(payload, tmp_path)
    assert result["omitted"] == ["external_symbols", "symbol_relationships"]
