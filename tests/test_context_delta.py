"""Offline X3 falsifiers. Wire bytes, not task/model savings, are asserted here."""
import copy
import hashlib
import json

import pytest

from specgraph.context import ContextPackager
from specgraph.graph import CausalTraceabilityMatrix


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def resign(handle):
    handle["snapshot"] = hashlib.sha256(wire({k: v for k, v in handle.items() if k != "snapshot"})).hexdigest()
    return handle


@pytest.fixture
def repo(tmp_path):
    for directory in ("src", "specs", "tests"):
        (tmp_path / directory).mkdir()
    (tmp_path / "specs/A.md").write_text(
        "---\nid: SPEC-A\ntitle: Contract\nstatus: ACCEPTED\nacceptance_criteria:\n"
        "  AC-01: Return café\n---\nIntent: " + "évidence " * 180 + "\n", encoding="utf-8")
    (tmp_path / "specs/B.md").write_text("---\nid: SPEC-B\ntitle: Other\n---\nOther intent\n", encoding="utf-8")
    (tmp_path / "src/a.py").write_text(
        'def first():\n    """@spec SPEC-A"""\n    return "café"\n\n'
        'def second():\n    """@spec SPEC-A"""\n    return 2\n', encoding="utf-8")
    (tmp_path / "tests/test_a.py").write_text(
        'def test_first():\n    """@verifies SPEC-A [AC-01]"""\n    assert True\n', encoding="utf-8")
    return tmp_path


def snapshot(repo, **kwargs):
    return ContextPackager.generate_spec_snapshot("SPEC-A", repo, work_id="WORK-X3", clock=lambda: 1000, **kwargs)


def delta(repo, handle, **kwargs):
    return ContextPackager.generate_spec_delta("SPEC-A", handle, repo, work_id="WORK-X3", clock=lambda: 1000, **kwargs)


def reconstruct(previous, change):
    assert change["kind"] == "delta"
    assert change["base_snapshot"] == previous["snapshot"]["snapshot"]
    units = {u["id"]: u for u in previous["units"]}
    for identity in change["removed"]:
        del units[identity]
    units.update({u["id"]: u for u in change["upserts"]})
    return {"kind": "full", "reason": None, "snapshot": change["snapshot"],
            "handle": change["handle"], "units": [units[k] for k in change["unit_order"]]}


