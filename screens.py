"""Modal screens (dialogs) and small supporting widgets for the Scicher TUI."""

import asyncio
import webbrowser
from typing import Any, ClassVar, TypeVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.system_commands import SystemCommandsProvider
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    Label,
    ListItem,
    ListView,
    Select,
    SelectionList,
    Static,
)

import openaccess
from doi_lookup import DoiRecord, build_doi_record, fetch_doi_info, fetch_pmcid_for_doi, render_doi_detail
from openaccess import OpenAccessInfo
from persistence import Settings, read_query_log
from pubmed_source import format_citation
from querymodel import (
    FIELD_CHOICES,
    MAX_RESULTS_CHOICES,
    PUBLICATION_TYPE_CHOICES,
    SPECIES_CHOICES,
    Filters,
    Term,
    load_mesh_terms,
    match_mesh_terms,
)

ResultT = TypeVar("ResultT")


class EscDismissScreen(ModalScreen[ResultT]):
    """A ModalScreen that can also be closed with Escape (dismissing with None the same way
    its Cancel button does) and navigated with arrow keys, not just Tab/Shift+Tab. Widgets
    that already use arrow keys themselves (Input, Select, SelectionList) see them first and
    keep their own behavior; this only kicks in for widgets like Button that don't."""

    BINDINGS: ClassVar = [
        ("escape", "dismiss_none", "Cancel"),
        ("left", "focus_previous", "Previous"),
        ("up", "focus_previous", "Previous"),
        ("right", "focus_next", "Next"),
        ("down", "focus_next", "Next"),
    ]

    def action_dismiss_none(self) -> None:
        self.dismiss(None)

    def action_focus_previous(self) -> None:
        self.focus_previous()

    def action_focus_next(self) -> None:
        self.focus_next()


class AddTermScreen(EscDismissScreen[Term | None]):
    """Modal dialog for adding a single search term."""

    def __init__(self, term: Term | None = None) -> None:
        super().__init__()
        self._term = term

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Edit search term" if self._term else "Add search term")
            yield Select(
                FIELD_CHOICES, value=self._term.field if self._term else "title/abstract",
                id="field_select",
            )
            yield Input(
                value=self._term.value if self._term else "",
                placeholder="term value, e.g. meteorosensitivity",
                id="value_input",
            )
            yield ListView(id="mesh_suggestions")
            with Horizontal():
                yield Button("Save" if self._term else "Add", variant="primary", id="add")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._mesh_active = (self._term.field if self._term else "title/abstract") == "MeSH"
        self._suppress_next_change = False
        self.query_one("#mesh_suggestions", ListView).display = False
        self.query_one("#value_input", Input).focus()

    def _suggestions_list(self) -> ListView:
        return self.query_one("#mesh_suggestions", ListView)

    def _update_suggestions(self, query: str) -> None:
        suggestions = self._suggestions_list()
        if not self._mesh_active:
            suggestions.display = False
            return
        matches = match_mesh_terms(load_mesh_terms(), query)
        suggestions.clear()
        if not matches:
            suggestions.display = False
            return
        for term in matches:
            suggestions.append(ListItem(Label(term), name=term))
        suggestions.display = True

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "field_select":
            self._mesh_active = event.value == "MeSH"
            self._update_suggestions(self.query_one("#value_input", Input).value)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "value_input":
            return
        if self._suppress_next_change:
            self._suppress_next_change = False
            return
        self._update_suggestions(event.value)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "mesh_suggestions" or event.item.name is None:
            return
        value_input = self.query_one("#value_input", Input)
        self._suppress_next_change = True
        value_input.value = event.item.name
        value_input.cursor_position = len(value_input.value)
        self._suggestions_list().display = False
        value_input.focus()

    def action_focus_next(self) -> None:
        suggestions = self._suggestions_list()
        if self.focused is self.query_one("#value_input", Input) and suggestions.display:
            suggestions.focus()
            if suggestions.index is None:
                suggestions.index = 0
            return
        self.focus_next()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add":
            value = self.query_one("#value_input", Input).value.strip()
            field_ = self.query_one("#field_select", Select).value
            if value:
                self.dismiss(Term(field=str(field_), value=value))
            else:
                self.dismiss(None)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        field_ = self.query_one("#field_select", Select).value
        self.dismiss(Term(field=str(field_), value=value) if value else None)


