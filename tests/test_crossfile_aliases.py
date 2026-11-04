"""Declared alias witnesses and fail-closed boundaries; no compiler execution."""
from pathlib import Path

import pytest

from specgraph.extractors import language_capabilities
from specgraph.extractors.tree_sitter import _extract, structural_edges
from specgraph.indexer import index_repository
from specgraph.query import project_query
from specgraph.storage import RepositoryIndex


@pytest.fixture(autouse=True)
def grammars():
    for language in ("typescript", "rust"):
        if language_capabilities()[language]["status"] != "AVAILABLE":
            pytest.skip("Cross-file qualification requires multilanguage wheels")


def extract(files):
    symbols = {}
    for path, source in files.items():
        found, errors = _extract(source.encode(), path, "rust" if path.endswith(".rs") else "typescript")
        assert not errors
        symbols.update({s.symbol_id: s for s in found})
    return symbols, structural_edges(symbols)


@pytest.mark.parametrize("language", ["typescript", "rust"])
def test_original_alias_fixture(language):
    """@verifies SPEC-SPECGRAPH-CROSSFILE-ALIASES:AC-01
    @verifies SPEC-SPECGRAPH-CROSSFILE-ALIASES:AC-04
    """
    if language == "typescript":
        files = {"src/ts/leaf.ts": "export function checksum(): number { return 41; }",
                 "src/ts/other.ts": "export function checksum(): number { return -1; }",
                 "src/ts/main.ts": "import { checksum as compute } from './leaf';\nexport function entry(): number { return compute(); }"}
        expected = ("src/ts/main.ts:entry", "src/ts/leaf.ts:checksum")
    else:
        files = {"src/rust/leaf.rs": "pub fn checksum() -> i32 { 41 }",
                 "src/rust/other.rs": "pub fn checksum() -> i32 { -1 }",
                 "src/rust/main.rs": "mod leaf;\nuse leaf::checksum as compute;\npub fn entry() -> i32 { compute() }"}
        expected = ("src/rust/main.rs:entry", "src/rust/leaf.rs:checksum")
    symbols, edges = extract(files)
    calls = [e for e in edges if e["type"] == "DEPENDS_ON_STATIC"]
    assert [(e["from"], e["to"]) for e in calls] == [expected]
    assert calls[0]["origin"] == "tree_sitter_" + language
    assert calls[0]["resolution_reason"] == "declared_crossfile_import"
    assert calls[0]["source_line"] > 0
    ref = symbols[expected[0]].references[0]
    assert ref.imported_target == expected[1]


@pytest.mark.parametrize("main,leaf,extra", [
    ("import { f as run } from './leaf'; function call(run:()=>void){run();}", "export function f(){}", {}),
    ("import { f as run } from './leaf'; function call(){const run=()=>{};run();}", "export function f(){}", {}),
    ("import { f as run } from './leaf'; function outer(run:()=>void){function inner(){run();}}", "export function f(){}", {}),
    ("import { f as run } from './leaf'; const run=()=>{}; function call(){run();}", "export function f(){}", {}),
    ("import { f as run } from './leaf'; function call(){run();}", "function f(){}", {}),
    ("import { f as run } from './leaf'; function call(){run();}", "export default function f(){}", {}),
    ("import type { f as run } from './leaf'; function call(){run();}", "export function f(){}", {}),
    ("import { type f as run } from './leaf'; function call(){run();}", "export function f(){}", {}),
    ("import { f as run } from '@app/leaf'; function call(){run();}", "export function f(){}", {}),
    ("import { f as run } from './leaf.js'; function call(){run();}", "export function f(){}", {}),
    ("import { f as run } from './leaf'; function call(){run();}", "export { f } from './other';", {"src/other.ts":"export function f(){}"}),
    ("import { f as run } from './leaf'; function call(){run();}", "", {"src/leaf/index.ts":"export function f(){}"}),
    ("import { f as run } from './leaf'; import { f as run } from './other'; function call(){run();}", "export function f(){}", {"src/other.ts":"export function f(){}"}),
    ("import { f as run } from './leaf'; function call(){run();}", "export function f(){} export function f(){}", {}),
    ("import * as run from './leaf'; function call(){run.f();}", "export function f(){}", {}),
])
def test_typescript_unsupported_or_shadowed_alias_has_no_false_edge(main, leaf, extra):
    """@verifies SPEC-SPECGRAPH-CROSSFILE-ALIASES:AC-02"""
    symbols, edges = extract({"src/main.ts": main, "src/leaf.ts": leaf, **extra})
    assert not [e for e in edges if e["type"] == "DEPENDS_ON_STATIC"]
    assert any(r.status == "unresolved" for s in symbols.values() for r in s.references)


