from types import SimpleNamespace
from unittest.mock import Mock, patch

from openaccess import (
    check_unpaywall,
    fetch_pmc_fulltext,
    resolve_access_for,
    resolve_open_access,
)

PMC_FULLTEXT_XML = """<?xml version="1.0"?>
<pmc-articleset>
  <article>
    <body>
      <sec>
        <title>Introduction</title>
        <p>First paragraph.</p>
        <p>Second paragraph.</p>
      </sec>
    </body>
  </article>
</pmc-articleset>
"""

PMC_NO_BODY_XML = "<pmc-articleset><article><front/></article></pmc-articleset>"


def test_check_unpaywall_returns_open_access_info_when_oa():
    fake_response = Mock()
    fake_response.raise_for_status = Mock()
    fake_response.json.return_value = {
        "is_oa": True,
        "oa_status": "gold",
        "best_oa_location": {"url": "https://example.org/free.pdf"},
    }
    with patch("openaccess.requests.get", return_value=fake_response):
        result = check_unpaywall("10.1000/example")
    assert result.is_free is True
    assert result.source == "Open Access (gold)"
    assert result.url == "https://example.org/free.pdf"


def test_check_unpaywall_returns_closed_when_not_oa():
    fake_response = Mock()
    fake_response.raise_for_status = Mock()
    fake_response.json.return_value = {"is_oa": False}
    with patch("openaccess.requests.get", return_value=fake_response):
        result = check_unpaywall("10.1000/example")
    assert result.is_free is False
    assert result.source == "Closed (paywalled)"


def test_check_unpaywall_returns_none_on_network_error():
    with patch("openaccess.requests.get", side_effect=ConnectionError("boom")):
        assert check_unpaywall("10.1000/example") is None


def test_resolve_open_access_prefers_pmc_without_network_call():
    with patch("openaccess.requests.get") as mock_get:
        result = resolve_open_access(pmc="123456", doi="10.1000/example")
    mock_get.assert_not_called()
    assert result.is_free is True
    assert result.source == "PMC"
    assert "PMC123456" in result.url


def test_resolve_open_access_falls_back_to_unpaywall_by_doi():
    fake_response = Mock()
    fake_response.raise_for_status = Mock()
    fake_response.json.return_value = {"is_oa": True, "oa_status": "green", "best_oa_location": {}}
    with patch("openaccess.requests.get", return_value=fake_response):
        result = resolve_open_access(pmc=None, doi="10.1000/example")
    assert result.source == "Open Access (green)"


def test_resolve_open_access_unknown_without_pmc_or_doi():
    result = resolve_open_access(pmc=None, doi=None)
    assert result.is_free is False
    assert result.source == "Unknown"


def test_resolve_access_for_arxiv_is_always_free_and_skips_network():
    article = SimpleNamespace(pdf_url="https://arxiv.org/pdf/2101.00001", url="https://arxiv.org/abs/2101.00001")
    with patch("openaccess.requests.get") as mock_get:
        result = resolve_access_for("arxiv:2101.00001", article)
    mock_get.assert_not_called()
    assert result.is_free is True
    assert result.source == "Open Access (arXiv preprint)"
    assert result.url == "https://arxiv.org/pdf/2101.00001"


def test_resolve_access_for_pubmed_delegates_to_resolve_open_access():
    article = SimpleNamespace(pmc="123456", doi="10.1000/example")
    result = resolve_access_for("pubmed:12345", article)
    assert result.source == "PMC"


def test_fetch_pmc_fulltext_flattens_body_to_plain_text():
    fake_response = Mock(text=PMC_FULLTEXT_XML)
    fake_response.raise_for_status = Mock()
    with patch("openaccess.requests.get", return_value=fake_response):
        text = fetch_pmc_fulltext("123456")
    assert "INTRODUCTION" in text
    assert "First paragraph." in text
    assert "Second paragraph." in text


def test_fetch_pmc_fulltext_reports_missing_body():
    fake_response = Mock(text=PMC_NO_BODY_XML)
    fake_response.raise_for_status = Mock()
    with patch("openaccess.requests.get", return_value=fake_response):
        text = fetch_pmc_fulltext("123456")
    assert "not available via the PMC API" in text


def test_fetch_pmc_fulltext_reports_network_errors_as_a_message_not_a_raise():
    with patch("openaccess.requests.get", side_effect=ConnectionError("boom")):
        text = fetch_pmc_fulltext("123456")
    assert text.startswith("Could not fetch full text:")


def test_fetch_pmc_fulltext_includes_api_key_when_set(monkeypatch):
    monkeypatch.setenv("NCBI_API_KEY", "secret-key")
    fake_response = Mock(text=PMC_FULLTEXT_XML)
    fake_response.raise_for_status = Mock()
    with patch("openaccess.requests.get", return_value=fake_response) as mock_get:
        fetch_pmc_fulltext("123456")
    assert mock_get.call_args.kwargs["params"]["api_key"] == "secret-key"
