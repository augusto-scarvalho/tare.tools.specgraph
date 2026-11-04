from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import yaml

@dataclass
class AdrNode:
    """Represents an Architecture Decision Record (ADR) or Request for Comments (RFC)."""
    id: str  # e.g., "ADR-046"
    title: str
    status: str = "ACCEPTED"  # "PROPOSED", "ACCEPTED", "REJECTED", "SUPERSEDED", "DEPRECATED"
    superseded_by: str | None = None  # e.g., "ADR-048"
    governed_specs: list[str] = field(default_factory=list)  # ["SPEC-001", "SPEC-002"]
    decision_drivers: list[str] = field(default_factory=list)
    file_path: str = ""
    body: str = ""
    raw_frontmatter: dict[str, Any] = field(default_factory=dict)

class AdrParser:
    """Extracts and parses ADRs/RFCs from Markdown documents with YAML frontmatter or standard headings."""

    FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
    STATUS_HEADING_REGEX = re.compile(r"##\s*Status\s*\n+([A-Za-z0-9_\-\s]+)", re.IGNORECASE)
    SUPERSEDED_REGEX = re.compile(r"superseded\s+by\s+\[?(ADR-[0-9]+|RFC-[0-9]+)\]?", re.IGNORECASE)
    TITLE_HEADING_REGEX = re.compile(r"^#\s+(?:ADR-[0-9]+:?\s*)?(.*)$", re.MULTILINE)

    @classmethod
    def parse_file(cls, path: Path | str, root_dir: Path | str = ".") -> tuple[list[AdrNode], list[str]]:
        errors: list[str] = []
        try:
            p = Path(path).resolve()
            root = Path(root_dir).resolve()

            if not p.exists() or p.suffix.lower() not in (".md", ".markdown"):
                return [], []

            try:
                rel_path = str(p.relative_to(root)).replace("\\", "/")
            except ValueError:
                return [], [f"Security Violation: ADR path '{p}' escapes project root '{root}'"]
        except Exception as pre_err:
            return [], [f"File Resolution Error for ADR '{path}': {pre_err}"]

        try:
            text = p.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError as u_err:
            errors.append(f"UTF-8 Decoding Error in ADR {rel_path}: {u_err}")
            return [], errors
        except Exception as io_err:
            errors.append(f"I/O Read Failure in ADR {rel_path}: {io_err}")
            return [], errors

        match = cls.FRONTMATTER_PATTERN.match(text)
        fm_data: dict[str, Any] = {}
        body = text

        if match:
            fm_text, body = match.groups()
            try:
                fm_data = yaml.safe_load(fm_text) or {}
                if not isinstance(fm_data, dict):
                    errors.append(f"Invalid YAML frontmatter in ADR {rel_path}: root must be a dict")
                    fm_data = {}
            except Exception as exc:
                errors.append(f"YAML Syntax Error in ADR {rel_path}: {exc}")
                fm_data = {}

        # 1. Derive ADR ID
        adr_id = str(fm_data.get("id") or fm_data.get("adr_id") or p.stem).upper()
        if not re.match(r"^(?:ADR|RFC)-[A-Za-z0-9_\-]+$", adr_id):
            # Check if filename is e.g. "0046-content-addressed-storage.md" -> ADR-0046
            m_num = re.match(r"^(\d+)", p.stem)
            if m_num:
                adr_id = f"ADR-{m_num.group(1)}"
            else:
                adr_id = f"ADR-{p.stem.upper()}"

        # 2. Derive Title
        title = str(fm_data.get("title") or "")
        if not title:
            m_title = cls.TITLE_HEADING_REGEX.search(body)
            title = m_title.group(1).strip() if m_title else p.stem.replace("_", " ").replace("-", " ").title()

        # 3. Derive Status
        ALLOWED_STATUSES = {"PROPOSED", "ACCEPTED", "APPROVED", "REJECTED", "SUPERSEDED", "DEPRECATED", "WITHDRAWN"}
        status = str(fm_data.get("status") or "").upper().strip()
        # Preserve the owner's original declaration in raw_frontmatter while
        # normalizing "Ratified" and its legacy Portuguese spelling, which
        # ADR-059 used before it was translated.
        status = {"RATIFICADO": "ACCEPTED", "RATIFIED": "ACCEPTED"}.get(status, status)
        if not status:
            m_st = cls.STATUS_HEADING_REGEX.search(body)
            if m_st:
                raw_st_line = m_st.group(1).strip().upper()
                if "SUPERSEDED" in raw_st_line:
                    status = "SUPERSEDED"
                elif "ACCEPTED" in raw_st_line or "APPROVED" in raw_st_line:
                    status = "ACCEPTED"
                elif "REJECTED" in raw_st_line:
                    status = "REJECTED"
                elif "DEPRECATED" in raw_st_line:
                    status = "DEPRECATED"
                elif "PROPOSED" in raw_st_line or "DRAFT" in raw_st_line:
                    status = "PROPOSED"
                elif "WITHDRAWN" in raw_st_line:
                    status = "WITHDRAWN"
                else:
                    status = raw_st_line.split()[0]
            else:
                status = "ACCEPTED"

        if status not in ALLOWED_STATUSES:
            errors.append(
                f"Invalid ADR status '{status}' in '{rel_path}': must be one of {sorted(list(ALLOWED_STATUSES))}"
            )

        # 4. Derive Superseded By
        superseded_by = fm_data.get("superseded_by")
        if not superseded_by:
            m_sup = cls.SUPERSEDED_REGEX.search(body)
            if m_sup:
                superseded_by = m_sup.group(1).upper()
        else:
            superseded_by = str(superseded_by).upper()

        # 5. Derive Governed Specs
        governed_specs_raw = fm_data.get("governed_specs") or fm_data.get("specs") or []
        governed_specs: list[str] = []
        if isinstance(governed_specs_raw, list):
            governed_specs = [str(s).upper() for s in governed_specs_raw if isinstance(s, (str, int))]
        elif isinstance(governed_specs_raw, str):
            governed_specs = [governed_specs_raw.upper()]

        # 6. Decision Drivers
        drivers_raw = fm_data.get("decision_drivers") or fm_data.get("drivers") or []
        drivers: list[str] = []
        if isinstance(drivers_raw, list):
            drivers = [str(d) for d in drivers_raw]

        adr_node = AdrNode(
            id=adr_id,
            title=title,
            status=status,
            superseded_by=superseded_by,
            governed_specs=governed_specs,
            decision_drivers=drivers,
            file_path=rel_path,
            body=body,
            raw_frontmatter=fm_data
        )

        return [adr_node], errors

    @classmethod
    def discover_adrs(
        cls,
        adr_dirs: list[str],
        excluded_patterns: list[str],
        root_dir: Path | str = "."
    ) -> tuple[list[AdrNode], list[str]]:
        root = Path(root_dir).resolve()
        adrs: list[AdrNode] = []
        errors: list[str] = []

        for adir in adr_dirs:
            target = (root / adir).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                errors.append(f"Security Violation: ADR dir '{adir}' escapes project root '{root}'")
                continue

            if target.is_file():
                files = [target] if target.suffix.lower() in (".md", ".markdown") else []
            else:
                files = sorted([f for f in target.rglob("*") if f.is_file() and f.suffix.lower() in (".md", ".markdown")])

            for f in files:
                rel = str(f.relative_to(root)).replace("\\", "/")
                if any(ex in rel.split("/") for ex in excluded_patterns):
                    continue
                adr_list, errs = cls.parse_file(f, root_dir=root)
                adrs.extend(adr_list)
                errors.extend(errs)

        return adrs, errors
