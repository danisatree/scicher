"""Open-access resolution: PMC/Unpaywall lookups and PMC full-text retrieval."""

import os
from dataclasses import dataclass
from typing import Any

import requests

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
