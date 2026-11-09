"""arXiv: a second, optional search source alongside PubMed."""

from dataclasses import dataclass, field

import requests

from querymodel import Group, Term

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
