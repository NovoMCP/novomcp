"""Tests for the search_literature PubMed fallback (roadmap v1.2.x).

Covers the NCBI esummary-record -> paper mapping and the regate that makes the
tool available with no Pinecone key. No network: the esummary record is a
fixture in the shape NCBI returns.
"""
from novomcp.mcp.tools import MCPToolExecutor, TOOL_LOCAL_REQUIREMENTS, _requirement_met


# A realistic esummary record (trimmed to the fields _pubmed_paper reads).
_ESUMMARY_REC = {
    "uid": "35140400",
    "pubdate": "2022 Feb 3",
    "source": "Nature",
    "fulljournalname": "Nature",
    "authors": [
        {"name": "Hu C", "authtype": "Author"},
        {"name": "Leche CA 2nd", "authtype": "Author"},
        {"name": "Kiyatkin A", "authtype": "Author"},
    ],
    "title": "Glioblastoma mutations alter EGFR dimer structure to prevent ligand bias.",
    "articleids": [
        {"idtype": "pubmed", "value": "35140400"},
        {"idtype": "doi", "value": "10.1038/s41586-021-04393-3"},
    ],
}


def test_pubmed_paper_shape():
    p = MCPToolExecutor._pubmed_paper("35140400", _ESUMMARY_REC)
    assert p["pmid"] == "35140400"
    assert p["title"].startswith("Glioblastoma mutations")
    assert p["authors"] == ["Hu C", "Leche CA 2nd", "Kiyatkin A"]
    assert p["year"] == 2022
    assert p["journal"] == "Nature"
    assert p["doi"] == "10.1038/s41586-021-04393-3"
    assert p["url"] == "https://pubmed.ncbi.nlm.nih.gov/35140400/"
    assert p["namespace"] == "pubmed"


def test_pubmed_paper_tolerates_missing_fields():
    p = MCPToolExecutor._pubmed_paper("1", {})
    assert p["pmid"] == "1"
    assert p["title"] == ""
    assert p["authors"] == []
    assert p["year"] is None
    assert p["doi"] == ""


def test_pubmed_paper_year_from_epubdate():
    p = MCPToolExecutor._pubmed_paper("2", {"epubdate": "2021 Dec 15"})
    assert p["year"] == 2021


def test_search_literature_available_without_pinecone(monkeypatch):
    # PubMed floor means the tool is available with no PINECONE_API_KEY set.
    monkeypatch.delenv("PINECONE_API_KEY", raising=False)
    reqs = TOOL_LOCAL_REQUIREMENTS["search_literature"]
    assert reqs == ["any"]
    assert all(_requirement_met(r) for r in reqs)


def test_search_patents_still_requires_pinecone(monkeypatch):
    # search_patents has no public fallback, so it stays gated.
    monkeypatch.delenv("PINECONE_API_KEY", raising=False)
    reqs = TOOL_LOCAL_REQUIREMENTS["search_patents"]
    assert reqs == ["env:PINECONE_API_KEY"]
    assert not all(_requirement_met(r) for r in reqs)
