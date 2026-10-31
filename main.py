"""Scicher: interactive PubMed query builder and search TUI."""

import asyncio
import csv
import os
import re
import webbrowser
from dataclasses import dataclass, field, replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar

import requests
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.suggester import SuggestFromList
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    ProgressBar,
    Select,
    SelectionList,
    Static,
)

from theme import CYBERLAB_THEME

FIELD_CHOICES = [
    ("Title/Abstract", "title/abstract"),
    ("MeSH", "MeSH"),
    ("Title", "title"),
    ("Text Word", "tw"),
    ("Author", "au"),
    ("Journal", "ta"),
    ("All Fields (raw/unparsed)", "ALL"),
]

MESH_TERMS_PATH = Path(__file__).parent / "data" / "mesh_terms.txt"


@lru_cache(maxsize=1)
def load_mesh_terms() -> list[str]:
    if not MESH_TERMS_PATH.exists():
        return []
    return MESH_TERMS_PATH.read_text(encoding="utf-8").splitlines()


@dataclass
class Term:
    field: str
    value: str


@dataclass
class Group:
    """A set of terms OR-ed together. Groups are AND-ed with each other."""

    terms: list[Term] = field(default_factory=list)


def quote_if_needed(value: str) -> str:
    return f'"{value}"' if " " in value and not value.startswith('"') else value


def render_term(term: Term) -> str:
    if term.field == "ALL":
        return term.value
    return f"{quote_if_needed(term.value)}[{term.field}]"


def render_group(group: Group) -> str:
    rendered = [render_term(t) for t in group.terms]
    if len(rendered) == 1:
        return rendered[0]
    return "(" + " OR ".join(rendered) + ")"


def render_query(groups: list[Group]) -> str:
    return " and ".join(render_group(g) for g in groups if g.terms)


PUBLICATION_TYPE_CHOICES = [
    "Meta-Analysis",
    "Systematic Review",
    "Randomized Controlled Trial",
    "Review",
    "Clinical Trial",
    "Case Reports",
    "Observational Study",
    "Practice Guideline",
]

SPECIES_CHOICES = [
    ("Any", "any"),
    ("Humans", "human"),
    ("Animals", "animal"),
]


@dataclass
class Filters:
    year_from: str = ""
    year_to: str = ""
    publication_types: frozenset[str] = frozenset()
    species: str = "any"
    free_full_text: bool = False

    def is_empty(self) -> bool:
        return not (
            self.year_from
            or self.year_to
            or self.publication_types
            or self.species != "any"
            or self.free_full_text
        )


def render_filters(filters: Filters) -> list[str]:
    clauses: list[str] = []
    if filters.year_from or filters.year_to:
        year_from = filters.year_from or "1900"
        year_to = filters.year_to or str(datetime.now().year)
        clauses.append(f'("{year_from}"[dp] : "{year_to}"[dp])')
    if filters.publication_types:
        pt_terms = [f'"{pt}"[pt]' for pt in sorted(filters.publication_types)]
        clause = " OR ".join(pt_terms)
        clauses.append(f"({clause})" if len(pt_terms) > 1 else clause)
    if filters.species == "human":
        clauses.append('"humans"[mesh]')
    elif filters.species == "animal":
        clauses.append('"animals"[mesh]')
    if filters.free_full_text:
        clauses.append('"free full text"[filter]')
    return clauses


def build_query(groups: list[Group], filters: Filters) -> str:
    clauses = [render_group(g) for g in groups if g.terms]
    clauses.extend(render_filters(filters))
    return " and ".join(clauses)


def split_top_level(text: str, sep: str) -> list[str]:
    """Split on `sep`, ignoring occurrences inside parentheses or quotes."""
    parts = []
    depth = 0
    in_quotes = False
    start = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '"':
            in_quotes = not in_quotes
        elif not in_quotes:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif depth == 0 and text[i : i + len(sep)] == sep:
                parts.append(text[start:i])
                i += len(sep)
                start = i
                continue
        i += 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


TERM_RE = re.compile(r'^(?:"(?P<qval>[^"]+)"|(?P<val>\S+))\[(?P<field>[^\]]+)\]$')
YEAR_RANGE_RE = re.compile(r'^\(\s*"(\d{4})"\[dp\]\s*:\s*"(\d{4})"\[dp\]\s*\)$')


def parse_term(text: str) -> Term | None:
    match = TERM_RE.match(text.strip())
    if not match:
        return None
    value = match.group("qval") if match.group("qval") is not None else match.group("val")
    return Term(field=match.group("field"), value=value)


