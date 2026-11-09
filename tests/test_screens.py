from types import SimpleNamespace
from unittest.mock import Mock, patch

from textual.app import App, ComposeResult
from textual.widgets import Checkbox, Input, ListView, Select, SelectionList

from openaccess import OpenAccessInfo
from querymodel import Filters
from screens import AddTermScreen, FiltersScreen, apply_article_action


class _HostApp(App):
    """Minimal host App for mounting a single modal screen in isolation."""

    def compose(self) -> ComposeResult:
        return []


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


async def test_add_term_screen_shows_mesh_suggestions_only_for_mesh_field():
    app = _HostApp()
    async with app.run_test() as pilot:
        screen = AddTermScreen()
        app.push_screen(screen)
        await pilot.pause()

        value_input = screen.query_one("#value_input", Input)
        suggestions = screen.query_one("#mesh_suggestions", ListView)

        value_input.value = "Migraine"
        await pilot.pause()
        assert suggestions.display is False  # default field is title/abstract, not MeSH

        screen.query_one("#field_select", Select).value = "MeSH"
        await pilot.pause()
        assert suggestions.display is True
        assert len(suggestions.children) > 0
        assert all("migraine" in child.name.casefold() for child in suggestions.children)


async def test_add_term_screen_selecting_a_suggestion_fills_the_input():
    app = _HostApp()
    async with app.run_test() as pilot:
        screen = AddTermScreen()
        app.push_screen(screen)
        await pilot.pause()

        screen.query_one("#field_select", Select).value = "MeSH"
        value_input = screen.query_one("#value_input", Input)
        value_input.value = "Migraine"
        await pilot.pause()

        suggestions = screen.query_one("#mesh_suggestions", ListView)
        first_match = suggestions.children[0].name
        suggestions.index = 0
        suggestions.action_select_cursor()
        await pilot.pause()

        assert value_input.value == first_match
        assert suggestions.display is False


async def test_add_term_screen_add_button_dismisses_with_term():
    app = _HostApp()
    async with app.run_test() as pilot:
        screen = AddTermScreen()
        result_box = {}

        def handle_result(term):
            result_box["term"] = term

        app.push_screen(screen, handle_result)
        await pilot.pause()

        screen.query_one("#value_input", Input).value = "migraine"
        await pilot.pause()
        await pilot.click("#add")
        await pilot.pause()

        assert result_box["term"].field == "title/abstract"
        assert result_box["term"].value == "migraine"


async def test_add_term_screen_cancel_dismisses_with_none():
    app = _HostApp()
    async with app.run_test() as pilot:
        screen = AddTermScreen()
        result_box = {"called": False, "term": "unset"}

        def handle_result(term):
            result_box["called"] = True
            result_box["term"] = term

        app.push_screen(screen, handle_result)
        await pilot.pause()
        await pilot.click("#cancel")
        await pilot.pause()

        assert result_box["called"] is True
        assert result_box["term"] is None


async def test_filters_screen_collects_all_fields_into_filters():
    app = _HostApp()
    async with app.run_test() as pilot:
        screen = FiltersScreen(Filters())
        result_box = {}
        app.push_screen(screen, lambda result: result_box.update(result=result))
        await pilot.pause()

        screen.query_one("#year_from_input", Input).value = "2015"
        screen.query_one("#year_to_input", Input).value = "2025"
        screen.query_one("#species_select", Select).value = "human"
        screen.query_one("#free_full_text", Checkbox).value = True
        screen.query_one("#max_results_select", Select).value = 100
        screen.query_one("#pub_types", SelectionList).select("Review")
        await pilot.pause()

        await pilot.click("#apply")
        await pilot.pause()

        filters = result_box["result"]
        assert filters == Filters(
            year_from="2015",
            year_to="2025",
            publication_types=frozenset({"Review"}),
            species="human",
            free_full_text=True,
            max_results=100,
        )


async def test_filters_screen_clear_dismisses_with_default_filters():
    app = _HostApp()
    async with app.run_test() as pilot:
        screen = FiltersScreen(Filters(species="human", free_full_text=True))
        result_box = {}
        app.push_screen(screen, lambda result: result_box.update(result=result))
        await pilot.pause()

        await pilot.click("#clear")
        await pilot.pause()

        assert result_box["result"] == Filters()


async def test_filters_screen_cancel_dismisses_with_none():
    app = _HostApp()
    async with app.run_test() as pilot:
        screen = FiltersScreen(Filters())
        result_box = {}
        app.push_screen(screen, lambda result: result_box.update(result=result))
        await pilot.pause()

        await pilot.click("#cancel")
        await pilot.pause()

        assert result_box["result"] is None
