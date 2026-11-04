"""Falsifiers for the supported static Python binding contract (C1)."""
from dataclasses import asdict
from pathlib import Path

from specgraph.api_parser import ApiAndMcpParser
from specgraph.code_parser import CodeParser, CodeSymbol
from specgraph.graph import CausalTraceabilityMatrix


def project(root: Path, files: dict[str, str], extra_config: str = "") -> CausalTraceabilityMatrix:
    (root / "specgraph.yaml").write_text(
        "governed_paths: [src]\nspec_paths: []\ntest_paths: []\nschema_paths: []\n"
        "api_paths: []\nadr_paths: []\n" + extra_config, encoding="utf-8")
    for path, source in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    matrix = CausalTraceabilityMatrix.build(root)
    assert matrix.parse_errors == []
    return matrix


def targets(matrix, source):
    return {e["to"] for e in matrix.edges
            if e["from"] == source and e["type"] == "DEPENDS_ON_STATIC"}


def calls(matrix, source):
    return [r for r in matrix.symbols[source].references if r.kind == "call"]


def test_three_original_falsifiers(tmp_path):
    """@verifies SPEC-SPECGRAPH-C1
    """
    matrix = project(tmp_path, {
        "src/pkg_a/common.py": "def go(): return 'a'\n",
        "src/pkg_b/common.py": "def go(): return 'b'\n",
        "src/consumer.py": "from pkg_b.common import go\ndef consumer(): return go()\n",
        "src/local.py": "def leaf(): return 1\ndef parent(): return leaf()\n",
        "src/unused.py": "import os\ndef pure(): return 1\n",
    })
    assert targets(matrix, "src/consumer.py:consumer") == {"src/pkg_b/common.py:go"}
    assert targets(matrix, "src/local.py:parent") == {"src/local.py:leaf"}
    assert targets(matrix, "src/unused.py:pure") == set()
    assert matrix.symbols["src/unused.py:pure"].static_dependencies == []
    edge = next(e for e in matrix.edges if e["from"] == "src/consumer.py:consumer")
    assert (edge["origin"], edge["status"], edge["reference_kind"], edge["source_line"]) == (
        "python_ast", "resolved", "call", 2)


def test_aliases_relative_imports_and_full_module_paths(tmp_path):
    matrix = project(tmp_path, {
        "src/pkg/service.py": "def execute(): return 1\n",
        "src/pkg/sub/consumer.py": (
            "from ..service import execute as run\n"
            "import pkg.service as service\nimport pkg.service\n"
            "from .. import service as sibling\n"
            "def consumer():\n    run(); service.execute(); pkg.service.execute(); sibling.execute()\n"),
        "src/pkg/__init__.py": "from .service import execute\ndef entry(): return execute()\n",
    })
    expected = {"src/pkg/service.py:execute"}
    assert targets(matrix, "src/pkg/sub/consumer.py:consumer") == expected
    assert len(calls(matrix, "src/pkg/sub/consumer.py:consumer")) == 4
    assert all(r.status == "resolved" for r in calls(matrix, "src/pkg/sub/consumer.py:consumer"))
    assert targets(matrix, "src/pkg/__init__.py:entry") == expected


def test_nested_scope_identity_and_closure_binding(tmp_path):
    matrix = project(tmp_path, {"src/nested.py": """
def outer():
    def helper(): return 1
    class Nested:
        def method(self):
            def inner(): return helper()
            return inner()
    return helper()
class Other:
    def outer(self):
        class Nested:
            def method(self): return 2
        return Nested
"""})
    ids = set(matrix.symbols)
    assert "src/nested.py:outer.Nested.method.inner" in ids
    assert "src/nested.py:Other.outer.Nested.method" in ids
    assert matrix.symbols["src/nested.py:outer.Nested.method.inner"].kind == "function"
    assert targets(matrix, "src/nested.py:outer.Nested.method.inner") == {"src/nested.py:outer.helper"}
    assert targets(matrix, "src/nested.py:outer.Nested.method") == {"src/nested.py:outer.Nested.method.inner"}
    # Nested body references must not leak into their enclosing function.
    assert targets(matrix, "src/nested.py:outer") == {"src/nested.py:outer.helper"}


