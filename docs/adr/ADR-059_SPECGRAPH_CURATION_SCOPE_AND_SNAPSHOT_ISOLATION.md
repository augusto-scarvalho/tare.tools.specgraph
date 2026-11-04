---
id: ADR-059
title: SpecGraph Historical Collection Curation Matrix, Operational Scope and Snapshot Isolation
status: SUPERSEDED
superseded_by: ADR-SPECGRAPH-015
date: 2026-08-20
authors: "Antigravity Mediator (Tripartite Consensus: Google Gemini 3.7 Flash High, Anthropic Claude Fable 5 High, OpenAI GPT-5.6 Sol Pro; under Human Operator governance)"
scope: tare.tools.specgraph & tare.tools.library
round_table_case: CASE-2026-08-20-SPECGRAPH-CURATION-AND-SCOPE
round_table_sha256: e2c2b01abae3b8c71516fbaa3ae6e4b90c993112a004fb84f3a2f5da0fbf6e4d
---

# ADR-059: SpecGraph Historical Collection Curation Matrix, Operational Scope and Snapshot Isolation

Ratified on 2026-08-20; superseded by [ADR-SPECGRAPH-015](ADR-SPECGRAPH-015.md) on 2026-09-27, which adopted the Rust core this ADR deferred (D1) and excluded ("Zero native compilation" in section 3). The rest is the record of the August 2026 curation; the storage limits continue in [ADR-SPECGRAPH-002](ADR-SPECGRAPH-002.md).

## 1. Context and Motivation
The forensic analysis of the SpecGraph historical collection (July 2026) found 70 research documents, 9 OpenSDD/EARS stages and 10 interface prototypes, in contrast with the operational implementation in pure Python (August 2026) with 68 passing unit tests. The local paths of these collections belong to the operator's custody and are not installation requirements.

This ADR consolidates the sovereign 4-round deliberation of the tripartite round table (`CASE-2026-08-20-SPECGRAPH-CURATION-AND-SCOPE`), establishing the **Normative Curation Matrix**, the **Transactional Read and Publication Invariants** and the **4-Step Implementation Plan** under the principle of **Zero Technical Hypertrophy & Zero Notarial Bureaucracy**.

---

## 2. Architectural Decision

### 2.1. Canonical Curation Matrix of the Historical Collection

The research documents and UI prototypes classified below were later removed from the tree; they are kept in Git history.

| Artifact / Decision | Original source | Normative Classification | Technical Justification & Canonical Action |
| :--- | :--- | :---: | :--- |
| **D1 (Rust-Core / PyO3)** | Doc 08 / ADR-024 | **DEFERRED** | Pure Python meets < 50 ms on 100k LOC. **Promotion trigger:** `specgraph bench` p95 > 500 ms over 100 samples in 2 consecutive runs on the reference profile. |
| **D2 (Model2Vec / ONNX EP)** | Doc 08 / ADR-025 | **DOCUMENTARY_RESEARCH** | No active semantic use case. Archived as research material until a vector-search spec exists. |
| **D3 (DuckDB & LanceDB)** | Doc 08 / ADR-025 | **DOCUMENTARY_RESEARCH** | SQLite WAL covers 100% of the scale ($10^3$–$10^4$ symbols). LanceDB deferred to > 1M vectors. |
| **Stages 000 to 030** | `specs/000..030` | **CURRENT / ACTIVE** | Imported into `specs/` (Constitution, Benchmark, Repo Map, Context Compiler). |
| **Stage 040 (Reviewer Bundle)**| `specs/040` | **NEXT_SLICE** | Active draft for immediate implementation through internal dogfooding. |
| **Stages 050 to 080** | `specs/050..080` | **DOCUMENTARY_RESEARCH** | Curation Brier, Agent Control Plane and Temporal Memory archived as research material. |
| **UI prototypes 01 to 05** | UI prototype images | **NEXT_SLICE** | Visual reference for the Step 4 single-file HTML viewer. |
| **UI prototypes 06 to 08** | UI prototype images | **DOCUMENTARY_RESEARCH** | Archived. |

---

### 2.2. Transactional Read and Publication Invariants (SQLite WAL & MCP)

1. **Monotonic Atomic Publication (Write):**
   Every reindexing (`specgraph index` or `sync-cache`) runs in an exclusive `BEGIN IMMEDIATE` transaction:
   - Allocates `revision_seq = COALESCE(MAX(revision_seq), 0) + 1` (monotonic integer).
   - Inserts nodes and edges and computes `content_hash = sha256(canonical_graph_jcs)`.
   - Removes the old generation, keeping history $K=1$.
   - Runs `PRAGMA wal_checkpoint(TRUNCATE)` as *best-effort* post-commit maintenance (degrading to PASSIVE under contention without invalidating the commit).

2. **Strict Snapshot Isolation in the MCP Server (Read):**
   Every MCP call (`get_context_envelope`, `query_causal_graph`, `check_drift`) is wrapped in an explicit `BEGIN DEFERRED ... COMMIT/ROLLBACK` transaction:
   - All reads happen under the same immutable WAL snapshot.
   - Every response must carry `revision_seq` (causal ordering) and `content_hash` (content identity).

---

### 2.3. Linear Implementation Order (4 Steps)

1. **Step 1: Native MCP Server (`src/specgraph/mcp_server.py`):**
   * Read-only tools with a transactional wrapper: `get_context_envelope`, `query_causal_graph`, `check_drift`.
2. **Step 2: Reviewer Context Bundle (`specgraph review-bundle <GIT_REF>`):**
   * Validated first in the `tare.tools.specgraph` repository through dogfooding with specs 000–030.
3. **Step 3: Library Mapping (`specgraph.yaml` in `tare.tools.library`):**
   * Binding of the canonical specs to the real modules.
4. **Step 4: Single-File HTML Viewer (`specgraph report --html`):**
   * Interactive dashboard, 100% offline (zero CDN).

---

## 3. Ratified Via Negativa
* **Zero native compilation:** Installation and execution stay in pure Python, without C/Rust compilers on Windows 11.
* **Single storage:** SQLite WAL + JSON on disk. Zero DuckDB/LanceDB/Neo4j in the operational runtime.
* **Zero ceremony:** Only 4 active stages; the rest is preserved as consultative historical heritage.
