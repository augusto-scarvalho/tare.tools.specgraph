from __future__ import annotations
import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

@dataclass
class SchemaField:
    """Represents a column, attribute, or property within a data schema."""
    name: str
    type_name: str
    required: bool = True
    description: str = ""

@dataclass
class SchemaContract:
    """Represents a formal Data Schema Contract (SQL DDL, JSON Schema, Pydantic/Dataclass model)."""
    schema_id: str  # e.g., "schema:sql:wal_blocks" or "schema:pydantic:src/models.py:TokenPayload"
    name: str
    kind: str  # "sql_table", "sql_view", "json_schema", "pydantic_model", "dataclass"
    file_path: str
    start_line: int = 1
    end_line: int = 1
    fields: list[SchemaField] = field(default_factory=list)
    linked_specs: list[str] = field(default_factory=list)  # ["SPEC-042", "SPEC-042:AC-01"]
    raw_definition: str = ""

class SQLSchemaParser:
    """Extracts tables, views, and columns from SQL DDL scripts with specification annotations."""

    SPEC_SQL_REGEX = re.compile(r"--\s*@spec\s+([^\s\n\r\[]+)(?:\s*\[([A-Za-z0-9_\-,\s]+)\])?", re.IGNORECASE)
    CREATE_TABLE_REGEX = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z0-9_\"\`\.\[\]]+)\s*\((.*?)\)\s*;",
        re.DOTALL | re.IGNORECASE
    )
    CREATE_VIEW_REGEX = re.compile(
        r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z0-9_\"\`\.\[\]]+)\s+AS\s+(.*?)\s*;",
        re.DOTALL | re.IGNORECASE
    )

    @classmethod
    def _extract_spec_tags(cls, comment_block: str) -> list[str]:
        links = []
        for match in cls.SPEC_SQL_REGEX.finditer(comment_block):
            raw_spec = match.group(1).strip().upper()
            criteria_str = match.group(2)
            if criteria_str:
                for c in criteria_str.split(","):
                    c_clean = c.strip().upper()
                    if c_clean:
                        links.append(f"{raw_spec}:{c_clean}")
            else:
                links.append(raw_spec)
        return list(dict.fromkeys(links))

    @classmethod
    def parse_file(cls, path: Path | str, root_dir: Path | str = ".") -> tuple[list[SchemaContract], list[str]]:
        errors: list[str] = []
        try:
            p = Path(path).resolve()
            root = Path(root_dir).resolve()

            if not p.exists() or p.suffix.lower() not in (".sql", ".ddl"):
                return [], []

            try:
                rel_path = str(p.relative_to(root)).replace("\\", "/")
            except ValueError:
                return [], [f"Security Violation: SQL path '{p}' escapes project root '{root}'"]
        except Exception as pre_err:
            return [], [f"File Resolution Error for SQL '{path}': {pre_err}"]

        try:
            text = p.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError as u_err:
            errors.append(f"UTF-8 Decoding Error in SQL {rel_path}: {u_err}")
            return [], errors
        except Exception as io_err:
            errors.append(f"I/O Read Failure in SQL {rel_path}: {io_err}")
            return [], errors

        contracts: list[SchemaContract] = []

        # Find line numbers by text splitting
        lines = text.splitlines()

        # Parse CREATE TABLE
        for match in cls.CREATE_TABLE_REGEX.finditer(text):
            raw_table_name = match.group(1).strip(" \"`[]")
            body = match.group(2)
            start_pos = match.start()
            start_line = text[:start_pos].count("\n") + 1
            end_line = start_line + match.group(0).count("\n")

            # Look for leading comments in the preceding 10 lines
            preceding_text = "\n".join(lines[max(0, start_line - 11):start_line - 1])
            combined_comments = preceding_text + "\n" + body
            links = cls._extract_spec_tags(combined_comments)

            # Parse columns with parenthesis awareness (e.g. NUMERIC(10,2))
            field_list: list[SchemaField] = []
            col_chunks = []
            cur_chunk = []
            p_depth = 0
            for ch in body:
                if ch == "(":
                    p_depth += 1
                    cur_chunk.append(ch)
                elif ch == ")":
                    p_depth = max(0, p_depth - 1)
                    cur_chunk.append(ch)
                elif ch == "," and p_depth == 0:
                    col_chunks.append("".join(cur_chunk).strip())
                    cur_chunk = []
                else:
                    cur_chunk.append(ch)
            if cur_chunk:
                col_chunks.append("".join(cur_chunk).strip())

            for col_clean in col_chunks:
                if not col_clean or col_clean.upper().startswith(("PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK", "CONSTRAINT")):
                    continue
                tokens = col_clean.split()
                if len(tokens) >= 2:
                    col_name = tokens[0].strip(" \"`[]")
                    col_type = tokens[1].upper()
                    not_null = "NOT NULL" in col_clean.upper()
                    field_list.append(SchemaField(
                        name=col_name,
                        type_name=col_type,
                        required=not_null
                    ))

            contracts.append(SchemaContract(
                schema_id=f"{rel_path}:{raw_table_name}",
                name=raw_table_name,
                kind="sql_table",
                file_path=rel_path,
                start_line=start_line,
                end_line=end_line,
                fields=field_list,
                linked_specs=links,
                raw_definition=match.group(0)
            ))

        # Parse CREATE VIEW
        for match in cls.CREATE_VIEW_REGEX.finditer(text):
            raw_view_name = match.group(1).strip(" \"`[]")
            start_pos = match.start()
            start_line = text[:start_pos].count("\n") + 1
            end_line = start_line + match.group(0).count("\n")

            preceding_text = "\n".join(lines[max(0, start_line - 11):start_line - 1])
            combined_comments = preceding_text + "\n" + match.group(0)
            links = cls._extract_spec_tags(combined_comments)

            contracts.append(SchemaContract(
                schema_id=f"{rel_path}:{raw_view_name}",
                name=raw_view_name,
                kind="sql_view",
                file_path=rel_path,
                start_line=start_line,
                end_line=end_line,
                fields=[],
                linked_specs=links,
                raw_definition=match.group(0)
            ))

        # Fail-closed validation: remove all parsed constructs and ensure no non-comment statements remain
        remainder = text
        for contract in contracts:
            if contract.raw_definition:
                remainder = remainder.replace(contract.raw_definition, "")

        remainder_cleaned = re.sub(r"--.*?$", "", remainder, flags=re.MULTILINE)
        remainder_cleaned = re.sub(r"/\*.*?\*/", "", remainder_cleaned, flags=re.DOTALL)
        remainder_cleaned = re.sub(r";", "", remainder_cleaned).strip()

        if remainder_cleaned:
            errors.append(f"Unrecognized SQL DDL content or invalid syntax in '{rel_path}': '{remainder_cleaned[:80]}'")

        return contracts, errors