def test_parameters_assignments_and_comprehensions_shadow_imports(tmp_path):
    matrix = project(tmp_path, {
        "src/lib.py": "def go(): return 1\n",
        "src/use.py": """
from lib import go
def parameter(go): return go()
def assigned():
    go = factory()
    return go()
def comp(values): return [go() for go in values]
def closure(): return lambda go: go()
def real(): return go()
"""})
    for name in ["parameter", "assigned", "comp", "closure"]:
        assert targets(matrix, f"src/use.py:{name}") == set()
        assert all(r.status == "unresolved" for r in calls(matrix, f"src/use.py:{name}"))
    assert targets(matrix, "src/use.py:real") == {"src/lib.py:go"}


def test_function_local_import_does_not_pollute_siblings(tmp_path):
    matrix = project(tmp_path, {
        "src/lib.py": "def go(): return 1\n",
        "src/use.py": "def one():\n    from lib import go\n    return go()\ndef two(): return go()\n",
    })
    assert targets(matrix, "src/use.py:one") == {"src/lib.py:go"}
    assert targets(matrix, "src/use.py:two") == set()
    assert calls(matrix, "src/use.py:two")[0].reason == "unbound_name"


def test_class_namespace_is_not_lexical_method_scope(tmp_path):
    matrix = project(tmp_path, {"src/classes.py": """
def helper(): return 0
class Example:
    def helper(): return 1
    def method(self): return helper()
    def dynamic(self): return self.helper()
def explicit(): return Example.helper()
"""})
    assert targets(matrix, "src/classes.py:Example.method") == {"src/classes.py:helper"}
    assert targets(matrix, "src/classes.py:Example.dynamic") == set()
    assert targets(matrix, "src/classes.py:explicit") == {"src/classes.py:Example.helper"}


def test_ambiguous_declarations_are_not_overwritten_or_arbitrarily_selected(tmp_path):
    matrix = project(tmp_path, {
        "src/lib.py": "def go(): return 1\ndef go(): return 2\n",
        "src/use.py": "from lib import go\ndef consumer(): return go()\n",
    })
    assert set(matrix.symbols) == {"src/lib.py:go@1,0", "src/lib.py:go@2,0", "src/use.py:consumer"}
    assert matrix.duplicate_warnings == []
    assert targets(matrix, "src/use.py:consumer") == set()
    # Coordinate suffixes are declaration identity, not part of Python lookup.
    assert calls(matrix, "src/use.py:consumer")[0].status == "ambiguous"


def test_unknown_external_and_dynamic_references_do_not_create_fake_nodes(tmp_path):
    matrix = project(tmp_path, {"src/use.py": """
import math
from unindexed import missing
def run(receiver):
    math.sqrt(4)
    missing()
    receiver.go()
    factory()()
"""})
    references = calls(matrix, "src/use.py:run")
    assert next(r for r in references if r.name == "math.sqrt").status == "external"
    assert next(r for r in references if r.name == "missing").status == "unresolved"
    assert next(r for r in references if r.name == "receiver.go").reason == "dynamic_or_shadowed_binding"
    assert any(r.reason == "dynamic_call_target" for r in references)
    assert targets(matrix, "src/use.py:run") == set()
    assert all(e["to"] in matrix.symbols for e in matrix.edges)


def test_configured_source_roots_and_index_scope_are_separate_from_governance(tmp_path):
    matrix = project(tmp_path, {
        "packages/team/pkg/lib.py": "def go(): return 1\n",
        "src/use.py": "from pkg.lib import go\ndef consumer(): return go()\n",
    }, "indexed_paths: [src, packages]\npython_source_roots: [packages/team, src]\n")
    assert targets(matrix, "src/use.py:consumer") == {"packages/team/pkg/lib.py:go"}
    assert [s.symbol_id for s in matrix.get_orphan_code()] == ["src/use.py:consumer"]


def test_module_collisions_remain_ambiguous(tmp_path):
    matrix = project(tmp_path, {
        "src/lib.py": "def go(): return 1\n",
        "other/lib.py": "def go(): return 2\n",
        "src/use.py": "from lib import go\ndef consumer(): return go()\n",
    }, "indexed_paths: [src, other]\npython_source_roots: [src, other]\n")
    assert targets(matrix, "src/use.py:consumer") == set()
    ref = calls(matrix, "src/use.py:consumer")[0]
    assert ref.status == "ambiguous"
    assert ref.candidates == ["other/lib.py:go", "src/lib.py:go"]


