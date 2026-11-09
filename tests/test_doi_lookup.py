from unittest.mock import Mock, patch

from doi_lookup import build_doi_record, fetch_doi_info, fetch_pmcid_for_doi, render_doi_detail
from openaccess import OpenAccessInfo

CROSSREF_WORK = {
    "DOI": "10.1000/example",
    "title": ["A Study of Things"],
    "author": [
        {"given": "Alice", "family": "Example"},
        {"given": "", "family": "NoGivenName"},
        {},
    ],
    "container-title": ["Nature"],
    "published-print": {"date-parts": [[2021, 3, 15]]},
    "volume": "10",
    "issue": "2",
    "page": "100-110",
    "ISSN": ["1234-5678"],
    "publisher": "Example Press",
    "type": "journal-article",
    "subject": ["Neuroscience"],
    "is-referenced-by-count": 42,
    "abstract": "<p>An <b>abstract</b>.</p>",
    "URL": "https://doi.org/10.1000/example",
}


def test_build_doi_record_flattens_crossref_work():
    record = build_doi_record(CROSSREF_WORK, pmc="123456")
    assert record.doi == "10.1000/example"
    assert record.title == "A Study of Things"
    assert record.authors == ["Alice Example", "NoGivenName"]
    assert record.journal == "Nature"
    assert record.year == "2021"
    assert record.volume == "10"
    assert record.issue == "2"
    assert record.pages == "100-110"
    assert record.pmc == "123456"
    assert record.issn == "1234-5678"
    assert record.publisher == "Example Press"
    assert record.publication_types == ["journal-article"]
    assert record.subjects == ["Neuroscience"]
    assert record.keywords == record.subjects
    assert record.citation_count == 42
    assert record.retracted is False
    assert record.abstract == "An abstract."
    assert record.url == "https://doi.org/10.1000/example"


def test_build_doi_record_handles_missing_optional_fields():
    record = build_doi_record({"DOI": "10.1000/bare"}, pmc=None)
    assert record.title == ""
    assert record.authors == []
    assert record.journal == ""
    assert record.year == ""
    assert record.volume is None
    assert record.pmc is None
    assert record.url == "https://doi.org/10.1000/bare"


def test_build_doi_record_detects_retraction():
    data = dict(CROSSREF_WORK, **{"update-to": [{"type": "retraction"}]})
    record = build_doi_record(data, pmc=None)
    assert record.retracted is True


def test_build_doi_record_falls_back_through_publication_date_variants():
    data = {"DOI": "10.1000/x", "published-online": {"date-parts": [[2019]]}}
    record = build_doi_record(data, pmc=None)
    assert record.year == "2019"


def test_render_doi_detail_flags_retraction_prominently():
    record = build_doi_record(dict(CROSSREF_WORK, **{"update-to": [{"type": "retraction"}]}), pmc=None)
    detail = render_doi_detail(record, OpenAccessInfo(is_free=False, source="Unknown"))
    assert detail.startswith("*** RETRACTED")


def test_render_doi_detail_includes_key_fields():
    record = build_doi_record(CROSSREF_WORK, pmc="123456")
    detail = render_doi_detail(record, OpenAccessInfo(is_free=True, source="PMC"))
    assert "DOI: 10.1000/example" in detail
    assert "Title: A Study of Things" in detail
    assert "Access: PMC" in detail
    assert "Cited by (Crossref): 42" in detail
    assert "Subjects: Neuroscience" in detail


def test_fetch_doi_info_returns_none_on_404():
    fake_response = Mock(status_code=404)
    with patch("doi_lookup.requests.get", return_value=fake_response):
        assert fetch_doi_info("10.1000/missing") is None


def test_fetch_doi_info_returns_message_payload_on_success():
    fake_response = Mock(status_code=200)
    fake_response.raise_for_status = Mock()
    fake_response.json.return_value = {"message": CROSSREF_WORK}
    with patch("doi_lookup.requests.get", return_value=fake_response):
        data = fetch_doi_info("10.1000/example")
    assert data == CROSSREF_WORK


def test_fetch_doi_info_returns_none_on_network_error():
    with patch("doi_lookup.requests.get", side_effect=ConnectionError("boom")):
        assert fetch_doi_info("10.1000/example") is None


def test_fetch_pmcid_for_doi_strips_pmc_prefix():
    fake_response = Mock()
    fake_response.raise_for_status = Mock()
    fake_response.json.return_value = {"records": [{"pmcid": "PMC123456"}]}
    with patch("doi_lookup.requests.get", return_value=fake_response):
        assert fetch_pmcid_for_doi("10.1000/example") == "123456"


def test_fetch_pmcid_for_doi_returns_none_when_not_in_pmc():
    fake_response = Mock()
    fake_response.raise_for_status = Mock()
    fake_response.json.return_value = {"records": [{}]}
    with patch("doi_lookup.requests.get", return_value=fake_response):
        assert fetch_pmcid_for_doi("10.1000/example") is None


def test_fetch_pmcid_for_doi_returns_none_on_network_error():
    with patch("doi_lookup.requests.get", side_effect=ConnectionError("boom")):
        assert fetch_pmcid_for_doi("10.1000/example") is None