class JSONSchemaParser:
    """Extracts JSON Schema contracts with $spec or description annotations."""

    SPEC_JSON_REGEX = re.compile(r"@spec\s+([^\s\n\r\[]+)(?:\s*\[([A-Za-z0-9_\-,\s]+)\])?", re.IGNORECASE)

    @classmethod
    def parse_file(cls, path: Path | str, root_dir: Path | str = ".") -> tuple[list[SchemaContract], list[str]]:
        errors: list[str] = []
        try:
            p = Path(path).resolve()
            root = Path(root_dir).resolve()

            if not p.exists() or p.suffix.lower() != ".json":
                return [], []

            try:
                rel_path = str(p.relative_to(root)).replace("\\", "/")
            except ValueError:
                return [], [f"Security Violation: JSON path '{p}' escapes project root '{root}'"]
        except Exception as pre_err:
            return [], [f"File Resolution Error for JSON '{path}': {pre_err}"]

        try:
            raw_text = p.read_text(encoding="utf-8", errors="strict")
            data = json.loads(raw_text)
        except UnicodeDecodeError as u_err:
            errors.append(f"UTF-8 Decoding Error in JSON {rel_path}: {u_err}")
            return [], errors
        except json.JSONDecodeError as j_err:
            errors.append(f"JSON Parse Failure in {rel_path}: {j_err}")
            return [], errors
        except Exception as io_err:
            errors.append(f"I/O Read Failure in JSON {rel_path}: {io_err}")
            return [], errors
            return [], errors

        if not isinstance(data, dict):
            return [], []

        # Check if it's a JSON Schema
        is_schema = "$schema" in data or "properties" in data or data.get("type") in ("object", "array")
        if not is_schema:
            return [], []

        schema_title = data.get("title") or p.stem
        links: list[str] = []

        if "$spec" in data and isinstance(data["$spec"], str):
            links.append(data["$spec"].strip().upper())
        elif "spec" in data and isinstance(data["spec"], str):
            links.append(data["spec"].strip().upper())

        desc = str(data.get("description") or "")
        for match in cls.SPEC_JSON_REGEX.finditer(desc):
            raw_spec = match.group(1).strip().upper()
            criteria_str = match.group(2)
            if criteria_str:
                for c in criteria_str.split(","):
                    c_clean = c.strip().upper()
                    if c_clean:
                        links.append(f"{raw_spec}:{c_clean}")
            else:
                links.append(raw_spec)

        props = data.get("properties", {})
        required_props = set(data.get("required", [])) if isinstance(data.get("required"), list) else set()
        field_list: list[SchemaField] = []

        if isinstance(props, dict):
            for prop_name, prop_val in props.items():
                p_type = prop_val.get("type", "any") if isinstance(prop_val, dict) else "any"
                p_desc = prop_val.get("description", "") if isinstance(prop_val, dict) else ""
                field_list.append(SchemaField(
                    name=prop_name,
                    type_name=str(p_type),
                    required=prop_name in required_props,
                    description=str(p_desc)
                ))

        contract = SchemaContract(
            schema_id=f"{rel_path}:{schema_title}",
            name=schema_title,
            kind="json_schema",
            file_path=rel_path,
            start_line=1,
            end_line=raw_text.count("\n") + 1,
            fields=field_list,
            linked_specs=list(dict.fromkeys(links)),
            raw_definition=raw_text[:2000]
        )

        return [contract], errors

