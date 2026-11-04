"""Deterministic vocabulary preparation shared by publication and retrieval.

@spec SPEC-SPECGRAPH-LEXICAL-INTENT
"""
from __future__ import annotations

import re
import unicodedata

PROJECTION_VERSION = 1
MAX_TERMS = 32
CANDIDATE_LIMIT = 100
MAX_VARIANTS = 2
STOPWORDS = frozenset("""a an the and or of for to from in on at by with without into is are was were
be been being do does did how what where which who when why this that these
those it its as can could would should please function method code
o a os as um uma uns umas e ou de da do das dos para por com sem em no na nos
nas ao aos que qual quais onde como quem quando porque se ser esta este isso
essa esse sao eh uma funcao metodo codigo
""".split())


def tokens(text: str) -> list[str]:
    """Decompose identifiers and accents without stemming or translation."""
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = "".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                   if not unicodedata.combining(c))
    return re.findall(r"[^\W_]+", text, flags=re.UNICODE)


def indexed_text(text: str) -> str:
    return " ".join(tokens(text))


def query_terms(query: str) -> list[str]:
    terms = list(dict.fromkeys(t for t in tokens(query) if t not in STOPWORDS))
    if len(terms) > MAX_TERMS:
        raise ValueError(f"query exceeds {MAX_TERMS} distinct searchable terms")
    return terms


def match_expression(terms: list[str], *, identifier: bool = False) -> str:
    # Tokens contain neither quotes nor FTS operators; still quote each literal.
    return (" AND " if identifier else " OR ").join('"' + t + '"' for t in terms)


def term_variants(term: str) -> list[str]:
    """Bounded English plural spellings, not prefixes, synonyms or translation.

    Keep the original spelling. Restrict rules to alphabetic ASCII words of at
    least three letters and protect common singular s endings. These mechanical
    alternatives are lexical evidence only: they do not establish word meaning.
    """
    if len(term) < 3 or not term.isascii() or not term.isalpha():
        return [term]
    variants = [term]
    if len(term) > 4 and term.endswith("ies"):
        variants.append(term[:-3] + "y")
    elif len(term) > 4 and term.endswith(("sses", "ches", "shes", "xes", "zes")):
        variants.append(term[:-2])
    elif len(term) > 3 and term.endswith("s") and not term.endswith(("ss", "us", "is")):
        variants.append(term[:-1])
    elif term.endswith("y") and term[-2] not in "aeiou":
        variants.append(term[:-1] + "ies")
    elif term.endswith(("s", "x", "z", "ch", "sh")):
        variants.append(term + "es")
    else:
        variants.append(term + "s")
    return list(dict.fromkeys(variants))[:MAX_VARIANTS]


def query_groups(terms: list[str], *, identifier: bool = False) -> list[list[str]]:
    """Preserve query-term identity while expanding only natural language."""
    return [[term] if identifier else term_variants(term) for term in terms]


def grouped_expression(groups: list[list[str]], *, identifier: bool = False) -> str:
    """Compile at most MAX_TERMS groups of MAX_VARIANTS literal FTS tokens."""
    return (" AND " if identifier else " OR ").join(
        "(" + match_expression(group) + ")" for group in groups)


def is_identifier(query: str) -> bool:
    return (not any(c.isspace() for c in query.strip())
            and (any(c in query for c in "_./:") or len(tokens(query)) > 1))
