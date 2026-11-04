from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import yaml

@dataclass
class SpecGraphConfig:
    """Configuration for specgraph governance boundaries and discovery paths."""
    governed_paths: list[str] = field(default_factory=lambda: ["src"])
    indexed_paths: list[str] | None = None
    python_source_roots: list[str] = field(default_factory=lambda: ["src", "."])
    search_paths: list[str] = field(default_factory=list)
    intent_mapping_paths: list[str] = field(default_factory=list)
    spec_paths: list[str] = field(default_factory=lambda: ["specs", "docs/specs"])
    test_paths: list[str] = field(default_factory=lambda: ["tests"])
    schema_paths: list[str] = field(default_factory=lambda: ["src", "schemas", "sql", "docs/schemas"])
    api_paths: list[str] = field(default_factory=lambda: ["src"])
    adr_paths: list[str] = field(default_factory=lambda: ["docs/adr", "adrs", "rfcs", "docs/decisions"])
    excluded_paths: list[str] = field(default_factory=lambda: [
        "__pycache__",
        ".git",
        ".pytest_cache",
        "fixtures",
        "scratch",
        ".system_generated"
    ])
    strict_orphan_check: bool = True
    project_name: str = "tare.tools.project"
    config_errors: list[str] = field(default_factory=list)

    @staticmethod
    def is_path_excluded(rel_path: str, excluded_paths: list[str]) -> bool:
        """Unified check whether a relative path is excluded by configuration rules."""
        clean_rel = rel_path.replace("\\", "/").strip("/")
        for exc in excluded_paths:
            clean_exc = exc.replace("\\", "/").strip("/")
            if not clean_exc:
                continue
            if clean_rel == clean_exc or clean_rel.startswith(f"{clean_exc}/"):
                return True
            parts = clean_rel.split("/")
            if clean_exc in parts:
                return True
        return False

    @classmethod
    def load(cls, root_dir: Path | str = ".") -> SpecGraphConfig:
        """Load configuration from specgraph.yaml if present with fail-closed error recording."""
        root = Path(root_dir).resolve()
        cfg_file = root / "specgraph.yaml"
        if not cfg_file.exists():
            cfg_file = root / ".specgraph.yaml"

        if cfg_file.exists():
            try:
                raw_text = cfg_file.read_text(encoding="utf-8", errors="strict")
                data = yaml.safe_load(raw_text)
                if data is None:
                    data = {}
                if not isinstance(data, dict):
                    return cls(config_errors=[f"Invalid configuration root in '{cfg_file}': must be a mapping/dict"])
                
                cfg_errors: list[str] = []
                def parse_path_list(key: str, default: list[str]) -> list[str]:
                    val = data.get(key, default)
                    if isinstance(val, str):
                        return [val]
                    if isinstance(val, list):
                        for item in val:
                            if not isinstance(item, str):
                                cfg_errors.append(f"Invalid item in '{key}': expected string, got {type(item).__name__}")
                        return [str(x) for x in val if isinstance(x, str)]
                    cfg_errors.append(f"Invalid type for '{key}': expected list or string, got {type(val).__name__}")
                    return default

                gov = parse_path_list("governed_paths", ["src"])
                indexed = parse_path_list("indexed_paths", gov) if "indexed_paths" in data else None
                source_roots = parse_path_list("python_source_roots", ["src", "."])
                search = parse_path_list("search_paths", [])
                mappings = parse_path_list("intent_mapping_paths", [])
                specs = parse_path_list("spec_paths", ["specs", "docs/specs"])
                tests = parse_path_list("test_paths", ["tests"])
                schemas = parse_path_list("schema_paths", ["src", "schemas", "sql", "docs/schemas"])
                apis = parse_path_list("api_paths", ["src"])
                adrs = parse_path_list("adr_paths", ["docs/adr", "adrs", "rfcs", "docs/decisions"])
                excl = parse_path_list("excluded_paths", ["__pycache__", ".git", ".pytest_cache", "fixtures", "scratch", ".system_generated"])
                strict = data.get("strict_orphan_check", True)
                if not isinstance(strict, bool):
                    cfg_errors.append(f"Invalid type for 'strict_orphan_check': expected boolean, got {type(strict).__name__}")
                    strict = True

                proj = data.get("project_name", "tare.tools.project")
                if not isinstance(proj, str):
                    cfg_errors.append(f"Invalid type for 'project_name': expected string, got {type(proj).__name__}")
                    proj = "tare.tools.project"

                return cls(
                    governed_paths=gov,
                    indexed_paths=indexed,
                    python_source_roots=source_roots,
                    search_paths=search,
                    intent_mapping_paths=mappings,
                    spec_paths=specs,
                    test_paths=tests,
                    schema_paths=schemas,
                    api_paths=apis,
                    adr_paths=adrs,
                    excluded_paths=excl,
                    strict_orphan_check=strict,
                    project_name=proj,
                    config_errors=cfg_errors
                )
            except Exception as exc:
                return cls(config_errors=[f"Configuration Parsing Failure in '{cfg_file}': {exc}"])
        return cls()

    def dump_yaml(self) -> str:
        """Return canonical YAML representation for specgraph.yaml."""
        data = {
            "schema_version": "1.0",
            "project_name": self.project_name,
            "governed_paths": self.governed_paths,
            "python_source_roots": self.python_source_roots,
            "search_paths": self.search_paths,
            "intent_mapping_paths": self.intent_mapping_paths,
            "spec_paths": self.spec_paths,
            "test_paths": self.test_paths,
            "schema_paths": self.schema_paths,
            "api_paths": self.api_paths,
            "adr_paths": self.adr_paths,
            "excluded_paths": self.excluded_paths,
            "strict_orphan_check": self.strict_orphan_check
        }
        if self.indexed_paths is not None:
            data["indexed_paths"] = self.indexed_paths
        return yaml.dump(data, sort_keys=False, indent=2)
