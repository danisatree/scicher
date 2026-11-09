"""PubMed fetching helpers and the CSV row shape shared by both search sources."""

from typing import Any

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