@pytest.mark.parametrize("main,leaf,extra", [
    ("mod leaf; use leaf::f as run; pub fn call(run:fn()){run();}", "pub fn f(){}", {}),
    ("mod leaf; use leaf::f as run; pub fn call(){let run=||{};run();}", "pub fn f(){}", {}),
    ("mod leaf; use leaf::f as run; pub fn call(){run();}", "fn f(){}", {}),
    ("mod leaf; use leaf::f as run; pub fn call(){run();}", "pub(crate) fn f(){}", {}),
    ("mod leaf; use leaf::f as run; pub fn call(){run();}", "#[cfg(feature=\"a\")] pub fn f(){}", {}),
    ("#[cfg(feature=\"a\")] mod leaf; use leaf::f as run; pub fn call(){run();}", "pub fn f(){}", {}),
    ("mod leaf; use leaf::f as run; make_bindings!(); pub fn call(){run();}", "pub fn f(){}", {}),
    ("use leaf::f as run; pub fn call(){run();}", "pub fn f(){}", {}),
    ("mod leaf; use leaf::{f as run}; pub fn call(){run();}", "pub fn f(){}", {}),
    ("mod leaf; use leaf::*; pub fn call(){f();}", "pub fn f(){}", {}),
    ("mod leaf; use crate::leaf::f as run; pub fn call(){run();}", "pub fn f(){}", {}),
    ("mod leaf; use leaf::f as run; pub fn call(){run();}", "pub use other::f;", {"src/other.rs":"pub fn f(){}"}),
    ("mod leaf; use leaf::f as run; pub fn call(){run();}", "", {"src/leaf/mod.rs":"pub fn f(){}"}),
])
def test_rust_unsupported_or_shadowed_alias_has_no_false_edge(main, leaf, extra):
    """@verifies SPEC-SPECGRAPH-CROSSFILE-ALIASES:AC-02"""
    symbols, edges = extract({"src/main.rs": main, "src/leaf.rs": leaf, **extra})
    assert not [e for e in edges if e["type"] == "DEPENDS_ON_STATIC"]
    assert any(r.status == "unresolved" for s in symbols.values() for r in s.references)


@pytest.mark.parametrize("language", ["typescript", "rust"])
@pytest.mark.parametrize("change", ["rename", "delete", "unexport"])
def test_cached_caller_rebinds_after_target_changes(tmp_path, language, change):
    """@verifies SPEC-SPECGRAPH-CROSSFILE-ALIASES:AC-03
    @verifies SPEC-SPECGRAPH-CROSSFILE-ALIASES:AC-05
    """
    root, state = tmp_path / "repo", tmp_path / "state"
    (root / "src").mkdir(parents=True)
    (root / "specgraph.yaml").write_text("project_name: aliases\nindexed_paths: [src]\n", encoding="utf-8")
    if language == "typescript":
        caller, target = "src/main.ts", "src/leaf.ts"
        text = "export function f(){}"
        main = "import { f as run } from './leaf'; export function call(){run();}"
    else:
        caller, target = "src/main.rs", "src/leaf.rs"
        text = "pub fn f(){}"
        main = "mod leaf; use leaf::f as run; pub fn call(){run();}"
    (root / caller).write_text(main, encoding="utf-8")
    (root / target).write_text(text, encoding="utf-8")
    index_repository(root, state_dir=state)
    first = project_query(root, caller + ":call", mode="path", target=target + ":f", state_dir=state)
    assert first["results"][0]["edges"][0]["resolution_reason"] == "declared_crossfile_import"
    if change == "rename":
        (root / target).rename(root / target.replace("leaf", "renamed"))
    elif change == "delete":
        (root / target).unlink()
    else:
        (root / target).write_text(text.replace("export ", "").replace("pub ", ""), encoding="utf-8")
    incremental = index_repository(root, state_dir=state)
    assert incremental["index_run"]["reused_extractions"] >= 1
    with RepositoryIndex(root, state_dir=state) as reader:
        payload = reader.snapshot_payload()
    assert not [e for e in payload["edges"] if e["type"] == "DEPENDS_ON_STATIC"]
    full = index_repository(root, state_dir=state, incremental=False)
    assert incremental["content_hash"] == full["content_hash"]


