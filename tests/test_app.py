from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from textual.widgets import DataTable

from main import ScicherApp
from querymodel import Term


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
