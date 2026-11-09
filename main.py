"""Scicher: interactive PubMed query builder and search TUI."""

import asyncio
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime
from typing import Any, ClassVar

from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Checkbox,
    DataTable,
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    ProgressBar,
    Static,
)

from arxiv_source import ArxivRecord, build_arxiv_query, fetch_arxiv
from openaccess import fetch_pmc_fulltext, resolve_access_for, resolve_open_access
from persistence import (
    DEFAULT_QUERY_LOG_PATH,
    Settings,
    apply_settings,
    append_query_log,
    load_settings,
    load_watch_snapshots,
    save_settings,
    save_watch_snapshot,
    write_results_csv,
)
from pubmed_source import article_to_row, fetch_pubmed_articles_batch, pubmed_language
from querymodel import Filters, Group, Term, build_query, describe_filters, parse_query, render_term
from screens import (
    AddTermScreen,
    ArticleActionsScreen,
    DoiLookupScreen,
    ExportCsvScreen,
    FiltersScreen,
    FullTextScreen,
    LoadQueryScreen,
    OrderedSystemCommandsProvider,
    QueryLogScreen,
    ResultsTable,
    SettingsScreen,
)
from theme import SCIENCE_DARK_THEME, SCIENCE_LIGHT_THEME


class ScicherApp(App):
    """TUI for building PubMed advanced-search queries and browsing results."""

    TITLE = ">_ SCICHER"
    SUB_TITLE = "SEARCH CONSOLE"
    CSS_PATH = "style.tcss"
    COMMANDS: ClassVar = {OrderedSystemCommandsProvider}
    BINDINGS: ClassVar = [
        ("1", "focus_query_terms", "Terms"),
        ("2", "focus_query_preview", "Query"),
        ("3", "focus_results", "Results"),
        ("4", "focus_detail", "Detail"),
        ("5", "doi_lookup", "DOI Lookup"),
        ("y", "sort_by_year", "Sort by year"),
        ("a", "add_term", "Add term"),
        ("n", "new_group", "New group (AND)"),
        ("m", "edit_selected", "Edit term"),
        ("d", "delete_selected", "Delete term"),
        ("f", "edit_filters", "Filters"),
        ("s", "run_search", "Search"),
        ("e", "export_csv", "Export"),
        ("l", "log_query", "Log query"),
        ("o", "load_query", "Load query"),
        ("u", "undo_remove", "Undo remove"),
        ("p", "edit_settings", "Settings"),
        ("t", "toggle_theme", "Toggle theme"),
        ("q", "quit", "Quit"),
        Binding("ctrl+p", "command_palette", "Commands", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.settings = load_settings()
        apply_settings(self.settings)
        self.groups: list[Group] = [Group()]
        self.filters = Filters()
        self._fetcher = None
        self._last_result_keys: list[str] = []
        self._last_query: str | None = None
        self._articles: dict[str, Any] = {}
        self._sort_column: str | None = None
        self._sort_reverse = False
        self._last_removed: tuple[str, Any, int] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="main_area"):
            with Vertical(id="results_section"):
                with Horizontal(id="kpi_row"):
                    yield Static("-", markup=False, id="kpi_total", classes="kpi_box")
                    yield Static("-", markup=False, id="kpi_sources", classes="kpi_box")
                    yield Static("-", markup=False, id="kpi_years", classes="kpi_box")
                    yield Static("-", markup=False, id="kpi_no_abstract", classes="kpi_box")
                    yield Static("-", markup=False, id="kpi_open_access", classes="kpi_box")
                yield ResultsTable(id="results_table")
            with Horizontal(id="bottom_row"):
                with Vertical(id="left_col"):
                    with Vertical(id="query_panel"):
                        yield ListView(id="groups_list")
                        with Horizontal(id="source_toggles"):
                            yield Checkbox(
                                "PubMed",
                                value=True,
                                id="source_pubmed",
                                compact=True,
                                tooltip="Search PubMed for this query. Uncheck to skip it.",
                            )
                            yield Checkbox(
                                "arXiv",
                                value=True,
                                id="source_arxiv",
                                compact=True,
                                tooltip=(
                                    "Search arXiv using the compatible parts of this query "
                                    "(MeSH/Journal terms have no arXiv equivalent and are "
                                    "dropped from that half of the search). Uncheck to skip it."
                                ),
                            )
                    with Vertical(id="query_section"):
                        with VerticalScroll(id="query_preview_scroll"):
                            yield Static("", id="query_preview", markup=False)
                with Vertical(id="detail_section"):
                    with VerticalScroll(id="detail_scroll"):
                        yield Static(
                            "Run a search and highlight a row to see its metadata here.",
                            id="detail_text",
                            markup=False,
                        )
            yield Label("", id="status", markup=False)
            yield ProgressBar(id="search_progress", show_eta=False)
        yield Footer()

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        # Drop the built-in "Theme" picker (we only ship two themes, toggled with 't')
        # and move "Quit" to the end of the list, where it belongs.
        quit_command: SystemCommand | None = None
        for command in super().get_system_commands(screen):
            if command.title == "Theme":
                continue
            if command.title == "Quit":
                quit_command = command
                continue
            yield command
        if quit_command is not None:
            yield quit_command

    def on_mount(self) -> None:
        self.register_theme(SCIENCE_DARK_THEME)
        self.register_theme(SCIENCE_LIGHT_THEME)
        self.theme = self.settings.theme

        query_panel = self.query_one("#query_panel")
        query_panel.border_title = "1. QUERY TERMS"
        query_panel.border_subtitle = "AND / OR"

        query_section = self.query_one("#query_section")
        query_section.border_title = "2. COMPILED QUERY"
        query_section.border_subtitle = "SEARCH STRATEGY"

        results_section = self.query_one("#results_section")
        results_section.border_title = "3. FOUND ARTICLES"

        self.query_one("#kpi_total").border_title = "FOUND"
        self.query_one("#kpi_sources").border_title = "SOURCES"
        self.query_one("#kpi_years").border_title = "YEAR RANGE"
        self.query_one("#kpi_no_abstract").border_title = "NO ABSTRACT"
        self.query_one("#kpi_open_access").border_title = "OPEN ACCESS (PMC)"

        detail_section = self.query_one("#detail_section")
        detail_section.border_title = "4. ARTICLE METADATA"

        self.refresh_groups_list()
        table = self.query_one("#results_table", DataTable)
        table.add_columns(
            ("Source", "source"),
            ("ID", "pmid"),
            ("Year", "year"),
            ("Lang", "language"),
            ("Journal", "journal"),
            ("Title", "title"),
        )
        table.cursor_type = "row"
        table.zebra_stripes = True

    def get_fetcher(self):
        if self._fetcher is None:
            from metapub import PubMedFetcher

            self._fetcher = PubMedFetcher()
        return self._fetcher

    def action_focus_query_terms(self) -> None:
        self.query_one("#groups_list", ListView).focus()

    def action_focus_query_preview(self) -> None:
        self.query_one("#query_preview_scroll", VerticalScroll).focus()

    def action_focus_results(self) -> None:
        self.query_one("#results_table", DataTable).focus()

    def action_focus_detail(self) -> None:
        self.query_one("#detail_scroll", VerticalScroll).focus()

    def action_doi_lookup(self) -> None:
        self.push_screen(DoiLookupScreen())

    NUMERIC_SORT_COLUMNS: ClassVar = {"pmid", "year"}

    def sort_results_by(self, column_key: str) -> None:
        table = self.query_one("#results_table", DataTable)
        if not table.row_count:
            return
        if self._sort_column == column_key:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column_key
            self._sort_reverse = False

        numeric = column_key in self.NUMERIC_SORT_COLUMNS

        def key_fn(value: object) -> Any:
            text = str(value)
            if numeric:
                return int(text) if text.isdigit() else -1
            return text.lower()

        table.sort(column_key, key=key_fn, reverse=self._sort_reverse)
        direction = "descending" if self._sort_reverse else "ascending"
        self.query_one("#status", Label).update(f"Sorted by {column_key} ({direction}).")

    def action_sort_by_year(self) -> None:
        self.sort_results_by("year")

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        if event.column_key.value:
            self.sort_results_by(event.column_key.value)

    def refresh_groups_list(self) -> None:
        list_view = self.query_one("#groups_list", ListView)
        list_view.clear()
        for gi, group in enumerate(self.groups):
            if not group.terms:
                empty_label = Label(f"[Group {gi + 1}: empty]", markup=False)
                list_view.append(ListItem(empty_label, name=f"{gi}:-1"))
                continue
            for ti, term in enumerate(group.terms):
                prefix = "  OR " if ti > 0 else f"Group {gi + 1}: "
                term_label = Label(f"{prefix}{render_term(term)}", markup=False)
                list_view.append(ListItem(term_label, name=f"{gi}:{ti}"))
        self.query_one("#query_preview", Static).update(
            f"Query: {self.current_query() or '(empty)'}"
        )
        query_section = self.query_one("#query_section")
        if self.filters.is_empty():
            query_section.border_subtitle = "PUBMED SYNTAX"
        else:
            query_section.border_subtitle = f"FILTERS ACTIVE (f to edit) · {describe_filters(self.filters)}"

    def current_query(self) -> str:
        return build_query(self.groups, self.filters)

    def action_new_group(self) -> None:
        self.groups.append(Group())
        self.refresh_groups_list()

    def action_add_term(self) -> None:
        def handle_result(term: Term | None) -> None:
            if term is not None:
                self.groups[-1].terms.append(term)
                self.refresh_groups_list()

        self.push_screen(AddTermScreen(), handle_result)

    def action_delete_selected(self) -> None:
        list_view = self.query_one("#groups_list", ListView)
        item = list_view.highlighted_child
        if item is None or not item.name:
            return
        gi_str, ti_str = item.name.split(":")
        gi, ti = int(gi_str), int(ti_str)
        if ti >= 0:
            del self.groups[gi].terms[ti]
        if not self.groups[gi].terms and len(self.groups) > 1:
            del self.groups[gi]
        if not self.groups:
            self.groups = [Group()]
        self.refresh_groups_list()

    def action_edit_selected(self) -> None:
        list_view = self.query_one("#groups_list", ListView)
        item = list_view.highlighted_child
        if item is None or not item.name:
            return
        gi_str, ti_str = item.name.split(":")
        gi, ti = int(gi_str), int(ti_str)
        if ti < 0:
            self.query_one("#status", Label).update("Selected group is empty, nothing to edit.")
            return
        term = self.groups[gi].terms[ti]

        def handle_result(updated: Term | None) -> None:
            if updated is not None:
                self.groups[gi].terms[ti] = updated
                self.refresh_groups_list()

        self.push_screen(AddTermScreen(term), handle_result)

    def action_edit_filters(self) -> None:
        def handle_result(updated: Filters | None) -> None:
            if updated is not None:
                self.filters = updated
                self.refresh_groups_list()

        self.push_screen(FiltersScreen(self.filters), handle_result)

    def action_load_query(self) -> None:
        def handle_result(result: tuple[str, str] | None) -> None:
            if result is None:
                return
            action, query = result
            groups, filters = parse_query(query)
            self.groups = groups or [Group()]
            self.filters = filters
            self.refresh_groups_list()
            if action == "watch":
                self.run_worker(self._watch_query(query), group="search", exclusive=True)
            else:
                self.query_one("#status", Label).update(f"Loaded saved query: {query}")

        self.push_screen(LoadQueryScreen(DEFAULT_QUERY_LOG_PATH), handle_result)

    async def _watch_query(self, query: str) -> None:
        """Re-runs a saved query and reports what's new since its last watched run,
        using the latest result-key snapshot for this exact query string."""
        previous = set(load_watch_snapshots().get(query, []))
        use_pubmed = self.query_one("#source_pubmed", Checkbox).value
        use_arxiv = self.query_one("#source_arxiv", Checkbox).value
        self.query_one("#status", Label).update(f"Watch: re-running {query} ...")
        await self._do_search(query, use_pubmed, use_arxiv)
        current = set(self._last_result_keys)
        save_watch_snapshot(query, sorted(current))
        status = self.query_one("#status", Label)
        if not previous:
            status.update(
                f"Watch: first run for this query — {len(current)} result(s) saved as baseline."
            )
        else:
            new_count = len(current - previous)
            status.update(
                f"Watch: {new_count} new since last run ({len(current)} total for: {query})."
            )

    def action_undo_remove(self) -> None:
        status = self.query_one("#status", Label)
        if self._last_removed is None:
            status.update("Nothing to undo.")
            return
        key, article, index = self._last_removed
        self._last_removed = None
        if article is not None:
            self._articles[key] = article
        if key not in self._last_result_keys:
            self._last_result_keys.insert(min(index, len(self._last_result_keys)), key)
        table = self.query_one("#results_table", DataTable)
        table.add_row(*self._row_values(key, article), key=key)  # always appended at the bottom
        id_label = "arXiv ID" if key.startswith("arxiv:") else "PMID"
        status.update(f"Restored {id_label} {key.split(':', 1)[1]} to results.")
        self._update_kpis()

    def action_edit_settings(self) -> None:
        def handle_result(updated: Settings | None) -> None:
            if updated is None:
                return
            self.settings = updated
            apply_settings(updated)
            self._fetcher = None  # force a fresh client so a new API key takes effect
            try:
                save_settings(updated)
            except OSError as exc:
                self.query_one("#status", Label).update(f"Could not save settings: {exc}")
                return
            self.query_one("#status", Label).update("Settings saved.")

        self.push_screen(SettingsScreen(self.settings), handle_result)

    def action_toggle_theme(self) -> None:
        new_theme = "science-light" if self.theme == "science-dark" else "science-dark"
        self.theme = new_theme
        self.settings = replace(self.settings, theme=new_theme)
        try:
            save_settings(self.settings)
        except OSError:
            pass  # theme toggle should never fail the UI even if the save doesn't stick

    def action_run_search(self) -> None:
        query = self.current_query()
        if not query:
            self.query_one("#status", Label).update("Add at least one term before searching.")
            return
        status = self.query_one("#status", Label)
        use_pubmed = self.query_one("#source_pubmed", Checkbox).value
        use_arxiv = self.query_one("#source_arxiv", Checkbox).value
        if not use_pubmed and not use_arxiv:
            status.update("Enable at least one source (PubMed or arXiv) before searching.")
            return
        status.update(f"Searching: {query} ...")
        self.run_worker(
            self._do_search(query, use_pubmed, use_arxiv), group="search", exclusive=True
        )

    async def _do_search(self, query: str, use_pubmed: bool, use_arxiv: bool) -> None:
        status = self.query_one("#status", Label)
        table = self.query_one("#results_table", DataTable)
        progress = self.query_one("#search_progress", ProgressBar)
        table.clear()
        self.query_one("#detail_text", Static).update(
            "Run a search and highlight a row to see its metadata here."
        )
        progress.display = True
        progress.update(total=None, progress=0)

        max_results = self.filters.max_results
        fetcher = self.get_fetcher()
        pmids: list[str] = []
        pubmed_note = ""
        if use_pubmed:
            try:
                pmids = await self.run_in_thread(
                    lambda: fetcher.pmids_for_query(query, retmax=max_results)
                )
            except Exception as exc:  # noqa: BLE001 - surface any NCBI/network error to the user
                pubmed_note = f"PubMed search failed: {exc}"
        else:
            pubmed_note = "PubMed disabled"

        arxiv_records: list[ArxivRecord] = []
        arxiv_note = ""
        if use_arxiv:
            arxiv_query = build_arxiv_query(self.groups)
            if arxiv_query:
                try:
                    arxiv_records = await self.run_in_thread(
                        lambda: fetch_arxiv(arxiv_query, max_results=max_results)
                    )
                except Exception as exc:  # noqa: BLE001 - arXiv is best-effort, never block PubMed results
                    arxiv_note = f"arXiv search failed: {exc}"
            else:
                arxiv_note = "arXiv skipped (no compatible terms, e.g. MeSH-only query)"
        else:
            arxiv_note = "arXiv disabled"

        total = len(pmids) + len(arxiv_records)
        self._last_result_keys = [f"pubmed:{pmid}" for pmid in pmids] + [
            f"arxiv:{r.id}" for r in arxiv_records
        ]
        self._last_query = query
        self._articles = {}
        try:
            append_query_log(DEFAULT_QUERY_LOG_PATH, query, str(total))
        except OSError:
            pass  # non-fatal: searching should still work even if the log can't be written

        notes = " ".join(f"[{note}]" for note in (pubmed_note, arxiv_note) if note)
        status.update(f"Found {len(pmids)} PubMed + {len(arxiv_records)} arXiv result(s). {notes}")
        if total == 0:
            progress.display = False
            self._update_kpis()
            return

        progress.update(total=total, progress=0)
        if pmids:
            pubmed_articles = await self.run_in_thread(
                lambda: fetch_pubmed_articles_batch(fetcher, pmids)
            )
            progress.advance(len(pubmed_articles))
            for pmid in pmids:
                if pmid not in pubmed_articles:
                    article = await self._fetch_article_with_retries(fetcher, pmid)
                    if article is not None:
                        pubmed_articles[pmid] = article
                    progress.advance(1)

            for pmid in pmids:
                article = pubmed_articles.get(pmid)
                key = f"pubmed:{pmid}"
                if article is not None:
                    self._articles[key] = article
                table.add_row(*self._row_values(key, article), key=key)

        for record in arxiv_records:
            key = f"arxiv:{record.id}"
            self._articles[key] = record
            table.add_row(*self._row_values(key, record), key=key)
            progress.advance(1)

        progress.display = False
        self._update_kpis()
        summary = (
            f"Found {len(pmids)} PubMed + {len(arxiv_records)} arXiv result(s) "
            f"for: {query} — details loaded, ready to export."
        )
        for note in (pubmed_note, arxiv_note):
            if note:
                summary += f" [{note}]"
        status.update(summary)

    def _row_values(self, key: str, article: Any) -> tuple[str, str, str, str, str, str]:
        """Builds a §3 table row's cell values for an article, or a "failed to fetch"
        placeholder if article is None (a PubMed fetch that didn't succeed). Shared between
        populating the table after a search and re-adding a row undone with 'u'."""
        is_arxiv = key.startswith("arxiv:")
        display_id = key.split(":", 1)[1]
        source = "arXiv" if is_arxiv else "PubMed"
        if article is None:
            return (source, display_id, "?", "-", "", "(failed to fetch details)")
        year = article.year or "?"
        language = "-" if is_arxiv else (pubmed_language(article) or "-")
        journal = article.journal or ""
        title = article.title or "(no title)"
        return (source, display_id, year, language, journal, title)

    def _update_kpis(self) -> None:
        """Recomputed from self._last_result_keys/self._articles (not passed-in counts),
        so removing a result from §3 keeps these numbers accurate without a re-search."""
        pmid_count = sum(1 for key in self._last_result_keys if key.startswith("pubmed:"))
        arxiv_count = sum(1 for key in self._last_result_keys if key.startswith("arxiv:"))
        articles = [self._articles[key] for key in self._last_result_keys if key in self._articles]
        years = [int(a.year) for a in articles if a.year and str(a.year).isdigit()]
        no_abstract = sum(1 for a in articles if not getattr(a, "abstract", None))
        open_access = sum(1 for a in articles if getattr(a, "pmc", None))
        self.query_one("#kpi_total", Static).update(str(pmid_count + arxiv_count))
        self.query_one("#kpi_sources", Static).update(f"{pmid_count} PubMed / {arxiv_count} arXiv")
        self.query_one("#kpi_years", Static).update(
            f"{min(years)}–{max(years)}" if years else "-"
        )
        self.query_one("#kpi_no_abstract", Static).update(str(no_abstract))
        self.query_one("#kpi_open_access", Static).update(str(open_access))

    async def run_in_thread(self, fn):
        return await asyncio.to_thread(fn)

    async def _fetch_article_with_retries(self, fetcher, pmid: str, attempts: int = 3) -> Any:
        """PubMed's eutils occasionally rejects a valid PMID under rapid sequential
        requests (no API key means a strict 3 req/sec limit); the same PMID almost
        always succeeds moments later, so a couple of short retries fix most of these.
        """
        for attempt in range(attempts):
            try:
                return await self.run_in_thread(lambda p=pmid: fetcher.article_by_pmid(p))
            except Exception:  # noqa: BLE001 - retry, then let the caller show a fallback
                if attempt < attempts - 1:
                    await asyncio.sleep(0.6)
        return None

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None or event.row_key.value is None:
            return
        self.run_worker(self._show_detail(str(event.row_key.value)), group="detail", exclusive=True)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key is None or event.row_key.value is None:
            return
        self.run_worker(
            self._open_article_actions(str(event.row_key.value)), group="detail", exclusive=True
        )

    async def _open_article_actions(self, key: str) -> None:
        article = self._articles.get(key)
        if article is None and key.startswith("pubmed:"):
            fetcher = self.get_fetcher()
            article = await self._fetch_article_with_retries(fetcher, key.split(":", 1)[1])
            if article is not None:
                self._articles[key] = article
        if article is None:
            self.query_one("#status", Label).update(f"Could not load {key} to open actions.")
            return
        access = await self.run_in_thread(lambda: resolve_access_for(key, article))
        display_id = key.split(":", 1)[1]
        is_arxiv = key.startswith("arxiv:")
        id_label = "arXiv ID" if is_arxiv else "PMID"
        result = await self.push_screen_wait(
            ArticleActionsScreen(display_id, article, access, id_label, is_arxiv)
        )
        if result == "remove":
            row_index = (
                self._last_result_keys.index(key) if key in self._last_result_keys else 0
            )
            self._last_removed = (key, self._articles.get(key), row_index)
            table = self.query_one("#results_table", DataTable)
            table.remove_row(key)
            self._articles.pop(key, None)
            if key in self._last_result_keys:
                self._last_result_keys.remove(key)
            self.query_one("#status", Label).update(
                f"Removed {id_label} {display_id} from results. Press 'u' to undo."
            )
            self._update_kpis()
            # DataTable's own cursor-moved event doesn't fire here: removing a row re-sets
            # cursor_coordinate to the *same* coordinate (just occupied by a different row
            # now), and its watcher only posts RowHighlighted when the coordinate changes.
            # So §4 has to be refreshed for whatever row the cursor landed on, by hand.
            if table.row_count:
                new_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
                if new_key is not None:
                    self.run_worker(self._show_detail(str(new_key)), group="detail")
            else:
                self.query_one("#detail_text", Static).update(
                    "Run a search and highlight a row to see its metadata here."
                )
        elif result == "fulltext":
            status = self.query_one("#status", Label)
            status.update(f"Fetching full text for PMC{article.pmc}...")
            text = await self.run_in_thread(lambda: fetch_pmc_fulltext(article.pmc))
            status.update(f"{id_label} {display_id} ready.")
            self.push_screen(FullTextScreen(article.title or "(no title)", text))

    async def _show_detail(self, key: str) -> None:
        detail = self.query_one("#detail_text", Static)
        display_id = key.split(":", 1)[1]
        id_label = "PMID" if key.startswith("pubmed:") else "arXiv ID"
        article = self._articles.get(key)
        if article is None and key.startswith("pubmed:"):
            detail.update(f"Loading metadata for {id_label} {display_id}...")
            fetcher = self.get_fetcher()
            article = await self._fetch_article_with_retries(fetcher, display_id)
            if article is not None:
                self._articles[key] = article
        if article is None:
            detail.update(f"Failed to load {id_label} {display_id} after several attempts.")
            return

        def render(access_line: str) -> str:
            return (
                f"{id_label}: {display_id}\n"
                f"Title: {article.title}\n"
                f"Authors: {', '.join(article.authors or [])}\n"
                f"Journal: {article.journal} ({article.year})\n"
                f"Volume/Issue/Pages: {article.volume or '?'}/{article.issue or '?'}/"
                f"{article.pages or '?'}\n"
                f"DOI: {article.doi or '-'}\n"
                f"PMC: {article.pmc or '-'}\n"
                f"Access: {access_line}\n"
                f"URL: {article.url}\n\n"
                f"Abstract:\n{article.abstract or '(no abstract)'}"
            )

        if key.startswith("arxiv:"):
            detail.update(render(resolve_access_for(key, article).source))
        elif article.pmc:
            detail.update(render("PMC (free)"))
        elif article.doi:
            detail.update(render("checking..."))
            access = await self.run_in_thread(lambda: resolve_open_access(article.pmc, article.doi))
            detail.update(render(access.source))
        else:
            detail.update(render("Unknown"))

    def action_log_query(self) -> None:
        status = self.query_one("#status", Label)
        query = self.current_query()
        if not query:
            status.update("Add at least one term before logging a query.")
            return

        default_path = DEFAULT_QUERY_LOG_PATH
        result_count = str(len(self._last_result_keys)) if query == self._last_query else ""

        def handle_result(path: str | None) -> None:
            if not path:
                return
            try:
                append_query_log(path, query, result_count)
            except OSError as exc:
                status.update(f"Logging query failed: {exc}")
                return
            status.update(f"Logged query to {path}")

        self.push_screen(QueryLogScreen(default_path), handle_result)

    def action_export_csv(self) -> None:
        status = self.query_one("#status", Label)
        if not self._last_result_keys:
            status.update("Run a search before exporting.")
            return

        default_path = f"scicher_results_{datetime.now():%Y%m%d_%H%M%S}.csv"

        def handle_result(path: str | None) -> None:
            if not path:
                return
            rows = [
                article_to_row(
                    key.split(":", 1)[1],
                    self._articles.get(key),
                    source="arXiv" if key.startswith("arxiv:") else "PubMed",
                )
                for key in self._last_result_keys
            ]
            try:
                write_results_csv(path, rows)
            except OSError as exc:
                status.update(f"Export failed: {exc}")
                return
            status.update(f"Exported {len(rows)} result(s) to {path}")

        self.push_screen(ExportCsvScreen(default_path), handle_result)


def main() -> None:
    ScicherApp().run()


if __name__ == "__main__":
    main()
