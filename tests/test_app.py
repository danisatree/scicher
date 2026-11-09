from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from textual.widgets import DataTable

import persistence
from main import ScicherApp
from querymodel import Term
from screens import FullTextScreen


def text_of(widget) -> str:
    """Static/Label expose their current text only via the private `visual` renderable."""
    return str(widget.visual)


def make_article(pmid, **overrides):
    defaults = dict(
        title=f"Article {pmid}",
        authors=["Smith J"],
        journal="Nature",
        year="2021",
        volume=None,
        issue=None,
        pages=None,
        doi=None,
        pmc=None,
        issn=None,
        publication_types=[],
        mesh=[],
        keywords=[],
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}",
        abstract="An abstract.",
        content=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    """Keep query-log/settings/export files the app writes inside the test's tmp dir."""
    monkeypatch.chdir(tmp_path)


async def test_add_edit_delete_term_updates_query_preview():
    app = ScicherApp()
    async with app.run_test() as pilot:
        app.groups[-1].terms.append(Term(field="title/abstract", value="migraine"))
        app.refresh_groups_list()
        assert app.current_query() == "migraine[title/abstract]"

        app.groups[0].terms[0] = Term(field="title/abstract", value="headache")
        app.refresh_groups_list()
        assert app.current_query() == "headache[title/abstract]"

        list_view = app.query_one("#groups_list")
        list_view.index = 0
        await pilot.pause()
        app.action_delete_selected()
        assert app.current_query() == ""


async def test_run_search_populates_table_and_kpis():
    app = ScicherApp()
    async with app.run_test() as pilot:
        app.groups[-1].terms.append(Term(field="title/abstract", value="migraine"))
        app.query_one("#source_arxiv").value = False

        fake_fetcher = Mock()
        fake_fetcher.pmids_for_query.return_value = ["1", "2"]
        articles = {"1": make_article("1"), "2": make_article("2", pmc="999")}

        with (
            patch.object(app, "get_fetcher", return_value=fake_fetcher),
            patch("main.fetch_pubmed_articles_batch", return_value=articles),
        ):
            app.action_run_search()
            await app.workers.wait_for_complete()
            await pilot.pause()

        table = app.query_one("#results_table", DataTable)
        assert table.row_count == 2
        assert text_of(app.query_one("#kpi_total")) == "2"
        assert text_of(app.query_one("#kpi_open_access")) == "1"


async def test_sort_results_by_toggles_direction_on_repeated_calls():
    app = ScicherApp()
    async with app.run_test():
        table = app.query_one("#results_table", DataTable)
        table.add_row("PubMed", "1", "2020", "-", "Nature", "A", key="a")
        table.add_row("PubMed", "2", "2022", "-", "Nature", "B", key="b")
        table.add_row("PubMed", "3", "2021", "-", "Nature", "C", key="c")

        app.sort_results_by("year")
        assert app._sort_reverse is False

        app.sort_results_by("year")
        assert app._sort_reverse is True


async def test_undo_remove_restores_row_and_updates_kpis():
    app = ScicherApp()
    async with app.run_test():
        article = make_article("1")
        key = "pubmed:1"
        app._articles[key] = article
        app._last_result_keys = [key]
        table = app.query_one("#results_table", DataTable)
        table.add_row(*app._row_values(key, article), key=key)
        app._update_kpis()

        table.remove_row(key)
        app._articles.pop(key)
        app._last_result_keys.remove(key)
        app._last_removed = (key, article, 0)
        app._update_kpis()
        assert text_of(app.query_one("#kpi_total")) == "0"

        app.action_undo_remove()

        assert table.row_count == 1
        assert key in app._articles
        assert text_of(app.query_one("#kpi_total")) == "1"


async def test_undo_remove_with_nothing_to_undo_updates_status():
    app = ScicherApp()
    async with app.run_test():
        app.action_undo_remove()
        assert text_of(app.query_one("#status")) == "Nothing to undo."


async def test_export_csv_writes_file_with_all_results(tmp_path):
    app = ScicherApp()
    async with app.run_test() as pilot:
        article = make_article("1")
        key = "pubmed:1"
        app._articles[key] = article
        app._last_result_keys = [key]

        app.action_export_csv()
        await pilot.pause()
        await pilot.press("enter")  # accept the default export path
        await pilot.pause()

        default_path = next(tmp_path.glob("scicher_results_*.csv"), None)
        assert default_path is not None
        content = default_path.read_text(encoding="utf-8")
        assert "Article 1" in content


async def test_run_search_without_any_source_enabled_shows_status():
    app = ScicherApp()
    async with app.run_test():
        app.groups[-1].terms.append(Term(field="title/abstract", value="migraine"))
        app.query_one("#source_pubmed").value = False
        app.query_one("#source_arxiv").value = False

        app.action_run_search()

        assert "Enable at least one source" in text_of(app.query_one("#status"))


async def test_fetch_article_with_retries_succeeds_after_a_transient_failure():
    app = ScicherApp()
    async with app.run_test():
        fetcher = Mock()
        fetcher.article_by_pmid.side_effect = [ConnectionError("boom"), make_article("1")]

        with patch("main.asyncio.sleep", AsyncMock()):
            article = await app._fetch_article_with_retries(fetcher, "1", attempts=3)

        assert article is not None
        assert article.title == "Article 1"
        assert fetcher.article_by_pmid.call_count == 2