class ExportCsvScreen(EscDismissScreen[str | None]):
    """Modal dialog for choosing the CSV file path to export results to."""

    def __init__(self, default_path: str) -> None:
        super().__init__()
        self._default_path = default_path

    def compose(self) -> ComposeResult:
        with Vertical(id="export_dialog"):
            yield Label("Export results to CSV")
            yield Input(value=self._default_path, id="path_input")
            with Horizontal():
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#path_input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            path = self.query_one("#path_input", Input).value.strip()
            self.dismiss(path or None)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value or None)


class QueryLogScreen(EscDismissScreen[str | None]):
    """Modal dialog for choosing the CSV log file to append the current query to."""

    def __init__(self, default_path: str) -> None:
        super().__init__()
        self._default_path = default_path

    def compose(self) -> ComposeResult:
        with Vertical(id="log_dialog"):
            yield Label("Append current query to log CSV (creates the file if missing)")
            yield Input(value=self._default_path, id="path_input")
            with Horizontal():
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#path_input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            path = self.query_one("#path_input", Input).value.strip()
            self.dismiss(path or None)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value or None)


class FiltersScreen(EscDismissScreen[Filters | None]):
    """Modal dialog for editing year/publication-type/species/full-text filters."""

    def __init__(self, filters: Filters) -> None:
        super().__init__()
        self._filters = filters

    def compose(self) -> ComposeResult:
        with Vertical(id="filters_dialog"):
            yield Label("Filters")
            with Horizontal(id="filters_columns"):
                with Vertical(id="filters_col_left"):
                    yield Label(
                        "Year published (leave both blank for no limit)", classes="filter_hint"
                    )
                    with Horizontal(id="year_row"):
                        yield Input(
                            value=self._filters.year_from,
                            placeholder="From year, e.g. 2015",
                            restrict=r"\d*",
                            id="year_from_input",
                        )
                        yield Input(
                            value=self._filters.year_to,
                            placeholder="To year, e.g. 2025",
                            restrict=r"\d*",
                            id="year_to_input",
                        )
                    yield Label("Species (PubMed/MeSH only)", classes="filter_hint")
                    yield Select(SPECIES_CHOICES, value=self._filters.species, id="species_select")
                    yield Checkbox(
                        "Free full text only (PubMed only)",
                        value=self._filters.free_full_text,
                        id="free_full_text",
                    )
                    yield Label(
                        "Max results per source (higher = slower, more API calls)",
                        classes="filter_hint",
                    )
                    yield Select(
                        MAX_RESULTS_CHOICES,
                        value=self._filters.max_results,
                        allow_blank=False,
                        id="max_results_select",
                    )
                with Vertical(id="filters_col_right"):
                    yield Label(
                        "Publication type (PubMed only; selected are OR-ed)",
                        classes="filter_hint",
                    )
                    yield SelectionList[str](
                        *[
                            (pt, pt, pt in self._filters.publication_types)
                            for pt in PUBLICATION_TYPE_CHOICES
                        ],
                        id="pub_types",
                    )
            with Horizontal():
                yield Button("Apply", variant="primary", id="apply")
                yield Button("Clear filters", id="clear")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply":
            self.dismiss(self._collect_filters())
        elif event.button.id == "clear":
            self.dismiss(Filters())
        else:
            self.dismiss(None)

    def _collect_filters(self) -> Filters:
        pub_types = self.query_one("#pub_types", SelectionList).selected
        species = self.query_one("#species_select", Select).value
        return Filters(
            year_from=self.query_one("#year_from_input", Input).value.strip(),
            year_to=self.query_one("#year_to_input", Input).value.strip(),
            publication_types=frozenset(pub_types),
            species=str(species),
            free_full_text=self.query_one("#free_full_text", Checkbox).value,
            max_results=int(self.query_one("#max_results_select", Select).value),
        )


