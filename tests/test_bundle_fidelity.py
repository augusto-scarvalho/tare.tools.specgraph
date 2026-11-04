"""Bounded falsifiers for existing context projections, not an audit engine."""

import json

import pytest

from specgraph.cli import main
from specgraph.context import ContextPackager
from specgraph.graph import CausalTraceabilityMatrix


@pytest.fixture
def project(tmp_path):
    for directory in ("src", "specs", "tests"):
        (tmp_path / directory).mkdir()
    for name in ("A", "B"):
        (tmp_path / "specs" / f"{name}.md").write_text(
            f"---\nid: SPEC-{name}\ntitle: Contract {name}\nstatus: ACCEPTED\n"
            "acceptance_criteria:\n  AC-01: Return the declared value\n---\n",
            encoding="utf-8",
        )
    (tmp_path / "src/auth.py").write_text(
        'def first():\n    """@spec SPEC-A"""\n    return 1\n\n'
        'def second():\n    """@spec SPEC-B"""\n    return 2\n',
        encoding="utf-8",
    )
    (tmp_path / "tests/test_auth.py").write_text(
        'def test_first():\n    """@verifies SPEC-A [AC-01]"""\n    assert True\n\n'
        'def test_second():\n    """@verifies SPEC-B [AC-01]"""\n    assert True\n',
        encoding="utf-8",
    )
    return tmp_path, CausalTraceabilityMatrix.build(tmp_path)


def render(project, **kwargs):
    root, matrix = project
    return ContextPackager.generate_reviewer_bundle(matrix, root_dir=root, **kwargs)


def test_bundle_preserves_each_test_spec_and_criterion(project):
    bundle = render(project, files=["src/auth.py"])
    lines = [line for line in bundle.splitlines() if " -> verifies " in line]
    assert len(lines) == 2
    assert "test_first" in lines[0] and "`SPEC-A:AC-01`" in lines[0]
    assert "test_second" in lines[1] and "`SPEC-B:AC-01`" in lines[1]


def test_file_selection_is_exact_not_substring(project):
    bundle = render(project, files=["auth.py"])
    assert "SPEC-A" not in bundle and "SPEC-B" not in bundle


def test_normalized_paths_and_edge_order_have_identical_output(project):
    first = render(project, files=["./src/auth.py", "src/auth.py"])
    project[1].edges.reverse()
    assert render(project, files=["src\\auth.py"]) == first


def test_bundle_deduplicates_shared_code_and_keeps_test_relations(project):
    matrix = project[1]
    matrix.edges.append({"from": "src/auth.py:first", "to": "SPEC-B", "type": "CODE_IMPLEMENTS_SPEC"})
    matrix.edges.append({"from": "tests/test_auth.py:test_first", "to": "SPEC-B", "type": "TEST_VERIFIES_SPEC"})
    bundle = render(project, files=["src/auth.py"])
    assert bundle.count("def first():") == 1
    assert "-> verifies `SPEC-A:AC-01`" in bundle
    assert "-> verifies `SPEC-B`" in bundle


def test_bundle_names_existing_content_generation(project):
    root, matrix = project
    bundle = render(project, spec_id="SPEC-A")
    assert f"**Generation Digest:** `{matrix.canonical_digest()}`" in bundle
    assert str(root.resolve()) in bundle


def test_bundle_rejects_unknown_spec(project):
    with pytest.raises(ValueError, match="not found"):
        render(project, spec_id="SPEC-MISSING")


def test_bundle_rejects_repository_escape(project):
    with pytest.raises(ValueError, match="escapes"):
        render(project, files=["../src/auth.py"])


def test_bundle_rejects_mixed_selectors(project):
    with pytest.raises(ValueError, match="either"):
        render(project, spec_id="SPEC-A", files=["src/auth.py"])


def test_bundle_default_limit_refuses_oversize_without_truncating(project):
    project[1].specs["SPEC-A"].acceptance_criteria["AC-02"] = "á" * 10_000
    with pytest.raises(ValueError, match="budget"):
        render(project, spec_id="SPEC-A")


def test_bundle_utf8_exact_boundary(project):
    project[1].specs["SPEC-A"].acceptance_criteria["AC-02"] = "á" * 1_000
    full = render(project, spec_id="SPEC-A", max_bytes=65536)
    size = len(full.encode("utf-8"))
    assert render(project, spec_id="SPEC-A", max_bytes=size) == full
    with pytest.raises(ValueError, match="budget"):
        render(project, spec_id="SPEC-A", max_bytes=size - 1)


@pytest.mark.parametrize("limit", [0, 1023, 65537])
def test_bundle_rejects_invalid_budget(project, limit):
    with pytest.raises(ValueError, match="max_bytes"):
        render(project, spec_id="SPEC-A", max_bytes=limit)


def test_cli_unknown_spec_fails_without_success_payload(project, capsys):
    assert main(["--root", str(project[0]), "bundle", "--spec", "SPEC-MISSING"]) == 1
    captured = capsys.readouterr()
    assert not captured.out and "not found" in captured.err


