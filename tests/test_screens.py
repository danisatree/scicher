from types import SimpleNamespace
from unittest.mock import Mock, patch

from openaccess import OpenAccessInfo
from screens import apply_article_action


def make_article(**overrides):
    defaults = dict(
        url="https://pubmed.ncbi.nlm.nih.gov/12345",
        doi="10.1000/example",
        pmc=None,
        authors=["Smith J"],
        title="A Study of Things",
        journal="Nature",
        year="2021",
        volume="10",
        issue="2",
        pages="100-110",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_screen():
    screen = Mock()
    screen.app.copy_to_clipboard = Mock()
    screen.notify = Mock()
    return screen


def test_open_pubmed_opens_article_url():
    with patch("screens.webbrowser.open") as mock_open:
        result = apply_article_action(make_screen(), make_article(), OpenAccessInfo(False, "Unknown"), "open_pubmed")
    mock_open.assert_called_once_with("https://pubmed.ncbi.nlm.nih.gov/12345")
    assert result is False


def test_open_free_opens_access_url_when_free():
    access = OpenAccessInfo(is_free=True, source="PMC", url="https://ncbi.nlm.nih.gov/pmc/123")
    with patch("screens.webbrowser.open") as mock_open:
        apply_article_action(make_screen(), make_article(), access, "open_free")
    mock_open.assert_called_once_with("https://ncbi.nlm.nih.gov/pmc/123")


def test_open_free_does_nothing_when_not_free():
    access = OpenAccessInfo(is_free=False, source="Unknown", url=None)
    with patch("screens.webbrowser.open") as mock_open:
        apply_article_action(make_screen(), make_article(), access, "open_free")
    mock_open.assert_not_called()


def test_open_scihub_uses_current_mirror_base_url():
    with patch("screens.openaccess.SCI_HUB_BASE_URL", "https://mirror.example/"):
        with patch("screens.webbrowser.open") as mock_open:
            apply_article_action(make_screen(), make_article(doi="10.1000/example"), OpenAccessInfo(False, "Unknown"), "open_scihub")
    mock_open.assert_called_once_with("https://mirror.example/10.1000/example")


def test_open_scihub_does_nothing_without_doi():
    with patch("screens.webbrowser.open") as mock_open:
        apply_article_action(make_screen(), make_article(doi=None), OpenAccessInfo(False, "Unknown"), "open_scihub")
    mock_open.assert_not_called()


def test_read_fulltext_returns_true_when_pmc_present_and_does_not_open_browser():
    with patch("screens.webbrowser.open") as mock_open:
        result = apply_article_action(
            make_screen(), make_article(pmc="123456"), OpenAccessInfo(False, "Unknown"), "read_fulltext"
        )
    assert result is True
    mock_open.assert_not_called()


def test_read_fulltext_returns_false_without_pmc():
    result = apply_article_action(
        make_screen(), make_article(pmc=None), OpenAccessInfo(False, "Unknown"), "read_fulltext"
    )
    assert result is False


def test_copy_citation_copies_to_clipboard_and_notifies():
    screen = make_screen()
    apply_article_action(screen, make_article(), OpenAccessInfo(False, "Unknown"), "copy_citation")
    screen.app.copy_to_clipboard.assert_called_once()
    screen.notify.assert_called_once()


def test_copy_doi_copies_doi_string():
    screen = make_screen()
    apply_article_action(screen, make_article(doi="10.1000/example"), OpenAccessInfo(False, "Unknown"), "copy_doi")
    screen.app.copy_to_clipboard.assert_called_once_with("10.1000/example")


def test_copy_url_copies_article_url():
    screen = make_screen()
    apply_article_action(screen, make_article(), OpenAccessInfo(False, "Unknown"), "copy_url")
    screen.app.copy_to_clipboard.assert_called_once_with("https://pubmed.ncbi.nlm.nih.gov/12345")