class LoadQueryScreen(EscDismissScreen[tuple[str, str] | None]):
    """Modal dialog for picking a previously logged query from a CSV file. Dismisses with
    ("load", query) for a plain reload, or ("watch", query) to re-run it and report what's
    new since the last run of that exact query (see persistence.WATCH_SNAPSHOTS_PATH)."""

    def __init__(self, default_path: str) -> None:
        super().__init__()
        self._default_path = default_path

    def compose(self) -> ComposeResult:
        with Vertical(id="load_dialog"):
            yield Label("Load a query previously saved with Log query (l)")
            with Horizontal(id="load_path_row"):
                yield Input(value=self._default_path, id="path_input")
                yield Button("Load", id="load")
            yield ListView(id="log_entries")
            with Horizontal():
                yield Button("Use selected", variant="primary", id="use")
                yield Button(
                    "Re-run & diff",
                    id="watch",
                    tooltip="Re-run this query now and report what's new since its last run.",
                )
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#path_input", Input).focus()
        self._load_entries()

    def _load_entries(self) -> None:
        path = self.query_one("#path_input", Input).value.strip()
        list_view = self.query_one("#log_entries", ListView)
        list_view.clear()
        rows = read_query_log(path) if path else []
        if not rows:
            list_view.append(ListItem(Label("(no saved queries found at this path)", markup=False)))
            return
        for row in rows:
            timestamp = row.get("timestamp", "?")
            count = row.get("result_count") or "?"
            query = row.get("query", "")
            label_text = f"[{timestamp}] ({count} results) {query}"
            list_view.append(ListItem(Label(label_text, markup=False), name=query))

    def _dismiss_selected(self, action: str) -> None:
        item = self.query_one("#log_entries", ListView).highlighted_child
        self.dismiss((action, item.name) if item and item.name else None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "load":
            self._load_entries()
        elif event.button.id == "use":
            self._dismiss_selected("load")
        elif event.button.id == "watch":
            self._dismiss_selected("watch")
        else:
            self.dismiss(None)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._load_entries()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.dismiss(("load", event.item.name) if event.item.name else None)


def apply_article_action(screen: Screen, article: Any, access: OpenAccessInfo, action: str) -> bool:
    """Shared handler for the open/copy article-action buttons, used by both
    ArticleActionsScreen and DoiLookupScreen so the two don't drift apart. Performs the
    side effect (opening a browser tab, copying to the clipboard) directly; returns True
    for "read_fulltext" since fetching/showing the text needs an async context the caller
    already has (a running worker), which this synchronous function doesn't."""
    if action == "open_pubmed" and article.url:
        webbrowser.open(article.url)
    elif action == "open_free" and access.url:
        webbrowser.open(access.url)
    elif action == "open_scihub" and article.doi:
        webbrowser.open(f"{openaccess.SCI_HUB_BASE_URL}{article.doi}")
    elif action == "read_fulltext" and article.pmc:
        return True
    elif action == "copy_citation":
        screen.app.copy_to_clipboard(format_citation(article))
        screen.notify("Citation copied to clipboard.")
    elif action == "copy_doi" and article.doi:
        screen.app.copy_to_clipboard(article.doi)
        screen.notify("DOI copied to clipboard.")
    elif action == "copy_url" and article.url:
        screen.app.copy_to_clipboard(article.url)
        screen.notify("URL copied to clipboard.")
    return False


class ArticleActionsScreen(EscDismissScreen[str | None]):
    """Modal offering actions for the selected article: open links, copy details, read the full
    text, or remove it from the results table. Dismisses with "remove" or "fulltext" to tell the
    caller what to do next, otherwise None."""

    def __init__(
        self,
        display_id: str,
        article: Any,
        access: OpenAccessInfo,
        id_label: str = "PMID",
        is_arxiv: bool = False,
    ) -> None:
        super().__init__()
        self._display_id = display_id
        self._article = article
        self._access = access
        self._id_label = id_label
        self._is_arxiv = is_arxiv

    def compose(self) -> ComposeResult:
        article = self._article
        with Vertical(id="actions_dialog"):
            yield Label(f"{self._id_label} {self._display_id} — open where?", markup=False)
            yield Label(article.title or "(no title)", markup=False, id="actions_title")
            yield Label(f"DOI: {article.doi or '(none on record)'}", markup=False)
            yield Label(f"Access: {self._access.source}", markup=False, id="actions_access")
            with Horizontal():
                yield Button("Open PubMed page", variant="primary", id="open_pubmed")
                yield Button(
                    "Open free copy",
                    id="open_free",
                    disabled=not (self._access.is_free and self._access.url),
                )
                yield Button(
                    "Try Sci-Hub (DOI)",
                    id="open_scihub",
                    disabled=self._is_arxiv or not article.doi,
                    tooltip="Not applicable to arXiv preprints — they're already open access."
                    if self._is_arxiv
                    else None,
                )
                yield Button(
                    "Read full text (PMC only)",
                    id="read_fulltext",
                    disabled=not article.pmc,
                )
            with Horizontal():
                yield Button("Copy citation", id="copy_citation")
                yield Button("Copy DOI", id="copy_doi", disabled=not article.doi)
                yield Button("Copy URL", id="copy_url", disabled=not article.url)
                yield Button("Remove from results", id="remove")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "remove":
            self.dismiss("remove")
            return
        if button_id == "cancel":
            self.dismiss(None)
            return
        if apply_article_action(self, self._article, self._access, button_id):
            self.dismiss("fulltext")
            return
        if button_id in {"copy_citation", "copy_doi", "copy_url"}:
            return  # keep the modal open so the user can copy more than one thing
        self.dismiss(None)  # close after opening a link


class FullTextScreen(EscDismissScreen[None]):
    """Modal showing a PMC Open Access article's full text as plain text."""

    def __init__(self, title: str, text: str) -> None:
        super().__init__()
        self._title = title
        self._text = text

    def compose(self) -> ComposeResult:
        with Vertical(id="fulltext_dialog"):
            yield Label(self._title, markup=False, id="fulltext_title")
            with VerticalScroll(id="fulltext_scroll"):
                yield Static(self._text, markup=False, id="fulltext_text")
            with Horizontal():
                yield Button("Close", variant="primary", id="close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class DoiLookupScreen(EscDismissScreen[None]):
    """§5 — look up a DOI directly (independent of §1-4's search results) and show
    everything Crossref/PMC have on it, with the same open/copy/full-text action buttons
    a search result gets, shown inline rather than behind a second modal."""

    def __init__(self) -> None:
        super().__init__()
        self._record: DoiRecord | None = None
        self._access: OpenAccessInfo | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="doi_dialog"):
            yield Label("DOI Lookup", markup=False)
            with Horizontal(id="doi_input_row"):
                yield Input(placeholder="10.xxxx/xxxxxxx", id="doi_input")
                yield Button("Look up", variant="primary", id="lookup")
            with VerticalScroll(id="doi_scroll"):
                yield Static(
                    "Enter a DOI above and press Look up (or Enter).",
                    markup=False,
                    id="doi_text",
                )
            with Horizontal():
                yield Button("Open DOI page", id="open_pubmed", disabled=True)
                yield Button("Open free copy", id="open_free", disabled=True)
                yield Button("Try Sci-Hub (DOI)", id="open_scihub", disabled=True)
                yield Button("Read full text (PMC only)", id="read_fulltext", disabled=True)
            with Horizontal():
                yield Button("Copy citation", id="copy_citation", disabled=True)
                yield Button("Copy DOI", id="copy_doi", disabled=True)
                yield Button("Copy URL", id="copy_url", disabled=True)
                yield Button("Close", id="close")

    def on_mount(self) -> None:
        self.query_one("#doi_input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "doi_input":
            self._do_lookup()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "lookup":
            self._do_lookup()
            return
        if button_id == "close":
            self.dismiss(None)
            return
        if self._record is None or self._access is None:
            return
        if apply_article_action(self, self._record, self._access, button_id):
            self.run_worker(self._read_fulltext(), exclusive=True)

    def _do_lookup(self) -> None:
        doi = self.query_one("#doi_input", Input).value.strip()
        if not doi:
            return
        self.run_worker(self._fetch(doi), exclusive=True)

    def _set_action_buttons_enabled(self, enabled: bool) -> None:
        for button_id in (
            "open_pubmed",
            "open_free",
            "open_scihub",
            "read_fulltext",
            "copy_citation",
            "copy_doi",
            "copy_url",
        ):
            self.query_one(f"#{button_id}", Button).disabled = not enabled

    async def _fetch(self, doi: str) -> None:
        text = self.query_one("#doi_text", Static)
        self._record = None
        self._access = None
        self._set_action_buttons_enabled(False)
        text.update(f"Looking up {doi} ...")

        data = await asyncio.to_thread(fetch_doi_info, doi)
        if data is None:
            text.update(f"No Crossref record found for DOI: {doi}")
            return

        pmc = await asyncio.to_thread(fetch_pmcid_for_doi, doi)
        record = build_doi_record(data, pmc)
        access = await asyncio.to_thread(openaccess.resolve_open_access, pmc, record.doi)
        self._record = record
        self._access = access
        text.update(render_doi_detail(record, access))

        self.query_one("#open_pubmed", Button).disabled = not record.url
        self.query_one("#open_free", Button).disabled = not (access.is_free and access.url)
        self.query_one("#open_scihub", Button).disabled = not record.doi
        self.query_one("#read_fulltext", Button).disabled = not record.pmc
        self.query_one("#copy_citation", Button).disabled = False
        self.query_one("#copy_doi", Button).disabled = not record.doi
        self.query_one("#copy_url", Button).disabled = not record.url

    async def _read_fulltext(self) -> None:
        if self._record is None or not self._record.pmc:
            return
        text = await asyncio.to_thread(openaccess.fetch_pmc_fulltext, self._record.pmc)
        self.app.push_screen(FullTextScreen(self._record.title or "(no title)", text))


class SettingsScreen(EscDismissScreen[Settings | None]):
    """Modal for configuring the NCBI API key and other service settings (saved locally)."""

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings

    def compose(self) -> ComposeResult:
        with Vertical(id="settings_dialog"):
            yield Label("Settings")
            with Horizontal(id="settings_columns"):
                with Vertical(id="filters_col_left"):
                    yield Label(
                        "NCBI API key (optional — raises the PubMed rate limit from 3 to 10 req/s)",
                        classes="filter_hint",
                    )
                    yield Input(
                        value=self._settings.ncbi_api_key,
                        placeholder="NCBI API key",
                        id="ncbi_api_key_input",
                    )
                    yield Label(
                        "Unpaywall contact email (used only for open-access lookups)",
                        classes="filter_hint",
                    )
                    yield Input(
                        value=self._settings.unpaywall_email,
                        placeholder=openaccess.UNPAYWALL_EMAIL,
                        id="unpaywall_email_input",
                    )
                with Vertical(id="filters_col_right"):
                    yield Label("Sci-Hub mirror (base URL, trailing slash)", classes="filter_hint")
                    yield Input(
                        value=self._settings.scihub_mirror,
                        placeholder=openaccess.SCI_HUB_BASE_URL,
                        id="scihub_mirror_input",
                    )
            with Horizontal():
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#ncbi_api_key_input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.dismiss(
                Settings(
                    ncbi_api_key=self.query_one("#ncbi_api_key_input", Input).value.strip(),
                    unpaywall_email=self.query_one("#unpaywall_email_input", Input).value.strip(),
                    scihub_mirror=self.query_one("#scihub_mirror_input", Input).value.strip(),
                )
            )
        else:
            self.dismiss(None)


class OrderedSystemCommandsProvider(SystemCommandsProvider):
    """Like SystemCommandsProvider, but keeps App.get_system_commands()'s own order
    instead of re-sorting it alphabetically — so "Quit" can be placed last."""

    async def discover(self):
        for name, help_text, callback, discover in self.app.get_system_commands(self.screen):
            if discover:
                yield DiscoveryHit(name, callback, help=help_text)


class ResultsTable(DataTable):
    """DataTable that opens the article actions menu with Space instead of Enter. Textual
    merges a subclass's BINDINGS with its parent's rather than replacing them, so Enter has
    to be explicitly re-bound to a no-op here to actually stop triggering the old behavior."""

    BINDINGS: ClassVar = [
        Binding("space", "select_cursor", "Actions", show=False),
        Binding("enter", "noop", "", show=False),
    ]

    def action_noop(self) -> None:
        pass