@pytest.mark.parametrize("fmt", ["md", "json"])
def test_cli_serialized_budget_includes_wrapper(project, capsys, fmt):
    root = project[0]
    spec = root / "specs/A.md"
    spec.write_text(spec.read_text(encoding="utf-8").replace("Return the declared value", "á" * 512), encoding="utf-8")
    args = ["--root", str(root), "bundle", "--spec", "SPEC-A", "--format", fmt]
    assert main(args + ["--max-bytes", "65536"]) == 0
    payload = capsys.readouterr().out
    size = len(payload.encode("utf-8"))
    assert size > 1024
    assert main(args + ["--max-bytes", str(size)]) == 0
    assert capsys.readouterr().out == payload
    assert main(args + ["--max-bytes", str(size - 1)]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "budget" in captured.err
    if fmt == "json":
        assert "Generation Digest" in json.loads(payload)["bundle"]


def context(project, **kwargs):
    root, matrix = project
    return ContextPackager.generate_spec_context(matrix, "SPEC-A", root_dir=root, **kwargs)


def test_context_preserves_intent_and_declared_criterion_links(project):
    matrix = project[1]
    matrix.specs["SPEC-A"].body = "Keep the exact normative intent."
    matrix.specs["SPEC-A"].acceptance_criteria["AC-02"] = "Second criterion"
    matrix.edges.append({"from": "tests/test_auth.py:test_first", "to": "SPEC-A", "type": "TEST_VERIFIES_SPEC", "verified_criterion": "SPEC-A:AC-02"})
    output = context(project)
    assert "Keep the exact normative intent." in output
    assert "Second criterion" in output
    assert "SPEC-A:AC-01" in output and "SPEC-A:AC-02" in output
    assert output.count("def test_first():") == 1
    assert "def test_second():" not in output


def test_context_names_root_and_existing_generation(project):
    output = context(project)
    assert f"**Generation Digest:** `{project[1].canonical_digest()}`" in output
    assert str(project[0]) in output


def test_context_order_and_duplicates_do_not_change_rendered_body(project):
    matrix = project[1]
    matrix.edges.extend(list(matrix.edges))
    matrix.edges.reverse()
    output = context(project)
    assert output.count("def first():") == 1
    assert output.count("def test_first():") == 1
    matrix.edges.reverse()
    assert context(project) == output


def test_context_refuses_default_oversize(project):
    project[1].specs["SPEC-A"].body = "á" * 10_000
    with pytest.raises(ValueError, match="budget"):
        context(project)


def test_context_exact_utf8_boundary(project):
    project[1].specs["SPEC-A"].body = "á" * 1_000
    output = context(project, max_bytes=65536)
    size = len(output.encode("utf-8"))
    assert context(project, max_bytes=size) == output
    with pytest.raises(ValueError, match="budget"):
        context(project, max_bytes=size - 1)


@pytest.mark.parametrize("limit", [0, 1023, 65537])
def test_context_rejects_invalid_budget(project, limit):
    with pytest.raises(ValueError, match="max_bytes"):
        context(project, max_bytes=limit)


def test_context_unknown_spec_is_cli_failure(project, capsys):
    with pytest.raises(ValueError, match="not found"):
        ContextPackager.generate_spec_context(project[1], "SPEC-MISSING", root_dir=project[0])
    assert main(["--root", str(project[0]), "context", "SPEC-MISSING"]) == 1
    captured = capsys.readouterr()
    assert not captured.out and "not found" in captured.err


def test_context_cli_counts_final_lf(project, capsys):
    root = project[0]
    spec = root / "specs/A.md"
    spec.write_text(spec.read_text(encoding="utf-8") + "á" * 512, encoding="utf-8")
    args = ["--root", str(root), "context", "SPEC-A"]
    assert main(args + ["--max-bytes", "65536"]) == 0
    output = capsys.readouterr().out
    size = len(output.encode("utf-8"))
    assert size > 1024
    assert main(args + ["--max-bytes", str(size)]) == 0
    assert capsys.readouterr().out == output
    assert main(args + ["--max-bytes", str(size - 1)]) == 1
    captured = capsys.readouterr()
    assert not captured.out and "budget" in captured.err


@pytest.mark.parametrize("renderer", ["context", "bundle"])
@pytest.mark.parametrize("failure", ["missing", "invalid_utf8", "escape", "wrong_root"])
def test_projections_refuse_unusable_sources(project, renderer, failure):
    root, matrix = project
    source = root / "src/auth.py"
    if failure == "missing":
        source.unlink()
    elif failure == "invalid_utf8":
        source.write_bytes(b"\xff")
    elif failure == "escape":
        matrix.symbols["src/auth.py:first"].file_path = "../outside.py"
    elif failure == "wrong_root":
        root = root / "src"
    with pytest.raises(ValueError):
        if renderer == "context":
            ContextPackager.generate_spec_context(matrix, "SPEC-A", root_dir=root)
        else:
            ContextPackager.generate_reviewer_bundle(matrix, spec_id="SPEC-A", root_dir=root)
