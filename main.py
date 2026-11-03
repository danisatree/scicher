"""Scicher: interactive PubMed query builder and search TUI."""

import asyncio
import contextlib
import csv
import json
import os
import re
import webbrowser
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar, TypeVar

import requests
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.command import DiscoveryHit
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.suggester import SuggestFromList
from textual.system_commands import SystemCommandsProvider
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

from theme import SCIENCE_DARK_THEME, SCIENCE_LIGHT_THEME

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

MAX_RESULTS_CHOICES = [
    ("10 per source", 10),
    ("25 per source", 25),
    ("50 per source (default)", 50),
    ("100 per source", 100),
    ("200 per source", 200),
]


@dataclass
class Filters:
    year_from: str = ""
    year_to: str = ""
    publication_types: frozenset[str] = frozenset()
    species: str = "any"
    free_full_text: bool = False
    max_results: int = 50

    def is_empty(self) -> bool:
        return not (
            self.year_from
            or self.year_to
            or self.publication_types
            or self.species != "any"
            or self.free_full_text
            or self.max_results != 50
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
    if filters.max_results != 50:
        parts.append(f"Max {filters.max_results}")
    return ", ".join(parts)


# --- arXiv: a second, optional search source alongside PubMed -----------------------

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

# Maps a builder field to an arXiv query prefix; fields with no arXiv equivalent
# (MeSH, Journal, raw/unparsed) are simply absent and get dropped from the arXiv half
# of the search rather than mistranslated into a meaningless full-text match.
ARXIV_FIELD_PREFIX = {
    "title": "ti",
    "tw": "all",
    "au": "au",
}


@dataclass
class ArxivRecord:
    """Exposes the same attribute names `_show_detail`/`article_to_row` read off a
    metapub PubMedArticle, so both sources flow through the same display/export code."""

    id: str
    title: str = ""
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    year: str = ""
    url: str = ""
    pdf_url: str = ""
    categories: list[str] = field(default_factory=list)
    doi: str | None = None
    journal: str = "arXiv"
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    pmc: str | None = None
    issn: str | None = None
    publication_types: list[str] = field(default_factory=lambda: ["Preprint"])
    mesh: list[str] = field(default_factory=list)

    @property
    def keywords(self) -> list[str]:
        return self.categories


def render_arxiv_term(term: Term) -> str | None:
    prefix = ARXIV_FIELD_PREFIX.get(term.field)
    value = f'"{term.value}"' if " " in term.value else term.value
    if term.field == "title/abstract":
        return f"(ti:{value} OR abs:{value})"
    if prefix is None:
        return None  # MeSH / Journal / raw-unparsed: no arXiv equivalent, drop the term
    return f"{prefix}:{value}"


def render_arxiv_group(group: Group) -> str | None:
    rendered = [r for r in (render_arxiv_term(t) for t in group.terms) if r]
    if not rendered:
        return None
    if len(rendered) == 1:
        return rendered[0]
    return "(" + " OR ".join(rendered) + ")"


def build_arxiv_query(groups: list[Group]) -> str | None:
    """Best-effort translation of the same term builder into arXiv's query syntax.

    Returns None if nothing in the query has an arXiv-compatible field (e.g. a
    MeSH-only query), so the caller can skip the arXiv search rather than run an
    unrestricted one.
    """
    clauses = [c for c in (render_arxiv_group(g) for g in groups) if c]
    if not clauses:
        return None
    return " AND ".join(clauses)


def fetch_arxiv(query: str, max_results: int = 50) -> list[ArxivRecord]:
    import xml.etree.ElementTree as ET

    resp = requests.get(
        ARXIV_API_URL,
        params={"search_query": query, "max_results": max_results},
        timeout=15,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.text)

    records = []
    for entry in root.findall("atom:entry", ARXIV_NS):
        entry_id = (entry.findtext("atom:id", default="", namespaces=ARXIV_NS) or "").strip()
        arxiv_id = entry_id.rsplit("/", 1)[-1] if entry_id else ""
        published = entry.findtext("atom:published", default="", namespaces=ARXIV_NS) or ""
        doi_el = entry.find("arxiv:doi", ARXIV_NS)
        pdf_url = next(
            (
                link.get("href", "")
                for link in entry.findall("atom:link", ARXIV_NS)
                if link.get("title") == "pdf"
            ),
            "",
        )
        records.append(
            ArxivRecord(
                id=arxiv_id,
                title=" ".join(
                    (entry.findtext("atom:title", default="", namespaces=ARXIV_NS) or "").split()
                ),
                authors=[
                    " ".join((a.findtext("atom:name", default="", namespaces=ARXIV_NS) or "").split())
                    for a in entry.findall("atom:author", ARXIV_NS)
                ],
                abstract=" ".join(
                    (entry.findtext("atom:summary", default="", namespaces=ARXIV_NS) or "").split()
                ),
                year=published[:4],
                url=entry_id,
                pdf_url=pdf_url,
                categories=[
                    c.get("term", "") for c in entry.findall("atom:category", ARXIV_NS) if c.get("term")
                ],
                doi=doi_el.text.strip() if doi_el is not None and doi_el.text else None,
            )
        )
    return records


def fetch_pubmed_articles_batch(fetcher: Any, pmids: list[str], chunk_size: int = 200) -> dict[str, Any]:
    """Fetch metadata for many PMIDs in as few HTTP requests as possible.

    NCBI's efetch accepts a batch of IDs per call; doing one request per article (as
    metapub's PubMedFetcher.article_by_pmid does) multiplies API load for no benefit.
    Any PMID missing from the result (a whole chunk failing, or NCBI quietly omitting
    one) is left for the caller to retry individually.
    """
    import xml.etree.ElementTree as ET

    from metapub import PubMedArticle

    articles: dict[str, Any] = {}
    for start in range(0, len(pmids), chunk_size):
        chunk = pmids[start : start + chunk_size]
        try:
            xml_text = fetcher.qs.efetch({"db": "pubmed", "id": chunk})
            root = ET.fromstring(xml_text)
        except Exception:  # noqa: BLE001 - leave this chunk's PMIDs for individual retry
            continue
        for el in root.findall(".//PubmedArticle") + root.findall(".//PubmedBookArticle"):
            pmid_el = el.find(".//PMID")
            if pmid_el is None or not pmid_el.text:
                continue
            wrapped = f"<PubmedArticleSet>{ET.tostring(el, encoding='unicode')}</PubmedArticleSet>"
            try:
                articles[pmid_el.text] = PubMedArticle(wrapped)
            except Exception:  # noqa: BLE001 - malformed entry, leave for individual retry
                continue
    return articles


CSV_FIELDS = [
    "source",
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


def pubmed_language(article: Any) -> str:
    """ISO 639-2/B language code (e.g. "eng", "fre") from PubMed's <Language> element.
    metapub.PubMedArticle doesn't expose this as an attribute, but it's on the underlying
    lxml tree. Returns "" if unavailable (no raw content, or a record predating the field)."""
    content = getattr(article, "content", None)
    if content is None:
        return ""
    element = content.find(".//Language")
    return (element.text or "").strip() if element is not None else ""


def article_to_row(pmid: str, article: Any, source: str = "PubMed") -> dict[str, str]:
    if article is None:
        return {field_name: "" for field_name in CSV_FIELDS} | {"pmid": pmid, "source": source}
    return {
        "source": source,
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


def format_citation(article: Any) -> str:
    """Plain-text citation (Vancouver-ish: Authors. Title. Journal (Year). vol/issue/pages. doi:X)."""
    authors = article.authors or []
    if len(authors) > 3:
        author_str = f"{authors[0]} et al."
    elif authors:
        author_str = ", ".join(authors)
    else:
        author_str = "(no authors listed)"
    title = article.title or "(no title)"
    journal_year = article.journal or ""
    if article.year:
        journal_year = f"{journal_year} ({article.year})" if journal_year else f"({article.year})"
    vip = "/".join(str(v) for v in (article.volume, article.issue, article.pages) if v)
    parts = [f"{author_str}.", f"{title}."]
    if journal_year:
        parts.append(f"{journal_year}.")
    if vip:
        parts.append(f"{vip}.")
    if article.doi:
        parts.append(f"doi:{article.doi}")
    return " ".join(parts)


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


WATCH_SNAPSHOTS_PATH = Path(__file__).parent / "scicher_watch_snapshots.json"


def load_watch_snapshots() -> dict[str, list[str]]:
    """Latest result-key snapshot per watched query, keyed by the exact query string.
    Only the most recent run is kept per query (not full history), so this stays small."""
    try:
        return json.loads(WATCH_SNAPSHOTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_watch_snapshot(query: str, keys: list[str]) -> None:
    snapshots = load_watch_snapshots()
    snapshots[query] = keys
    with contextlib.suppress(OSError):
        WATCH_SNAPSHOTS_PATH.write_text(json.dumps(snapshots, indent=2), encoding="utf-8")


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
    new since the last run of that exact query (see WATCH_SNAPSHOTS_PATH)."""

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


SCI_HUB_BASE_URL = "https://sci-hub.st/"
UNPAYWALL_EMAIL = "oa-lookup@scicher.app"

SETTINGS_PATH = Path(__file__).parent / "scicher_settings.json"


@dataclass
class Settings:
    """User-configurable credentials and preferences, stored locally and never committed."""

    ncbi_api_key: str = ""
    unpaywall_email: str = ""
    scihub_mirror: str = ""
    theme: str = "science-dark"


def load_settings() -> Settings:
    if not SETTINGS_PATH.exists():
        return Settings()
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Settings()
    return Settings(
        ncbi_api_key=data.get("ncbi_api_key", ""),
        unpaywall_email=data.get("unpaywall_email", ""),
        scihub_mirror=data.get("scihub_mirror", ""),
        theme=data.get("theme", "science-dark"),
    )


def save_settings(settings: Settings) -> None:
    SETTINGS_PATH.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(SETTINGS_PATH, 0o600)  # owner read/write only; API key lives in this file


def apply_settings(settings: Settings) -> None:
    """Push settings into the running process: env var for metapub, module globals here."""
    global UNPAYWALL_EMAIL, SCI_HUB_BASE_URL

    if settings.ncbi_api_key:
        os.environ["NCBI_API_KEY"] = settings.ncbi_api_key
        try:
            import metapub.eutils_common as _eutils_common

            _eutils_common.API_KEY = settings.ncbi_api_key
        except ImportError:
            pass

    if settings.unpaywall_email:
        UNPAYWALL_EMAIL = settings.unpaywall_email
    if settings.scihub_mirror:
        SCI_HUB_BASE_URL = settings.scihub_mirror


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


def resolve_access_for(key: str, article: Any) -> OpenAccessInfo:
    """Like resolve_open_access, but arXiv preprints are always freely downloadable —
    no need to spend a network round-trip asking Unpaywall about one."""
    if key.startswith("arxiv:"):
        return OpenAccessInfo(
            is_free=True,
            source="Open Access (arXiv preprint)",
            url=getattr(article, "pdf_url", "") or getattr(article, "url", None),
        )
    return resolve_open_access(article.pmc, article.doi)


def fetch_pmc_fulltext(pmcid: str) -> str:
    """Fetch a PMC Open Access article's full text via efetch and flatten its JATS XML body
    to plain text. Returns an explanatory message instead of raising if the article isn't in
    the OA subset (most PMC entries aren't) or the request otherwise fails."""
    import xml.etree.ElementTree as ET

    params = {"db": "pmc", "id": pmcid, "retmode": "xml"}
    api_key = os.environ.get("NCBI_API_KEY")
    if api_key:
        params["api_key"] = api_key
    try:
        resp = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi", params=params, timeout=20
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
    except Exception as exc:  # noqa: BLE001 - surface as a readable message, not a traceback
        return f"Could not fetch full text: {exc}"

    body = root.find(".//body")
    if body is None:
        return (
            "Full text is not available via the PMC API for this article "
            "(it may not be in the PMC Open Access subset)."
        )

    paragraphs: list[str] = []
    for el in body.iter():
        text = "".join(el.itertext()).strip()
        if not text:
            continue
        if el.tag == "title":
            paragraphs.append(f"\n{text.upper()}\n")
        elif el.tag == "p":
            paragraphs.append(text)
    return "\n\n".join(paragraphs) or "(full text retrieved but contained no readable paragraphs)"


@dataclass
class DoiRecord:
    """Exposes the same attribute names ArticleActionsScreen/format_citation read off a
    metapub PubMedArticle or ArxivRecord, so a Crossref-only lookup (§5 DOI Lookup) flows
    through the same actions modal/clipboard code unchanged."""

    doi: str
    title: str = ""
    authors: list[str] = field(default_factory=list)
    journal: str = ""
    year: str = ""
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    pmc: str | None = None
    issn: str | None = None
    publisher: str = ""
    publication_types: list[str] = field(default_factory=list)
    mesh: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    citation_count: int | None = None
    retracted: bool = False
    abstract: str = ""
    url: str = ""

    @property
    def keywords(self) -> list[str]:
        return self.subjects


def fetch_doi_info(doi: str) -> dict[str, Any] | None:
    """Raw Crossref /works/{doi} metadata, or None if the DOI isn't registered there."""
    try:
        resp = requests.get(
            f"https://api.crossref.org/works/{doi}", params={"mailto": UNPAYWALL_EMAIL}, timeout=15
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json().get("message")
    except Exception:  # noqa: BLE001 - surfaced as "no record found" by the caller
        return None


def fetch_pmcid_for_doi(doi: str) -> str | None:
    """PMC id for a DOI via NCBI's ID Converter, or None if it's not in PMC."""
    try:
        resp = requests.get(
            "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/",
            params={"ids": doi, "format": "json", "email": UNPAYWALL_EMAIL, "tool": "scicher"},
            timeout=15,
        )
        resp.raise_for_status()
        records = resp.json().get("records", [])
        if records and "pmcid" in records[0]:
            pmcid = records[0]["pmcid"]
            return pmcid.removeprefix("PMC") if pmcid.startswith("PMC") else pmcid
    except Exception:  # noqa: BLE001 - PMC lookup is best-effort, never block the DOI lookup
        pass
    return None


def build_doi_record(data: dict[str, Any], pmc: str | None) -> DoiRecord:
    """Flattens a raw Crossref /works/{doi} response into a DoiRecord."""
    authors = []
    for author in data.get("author", []) or []:
        name = f"{author.get('given', '')} {author.get('family', '')}".strip()
        if name:
            authors.append(name)
    date_parts = (
        (data.get("published-print") or data.get("published-online") or data.get("published") or {}).get(
            "date-parts"
        )
        or [[]]
    )[0]
    abstract = re.sub(r"<[^>]+>", "", data.get("abstract", "") or "").strip()
    doi = data.get("DOI", "")
    retracted = any(u.get("type") == "retraction" for u in (data.get("update-to") or []))
    return DoiRecord(
        doi=doi,
        title=(data.get("title") or [""])[0],
        authors=authors,
        journal=(data.get("container-title") or [""])[0],
        year=str(date_parts[0]) if date_parts else "",
        volume=data.get("volume"),
        issue=data.get("issue"),
        pages=data.get("page"),
        pmc=pmc,
        issn=(data.get("ISSN") or [None])[0],
        publisher=data.get("publisher", ""),
        publication_types=[data["type"]] if data.get("type") else [],
        subjects=data.get("subject") or [],
        citation_count=data.get("is-referenced-by-count"),
        retracted=retracted,
        abstract=abstract,
        url=data.get("URL") or (f"https://doi.org/{doi}" if doi else ""),
    )


def render_doi_detail(record: DoiRecord, access: OpenAccessInfo) -> str:
    lines = []
    if record.retracted:
        lines.append("*** RETRACTED — verify before citing! ***\n")
    lines.append(f"DOI: {record.doi}")
    lines.append(f"Title: {record.title or '(no title)'}")
    lines.append(f"Authors: {', '.join(record.authors) or '(none listed)'}")
    lines.append(f"Journal: {record.journal or '?'} ({record.year or '?'})")
    lines.append(f"Publisher: {record.publisher or '-'}")
    lines.append(
        f"Volume/Issue/Pages: {record.volume or '?'}/{record.issue or '?'}/{record.pages or '?'}"
    )
    lines.append(f"Type: {', '.join(record.publication_types) or '-'}")
    lines.append(f"PMC: {record.pmc or '-'}")
    lines.append(f"Access: {access.source}")
    count = record.citation_count
    lines.append(f"Cited by (Crossref): {count if count is not None else '?'}")
    if record.subjects:
        lines.append(f"Subjects: {', '.join(record.subjects)}")
    lines.append(f"URL: {record.url}")
    lines.append("")
    lines.append("Abstract:")
    lines.append(record.abstract or "(no abstract available via Crossref)")
    return "\n".join(lines)


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
        article = self._article
        button_id = event.button.id
        if button_id == "open_pubmed" and article.url:
            webbrowser.open(article.url)
        elif button_id == "open_free" and self._access.url:
            webbrowser.open(self._access.url)
        elif button_id == "open_scihub" and article.doi:
            webbrowser.open(f"{SCI_HUB_BASE_URL}{article.doi}")
        elif button_id == "read_fulltext" and article.pmc:
            self.dismiss("fulltext")
            return
        elif button_id == "copy_citation":
            self.app.copy_to_clipboard(format_citation(article))
            self.notify("Citation copied to clipboard.")
            return
        elif button_id == "copy_doi" and article.doi:
            self.app.copy_to_clipboard(article.doi)
            self.notify("DOI copied to clipboard.")
            return
        elif button_id == "copy_url" and article.url:
            self.app.copy_to_clipboard(article.url)
            self.notify("URL copied to clipboard.")
            return
        elif button_id == "remove":
            self.dismiss("remove")
            return
        self.dismiss(None)


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
    everything Crossref/PMC have on it, with the same open/copy/full-text actions as a
    search result via a nested ArticleActionsScreen."""

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
                yield Button("Actions...", id="actions", disabled=True)
                yield Button("Close", id="close")

    def on_mount(self) -> None:
        self.query_one("#doi_input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "doi_input":
            self._do_lookup()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "lookup":
            self._do_lookup()
        elif event.button.id == "actions":
            self.run_worker(self._show_actions(), exclusive=True)
        else:
            self.dismiss(None)

    def _do_lookup(self) -> None:
        doi = self.query_one("#doi_input", Input).value.strip()
        if not doi:
            return
        self.run_worker(self._fetch(doi), exclusive=True)

    async def _fetch(self, doi: str) -> None:
        text = self.query_one("#doi_text", Static)
        actions_button = self.query_one("#actions", Button)
        actions_button.disabled = True
        self._record = None
        self._access = None
        text.update(f"Looking up {doi} ...")

        data = await asyncio.to_thread(fetch_doi_info, doi)
        if data is None:
            text.update(f"No Crossref record found for DOI: {doi}")
            return

        pmc = await asyncio.to_thread(fetch_pmcid_for_doi, doi)
        record = build_doi_record(data, pmc)
        access = await asyncio.to_thread(resolve_open_access, pmc, record.doi)
        self._record = record
        self._access = access
        text.update(render_doi_detail(record, access))
        actions_button.disabled = False

    async def _show_actions(self) -> None:
        if self._record is None or self._access is None:
            return
        result = await self.app.push_screen_wait(
            ArticleActionsScreen(self._record.doi, self._record, self._access, "DOI")
        )
        if result == "fulltext" and self._record.pmc:
            text = await asyncio.to_thread(fetch_pmc_fulltext, self._record.pmc)
            self.app.push_screen(FullTextScreen(self._record.title or "(no title)", text))
        # "remove" has nothing to remove here (no results table in this mode) — ignored.


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
                        placeholder=UNPAYWALL_EMAIL,
                        id="unpaywall_email_input",
                    )
                with Vertical(id="filters_col_right"):
                    yield Label("Sci-Hub mirror (base URL, trailing slash)", classes="filter_hint")
                    yield Input(
                        value=self._settings.scihub_mirror,
                        placeholder=SCI_HUB_BASE_URL,
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
