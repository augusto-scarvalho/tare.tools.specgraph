import pytest
from specgraph.adr_parser import AdrParser
from specgraph.benchmark import benchmark_queries


def test_benchmark_rejects_empty_corpus_without_opening_index(tmp_path):
    with pytest.raises(ValueError, match="nonempty"):
        benchmark_queries(tmp_path, [])


@pytest.mark.parametrize("declared", ["Ratified", "Ratificado"])
def test_historical_ratification_is_normalized_without_rewriting_source(tmp_path, declared):
    source = tmp_path / "ADR-059.md"
    source.write_text(f"---\nid: ADR-059\nstatus: {declared}\n---\n# Scope\n", encoding="utf-8")
    nodes, errors = AdrParser.parse_file(source, tmp_path)
    assert not errors
    assert nodes[0].status == "ACCEPTED"
    assert nodes[0].raw_frontmatter["status"] == declared