@pytest.mark.parametrize("edit", ["code", "spec", "test_delete", "link_delete", "symbol_delete", "shift", "unrelated"])
def test_fresh_reconstruction_after_edits(repo, edit):
    before = snapshot(repo)
    source = repo / "src/a.py"
    tests = repo / "tests/test_a.py"
    spec = repo / "specs/A.md"
    if edit == "code":
        source.write_text(source.read_text(encoding="utf-8").replace('return "café"', 'return "changed"'), encoding="utf-8")
    elif edit == "spec":
        spec.write_text(spec.read_text(encoding="utf-8").replace("Return café", "Return a revised obligation"), encoding="utf-8")
    elif edit == "test_delete":
        tests.unlink()
    elif edit == "link_delete":
        tests.write_text('def test_first():\n    assert True\n', encoding="utf-8")
    elif edit == "symbol_delete":
        source.write_text(source.read_text(encoding="utf-8").split("def second")[0], encoding="utf-8")
    elif edit == "shift":
        source.write_text("# line shift\n\n" + source.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        (repo / "src/unrelated.py").write_text("value = 5\n", encoding="utf-8")
    current = snapshot(repo)
    change = delta(repo, before["handle"])
    assert reconstruct(before, change) == current
    assert len(wire(change)) < len(wire(current))
    assert before["snapshot"]["generation"] != current["snapshot"]["generation"]
    if edit in ("test_delete", "link_delete", "symbol_delete"):
        assert change["removed"]
    if edit == "unrelated":
        assert not change["upserts"] and not change["removed"]
    if edit == "shift":
        old = next(u for u in before["units"] if u["id"].startswith("code:"))
        new = next(u for u in current["units"] if u["id"] == old["id"])
        assert new["start_line"] == old["start_line"] + 2
        assert new["digest"] != old["digest"]


def test_spec_intent_and_ac_removal_reconstruct(repo):
    before = snapshot(repo)
    path = repo / "specs/A.md"
    text = path.read_text(encoding="utf-8").replace("  AC-01: Return café\n", "")
    path.write_text(text.replace("Intent:", "Revised intent:"), encoding="utf-8")
    change = delta(repo, before["handle"])
    if change["kind"] == "delta":
        assert reconstruct(before, change) == snapshot(repo)
    else:
        assert change["kind"] == "refresh" and change["reason"] == "delta_not_smaller"
        assert change["units"] == snapshot(repo)["units"]
    assert not snapshot(repo)["units"][0]["acceptance_criteria"]


@pytest.mark.parametrize("mutation", [
    "expired", "future", "ttl", "bool", "units_type", "digest", "generation", "snapshot",
    "root", "work", "spec", "oversize", "extra", "list", "none", "cycle", "count",
    "integer", "timestamp_type", "wire_size",
])
def test_bad_handles_refresh_with_complete_context(repo, mutation):
    full = snapshot(repo)
    handle = copy.deepcopy(full["handle"])
    if mutation == "expired":
        handle.update(created=0, expires=1000)
    elif mutation == "future":
        handle.update(created=1001, expires=2000)
    elif mutation == "ttl":
        handle.update(created=0, expires=3601)
    elif mutation == "bool":
        handle["created"] = True
    elif mutation == "units_type":
        handle["units"] = []
    elif mutation in ("digest", "generation", "snapshot"):
        if mutation == "digest":
            handle["units"][next(iter(handle["units"]))] = "not a digest"
        else:
            handle[mutation] = "not a digest"
    elif mutation in ("root", "work", "spec"):
        handle[{"root": "root", "work": "work_id", "spec": "spec_id"}[mutation]] = "OTHER"
    elif mutation == "oversize":
        handle["root"] = "界" * 20000
    elif mutation == "integer":
        handle["created"] = 10 ** 1000
    elif mutation == "timestamp_type":
        handle["created"] = "1000"
    elif mutation == "wire_size":
        handle["units"] = {str(i) + "x" * 400: "0" * 64 for i in range(40)}
        assert len(wire(handle)) > 16384
    elif mutation == "extra":
        handle["old_source"] = "forbidden"
    elif mutation == "list":
        handle = []
    elif mutation == "none":
        handle = None
    elif mutation == "cycle":
        handle["units"] = handle
    else:
        handle["units"] = {str(i): "0" * 64 for i in range(129)}
    # Re-sign structurally plausible corruptions to exercise validation beyond binding hash.
    if mutation not in ("cycle", "list", "none", "snapshot"):
        resign(handle)
    result = delta(repo, handle)
    assert result["kind"] == "refresh"
    assert result["reason"] == "invalid_handle"
    assert result["units"] == full["units"]
    assert result["handle"] == full["handle"]


def test_ttl_boundary_and_injected_clock(repo):
    first = snapshot(repo, ttl_seconds=1)
    for now, expected in ((1000, "delta"), (1000.999, "delta"), (1001, "refresh")):
        result = ContextPackager.generate_spec_delta("SPEC-A", first["handle"], repo,
            work_id="WORK-X3", clock=lambda: now)
        assert result["kind"] == expected
    for ttl in (0, -1, 3601, True, float("nan")):
        with pytest.raises(ValueError, match="ttl_seconds"):
            snapshot(repo, ttl_seconds=ttl)


def test_wrong_actual_root_work_and_spec(repo, tmp_path):
    full = snapshot(repo)
    for field in ("root", "work_id", "spec_id"):
        h = copy.deepcopy(full["handle"])
        h[field] = str(tmp_path / "other") if field == "root" else "OTHER"
        resign(h)
        assert delta(repo, h)["reason"] == "invalid_handle"
    result = ContextPackager.generate_spec_delta("SPEC-B", full["handle"], repo,
        work_id="WORK-X3", clock=lambda: 1000)
    assert result["kind"] == "refresh" and result["snapshot"]["spec_id"] == "SPEC-B"
    for work in ("", " ", None, True):
        with pytest.raises(ValueError, match="work_id"):
            ContextPackager.generate_spec_snapshot("SPEC-A", repo, work_id=work)


def test_huge_delta_refresh_is_full_not_truncated(repo):
    before = snapshot(repo)
    (repo / "specs/A.md").write_text("---\nid: SPEC-A\ntitle: Entirely new\n---\n" + "new intent " * 800, encoding="utf-8")
    (repo / "src/a.py").unlink()
    (repo / "tests/test_a.py").unlink()
    result = delta(repo, before["handle"])
    assert result["kind"] == "refresh" and result["reason"] == "delta_not_smaller"
    assert result["units"] == snapshot(repo)["units"]


def test_utf8_full_wire_boundary_and_exact_content(repo):
    full = snapshot(repo)
    assert full == snapshot(repo)
    size = len(wire(full))
    assert size > len(wire(full).decode("utf-8"))
    assert snapshot(repo, max_bytes=size) == full
    with pytest.raises(ValueError, match="budget"):
        snapshot(repo, max_bytes=size - 1)
    for unit in full["units"]:
        if unit["kind"] == "spec":
            assert unit["content"] == (repo / unit["file"]).read_bytes().decode("utf-8")
        else:
            lines = (repo / unit["file"]).read_bytes().decode("utf-8").splitlines(keepends=True)
            assert unit["content"] == "".join(lines[unit["start_line"] - 1:unit["end_line"]])
        assert unit["digest"] == hashlib.sha256(wire({k: v for k, v in unit.items() if k != "digest"})).hexdigest()


def test_refresh_metadata_cannot_overflow_an_exact_full_budget(repo):
    full = snapshot(repo)
    limit = len(wire(full))
    result = delta(repo, None, max_bytes=limit)
    assert result['kind'] == 'refresh' and result['reason'] == 'invalid_handle'
    assert len(wire(result)) <= limit
    assert result['units'] == full['units']
    assert result['snapshot'] == full['snapshot']


def test_handle_limit_keeps_all_obligations(repo):
    (repo / "specs/A.md").write_text("---\nid: SPEC-A\ntitle: Small\nacceptance_criteria:\n  AC-01: Return\n---\nIntent\n", encoding="utf-8")
    (repo / "src/a.py").write_text("\n".join(
        f'def f{i}():\n    """@spec SPEC-A"""\n    pass\n' for i in range(129)), encoding="utf-8")
    result = snapshot(repo)
    assert result["kind"] == "refresh" and result["reason"] == "handle_limit"
    assert result["handle"] is None and len(result["units"]) == 131
    assert delta(repo, {}) == result


def test_generation_binding_and_chained_deltas(repo):
    before = snapshot(repo)
    h = copy.deepcopy(before["handle"])
    h["generation"] = "0" * 64  # Well-typed, but no longer bound to the snapshot.
    assert delta(repo, h)["kind"] == "refresh"
    h = copy.deepcopy(before["handle"])
    h["units"][next(iter(h["units"]))] = "1" * 64
    assert delta(repo, h)["kind"] == "refresh"
    prior = json.loads(wire(before))
    source = repo / "src/a.py"
    for value in (3, 4, 5):
        source.write_text(source.read_text(encoding="utf-8").replace(f"return {value - 1}", f"return {value}"), encoding="utf-8")
        change = json.loads(wire(delta(repo, prior["handle"])))
        assert change["base_snapshot"] == prior["snapshot"]["snapshot"]
        prior = reconstruct(prior, change)
        assert prior == snapshot(repo)
    assert set(prior["handle"]) == {"version", "root", "work_id", "spec_id", "generation", "created", "expires", "units", "snapshot"}
    assert all(len(d) == 64 for d in prior["handle"]["units"].values())


def test_indivisible_obligation_refuses_instead_of_truncating(repo):
    path = repo / "specs/A.md"
    path.write_text(path.read_text(encoding="utf-8") + "界" * 25000, encoding="utf-8")
    with pytest.raises(ValueError, match="budget"):
        snapshot(repo)
    with pytest.raises(ValueError, match="budget"):
        delta(repo, {})


def test_no_writes_history_or_legacy_changes(repo):
    matrix = CausalTraceabilityMatrix.build(repo)
    legacy = ContextPackager.generate_spec_context(matrix, "SPEC-A", repo)
    reviewer = ContextPackager.generate_reviewer_bundle(matrix, spec_id="SPEC-A", root_dir=repo)
    files = {p.relative_to(repo): (p.read_bytes(), p.stat().st_mtime_ns) for p in repo.rglob("*") if p.is_file()}
    attrs = dict(vars(ContextPackager))
    full = snapshot(repo)
    for _ in range(5):
        change = delta(repo, full["handle"])
        assert reconstruct(full, change) == full
    assert dict(vars(ContextPackager)) == attrs
    assert {p.relative_to(repo): (p.read_bytes(), p.stat().st_mtime_ns) for p in repo.rglob("*") if p.is_file()} == files
    assert not (repo / ".specgraph").exists()
    assert ContextPackager.generate_spec_context(matrix, "SPEC-A", repo) == legacy
    assert ContextPackager.generate_reviewer_bundle(matrix, spec_id="SPEC-A", root_dir=repo) == reviewer
    for text in ("Return café", "Intent:", "def first", "def test_first", "SPEC-A:AC-01"):
        assert text in legacy
