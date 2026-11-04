from html.parser import HTMLParser
import json
from pathlib import Path

import pytest

from specgraph.indexer import index_repository
from specgraph.report import report_html
from specgraph.storage import RepositoryIndex


class InspectHTML(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.scripts = []
        self.script_attrs = []
        self.tags = []
        self.active = False
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag == "script":
            self.active = True
            self.scripts.append("")
            self.script_attrs.append(dict(attrs))

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, text):
        if self.active:
            self.scripts[-1] += text


def fixture(tmp_path, body=None):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src/main.py").write_text(body or "def leaf(): return 1\ndef top(): return leaf()\n", encoding="utf-8")
    state = tmp_path / "state"
    metadata = index_repository(root, state_dir=state)
    return root, state, metadata


def test_html_contains_same_generation_graph_and_source_snapshot(tmp_path):
    """@verifies SPEC-SPECGRAPH-C6
    """
    root, state, metadata = fixture(tmp_path)
    output = report_html(root, state_dir=state)
    parsed = InspectHTML(output)
    data = json.loads(parsed.scripts[0])
    assert data["revision_seq"] == metadata["revision_seq"]
    assert data["content_hash"] == metadata["content_hash"]
    assert len(data["nodes"]) == 2
    assert data["edges"][0]["from"].endswith(":top")
    assert data["edges"][0]["to"].endswith(":leaf")
    assert data["nodes"][0]["source_snippet"] == (root / "src/main.py").read_bytes().decode("utf-8").splitlines(keepends=True)[0]
    assert all("src" not in attrs for attrs in parsed.script_attrs)
    assert not any(tag == "link" and attrs.get("href", "").startswith("http") for tag, attrs in parsed.tags)
    assert "connect-src 'none'" in output
    assert "innerHTML" not in parsed.scripts[1]
    assert "traverse" in parsed.scripts[1]


def test_untrusted_source_cannot_break_out_of_json_script(tmp_path):
    attack = "</script><script>alert('source')</script>"
    root, state, _ = fixture(tmp_path, 'def malicious():\n    """' + attack + '"""\n    return 1\n')
    output = report_html(root, state_dir=state)
    parsed = InspectHTML(output)
    assert len(parsed.scripts) == 2
    assert attack not in output
    data = json.loads(parsed.scripts[0])
    assert attack in data["nodes"][0]["source_snippet"]
    assert "script-src 'sha256-" in output


def test_export_is_source_independent_and_deterministic(tmp_path, monkeypatch):
    root, state, _ = fixture(tmp_path)
    (root / "src/main.py").unlink()
    def forbidden(*args, **kwargs):
        raise AssertionError("Export attempted current source/full graph read")
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(RepositoryIndex, "snapshot_payload", forbidden)
    first = report_html(root, state_dir=state)
    second = report_html(root, state_dir=state)
    assert first == second
    assert "return leaf()" in first


def test_limits_are_visible_and_preserve_complete_source_units(tmp_path):
    root, state, _ = fixture(tmp_path, 'def lengthy():\n    """' + "ação " * 5000 + '"""\n    return 1\ndef extra(): return 2\n')
    output = report_html(root, state_dir=state, max_nodes=1)
    data = json.loads(InspectHTML(output).scripts[0])
    assert len(data["nodes"]) == 1
    assert any(item["reason"] == "max_nodes" for item in data["omitted"])
    bounded = report_html(root, state_dir=state, max_bytes=16000)
    assert len(bounded.encode("utf-8")) <= 16000
    data = json.loads(InspectHTML(bounded).scripts[0])
    assert any(item["reason"] == "source_max_bytes" for item in data["omitted"])
    lengthy = next(node for node in data["nodes"] if node["name"] == "lengthy")
    assert lengthy["source_snippet"] is None


@pytest.mark.parametrize("kwargs", [{"max_nodes": 0}, {"max_edges": 0}, {"max_bytes": 1000}, {"max_hops": 11}])
def test_invalid_export_limits(tmp_path, kwargs):
    root, state, _ = fixture(tmp_path)
    with pytest.raises(ValueError):
        report_html(root, state_dir=state, **kwargs)
