from unittest.mock import Mock, patch

from arxiv_source import build_arxiv_query, fetch_arxiv, render_arxiv_group, render_arxiv_term
from querymodel import Group, Term

ATOM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2101.00001v1</id>
    <title>  A   Study   of   Things  </title>
    <summary>  An abstract   about things.  </summary>
    <published>2021-01-05T00:00:00Z</published>
    <author><name>Alice Example</name></author>
    <author><name>Bob Example</name></author>
    <category term="cs.LG"/>
    <category term="cs.AI"/>
    <link title="pdf" href="http://arxiv.org/pdf/2101.00001v1"/>
    <arxiv:doi>10.1000/example.doi</arxiv:doi>
  </entry>
</feed>
"""


def test_render_arxiv_term_title_abstract_searches_both_fields():
    term = Term(field="title/abstract", value="migraine")
    assert render_arxiv_term(term) == "(ti:migraine OR abs:migraine)"


def test_render_arxiv_term_quotes_multiword_values():
    term = Term(field="title/abstract", value="von Willebrand")
    assert render_arxiv_term(term) == '(ti:"von Willebrand" OR abs:"von Willebrand")'


def test_render_arxiv_term_maps_known_fields():
    assert render_arxiv_term(Term(field="title", value="migraine")) == "ti:migraine"
    assert render_arxiv_term(Term(field="tw", value="migraine")) == "all:migraine"
    assert render_arxiv_term(Term(field="au", value="Smith")) == "au:Smith"


def test_render_arxiv_term_returns_none_for_incompatible_fields():
    assert render_arxiv_term(Term(field="MeSH", value="Migraine Disorders")) is None
    assert render_arxiv_term(Term(field="ta", value="Nature")) is None
    assert render_arxiv_term(Term(field="ALL", value="raw text")) is None


def test_render_arxiv_group_drops_incompatible_terms_but_keeps_compatible_ones():
    group = Group(terms=[Term(field="MeSH", value="Migraine"), Term(field="title", value="migraine")])
    assert render_arxiv_group(group) == "ti:migraine"


def test_render_arxiv_group_returns_none_when_nothing_compatible():
    group = Group(terms=[Term(field="MeSH", value="Migraine")])
    assert render_arxiv_group(group) is None


def test_render_arxiv_group_multiple_compatible_terms_are_ored():
    group = Group(terms=[Term(field="title", value="migraine"), Term(field="au", value="Smith")])
    assert render_arxiv_group(group) == "(ti:migraine OR au:Smith)"


def test_build_arxiv_query_joins_compatible_groups_with_and():
    groups = [
        Group(terms=[Term(field="title", value="migraine")]),
        Group(terms=[Term(field="au", value="Smith")]),
    ]
    assert build_arxiv_query(groups) == "ti:migraine AND au:Smith"


def test_build_arxiv_query_none_for_mesh_only_query():
    groups = [Group(terms=[Term(field="MeSH", value="Migraine Disorders")])]
    assert build_arxiv_query(groups) is None


def test_fetch_arxiv_parses_entries_from_atom_feed():
    fake_response = Mock(text=ATOM_FEED)
    fake_response.raise_for_status = Mock()

    with patch("arxiv_source.requests.get", return_value=fake_response) as mock_get:
        records = fetch_arxiv("ti:migraine", max_results=25)

    mock_get.assert_called_once()
    assert mock_get.call_args.kwargs["params"] == {"search_query": "ti:migraine", "max_results": 25}

    assert len(records) == 1
    record = records[0]
    assert record.id == "2101.00001v1"
    assert record.title == "A Study of Things"
    assert record.abstract == "An abstract about things."
    assert record.authors == ["Alice Example", "Bob Example"]
    assert record.year == "2021"
    assert record.url == "http://arxiv.org/abs/2101.00001v1"
    assert record.pdf_url == "http://arxiv.org/pdf/2101.00001v1"
    assert record.categories == ["cs.LG", "cs.AI"]
    assert record.doi == "10.1000/example.doi"
    assert record.keywords == record.categories
    assert record.publication_types == ["Preprint"]
