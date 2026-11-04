---
id: ADR-001
title: SpecGraph North Star — Universal Project Intelligence & Spec-Driven Development
status: ACCEPTED
governed_specs: [SPEC-SPECGRAPH-001]
decision_source: CASE-2026-08-17-SPECGRAPH-NORTH-STAR-V1
---

# ADR-001: SpecGraph North Star — Universal Project Intelligence & Spec-Driven Development (SDD) Platform

- **Status:** Ratified and Approved by Tripartite Deliberation (`CASE-2026-08-17-SPECGRAPH-NORTH-STAR-V1`)
- **Date:** 2026-08-17
- **Authors:** Antigravity Mediator (under Operator direction and Tripartite consensus: Google Gemini 3.7 Flash High, Anthropic Claude Fable 5 High, OpenAI GPT-5.6 Sol High)
- **Scope:** `tare.tools.specgraph` (Universal Standalone Repository)

---

## 1. Context and Motivation

Accelerated software development by human teams and autonomous agent swarms suffers from critical **contextual amnesia and intent loss**:
1. Code evolves rapidly, but the rationales behind architectural decisions (*ADRs/Specs*) are lost, resulting in *documentation drift*.
2. Vector retrieval tools (*RAG*) locate statistical similarity, but are blind to causality and logical dependencies.
3. Static indexers (*Graphify/SCIP*) map code syntax, but do not understand business intent or verify acceptance criteria.

**`tare.tools.specgraph`** was conceived to resolve this gap, transforming software engineering through the **SDD (Spec-Driven Development)** paradigm and the **Living Causal Traceability Matrix**:

$$\text{Requirement / Intent} \longrightarrow \text{Design / Spec / ADR} \longrightarrow \text{Task (DAG)} \longrightarrow \text{Code (AST)} \longrightarrow \text{Test (Falsifier)} \longrightarrow \text{Evidence (Attestation)}$$

---

## 2. Architectural Decisions

### A. Total Decoupling from the Microkernel (`tare-kernel`):
* The **`tare-kernel`** is the low-level runtime for sandboxing, atomic CAS, and agent attestation.
* **`tare.tools.specgraph`** is an **independent, modular, and universal platform and library**:
  * Operates directly for human engineers via CLI (`specgraph`) and interactive Web visualization;
  * Operates for any AI coding agent on the market (Claude Code, OpenAI Codex, Cursor, Aider, Copilot);
  * Runs **100% local-first** with sub-50ms latencies on user workstations.

### B. Concrete Syntax and Low Annotation Overhead:
* **Specs:** YAML frontmatter with `implements: [REQ-01]` and `target_symbols: [...]`.
* **Code:** Docstring tags (`@spec SPEC-042`) or declarative mapping in `specgraph.yaml` (zero-touch in source code).
* **Tests:** Native pytest marker `@pytest.mark.verifies("SPEC-042", "AC-01")`.
* **Dependencies:** `DEPENDS_ON_STATIC` inferred 100% via AST from code imports.

### C. Bounded Governance Scope (`governed_paths`):
* The *Zero Orphan Code* gate applies strictly to files declared in `governed_paths` (e.g., `src/`), excluding fixtures, temporary scratch scripts, and generated code configured in `excluded_paths`.

### D. Content-Addressed Identity and Strict Idempotence:
* Graph generations are purely content-addressed:
  $$\text{generation\_id} = \text{sha256}(\text{tree\_hash} + \text{canonical\_specgraph\_digest})$$
  Reindexing identical code produces the exact same hash without ledger pollution.

### E. Evidence Trust Tiers:
* **Tier 1 (Authoritative):** Tests re-executed and attested by CI with verified cryptographic hashes. Only Tier 1 approves merge gates.
* **Tier 2 (Static Inference):** Confirmed static imports and assisted re-bindings.
* **Tier 3 (Consultative):** Natural language semantic suggestions for the `explain` command.

---

## 3. Four-Phase Implementation Roadmap

1. **Phase 1 (Python MVP Vertical Slice):**
   - 1 Language: Python (Native AST);
   - 1 Format: OpenSDD / Markdown with YAML frontmatter;
   - Complete CLI lifecycle: `specgraph init` $\rightarrow$ `trace` $\rightarrow$ `drift-check` $\rightarrow$ `doctor`;
   - Real dogfooding on `tare.tools` repositories with synthetic mutation suite (100% recall).
2. **Phase 2 (SDD Engine & Real-Time Drift Detection):**
   - Assisted re-binding and burn-down of legacy traceability debt;
   - Ingestion of historical transcript corpora as informative context layers.
3. **Phase 3 (Interactive Web Visualizer & Multi-Language Expansion):**
   - Single-file HTML GUI with 2D graph topology visualization and impact analysis simulation;
   - Tree-sitter parsers for TypeScript and Rust.
4. **Phase 4 (Agent Integration & MCP Server):**
   - *Context Envelope* provider for Claude Code, Cursor, and OpenAI Codex via local MCP Server;
   - Integration with `tare-kernel` audit gates.

---

## 4. Consequences and Benefits

- **Intent Preservation:** No decision or acceptance criterion is lost during code evolution.
- **Drastic Token Reduction:** Agents receive only the exact transitive closure required for the task ($\ge 65\%$ context savings).
- **Zero False Blockers:** Fixtures and utility scripts do not require artificial specs.
- **Sovereignty and Speed:** 100% offline, local, and deterministic operation.
