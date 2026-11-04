from __future__ import annotations

from dataclasses import asdict, dataclass

from .code_parser import CodeSymbol


@dataclass(frozen=True)
class SymbolSearchResult:
    """A bounded, deterministic structural-symbol search result."""

    symbol_id: str
    name: str
    kind: str
    file_path: str
    start_line: int
    end_line: int
    linked_specs: tuple[str, ...]
    score: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def search_symbols(
    symbols: list[CodeSymbol],
    query: str,
    *,
    limit: int = 20,
) -> list[SymbolSearchResult]:
    """Find AST symbols by literal name, id, or path without regex ambiguity."""

    normalized = query.strip().casefold()
    if not normalized:
        raise ValueError("query must not be empty")
    if len(normalized.encode("utf-8")) > 512:
        raise ValueError("query exceeds 512 UTF-8 bytes")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")

    ranked: list[SymbolSearchResult] = []
    for symbol in symbols:
        name = symbol.name.casefold()
        leaf = name.rsplit(".", 1)[-1]
        symbol_id = symbol.symbol_id.casefold()
        file_path = symbol.file_path.casefold()

        if normalized == name or normalized == leaf:
            score = 400
        elif name.endswith(f".{normalized}"):
            score = 300
        elif normalized in name:
            score = 200
        elif normalized in symbol_id or normalized in file_path:
            score = 100
        else:
            continue

        ranked.append(
            SymbolSearchResult(
                symbol_id=symbol.symbol_id,
                name=symbol.name,
                kind=symbol.kind,
                file_path=symbol.file_path,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                linked_specs=tuple(symbol.linked_specs),
                score=score,
            )
        )

    ranked.sort(key=lambda item: (-item.score, item.symbol_id.casefold()))
    return ranked[:limit]