@pytest.mark.parametrize("body", [
    "use other::f as run; run();",
    "const run:fn()=||{}; run();",
    "if let Some(run)=Some(||{}) {run();}",
    "while let Some(run)=Some(||{}) {run();break;}",
    "match Some(||{}) {Some(run)=>run(),None=>{}}",
    "match value {S{run}=>run()}",
    "for run in [||{}] {run();}",
    "(|run:fn()|{run();})(||{});",
])
def test_rust_pattern_and_local_import_shadowing_from_independent_review(body):
    """@verifies SPEC-SPECGRAPH-CROSSFILE-ALIASES:AC-02"""
    symbols, edges = extract({
        "src/main.rs": "mod leaf; mod other; use leaf::f as run; pub fn call(){" + body + "}",
        "src/leaf.rs": "pub fn f(){}", "src/other.rs": "pub fn f(){}"})
    assert not [e for e in edges if e["type"] == "DEPENDS_ON_STATIC"]
    assert any(r.name == "run" and r.status == "unresolved" for s in symbols.values() for r in s.references)


@pytest.mark.parametrize("body", [
    "try{}catch(run){run();}",
    "try{}catch({run}){run();}",
    "for(const run of callbacks){run();}",
    "for(const {run} of callbacks){run();}",
])
def test_typescript_catch_and_iteration_shadowing_from_independent_review(body):
    """@verifies SPEC-SPECGRAPH-CROSSFILE-ALIASES:AC-02"""
    symbols, edges = extract({
        "src/main.ts": "import {f as run} from './leaf'; function call(callbacks:any[]){" + body + "}",
        "src/leaf.ts": "export function f(){}"})
    assert not [e for e in edges if e["type"] == "DEPENDS_ON_STATIC"]
    assert any(r.name == "run" and r.status == "unresolved" for s in symbols.values() for r in s.references)


@pytest.mark.parametrize("body", [
    "use other::*; run();",
    "use other::{*}; run();",
    "use other::*; fn nested(){run();} nested();",
])
def test_rust_local_glob_cannot_preserve_outer_alias(body):
    """@verifies SPEC-SPECGRAPH-CROSSFILE-ALIASES:AC-02"""
    symbols, edges = extract({
        "src/main.rs": "mod leaf; mod other; use leaf::f as run; pub fn call(){" + body + "}",
        "src/leaf.rs": "pub fn f(){}", "src/other.rs": "pub fn run(){}"})
    assert not [e for e in edges if e["type"] == "DEPENDS_ON_STATIC"]


@pytest.mark.parametrize("filename,main,leaf,other", [
    ("main.ts", "import {f as run} from './leaf'; import run from './other'; function call(){run();}", "export function f(){}", "export default function run(){}"),
    ("main.rs", "mod leaf; mod other; use leaf::f as run; use crate::other::run; pub fn call(){run();}", "pub fn f(){}", "pub fn run(){}"),
])
def test_unsupported_import_cannot_hide_duplicate_binding(filename, main, leaf, other):
    """@verifies SPEC-SPECGRAPH-CROSSFILE-ALIASES:AC-02"""
    suffix = Path(filename).suffix
    symbols, edges = extract({"src/" + filename: main, "src/leaf" + suffix: leaf, "src/other" + suffix: other})
    assert not [e for e in edges if e["type"] == "DEPENDS_ON_STATIC"]


@pytest.mark.parametrize("conditional", ["if", "while"])
def test_rust_let_condition_initializer_does_not_shadow_its_callee(conditional):
    """@verifies SPEC-SPECGRAPH-CROSSFILE-ALIASES:AC-02"""
    symbols, edges = extract({"src/main.rs":
        "fn load()->Option<fn()>{None} fn call(){" + conditional + " let Some(run)=load(){run();} }"})
    assert [(e["from"], e["to"]) for e in edges if e["type"] == "DEPENDS_ON_STATIC"] == [("src/main.rs:call", "src/main.rs:load")]
    call = symbols["src/main.rs:call"]
    assert next(r for r in call.references if r.name == "run").status == "unresolved"
