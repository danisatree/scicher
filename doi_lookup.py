"""DOI lookup via Crossref/PMC — independent of PubMed/arXiv search results (app §5)."""

import re
from dataclasses import dataclass, field
from typing import Any

import requests

import openaccess


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
            f"https://api.crossref.org/works/{doi}",
            params={"mailto": openaccess.UNPAYWALL_EMAIL},
            timeout=15,
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
            params={
                "ids": doi,
                "format": "json",
                "email": openaccess.UNPAYWALL_EMAIL,
                "tool": "scicher",
            },
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


def render_doi_detail(record: DoiRecord, access: "openaccess.OpenAccessInfo") -> str:
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
