from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from specgraph.ground import encode_grounding_report, ground_repository


def _entry(doc_id: str, repo: str, *, title: str = "Title") -> dict[str, object]:
    return {
        "id": doc_id,
        "doc_type": "spec",
        "title": title,
        "relative_path": f"specs/{doc_id}.md",
        "sha256": hashlib.sha256(doc_id.encode()).hexdigest(),
        "target_repositories": [repo],
        "acceptance_criteria": ["AC-01: Remain bounded"],
    }


def _manifest(path: Path, entries: list[dict[str, object]]) -> Path:
    path.write_text(
        json.dumps(
            {
                "version": "2.0.0",
                "specs": entries,
                "adrs": [],
                "experiments": [],
                "post_mortems": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def _v3_entry(
    semantic_id: str,
    repo: str,
    *,
    authority_state: str = "UNMANAGED_ACTIVE",
) -> dict[str, object]:
    digest = hashlib.sha256(semantic_id.encode()).hexdigest()
    relative_path = f"specs/{semantic_id}.md"
    return {
        "id": digest,
        "semantic_document_id": semantic_id,
        "doc_type": "spec",
        "title": semantic_id,
        "status": "UNMANAGED",
        "relative_path": relative_path,
        "sha256": digest,
        "authority_state": authority_state,
        "source_paths": [
            {
                "relative_path": relative_path,
                "semantic_document_id": semantic_id,
                "authority_state": authority_state,
                "editorial_status": "UNMANAGED",
            }
        ],
        "target_repositories": [repo],
        "acceptance_criteria": [],
    }


def _v3_manifest(path: Path, entries: list[dict[str, object]]) -> Path:
    path.write_text(
        json.dumps(
            {
                "version": "3.0.0",
                "authority_vocabulary": "authority_state/v1",
                "specs": entries,
                "adrs": [],
                "experiments": [],
                "post_mortems": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_grounding_is_bounded_deterministic_and_target_scoped(tmp_path: Path):
    path = _manifest(
        tmp_path / "manifest.json",
        [_entry("SPEC-B", "repo-b"), _entry("SPEC-A", "repo-a")],
    )

    first = ground_repository(path, "repo-a")
    second = ground_repository(path, "repo-a")

    assert first == second
    assert first.schema == "tare.tools/repository-grounding/1"
    assert first.status == "READY"
    assert len(first.grounding_scope_sha256) == 64
    assert [entry.id for entry in first.entries] == ["SPEC-A"]
    assert first.byte_count == len(encode_grounding_report(first))
    assert b"SPEC-B" not in encode_grounding_report(first)


def test_grounding_reports_missing_mapping_without_reading_documents(tmp_path: Path):
    path = _manifest(tmp_path / "manifest.json", [_entry("SPEC-A", "repo-a")])

    report = ground_repository(path, "repo-z")

    assert report.status == "NOT_MAPPED"
    assert report.entries == ()


def test_work_selection_is_exact_and_ignores_unrelated_manifest_drift(
    tmp_path: Path,
) -> None:
    path = _manifest(
        tmp_path / "manifest.json",
        [_entry("SPEC-A", "repo-a"), _entry("SPEC-B", "repo-a")],
    )
    baseline = ground_repository(path, "repo-a", include_ids=["SPEC-A"])

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["specs"][1]["title"] = "Unrelated changed title"
    path.write_text(json.dumps(raw), encoding="utf-8")
    unchanged = ground_repository(
        path,
        "repo-a",
        include_ids=["SPEC-A"],
        expected_scope_sha256=baseline.grounding_scope_sha256,
    )

    assert unchanged.status == "READY"
    assert unchanged.manifest_sha256 != baseline.manifest_sha256
    assert unchanged.grounding_scope_sha256 == baseline.grounding_scope_sha256
    assert [entry.id for entry in unchanged.entries] == ["SPEC-A"]


def test_selected_grounding_fails_closed_on_missing_id_and_scope_drift(
    tmp_path: Path,
) -> None:
    path = _manifest(tmp_path / "manifest.json", [_entry("SPEC-A", "repo-a")])
    missing = ground_repository(
        path, "repo-a", include_ids=["SPEC-A", "SPEC-MISSING"]
    )
    baseline = ground_repository(path, "repo-a", include_ids=["SPEC-A"])
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["specs"][0]["title"] = "Changed selected title"
    path.write_text(json.dumps(raw), encoding="utf-8")
    drifted = ground_repository(
        path,
        "repo-a",
        include_ids=["SPEC-A"],
        expected_scope_sha256=baseline.grounding_scope_sha256,
    )

    assert missing.status == "INCOMPLETE_MAPPING"
    assert missing.reason_code == "REQUIRED_ID_NOT_MAPPED"
    assert drifted.status == "DRIFT"
    assert drifted.reason_code == "GROUNDING_SCOPE_CHANGED"
    assert drifted.entries == ()


def test_duplicate_selected_identity_fails_closed(tmp_path: Path):
    path = _manifest(
        tmp_path / "manifest.json",
        [_entry("SPEC-A", "repo-a"), _entry("SPEC-A", "repo-a")],
    )

    report = ground_repository(path, "repo-a")

    assert report.status == "AMBIGUOUS_SSOT"
    assert report.reason_code == "DUPLICATE_DOCUMENT_ID"
    assert report.entries == ()


def test_output_budget_truncates_at_entry_boundary(tmp_path: Path):
    path = _manifest(
        tmp_path / "manifest.json",
        [_entry(f"SPEC-{index:02d}", "repo-a", title="T" * 400) for index in range(20)],
    )

    report = ground_repository(path, "repo-a", max_bytes=1_500)

    assert report.status == "READY_TRUNCATED"
    assert 0 < report.returned_count < report.matched_count
    assert report.omitted_count == report.matched_count - report.returned_count
    assert len(encode_grounding_report(report)) <= 1_500


def test_invalid_and_unavailable_manifests_are_sanitized(tmp_path: Path):
    malformed = tmp_path / "bad.json"
    malformed.write_text("not-json", encoding="utf-8")

    invalid = ground_repository(malformed, "repo-a")
    unavailable = ground_repository(tmp_path / "missing.json", "repo-a")

    assert invalid.status == "SOURCE_INVALID"
    assert unavailable.status == "UNAVAILABLE"
    assert "not-json" not in encode_grounding_report(invalid).decode()


def test_cli_stdout_is_exact_declared_grounding_bytes(tmp_path: Path):
    path = _manifest(tmp_path / "manifest.json", [_entry("SPEC-A", "repo-a")])

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "specgraph.cli",
            "ground",
            "--repo",
            "repo-a",
            "--library-manifest",
            str(path),
            "--include-id",
            "SPEC-A",
        ],
        check=False,
        capture_output=True,
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["requested_ids"] == ["SPEC-A"]
    assert len(completed.stdout) == payload["byte_count"]
    assert not completed.stdout.endswith(b"\n")


def test_v3_projection_preserves_authority_and_provenance(tmp_path: Path):
    path = _v3_manifest(
        tmp_path / "manifest.json",
        [_v3_entry("SPEC-A", "repo-a")],
    )

    report = ground_repository(path, "repo-a", include_ids=["SPEC-A"])

    assert report.status == "READY"
    assert report.entries[0].id == report.entries[0].sha256
    assert report.entries[0].semantic_document_id == "SPEC-A"
    assert report.entries[0].authority_state == "UNMANAGED_ACTIVE"
    assert report.entries[0].source_paths[0].relative_path == "specs/SPEC-A.md"


def test_v3_projection_rejects_duplicate_payload_hash(tmp_path: Path):
    first = _v3_entry("SPEC-A", "repo-a")
    second = dict(first)
    second["semantic_document_id"] = "SPEC-B"
    second["source_paths"] = [
        {
            "relative_path": "specs/SPEC-B.md",
            "semantic_document_id": "SPEC-B",
            "authority_state": "UNMANAGED_ACTIVE",
            "editorial_status": "UNMANAGED",
        }
    ]
    second["relative_path"] = "specs/SPEC-B.md"
    path = _v3_manifest(tmp_path / "manifest.json", [first, second])

    report = ground_repository(path, "repo-a")

    assert report.status == "AMBIGUOUS_SSOT"
    assert report.reason_code == "DUPLICATE_DOCUMENT_ID"


def test_v3_projection_fails_closed_on_unknown_or_inactive_authority(tmp_path: Path):
    unknown = _v3_entry("SPEC-A", "repo-a", authority_state="FUTURE")
    unknown_path = _v3_manifest(tmp_path / "unknown.json", [unknown])
    inactive = _v3_entry("SPEC-B", "repo-a", authority_state="EXCLUDED")
    inactive_path = _v3_manifest(tmp_path / "inactive.json", [inactive])

    unknown_report = ground_repository(unknown_path, "repo-a")
    inactive_report = ground_repository(inactive_path, "repo-a")

    assert unknown_report.reason_code == "INVALID_MANIFEST_CONTRACT"
    assert inactive_report.reason_code == "INVALID_MANIFEST_CONTRACT"
