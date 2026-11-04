"""Counterexamples for C4 AC-04: hints never outrank current published facts."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from specgraph import resolve as recovery
from specgraph.indexer import index_repository
from specgraph.resolve import resolve_repository, runtime_directory
from specgraph.storage import RepositoryIndex


@pytest.fixture
def repository(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    monkeypatch.setenv("TARE_SPECGRAPH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(recovery.IncrementalCacheEngine, "detect_staleness", lambda **_: SimpleNamespace(is_stale=False))
    return root


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def learn_literal(root, *, query="Needle", limit=20):
    return resolve_repository(root, query, limit=limit, structural_probe=lambda *_: ("MISS", []))


def test_literal_origin_and_source_hash_survive_reuse(repository):
    path = repository / "src/note.txt"
    path.write_text("Needle literal evidence\n", encoding="utf-8")
    first = learn_literal(repository)
    second = learn_literal(repository)
    assert first.source == "traditional"
    assert second.source == "learned"
    assert second.results[0]["result_type"] == "literal_text"
    assert second.results[0]["evidence_sha256"] == digest(path)
    assert "symbol_id" not in second.results[0]
    stored = json.loads((runtime_directory(repository) / recovery.RECOVERY_INDEX).read_text())
    assert stored["version"] == 2
    assert stored["repository_root"] == os.path.normcase(str(repository.resolve()))
    assert next(iter(stored["entries"].values()))["evidence"][0]["origin"] == "literal_text"


def test_new_published_structure_preempts_literal_even_with_injected_probe(repository):
    (repository / "src/note.txt").write_text("Needle\n", encoding="utf-8")
    assert learn_literal(repository).source == "traditional"
    source = repository / "src/implementation.py"
    source.write_text("def Needle(): return 1\n", encoding="utf-8")
    metadata = index_repository(repository)
    def forbidden(*args):
        raise AssertionError("Published structure must outrank recovery probes")
    result = resolve_repository(repository, "Needle", structural_probe=forbidden, fallback_probe=forbidden)
    assert result.source == "specgraph"
    assert result.results[0]["result_type"] == "symbol"
    assert result.results[0]["symbol_id"] == "src/implementation.py:Needle"
    assert result.results[0]["evidence_sha256"] == digest(source)
    assert result.snapshot["content_hash"] == metadata["content_hash"]
    assert result.snapshot["results_from_snapshot"] is True


def test_new_generation_invalidates_hint_even_when_matching_file_is_unchanged(repository):
    (repository / "src/note.txt").write_text("Needle\n", encoding="utf-8")
    first_generation = index_repository(repository)
    first = resolve_repository(repository, "Needle")
    assert first.source == "traditional"
    assert resolve_repository(repository, "Needle").source == "learned"
    (repository / "src/unrelated.py").write_text("def unrelated(): pass\n", encoding="utf-8")
    second_generation = index_repository(repository)
    result = resolve_repository(repository, "Needle")
    assert second_generation["revision_seq"] > first_generation["revision_seq"]
    assert result.source == "traditional"
    assert "STALE_LEARNED_EVIDENCE" in result.signals
    assert result.snapshot["revision_seq"] == second_generation["revision_seq"]
    assert result.snapshot["results_from_snapshot"] is False


def test_limit_expansion_cannot_reuse_a_smaller_hint(repository):
    (repository / "src/a.txt").write_text("Needle\n", encoding="utf-8")
    (repository / "src/b.txt").write_text("Needle\n", encoding="utf-8")
    assert learn_literal(repository, limit=1).count == 1
    wider = learn_literal(repository, limit=20)
    assert wider.source == "traditional"
    assert wider.count == 2
    assert {result["file_path"] for result in wider.results} == {"src/a.txt", "src/b.txt"}


def test_query_case_is_part_of_literal_recovery_identity(repository):
    (repository / "src/note.txt").write_text("Needle\nneedle\n", encoding="utf-8")
    assert learn_literal(repository, query="Needle").results[0]["line"] == 1
    assert learn_literal(repository, query="needle").results[0]["line"] == 2
    assert learn_literal(repository, query="Needle").source == "learned"


def test_config_exclusion_invalidates_unchanged_literal(repository):
    (repository / "src/note.txt").write_text("Needle\n", encoding="utf-8")
    assert learn_literal(repository).source == "traditional"
    (repository / "specgraph.yaml").write_text("excluded_paths: [src/note.txt]\n", encoding="utf-8")
    response = learn_literal(repository)
    assert response.status == "MISS"
    assert "STALE_LEARNED_EVIDENCE" in response.signals


def test_rename_and_same_size_same_mtime_edits_invalidate_hashes(repository):
    source = repository / "src/old.txt"
    source.write_text("Needle old\n", encoding="utf-8")
    first = learn_literal(repository)
    old_stat = source.stat()
    source.write_text("Needle new\n", encoding="utf-8")
    os.utime(source, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    second = learn_literal(repository)
    assert second.results[0]["evidence_sha256"] != first.results[0]["evidence_sha256"]
    source.rename(repository / "src/new.txt")
    renamed = learn_literal(repository)
    assert renamed.source == "traditional"
    assert renamed.results[0]["file_path"] == "src/new.txt"


def test_shared_state_override_is_root_namespaced_and_rejects_copied_cache(repository):
    (repository / "src/note.txt").write_text("Needle\n", encoding="utf-8")
    learn_literal(repository)
    other = repository.parent / "other"
    (other / "src").mkdir(parents=True)
    (other / "src/note.txt").write_text("Needle\n", encoding="utf-8")
    assert runtime_directory(repository) != runtime_directory(other)
    destination = runtime_directory(other)
    destination.mkdir(parents=True)
    shutil.copy2(runtime_directory(repository) / recovery.RECOVERY_INDEX, destination / recovery.RECOVERY_INDEX)
    result = learn_literal(other)
    assert result.source == "traditional"
    stored = json.loads((destination / recovery.RECOVERY_INDEX).read_text())
    assert stored["repository_root"] == os.path.normcase(str(other.resolve()))


def test_publication_during_recall_preempts_the_old_hint(repository, monkeypatch):
    (repository / "src/note.txt").write_text("Needle\n", encoding="utf-8")
    old = index_repository(repository)
    resolve_repository(repository, "Needle")
    validate = recovery._validate_results
    published = []
    def publish_while_validating(*args, **kwargs):
        valid = validate(*args, **kwargs)
        if not published:
            (repository / "src/new.py").write_text("def Needle(): pass\n", encoding="utf-8")
            published.append(index_repository(repository))
        return valid
    monkeypatch.setattr(recovery, "_validate_results", publish_while_validating)
    result = resolve_repository(repository, "Needle")
    assert result.source == "specgraph"
    assert result.results[0]["symbol_id"] == "src/new.py:Needle"
    assert result.snapshot["revision_seq"] == published[0]["revision_seq"] > old["revision_seq"]


def test_publication_during_fallback_wins_before_a_literal_is_learned(repository):
    (repository / "src/note.txt").write_text("Needle\n", encoding="utf-8")
    published = []
    def fallback(root, query, limit, seconds):
        result = recovery._traditional_literal_scan(root, query, limit, seconds)
        (root / "src/new.py").write_text("def Needle(): pass\n", encoding="utf-8")
        published.append(index_repository(root))
        return result
    result = resolve_repository(repository, "Needle", fallback_probe=fallback)
    assert result.source == "specgraph"
    assert result.snapshot["content_hash"] == published[0]["content_hash"]
    assert not (runtime_directory(repository) / recovery.RECOVERY_INDEX).exists()


def test_concurrent_writer_cannot_mix_search_rows_with_a_new_snapshot_identity(repository, monkeypatch):
    path = repository / "src/code.py"
    path.write_text("def Needle(): return 1\n", encoding="utf-8")
    old = index_repository(repository)
    from specgraph import retrieval
    search = retrieval.retrieve
    published = []
    def publish_during_snapshot(self, query, **kwargs):
        if not published:
            path.write_text("def NeedleNew(): return 2\n", encoding="utf-8")
            with ThreadPoolExecutor(max_workers=1) as pool:
                published.append(pool.submit(index_repository, repository).result(timeout=10))
        return search(self, query, **kwargs)
    monkeypatch.setattr(retrieval, "retrieve", publish_during_snapshot)
    first = resolve_repository(repository, "Needle")
    assert first.snapshot["content_hash"] == old["content_hash"]
    assert first.results[0]["symbol_id"] == "src/code.py:Needle"
    second = resolve_repository(repository, "NeedleNew")
    assert second.snapshot["content_hash"] == published[0]["content_hash"]
    assert second.results[0]["symbol_id"] == "src/code.py:NeedleNew"


def test_changed_probe_coordinates_are_not_relabelled_with_new_bytes(repository):
    path = repository / "src/note.txt"
    path.write_text("Needle old\n", encoding="utf-8")
    def racing_fallback(root, query, limit, seconds):
        result = recovery._traditional_literal_scan(root, query, limit, seconds)
        path.write_text("Needle new\n", encoding="utf-8")
        return result
    result = resolve_repository(repository, "Needle", fallback_probe=racing_fallback)
    assert result.status == "MISS"
    assert "UNVERIFIABLE_FALLBACK_EVIDENCE" in result.signals
    assert not (runtime_directory(repository) / recovery.RECOVERY_INDEX).exists()


def test_structural_hint_requires_adapter_source_hash_and_preserves_type(repository, monkeypatch):
    path = repository / "src/code.py"
    path.write_text("def Needle(): pass\n", encoding="utf-8")
    monkeypatch.setattr(recovery.IncrementalCacheEngine, "detect_staleness", lambda **_: SimpleNamespace(is_stale=True))
    unpinned = {"result_type": "symbol", "symbol_id": "src/code.py:Needle", "name": "Needle", "file_path": "src/code.py", "start_line": 1, "end_line": 1}
    first = resolve_repository(repository, "Needle", structural_probe=lambda *_: ("HIT", [unpinned]))
    assert first.recovery_action == "RETURNED_UNCACHED_EVIDENCE"
    assert not (runtime_directory(repository) / recovery.RECOVERY_INDEX).exists()
    pinned = dict(unpinned, evidence_sha256=digest(path))
    second = resolve_repository(repository, "Needle", structural_probe=lambda *_: ("HIT", [pinned]))
    assert second.recovery_action == "INDEXED_CURRENT_EVIDENCE"
    reused = resolve_repository(repository, "Needle", structural_probe=lambda *_: ("MISS", []))
    assert reused.source == "learned"
    assert reused.results[0]["result_type"] == "symbol"
    assert reused.results[0]["evidence_sha256"] == pinned["evidence_sha256"]


def test_partial_invalid_hint_is_evicted_as_a_unit(repository):
    first = repository / "src/a.txt"
    second = repository / "src/b.txt"
    first.write_text("Needle\n", encoding="utf-8")
    second.write_text("Needle\n", encoding="utf-8")
    assert learn_literal(repository).count == 2
    second.unlink()
    third = repository / "src/c.txt"
    third.write_text("Needle\n", encoding="utf-8")
    recovered = learn_literal(repository)
    assert recovered.source == "traditional"
    assert {hit["file_path"] for hit in recovered.results} == {"src/a.txt", "src/c.txt"}


def test_indexed_hits_never_scan_sources_or_launch_a_structural_subprocess(repository, monkeypatch):
    (repository / "src/code.py").write_text("def Needle(): pass\n", encoding="utf-8")
    index_repository(repository)
    def forbidden(*args, **kwargs):
        raise AssertionError("Indexed hit must stay on the read model")
    monkeypatch.setattr(recovery.IncrementalCacheEngine, "detect_staleness", forbidden)
    monkeypatch.setattr(recovery, "_structural_subprocess", forbidden)
    monkeypatch.setattr(recovery, "_recall", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    assert resolve_repository(repository, "Needle").source == "specgraph"


def test_unwritable_hint_and_telemetry_state_does_not_block_results(repository, monkeypatch):
    (repository / "src/note.txt").write_text("Needle\n", encoding="utf-8")
    def denied(*args, **kwargs):
        raise PermissionError("read-only state")
    monkeypatch.setattr(Path, "mkdir", denied)
    response = learn_literal(repository)
    assert response.status == "HIT"
    assert response.recovery_action == "RETURNED_UNCACHED_EVIDENCE"


def test_config_change_during_probe_cannot_create_out_of_scope_learning(repository):
    (repository / "src/note.txt").write_text("Needle\n", encoding="utf-8")
    def changing_scope(root, query, limit, seconds):
        result = recovery._traditional_literal_scan(root, query, limit, seconds)
        (root / "specgraph.yaml").write_text("excluded_paths: [src/note.txt]\n", encoding="utf-8")
        return result
    result = resolve_repository(repository, "Needle", fallback_probe=changing_scope)
    assert result.status == "MISS"
    assert "UNVERIFIABLE_FALLBACK_EVIDENCE" in result.signals
    assert not (runtime_directory(repository) / recovery.RECOVERY_INDEX).exists()


def test_corrupted_origin_cannot_promote_literal_learning_to_structure(repository):
    (repository / "src/note.txt").write_text("Needle\n", encoding="utf-8")
    learn_literal(repository)
    cache_path = runtime_directory(repository) / recovery.RECOVERY_INDEX
    cached = json.loads(cache_path.read_text())
    next(iter(cached["entries"].values()))["evidence"][0]["origin"] = "symbol"
    cache_path.write_text(json.dumps(cached), encoding="utf-8")
    result = learn_literal(repository)
    assert result.source == "traditional"
    assert result.results[0]["result_type"] == "literal_text"
    assert "STALE_LEARNED_EVIDENCE" in result.signals


def test_recovery_normalizes_query_whitespace_consistently(repository):
    (repository / "src/note.txt").write_text("Needle\n", encoding="utf-8")
    result = learn_literal(repository, query="  Needle  ")
    assert result.status == "HIT"
    assert result.query == "  Needle  "
    assert result.results[0]["column"] == 1
    assert learn_literal(repository, query="Needle").source == "learned"


def test_malformed_cached_path_cannot_break_recovery(repository):
    (repository / "src/note.txt").write_text("Needle\n", encoding="utf-8")
    learn_literal(repository)
    cache_path = runtime_directory(repository) / recovery.RECOVERY_INDEX
    cached = json.loads(cache_path.read_text())
    next(iter(cached["entries"].values()))["evidence"][0]["result"]["file_path"] = "src/\u0000.txt"
    cached["entries"]["invalid-entry"] = "not an entry object"
    cache_path.write_text(json.dumps(cached), encoding="utf-8")
    result = learn_literal(repository)
    assert result.source == "traditional"
    assert result.results[0]["file_path"] == "src/note.txt"
