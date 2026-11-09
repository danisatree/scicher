"""PubMed advanced-search query model: terms, groups, filters, and round-trip (de)serialization."""

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path

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


MESH_SUGGESTION_LIMIT = 10


def match_mesh_terms(terms: list[str], query: str, limit: int = MESH_SUGGESTION_LIMIT) -> list[str]:
    """Rank MeSH terms for `query`: prefix matches first, then matches anywhere in the
    term (so a later word like "Disease" in "von Willebrand Disease" still surfaces),
    both case-insensitive, each group alphabetical, capped to `limit` results."""
    if not query:
        return []
    query_fold = query.casefold()
    prefix_matches = []
    other_matches = []
    for term in terms:
        term_fold = term.casefold()
        if term_fold.startswith(query_fold):
            prefix_matches.append(term)
        elif query_fold in term_fold:
            other_matches.append(term)
    return (prefix_matches + other_matches)[:limit]


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