class ASTPydanticVisitor(ast.NodeVisitor):
    """Inspects Python AST to discover Pydantic BaseModels and Dataclasses."""

    SPEC_REGEX = re.compile(r"@spec\s+([^\s\n\r\[]+)(?:\s*\[([A-Za-z0-9_\-,\s]+)\])?", re.IGNORECASE)

    def __init__(self, rel_path: str):
        self.rel_path = rel_path.replace("\\", "/")
        self.contracts: list[SchemaContract] = []

    def _extract_spec_links(self, docstring: str | None) -> list[str]:
        if not docstring:
            return []
        links = []
        for match in self.SPEC_REGEX.finditer(docstring):
            raw_spec = match.group(1).strip().upper()
            criteria_str = match.group(2)
            if criteria_str:
                for c in criteria_str.split(","):
                    c_clean = c.strip().upper()
                    if c_clean:
                        links.append(f"{raw_spec}:{c_clean}")
            else:
                links.append(raw_spec)
        return list(dict.fromkeys(links))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Check base classes for BaseModel
        is_pydantic = False
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id in ("BaseModel", "Schema"):
                is_pydantic = True
            elif isinstance(base, ast.Attribute) and base.attr in ("BaseModel", "Schema"):
                is_pydantic = True

        # Check decorators for @dataclass
        is_dataclass = False
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name) and dec.id in ("dataclass", "pydantic_dataclass"):
                is_dataclass = True
            elif isinstance(dec, ast.Attribute) and dec.attr in ("dataclass", "pydantic_dataclass"):
                is_dataclass = True
            elif isinstance(dec, ast.Call):
                func = dec.func
                if isinstance(func, ast.Name) and func.id in ("dataclass", "pydantic_dataclass"):
                    is_dataclass = True
                elif isinstance(func, ast.Attribute) and func.attr in ("dataclass", "pydantic_dataclass"):
                    is_dataclass = True

        if is_pydantic or is_dataclass:
            doc = ast.get_docstring(node)
            links = self._extract_spec_links(doc)
            fields: list[SchemaField] = []

            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    field_name = item.target.id
                    type_str = ast.unparse(item.annotation) if hasattr(ast, "unparse") else "Any"
                    fields.append(SchemaField(
                        name=field_name,
                        type_name=type_str,
                        required=item.value is None
                    ))

            kind = "pydantic_model" if is_pydantic else "dataclass"
            self.contracts.append(SchemaContract(
                schema_id=f"{self.rel_path}:{node.name}",
                name=node.name,
                kind=kind,
                file_path=self.rel_path,
                start_line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                fields=fields,
                linked_specs=links,
                raw_definition=doc or ""
            ))

        self.generic_visit(node)

