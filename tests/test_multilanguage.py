from dataclasses import asdict
from pathlib import Path

import pytest

from specgraph.extractors import TreeSitterCodeParser, language_capabilities, structural_edges
from specgraph.extractors import tree_sitter as extractor
from specgraph.graph import CausalTraceabilityMatrix
from specgraph.indexer import index_repository
from specgraph.query import project_query
from specgraph.storage import RepositoryIndex


def require(language):
    if language_capabilities()[language]["status"] != "AVAILABLE":
        pytest.skip(f"Install specgraph[multilanguage] wheels for {language} qualification")


def parse(tmp_path, name, source):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return TreeSitterCodeParser.parse_file(path, tmp_path)


def test_typescript_ast_definitions_ranges_and_real_call_paths(tmp_path):
    """@verifies SPEC-SPECGRAPH-C6
    """
    require("typescript")
    symbols, errors = parse(tmp_path, "demo.ts", '''/** @spec SPEC-DEMO */
export interface Runner { run(value: number): string; }
type Identifier = string;
function leaf() { return 1; }
export class Box { run(value: number) { return leaf(); } }
const arrow = (value: number) => leaf();
''')
    assert not errors
    by_name = {symbol.name: symbol for symbol in symbols}
    assert {"Runner", "Runner.run", "Identifier", "leaf", "Box", "Box.run", "arrow"} <= set(by_name)
    assert by_name["Runner"].linked_specs == ["SPEC-DEMO"]
    assert by_name["Box.run"].start_line == 5
    assert by_name["Box.run"].end_line == 5
    assert by_name["Box.run"].language == "typescript"
    assert by_name["Box.run"].references[0].target == "demo.ts:leaf"
    edges = structural_edges({symbol.symbol_id: symbol for symbol in symbols})
    assert any(edge["type"] == "CONTAINS" and edge["from"] == "demo.ts:Box" and edge["to"] == "demo.ts:Box.run" for edge in edges)
    assert all(edge["origin"] == "tree_sitter_typescript" for edge in edges)


def test_tsx_uses_its_own_grammar(tmp_path):
    require("typescript")
    symbols, errors = parse(tmp_path, "widget.tsx", "export const Widget = () => <section><span>hello</span></section>;\n")
    assert not errors
    assert symbols[0].name == "Widget"
    assert symbols[0].kind == "arrow_function"


def test_rust_ast_preserves_impl_trait_module_scopes(tmp_path):
    """@verifies SPEC-SPECGRAPH-C6
    """
    require("rust")
    symbols, errors = parse(tmp_path, "lib.rs", '''struct Box { value: u32 }
trait Runner { fn run(&self); }
impl Runner for Box { fn run(&self) { leaf(); } }
mod inner { pub fn item() {} }
fn leaf() {}
enum Mode { A, B }
type Identifier = u32;
''')
    assert not errors
    by_name = {symbol.name: symbol for symbol in symbols}
    assert {"Box", "Runner", "Runner.run", "impl<Box as Runner>", "impl<Box as Runner>.run", "inner", "inner.item", "leaf", "Mode", "Identifier"} <= set(by_name)
    assert by_name["impl<Box as Runner>.run"].kind == "method"
    assert by_name["impl<Box as Runner>.run"].references[0].target == "lib.rs:leaf"
    assert by_name["inner.item"].container_id == "lib.rs:inner"


@pytest.mark.parametrize("filename,source", [
    ("code.ts", "function leaf() {} function use(leaf: () => void) { leaf(); }"),
    ("code.ts", "function leaf() {} function use() { function leaf() {} leaf(); }"),
    ("code.rs", "fn leaf() {} fn caller(leaf: fn()) { leaf(); }"),
    ("code.rs", "fn leaf() {} mod nested { fn caller() { leaf(); } }"),
])
def test_shadowing_and_rust_module_boundaries_do_not_get_false_edges(tmp_path, filename, source):
    require("rust" if filename.endswith(".rs") else "typescript")
    symbols, errors = parse(tmp_path, filename, source)
    assert not errors
    calls = [reference for symbol in symbols for reference in symbol.references]
    assert calls and all(reference.status == "unresolved" for reference in calls)
    assert not any(edge["type"] == "DEPENDS_ON_STATIC" for edge in structural_edges({symbol.symbol_id: symbol for symbol in symbols}))


