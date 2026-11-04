from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import yaml

@dataclass
class SpecNode:
    """Represents a formal Specification Document or Decision Record."""
    id: str
    title: str
    status: str = "DRAFT"
    implements: list[str] = field(default_factory=list)
    target_symbols: list[str] = field(default_factory=list)
    acceptance_criteria: dict[str, str] = field(default_factory=dict)
    file_path: str = ""
    body: str = ""
    raw_frontmatter: dict[str, Any] = field(default_factory=dict)

class SpecParser:
    """Extracts and parses SpecNodes from Markdown documents with strict error capture."""

    FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)

    @classmethod
    def parse_file(cls, path: Path | str, root_dir: Path | str = ".") -> tuple[list[SpecNode], list[str]]:
        errors: list[str] = []
        try:
            p = Path(path).resolve()
            root = Path(root_dir).resolve()

            if not p.exists() or p.suffix.lower() not in (".md", ".markdown"):
                return [], []

            try:
                rel_path = str(p.relative_to(root)).replace("\\", "/")
            except ValueError:
                return [], [f"Security Violation: Spec path '{p}' escapes project root '{root}'"]
        except Exception as pre_err:
            return [], [f"File Resolution Error for spec '{path}': {pre_err}"]

        try:
            text = p.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError as u_err:
            errors.append(f"UTF-8 Decoding Error in spec {rel_path}: {u_err}")
            return [], errors
        except Exception as io_err:
            errors.append(f"I/O Read Failure in spec {rel_path}: {io_err}")
            return [], errors

        match = cls.FRONTMATTER_PATTERN.match(text)
        
        if match:
            fm_text, body = match.groups()
            try:
                fm_data = yaml.safe_load(fm_text) or {}
                if not isinstance(fm_data, dict):
                    errors.append(f"Invalid YAML frontmatter in {rel_path}: root must be a mapping/dict")
                    fm_data = {}
            except Exception as exc:
                errors.append(f"YAML Syntax Error in frontmatter of {rel_path}: {exc}")
                fm_data = {}
            
            spec_id = str(fm_data.get("id") or fm_data.get("spec_id") or p.stem).upper()
            title = str(fm_data.get("title") or p.stem.replace("_", " ").replace("-", " ").title())
            status = str(fm_data.get("status") or "DRAFT").upper()

            ALLOWED_STATUSES = {"DRAFT", "PROPOSED", "ACCEPTED", "APPROVED", "IMPLEMENTED", "REJECTED", "SUPERSEDED", "DEPRECATED", "WITHDRAWN"}
            if status not in ALLOWED_STATUSES:
                errors.append(f"Invalid Spec status '{status}' in '{rel_path}': must be one of {sorted(list(ALLOWED_STATUSES))}")
            
            raw_impl = fm_data.get("implements") or []
            if isinstance(raw_impl, str):
                implements = [raw_impl.upper()]
            elif isinstance(raw_impl, list):
                implements = [str(x).upper() for x in raw_impl]
            else:
                implements = []

            raw_targets = fm_data.get("target_symbols") or []
            if isinstance(raw_targets, str):
                target_symbols = [raw_targets]
            elif isinstance(raw_targets, list):
                target_symbols = [str(x) for x in raw_targets]
            else:
                target_symbols = []
            
            raw_ac = fm_data.get("acceptance_criteria") or fm_data.get("criteria") or {}
            ac_dict: dict[str, str] = {}
            if isinstance(raw_ac, dict):
                for k, v in raw_ac.items():
                    ac_dict[str(k).upper()] = str(v)
            elif isinstance(raw_ac, list):
                for i, item in enumerate(raw_ac, 1):
                    ac_id = f"AC-{i:02d}"
                    ac_dict[ac_id] = str(item)
            
            node = SpecNode(
                id=spec_id,
                title=title,
                status=status,
                implements=implements,
                target_symbols=target_symbols,
                acceptance_criteria=ac_dict,
                file_path=rel_path,
                body=body.strip(),
                raw_frontmatter=fm_data
            )
            return [node], errors
        else:
            spec_id = p.stem.upper()
            title = p.stem.replace("_", " ").replace("-", " ").title()
            first_line = text.strip().split("\n")[0] if text.strip() else ""
            if first_line.startswith("#"):
                title = first_line.lstrip("#").strip()
                
            return [SpecNode(
                id=spec_id,
                title=title,
                status="DRAFT",
                file_path=rel_path,
                body=text.strip()
            )], errors

    @classmethod
    def discover_specs(
        cls,
        spec_dirs: list[str],
        excluded_paths: list[str] | None = None,
        root_dir: Path | str = "."
    ) -> tuple[list[SpecNode], list[str]]:
        root = Path(root_dir).resolve()
        specs: list[SpecNode] = []
        errors: list[str] = []
        excluded = set(excluded_paths or [])

        for sdir in spec_dirs:
            target = (root / sdir).resolve()

            try:
                target.relative_to(root)
            except ValueError:
                errors.append(f"Security Violation: Spec dir '{sdir}' escapes project root '{root}'")
                continue

            if not target.exists():
                continue

            if target.is_file() and target.suffix.lower() in (".md", ".markdown"):
                rel = str(target.relative_to(root)).replace("\\", "/")
                if not any(ex in rel.split("/") for ex in excluded):
                    s_list, errs = cls.parse_file(target, root_dir=root)
                    specs.extend(s_list)
                    errors.extend(errs)
            elif target.is_dir():
                md_files = list(target.rglob("*.md")) + list(target.rglob("*.markdown"))
                unique_sorted_files = sorted(list(set(md_files)), key=lambda p: str(p).replace("\\", "/"))
                for md_file in unique_sorted_files:
                    rel = str(md_file.relative_to(root)).replace("\\", "/")
                    if any(ex in rel.split("/") for ex in excluded):
                        continue
                    s_list, errs = cls.parse_file(md_file, root_dir=root)
                    specs.extend(s_list)
                    errors.extend(errs)

        return specs, errors