class SchemaParser:
    """Unified parser for Data Schemas across SQL DDL, JSON Schema, and Python Models."""

    @classmethod
    def parse_python_models(cls, path: Path | str, root_dir: Path | str = ".") -> tuple[list[SchemaContract], list[str]]:
        errors: list[str] = []
        try:
            p = Path(path).resolve()
            root = Path(root_dir).resolve()

            if not p.exists() or p.suffix != ".py":
                return [], []

            try:
                rel_path = str(p.relative_to(root)).replace("\\", "/")
            except ValueError:
                return [], [f"Security Violation: Path '{p}' escapes project root '{root}'"]
        except Exception as pre_err:
            return [], [f"File Resolution Error for Python Model '{path}': {pre_err}"]

        try:
            code = p.read_text(encoding="utf-8", errors="strict")
            tree = ast.parse(code, filename=str(p))
            visitor = ASTPydanticVisitor(rel_path)
            visitor.visit(tree)
            return visitor.contracts, []
        except UnicodeDecodeError as u_err:
            errors.append(f"UTF-8 Decoding Error in python schema {rel_path}: {u_err}")
            return [], errors
        except SyntaxError as syn_err:
            errors.append(f"AST SyntaxError in schema {rel_path}:{syn_err.lineno}: {syn_err.msg}")
            return [], errors
        except Exception as exc:
            errors.append(f"AST Parse Failure in schema {rel_path}: {exc}")
            return [], errors

    @classmethod
    def discover_schemas(
        cls,
        search_dirs: list[str],
        excluded_patterns: list[str],
        root_dir: Path | str = "."
    ) -> tuple[list[SchemaContract], list[str]]:
        root = Path(root_dir).resolve()
        schemas: list[SchemaContract] = []
        errors: list[str] = []

        for sdir in search_dirs:
            target = (root / sdir).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                errors.append(f"Security Violation: Schema search path '{sdir}' escapes project root '{root}'")
                continue

            if not target.exists():
                continue

            files = [target] if target.is_file() else sorted(target.rglob("*"))
            for f in files:
                if not f.is_file():
                    continue
                rel = str(f.relative_to(root)).replace("\\", "/")
                if any(ex in rel.split("/") for ex in excluded_patterns):
                    continue

                if f.suffix.lower() in (".sql", ".ddl"):
                    res, errs = SQLSchemaParser.parse_file(f, root_dir=root)
                    schemas.extend(res)
                    errors.extend(errs)
                elif f.suffix.lower() == ".json":
                    res, errs = JSONSchemaParser.parse_file(f, root_dir=root)
                    schemas.extend(res)
                    errors.extend(errs)
                elif f.suffix.lower() == ".py":
                    res, errs = cls.parse_python_models(f, root_dir=root)
                    schemas.extend(res)
                    errors.extend(errs)

        return schemas, errors