async def test_fetch_article_with_retries_gives_up_after_exhausting_attempts():
    app = ScicherApp()
    async with app.run_test():
        fetcher = Mock()
        fetcher.article_by_pmid.side_effect = ConnectionError("boom")

        with patch("main.asyncio.sleep", AsyncMock()):
            article = await app._fetch_article_with_retries(fetcher, "1", attempts=2)

        assert article is None
        assert fetcher.article_by_pmid.call_count == 2


async def test_show_detail_pubmed_article_with_pmc_is_free_without_network_call():
    app = ScicherApp()
    async with app.run_test():
        key = "pubmed:1"
        app._articles[key] = make_article("1", pmc="999", doi="10.1000/example")

        with patch("main.resolve_open_access") as mock_resolve:
            await app._show_detail(key)

        mock_resolve.assert_not_called()
        detail_text = text_of(app.query_one("#detail_text"))
        assert "Access: PMC (free)" in detail_text
        assert "Title: Article 1" in detail_text


async def test_show_detail_pubmed_article_without_pmc_checks_unpaywall():
    app = ScicherApp()
    async with app.run_test():
        key = "pubmed:1"
        app._articles[key] = make_article("1", pmc=None, doi="10.1000/example")

        with patch("main.resolve_open_access", return_value=Mock(source="Open Access (gold)")) as mock_resolve:
            await app._show_detail(key)

        mock_resolve.assert_called_once()
        detail_text = text_of(app.query_one("#detail_text"))
        assert "Access: Open Access (gold)" in detail_text


async def test_show_detail_arxiv_record_uses_resolve_access_for():
    app = ScicherApp()
    async with app.run_test():
        key = "arxiv:2101.00001"
        article = make_article("2101.00001", pmc=None, doi=None)
        app._articles[key] = article

        await app._show_detail(key)

        detail_text = text_of(app.query_one("#detail_text"))
        assert "Access: Open Access (arXiv preprint)" in detail_text
        assert "arXiv ID: 2101.00001" in detail_text


async def test_open_article_actions_remove_updates_state_and_status():
    app = ScicherApp()
    async with app.run_test():
        key = "pubmed:1"
        article = make_article("1")
        app._articles[key] = article
        app._last_result_keys = [key]
        table = app.query_one("#results_table", DataTable)
        table.add_row(*app._row_values(key, article), key=key)

        with patch.object(app, "push_screen_wait", AsyncMock(return_value="remove")):
            await app._open_article_actions(key)

        assert key not in app._articles
        assert key not in app._last_result_keys
        assert table.row_count == 0
        assert app._last_removed[0] == key
        assert "Removed PMID 1 from results" in text_of(app.query_one("#status"))


async def test_open_article_actions_fulltext_opens_full_text_screen():
    app = ScicherApp()
    async with app.run_test() as pilot:
        key = "pubmed:1"
        article = make_article("1", pmc="999")
        app._articles[key] = article
        app._last_result_keys = [key]

        with (
            patch.object(app, "push_screen_wait", AsyncMock(return_value="fulltext")),
            patch("main.fetch_pmc_fulltext", return_value="full text body"),
        ):
            await app._open_article_actions(key)
            await pilot.pause()

        assert "PMID 1 ready." in text_of(app.query_one("#status"))
        assert isinstance(app.screen_stack[-1], FullTextScreen)


async def test_action_load_query_parses_and_loads_saved_query():
    app = ScicherApp()
    async with app.run_test():
        with patch.object(app, "push_screen", side_effect=lambda _screen, callback: callback(("load", "migraine[title/abstract]"))):
            app.action_load_query()

        assert app.current_query() == "migraine[title/abstract]"
        assert "Loaded saved query: migraine[title/abstract]" in text_of(app.query_one("#status"))


async def test_action_load_query_watch_triggers_rerun_and_diff(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "WATCH_SNAPSHOTS_PATH", tmp_path / "snapshots.json")
    app = ScicherApp()
    async with app.run_test() as pilot:
        app.query_one("#source_arxiv").value = False
        fake_fetcher = Mock()
        fake_fetcher.pmids_for_query.return_value = ["1"]

        with (
            patch.object(app, "push_screen", side_effect=lambda _screen, callback: callback(("watch", "migraine[title/abstract]"))),
            patch.object(app, "get_fetcher", return_value=fake_fetcher),
            patch("main.fetch_pubmed_articles_batch", return_value={"1": make_article("1")}),
        ):
            app.action_load_query()
            await app.workers.wait_for_complete()
            await pilot.pause()

        status = text_of(app.query_one("#status"))
        assert "Watch: first run for this query" in status
        assert "1 result(s) saved as baseline" in status


async def test_action_log_query_appends_current_query_to_chosen_path(tmp_path):
    app = ScicherApp()
    async with app.run_test():
        app.groups[-1].terms.append(Term(field="title/abstract", value="migraine"))
        log_path = tmp_path / "custom_log.csv"

        with patch.object(app, "push_screen", side_effect=lambda _screen, callback: callback(str(log_path))):
            app.action_log_query()

        assert log_path.exists()
        assert "migraine[title/abstract]" in log_path.read_text(encoding="utf-8")
        assert f"Logged query to {log_path}" in text_of(app.query_one("#status"))


async def test_action_log_query_without_a_term_shows_status_and_does_not_open_dialog():
    app = ScicherApp()
    async with app.run_test():
        with patch.object(app, "push_screen") as mock_push_screen:
            app.action_log_query()

        mock_push_screen.assert_not_called()
        assert "Add at least one term before logging a query." in text_of(app.query_one("#status"))