def test_nested_and_repeated_api_handlers_share_code_identity(tmp_path):
    file = tmp_path / "api.py"
    file.write_text("""
def factory():
    @app.get('/nested')
    def handle(): return 1
@app.get('/first')
def handle(): return 2
@app.get('/second')
def handle(): return 3
""", encoding="utf-8")
    symbols, errors = CodeParser.parse_file(file, tmp_path)
    endpoints, _, api_errors = ApiAndMcpParser.parse_file(file, tmp_path)
    assert not errors and not api_errors
    assert {e.handler_symbol for e in endpoints}.issubset({s.symbol_id for s in symbols})
    assert next(e for e in endpoints if e.path == "/nested").handler_symbol == "api.py:factory.handle"


def test_reference_json_round_trip_and_rebind_after_target_removed(tmp_path):
    matrix = project(tmp_path, {
        "src/lib.py": "def go(): return 1\n",
        "src/use.py": "from lib import go\ndef consumer(): return go()\n",
    })
    symbol = matrix.symbols["src/use.py:consumer"]
    matrix.symbols[symbol.symbol_id] = CodeSymbol(**asdict(symbol))
    del matrix.symbols["src/lib.py:go"]
    matrix._assemble_and_validate_edges()
    assert targets(matrix, symbol.symbol_id) == set()
    assert calls(matrix, symbol.symbol_id)[0].status == "unresolved"


def test_global_nonlocal_and_conditional_bindings_are_conservative(tmp_path):
    matrix = project(tmp_path, {
        "src/lib.py": "if enabled:\n    def optional(): return 1\ndef rebound(): return 2\nrebound = factory()\n",
        "src/use.py": """
from lib import optional, rebound
def leaf(): return 1
def explicit_global():
    global leaf
    return leaf()
def mutated_global():
    global leaf
    leaf = factory()
    return leaf()
def outer():
    def local(): return 1
    def inner():
        nonlocal local
        return local()
    return inner()
def conditional():
    if enabled:
        from lib import rebound as candidate
    return candidate()
def consume():
    optional()
    rebound()
"""})
    assert targets(matrix, "src/use.py:explicit_global") == {"src/use.py:leaf"}
    assert targets(matrix, "src/use.py:outer.inner") == {"src/use.py:outer.local"}
    for name in ["mutated_global", "conditional", "consume"]:
        assert targets(matrix, f"src/use.py:{name}") == set()
    assert calls(matrix, "src/use.py:conditional")[0].status == "ambiguous"
    assert all(r.status == "ambiguous" for r in calls(matrix, "src/use.py:consume"))


def test_overlapping_paths_do_not_duplicate_declarations(tmp_path):
    matrix = project(tmp_path, {"src/pkg/lib.py": "def go(): return 1\n"},
                     "indexed_paths: [src, src/pkg]\n")
    assert matrix.duplicate_warnings == []
    assert len(matrix.symbols) == 1


def test_invalid_relative_import_and_star_import_remain_unknown(tmp_path):
    matrix = project(tmp_path, {
        "src/use.py": "from ..lib import missing\nfrom unindexed import *\ndef leaf(): return 1\ndef run(): return leaf(), missing()\n",
    })
    assert targets(matrix, "src/use.py:run") == set()
    assert all(r.status in {"ambiguous", "unresolved"} for r in calls(matrix, "src/use.py:run"))


def test_imperative_handler_uses_lexical_binding_and_refuses_parameter_shadow(tmp_path):
    source = tmp_path / "api.py"
    source.write_text("""
def handler(): return 0
def factory():
    def handler(): return 1
    app.add_api_route('/nested', handler)
def dynamic(handler):
    app.add_api_route('/dynamic', handler)
""", encoding="utf-8")
    endpoints, _, errors = ApiAndMcpParser.parse_file(source, tmp_path)
    assert not errors
    assert next(e for e in endpoints if e.path == "/nested").handler_symbol == "api.py:factory.handler"
    assert next(e for e in endpoints if e.path == "/dynamic").handler_symbol == "api.py:dynamic_handler"
