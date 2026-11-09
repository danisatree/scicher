from types import SimpleNamespace
from unittest.mock import Mock

from pubmed_source import (
    CSV_FIELDS,
    article_to_row,
    fetch_pubmed_articles_batch,
    format_citation,
    pubmed_language,
)


def minimal_pubmed_article_xml(pmid: str, title: str) -> str:
    """A single <PubmedArticle> element with just enough structure for
    metapub.PubMedArticle to parse without error."""
    return (
        "<PubmedArticle>"
        '<MedlineCitation Status="MEDLINE" Owner="NLM">'
        f"<PMID Version=\"1\">{pmid}</PMID>"
        '<Article PubModel="Print">'
        '<Journal><ISSN IssnType="Print">0000-0000</ISSN>'
        '<JournalIssue CitedMedium="Print"><Volume>1</Volume><Issue>1</Issue>'
        "<PubDate><Year>2020</Year></PubDate></JournalIssue>"
        "<Title>Test Journal</Title></Journal>"
        f"<ArticleTitle>{title}</ArticleTitle>"
        "<AuthorList><Author><LastName>Smith</LastName><ForeName>J</ForeName>"
        "<Initials>J</Initials></Author></AuthorList>"
        "</Article></MedlineCitation>"
        "<PubmedData><ArticleIdList>"
        f'<ArticleId IdType="pubmed">{pmid}</ArticleId>'
        "</ArticleIdList></PubmedData>"
        "</PubmedArticle>"
    )


def pubmed_article_set_xml(*articles: str) -> str:
    return f"<PubmedArticleSet>{''.join(articles)}</PubmedArticleSet>"


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


def test_fetch_pubmed_articles_batch_parses_all_articles_in_one_chunk():
    fetcher = Mock()
    fetcher.qs.efetch.return_value = pubmed_article_set_xml(
        minimal_pubmed_article_xml("111", "First Article"),
        minimal_pubmed_article_xml("222", "Second Article"),
    )

    articles = fetch_pubmed_articles_batch(fetcher, ["111", "222"])

    assert set(articles) == {"111", "222"}
    assert articles["111"].title == "First Article"
    assert articles["222"].title == "Second Article"
    fetcher.qs.efetch.assert_called_once_with({"db": "pubmed", "id": ["111", "222"]})


def test_fetch_pubmed_articles_batch_splits_into_chunks():
    fetcher = Mock()
    fetcher.qs.efetch.side_effect = [
        pubmed_article_set_xml(minimal_pubmed_article_xml("111", "First")),
        pubmed_article_set_xml(minimal_pubmed_article_xml("222", "Second")),
    ]

    articles = fetch_pubmed_articles_batch(fetcher, ["111", "222"], chunk_size=1)

    assert fetcher.qs.efetch.call_count == 2
    assert set(articles) == {"111", "222"}


def test_fetch_pubmed_articles_batch_leaves_missing_pmids_for_caller_to_retry():
    fetcher = Mock()
    fetcher.qs.efetch.return_value = pubmed_article_set_xml(
        minimal_pubmed_article_xml("111", "First Article")
    )

    articles = fetch_pubmed_articles_batch(fetcher, ["111", "999"])

    assert set(articles) == {"111"}


def test_fetch_pubmed_articles_batch_skips_chunk_on_request_failure():
    fetcher = Mock()
    fetcher.qs.efetch.side_effect = ConnectionError("boom")

    articles = fetch_pubmed_articles_batch(fetcher, ["111", "222"])

    assert articles == {}


def test_fetch_pubmed_articles_batch_skips_malformed_xml():
    fetcher = Mock()
    fetcher.qs.efetch.return_value = "<not valid xml"

    articles = fetch_pubmed_articles_batch(fetcher, ["111"])

    assert articles == {}
