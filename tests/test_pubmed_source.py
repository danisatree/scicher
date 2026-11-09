from types import SimpleNamespace
from unittest.mock import Mock

from pubmed_source import CSV_FIELDS, article_to_row, format_citation, pubmed_language


def make_article(**overrides):
    defaults = dict(
        title="A Study of Things",
        authors=["Smith J", "Jones K"],
        journal="Nature",
        year="2021",
        volume="10",
        issue="2",
        pages="100-110",
        doi="10.1000/example",
        pmc="123456",
        issn="1234-5678",
        publication_types=["Journal Article"],
        mesh=["Migraine Disorders"],
        keywords=["migraine"],
        url="https://pubmed.ncbi.nlm.nih.gov/12345",
        abstract="An abstract.",
        content=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_pubmed_language_returns_empty_string_without_content():
    assert pubmed_language(make_article(content=None)) == ""


def test_pubmed_language_reads_language_element_from_content():
    language_el = Mock()
    language_el.text = " eng "
    content = Mock()
    content.find.return_value = language_el
    assert pubmed_language(make_article(content=content)) == "eng"
    content.find.assert_called_once_with(".//Language")


def test_pubmed_language_returns_empty_string_when_element_missing():
    content = Mock()
    content.find.return_value = None
    assert pubmed_language(make_article(content=content)) == ""


def test_article_to_row_none_article_gives_blank_row_with_pmid_and_source():
    row = article_to_row("12345", None, source="PubMed")
    assert row["pmid"] == "12345"
    assert row["source"] == "PubMed"
    assert all(row[field] == "" for field in CSV_FIELDS if field not in {"pmid", "source"})


def test_article_to_row_joins_list_fields_with_semicolons():
    row = article_to_row("12345", make_article(), source="PubMed")
    assert row["authors"] == "Smith J; Jones K"
    assert row["mesh"] == "Migraine Disorders"
    assert row["keywords"] == "migraine"
    assert row["title"] == "A Study of Things"
    assert row["pmc"] == "123456"


def test_article_to_row_handles_publication_types_as_dict():
    article = make_article(publication_types={"D016428": "Journal Article", "D016454": "Review"})
    row = article_to_row("12345", article, source="PubMed")
    assert row["publication_types"] == "Journal Article; Review"


def test_article_to_row_defaults_missing_fields_to_empty_string():
    article = make_article(title=None, authors=None, doi=None, mesh=None)
    row = article_to_row("12345", article, source="PubMed")
    assert row["title"] == ""
    assert row["authors"] == ""
    assert row["doi"] == ""
    assert row["mesh"] == ""


def test_format_citation_no_authors():
    article = make_article(authors=[])
    assert format_citation(article).startswith("(no authors listed).")


def test_format_citation_three_or_fewer_authors_are_listed():
    article = make_article(authors=["Smith J", "Jones K"])
    citation = format_citation(article)
    assert citation.startswith("Smith J, Jones K.")


def test_format_citation_more_than_three_authors_uses_et_al():
    article = make_article(authors=["Smith J", "Jones K", "Lee M", "Park S"])
    citation = format_citation(article)
    assert citation.startswith("Smith J et al.")


def test_format_citation_includes_journal_year_volume_issue_pages_and_doi():
    citation = format_citation(make_article())
    assert "A Study of Things." in citation
    assert "Nature (2021)." in citation
    assert "10/2/100-110." in citation
    assert "doi:10.1000/example" in citation


def test_format_citation_omits_missing_optional_parts():
    article = make_article(journal="", year="", volume=None, issue=None, pages=None, doi=None)
    citation = format_citation(article)
    assert "doi:" not in citation
    assert citation.endswith("A Study of Things.")