def parse_query(query: str) -> tuple[list[Group], Filters]:
    """Parse a previously-rendered PubMed query string back into groups and filters.

    Best-effort: clauses this app itself generated (via build_query) always round-trip
    exactly. Anything unrecognized becomes a single fallback term so no text is lost.
    """
    groups: list[Group] = []
    filters = Filters()
    pub_types: set[str] = set()

    for clause in split_top_level(query, " and "):
        year_match = YEAR_RANGE_RE.match(clause)
        if year_match:
            filters = replace(filters, year_from=year_match.group(1), year_to=year_match.group(2))
            continue
        if clause == '"humans"[mesh]':
            filters = replace(filters, species="human")
            continue
        if clause == '"animals"[mesh]':
            filters = replace(filters, species="animal")
            continue
        if clause == '"free full text"[filter]':
            filters = replace(filters, free_full_text=True)
            continue

        inner = clause[1:-1] if clause.startswith("(") and clause.endswith(")") else clause
        parts = [p.strip() for p in inner.split(" OR ")]
        maybe_terms = [parse_term(p) for p in parts]
        terms = [t for t in maybe_terms if t is not None]

        if terms and len(terms) == len(maybe_terms):
            if all(term.field == "pt" for term in terms):
                pub_types.update(term.value for term in terms)
            else:
                groups.append(Group(terms=terms))
        else:
            groups.append(Group(terms=[Term(field="ALL", value=clause)]))

    if pub_types:
        filters = replace(filters, publication_types=frozenset(pub_types))
    return groups, filters


def describe_filters(filters: Filters) -> str:
    parts = []
    if filters.year_from or filters.year_to:
        parts.append(f"{filters.year_from or '…'}-{filters.year_to or '…'}")
    if filters.publication_types:
        parts.append(f"{len(filters.publication_types)} type(s)")
    if filters.species != "any":
        parts.append(filters.species.capitalize())
    if filters.free_full_text:
        parts.append("Free full text")
    return ", ".join(parts)


CSV_FIELDS = [
    "pmid",
    "title",
    "authors",
    "journal",
    "year",
    "volume",
    "issue",
    "pages",
    "doi",
    "pmc",
    "issn",
    "publication_types",
    "mesh",
    "keywords",
    "url",
    "abstract",
]


def article_to_row(pmid: str, article: Any) -> dict[str, str]:
    if article is None:
        return {field_name: "" for field_name in CSV_FIELDS} | {"pmid": pmid}
    return {
        "pmid": pmid,
        "title": article.title or "",
        "authors": "; ".join(article.authors or []),
        "journal": article.journal or "",
        "year": article.year or "",
        "volume": article.volume or "",
        "issue": article.issue or "",
        "pages": article.pages or "",
        "doi": article.doi or "",
        "pmc": article.pmc or "",
        "issn": article.issn or "",
        "publication_types": "; ".join((article.publication_types or {}).values())
        if isinstance(article.publication_types, dict)
        else "; ".join(article.publication_types or []),
        "mesh": "; ".join(article.mesh or []),
        "keywords": "; ".join(article.keywords or []),
        "url": article.url or "",
        "abstract": article.abstract or "",
    }