def test_dynamic_calls_are_occurrences_not_invented_targets(tmp_path):
    require("typescript")
    symbols, errors = parse(tmp_path, "dynamic.ts", "function invoke(receiver: any) { receiver.execute(); }\n")
    assert not errors
    ref = symbols[0].references[0]
    assert ref.name == "receiver.execute"
    assert ref.status == "unresolved" and ref.target is None
    assert ref.origin == "tree_sitter_typescript"


def test_namespace_functions_do_not_bind_unqualified_global_calls(tmp_path):
    require("typescript")
    symbols, errors = parse(tmp_path, "namespace.ts", "namespace Box { export function leaf() {} } function outside() { leaf(); }")
    assert not errors
    by_name = {symbol.name: symbol for symbol in symbols}
    assert "Box.leaf" in by_name
    assert by_name["outside"].references[0].status == "unresolved"


def test_overloads_do_not_silently_collapse(tmp_path):
    require("typescript")
    symbols, errors = parse(tmp_path, "overload.ts", "function parse(x: number): number;\nfunction parse(x: string): string;\nfunction parse(x: any) { return x; }\n")
    assert not errors
    assert len(symbols) == 3
    assert len({symbol.symbol_id for symbol in symbols}) == 3
    assert all(symbol.binding_status == "ambiguous" for symbol in symbols)


@pytest.mark.parametrize("filename,source", [("broken.ts", "function broken( {"), ("broken.rs", "fn broken( { ")])
def test_syntax_errors_do_not_publish_partial_ast_as_complete(tmp_path, filename, source):
    require("rust" if filename.endswith(".rs") else "typescript")
    symbols, errors = parse(tmp_path, filename, source)
    assert symbols == []
    assert errors and "syntax error" in errors[0]


def test_missing_grammar_is_explicit_and_has_no_regex_fallback(tmp_path, monkeypatch):
    def absent(*args, **kwargs):
        raise ImportError("grammar absent")
    monkeypatch.setattr(extractor, "_parser", absent)
    symbols, errors = parse(tmp_path, "code.ts", "function plausible() {}")
    assert symbols == []
    assert "UNAVAILABLE typescript" in errors[0]
    assert language_capabilities()["typescript"]["status"] == "UNAVAILABLE"


def test_utf8_and_path_guards(tmp_path):
    bad = tmp_path / "bad.ts"
    bad.write_bytes(b"\xff")
    assert TreeSitterCodeParser.parse_file(bad, tmp_path)[1]
    outside = tmp_path.parent / "outside.ts"
    assert "Security Violation" in TreeSitterCodeParser.parse_file(outside, tmp_path)[1][0]


def test_multilanguage_index_full_incremental_equivalence_and_hot_context(tmp_path):
    require("typescript")
    require("rust")
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src/demo.ts").write_text("function leaf() {return 1;} function top() {return leaf();}\n", encoding="utf-8")
    (root / "src/lib.rs").write_text("fn item() -> u32 {1}\n", encoding="utf-8")
    state = tmp_path / "state"
    first = index_repository(root, state_dir=state)
    second = index_repository(root, state_dir=state)
    full = index_repository(root, state_dir=state, incremental=False)
    assert first["content_hash"] == second["content_hash"] == full["content_hash"]
    assert second["index_run"]["parsed_extractions"] == 0
    assert {"typescript", "rust"} <= set(full["coverage"]["languages"])
    path = project_query(root, "src/demo.ts:top", mode="path", target="src/demo.ts:leaf", state_dir=state)
    assert path["results"][0]["edges"][0]["origin"] == "tree_sitter_typescript"
    context = project_query(root, "src/lib.rs:item", mode="context", state_dir=state)
    assert context["results"][0]["source_snippet"]["body"] == (root / "src/lib.rs").read_bytes().decode("utf-8")
    # Deletion removes facts from incremental state just as full rebuild does.
    (root / "src/lib.rs").unlink()
    updated = index_repository(root, state_dir=state)
    rebuilt = index_repository(root, state_dir=state, incremental=False)
    assert updated["content_hash"] == rebuilt["content_hash"]
    with RepositoryIndex(root, state_dir=state) as reader:
        assert not reader.search("src/lib.rs")


def test_matrix_python_bindings_do_not_reinterpret_typescript_symbols(tmp_path):
    require("typescript")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.ts").write_text("function leaf() {} function top() { leaf(); }", encoding="utf-8")
    matrix = CausalTraceabilityMatrix.build(tmp_path)
    assert len(matrix.symbols) == 2
    assert matrix.edges[0]["origin"] == "tree_sitter_typescript"
    assert all(symbol.language == "typescript" for symbol in matrix.symbols.values())