def write_results_csv(path: str, rows: list[dict[str, str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


QUERY_LOG_FIELDS = ["timestamp", "query", "result_count"]
DEFAULT_QUERY_LOG_PATH = "scicher_query_log.csv"


def append_query_log(path: str, query: str, result_count: str) -> None:
    needs_header = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=QUERY_LOG_FIELDS)
        if needs_header:
            writer.writeheader()
        writer.writerow(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "query": query,
                "result_count": result_count,
            }
        )


def read_query_log(path: str) -> list[dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        rows = [row for row in csv.DictReader(fh) if row.get("query")]
    rows.reverse()
    return rows


class AddTermScreen(ModalScreen[Term | None]):
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
            with Horizontal():
                yield Button("Save" if self._term else "Add", variant="primary", id="add")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._update_suggester(self._term.field if self._term else "title/abstract")
        self.query_one("#value_input", Input).focus()

    def _update_suggester(self, field_: str) -> None:
        value_input = self.query_one("#value_input", Input)
        if field_ == "MeSH":
            terms = load_mesh_terms()
            value_input.suggester = SuggestFromList(terms, case_sensitive=False) if terms else None
        else:
            value_input.suggester = None

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "field_select":
            self._update_suggester(str(event.value))

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


class ExportCsvScreen(ModalScreen[str | None]):
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


class QueryLogScreen(ModalScreen[str | None]):
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


class FiltersScreen(ModalScreen[Filters | None]):
    """Modal dialog for editing year/publication-type/species/full-text filters."""

    def __init__(self, filters: Filters) -> None:
        super().__init__()
        self._filters = filters

    def compose(self) -> ComposeResult:
        with Vertical(id="filters_dialog"):
            yield Label("Filters")
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
            yield Label("Publication type (any selected are OR-ed)")
            yield SelectionList[str](
                *[
                    (pt, pt, pt in self._filters.publication_types)
                    for pt in PUBLICATION_TYPE_CHOICES
                ],
                id="pub_types",
            )
            yield Label("Species")
            yield Select(SPECIES_CHOICES, value=self._filters.species, id="species_select")
            yield Checkbox(
                "Free full text only",
                value=self._filters.free_full_text,
                id="free_full_text",
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
        )


class LoadQueryScreen(ModalScreen[str | None]):
    """Modal dialog for picking a previously logged query from a CSV file."""

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

    def _dismiss_selected(self) -> None:
        item = self.query_one("#log_entries", ListView).highlighted_child
        self.dismiss(item.name if item and item.name else None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "load":
            self._load_entries()
        elif event.button.id == "use":
            self._dismiss_selected()
        else:
            self.dismiss(None)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._load_entries()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.dismiss(event.item.name if event.item.name else None)


SCI_HUB_BASE_URL = "https://sci-hub.st/"
UNPAYWALL_EMAIL = "oa-lookup@scicher.app"


@dataclass
class OpenAccessInfo:
    is_free: bool
    source: str
    url: str | None = None


def check_unpaywall(doi: str) -> OpenAccessInfo | None:
    """Look up open-access status for a DOI via the free Unpaywall API."""
    try:
        resp = requests.get(
            f"https://api.unpaywall.org/v2/{doi}",
            params={"email": UNPAYWALL_EMAIL},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001 - OA status is a nice-to-have, never block on it
        return None
    if data.get("is_oa"):
        best_location = data.get("best_oa_location") or {}
        return OpenAccessInfo(
            is_free=True,
            source=f"Open Access ({data.get('oa_status', 'oa')})",
            url=best_location.get("url"),
        )
    return OpenAccessInfo(is_free=False, source="Closed (paywalled)")


def resolve_open_access(pmc: str | None, doi: str | None) -> OpenAccessInfo:
    """PMC presence is an instant, guaranteed-free signal; fall back to Unpaywall by DOI."""
    if pmc:
        return OpenAccessInfo(
            is_free=True,
            source="PMC",
            url=f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmc}/",
        )
    if doi:
        oa = check_unpaywall(doi)
        if oa is not None:
            return oa
    return OpenAccessInfo(is_free=False, source="Unknown")


class ArticleActionsScreen(ModalScreen[None]):
    """Modal offering to open the selected article's PubMed page, free copy, or Sci-Hub mirror."""

    def __init__(
        self,
        pmid: str,
        title: str | None,
        url: str | None,
        doi: str | None,
        access: OpenAccessInfo,
    ) -> None:
        super().__init__()
        self._pmid = pmid
        self._title = title
        self._url = url
        self._doi = doi
        self._access = access

    def compose(self) -> ComposeResult:
        with Vertical(id="actions_dialog"):
            yield Label(f"PMID {self._pmid} — open where?", markup=False)
            yield Label(self._title or "(no title)", markup=False, id="actions_title")
            yield Label(f"DOI: {self._doi or '(none on record)'}", markup=False)
            yield Label(f"Access: {self._access.source}", markup=False, id="actions_access")
            with Horizontal():
                yield Button("Open PubMed page", variant="primary", id="open_pubmed")
                yield Button(
                    "Open free copy",
                    id="open_free",
                    disabled=not (self._access.is_free and self._access.url),
                )
                yield Button(
                    "Try Sci-Hub (DOI)", id="open_scihub", disabled=not self._doi
                )
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "open_pubmed" and self._url:
            webbrowser.open(self._url)
        elif event.button.id == "open_free" and self._access.url:
            webbrowser.open(self._access.url)
        elif event.button.id == "open_scihub" and self._doi:
            webbrowser.open(f"{SCI_HUB_BASE_URL}{self._doi}")
        self.dismiss(None)


class ScicherApp(App):
    """TUI for building PubMed advanced-search queries and browsing results."""

    TITLE = ">_ SCICHER"
    SUB_TITLE = "SEARCH CONSOLE"
    CSS_PATH = "style.tcss"
    BINDINGS: ClassVar = [
        ("1", "focus_query_terms", "Terms"),
        ("2", "focus_query_preview", "Query"),
        ("3", "focus_results", "Results"),
        ("4", "focus_detail", "Detail"),
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
        ("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.groups: list[Group] = [Group()]
        self.filters = Filters()
        self._fetcher = None
        self._last_pmids: list[str] = []
        self._last_query: str | None = None
        self._articles: dict[str, Any] = {}
        self._sort_column: str | None = None
        self._sort_reverse = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="main_area"):
            with Vertical(id="results_section"):
                yield DataTable(id="results_table")
            with Horizontal(id="bottom_row"):
                with Vertical(id="left_col"):
                    with Vertical(id="query_panel"):
                        yield ListView(id="groups_list")
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

    def on_mount(self) -> None:
        self.register_theme(CYBERLAB_THEME)
        self.theme = "cyberlab"

        query_panel = self.query_one("#query_panel")
        query_panel.border_title = "1. QUERY TERMS"
        query_panel.border_subtitle = "AND / OR"

        query_section = self.query_one("#query_section")
        query_section.border_title = "2. COMPILED QUERY"
        query_section.border_subtitle = "SEARCH STRATEGY"

        results_section = self.query_one("#results_section")
        results_section.border_title = "3. FOUND ARTICLES"

        detail_section = self.query_one("#detail_section")
        detail_section.border_title = "4. ARTICLE METADATA"

        self.refresh_groups_list()
        table = self.query_one("#results_table", DataTable)
        table.add_columns(("PMID", "pmid"), ("Year", "year"), ("Journal", "journal"), ("Title", "title"))
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
        def handle_result(query: str | None) -> None:
            if not query:
                return
            groups, filters = parse_query(query)
            self.groups = groups or [Group()]
            self.filters = filters
            self.refresh_groups_list()
            self.query_one("#status", Label).update(f"Loaded saved query: {query}")

        self.push_screen(LoadQueryScreen(DEFAULT_QUERY_LOG_PATH), handle_result)

    def action_run_search(self) -> None:
        query = self.current_query()
        if not query:
            self.query_one("#status", Label).update("Add at least one term before searching.")
            return
        status = self.query_one("#status", Label)
        status.update(f"Searching: {query} ...")
        self.run_worker(self._do_search(query), group="search", exclusive=True)

    async def _do_search(self, query: str) -> None:
        status = self.query_one("#status", Label)
        table = self.query_one("#results_table", DataTable)
        progress = self.query_one("#search_progress", ProgressBar)
        table.clear()
        self.query_one("#detail_text", Static).update(
            "Run a search and highlight a row to see its metadata here."
        )
        progress.display = True
        progress.update(total=None, progress=0)
        try:
            fetcher = self.get_fetcher()
            pmids = await self.run_in_thread(lambda: fetcher.pmids_for_query(query, retmax=50))
        except Exception as exc:  # noqa: BLE001 - surface any NCBI/network error to the user
            status.update(f"Search failed: {exc}")
            progress.display = False
            return

        self._last_pmids = pmids
        self._last_query = query
        self._articles = {}
        try:
            append_query_log(DEFAULT_QUERY_LOG_PATH, query, str(len(pmids)))
        except OSError:
            pass  # non-fatal: searching should still work even if the log can't be written
        status.update(f"Found {len(pmids)} result(s), loading details...")
        if not pmids:
            progress.display = False
            return

        progress.update(total=len(pmids), progress=0)
        for pmid in pmids:
            article = await self._fetch_article_with_retries(fetcher, pmid)
            if article is not None:
                self._articles[pmid] = article
                title = article.title or "(no title)"
                year = article.year or "?"
                journal = article.journal or ""
            else:
                title = "(failed to fetch details)"
                year = "?"
                journal = ""
            table.add_row(pmid, year, journal, title, key=pmid)
            progress.advance(1)

        progress.display = False
        status.update(
            f"Found {len(pmids)} result(s) for: {query} — details loaded, ready to export."
        )

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

    async def _open_article_actions(self, pmid: str) -> None:
        article = self._articles.get(pmid)
        if article is None:
            fetcher = self.get_fetcher()
            article = await self._fetch_article_with_retries(fetcher, pmid)
            if article is None:
                self.query_one("#status", Label).update(
                    f"Could not load PMID {pmid} to open actions."
                )
                return
            self._articles[pmid] = article
        access = await self.run_in_thread(lambda: resolve_open_access(article.pmc, article.doi))
        self.push_screen(
            ArticleActionsScreen(pmid, article.title, article.url, article.doi, access)
        )

    async def _show_detail(self, pmid: str) -> None:
        detail = self.query_one("#detail_text", Static)
        article = self._articles.get(pmid)
        if article is None:
            detail.update(f"Loading metadata for PMID {pmid}...")
            fetcher = self.get_fetcher()
            article = await self._fetch_article_with_retries(fetcher, pmid)
            if article is None:
                detail.update(f"Failed to load PMID {pmid} after several attempts.")
                return
            self._articles[pmid] = article

        def render(access_line: str) -> str:
            return (
                f"PMID: {pmid}\n"
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

        if article.pmc:
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
        result_count = str(len(self._last_pmids)) if query == self._last_query else ""

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
        if not self._last_pmids:
            status.update("Run a search before exporting.")
            return

        default_path = f"scicher_results_{datetime.now():%Y%m%d_%H%M%S}.csv"

        def handle_result(path: str | None) -> None:
            if not path:
                return
            rows = [article_to_row(pmid, self._articles.get(pmid)) for pmid in self._last_pmids]
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
