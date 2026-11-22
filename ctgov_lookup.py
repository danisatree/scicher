"""ClinicalTrials.gov trial lookup by NCT ID — independent of PubMed/arXiv search results (app §6)."""

from dataclasses import dataclass, field
from typing import Any

import requests

CTGOV_API_URL = "https://clinicaltrials.gov/api/v2/studies"


@dataclass
class TrialRecord:
    """Exposes the same attribute names ArticleActionsScreen/format_citation read off a
    metapub PubMedArticle or ArxivRecord (doi/pmc/authors/journal/volume/issue/pages),
    permanently empty here since none apply to a trial registration — the trial screen
    uses its own trimmed action set instead of the shared article-action helpers."""

    nct_id: str
    title: str = ""
    official_title: str = ""
    status: str = ""
    phase: str = ""
    study_type: str = ""
    sponsor: str = ""
    conditions: list[str] = field(default_factory=list)
    interventions: list[str] = field(default_factory=list)
    enrollment: int | None = None
    start_date: str = ""
    completion_date: str = ""
    brief_summary: str = ""
    eligibility_criteria: str = ""
    why_stopped: str = ""

    doi: str = ""
    pmc: str | None = None
    authors: list[str] = field(default_factory=list)
    journal: str = ""
    year: str = ""
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None

    @property
    def url(self) -> str:
        return f"https://clinicaltrials.gov/study/{self.nct_id}"

    @property
    def keywords(self) -> list[str]:
        return self.conditions


def normalize_nct_id(value: str) -> str:
    """Upper-cases and prepends "NCT" if the user typed only the digits. No further
    validation — a malformed id is left for the API's 404 to reject, matching how
    DOI lookup does no client-side format checking either."""
    value = value.strip().upper()
    if value.isdigit():
        return f"NCT{value}"
    return value


def fetch_trial_info(nct_id: str) -> dict[str, Any] | None:
    """Raw ClinicalTrials.gov v2 /studies/{nctId} metadata, or None if not found."""
    try:
        resp = requests.get(
            f"{CTGOV_API_URL}/{normalize_nct_id(nct_id)}",
            params={"format": "json"},
            timeout=15,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception:  # noqa: BLE001 - surfaced as "no record found" by the caller
        return None


def build_trial_record(data: dict[str, Any]) -> TrialRecord:
    """Flattens a raw ClinicalTrials.gov v2 study response into a TrialRecord."""
    protocol = data.get("protocolSection") or {}
    identification = protocol.get("identificationModule") or {}
    status_module = protocol.get("statusModule") or {}
    sponsor_module = protocol.get("sponsorCollaboratorsModule") or {}
    design = protocol.get("designModule") or {}
    conditions_module = protocol.get("conditionsModule") or {}
    arms = protocol.get("armsInterventionsModule") or {}
    description = protocol.get("descriptionModule") or {}
    eligibility = protocol.get("eligibilityModule") or {}

    start_date = (status_module.get("startDateStruct") or {}).get("date", "")
    enrollment_info = design.get("enrollmentInfo") or {}
    phases = design.get("phases") or []
    interventions = [
        f"{i.get('type', '')}: {i.get('name', '')}".strip(": ").strip()
        for i in arms.get("interventions") or []
    ]

    return TrialRecord(
        nct_id=identification.get("nctId", ""),
        title=identification.get("briefTitle", ""),
        official_title=identification.get("officialTitle", ""),
        status=status_module.get("overallStatus", ""),
        phase=", ".join(phases),
        study_type=design.get("studyType", ""),
        sponsor=(sponsor_module.get("leadSponsor") or {}).get("name", ""),
        conditions=conditions_module.get("conditions") or [],
        interventions=interventions,
        enrollment=enrollment_info.get("count"),
        start_date=start_date,
        completion_date=(status_module.get("completionDateStruct") or {}).get("date", ""),
        brief_summary=description.get("briefSummary", ""),
        eligibility_criteria=eligibility.get("eligibilityCriteria", ""),
        why_stopped=status_module.get("whyStopped", ""),
        year=start_date[:4],
    )


def render_trial_detail(record: TrialRecord) -> str:
    lines = []
    lines.append(f"NCT ID: {record.nct_id}")
    lines.append(f"Title: {record.title or '(no title)'}")
    if record.official_title and record.official_title != record.title:
        lines.append(f"Official title: {record.official_title}")
    status_line = f"Status: {record.status or '?'}"
    if record.why_stopped:
        status_line += f" (why stopped: {record.why_stopped})"
    lines.append(status_line)
    lines.append(f"Phase: {record.phase or '-'} | Study type: {record.study_type or '?'}")
    lines.append(f"Sponsor: {record.sponsor or '?'}")
    lines.append(f"Conditions: {', '.join(record.conditions) or '-'}")
    lines.append(f"Interventions: {', '.join(record.interventions) or '-'}")
    lines.append(f"Enrollment: {record.enrollment if record.enrollment is not None else '?'}")
    lines.append(f"Start date: {record.start_date or '?'}")
    lines.append(f"Completion date: {record.completion_date or '?'}")
    lines.append(f"URL: {record.url}")
    lines.append("")
    lines.append("Eligibility criteria:")
    lines.append(record.eligibility_criteria or "(none listed)")
    lines.append("")
    lines.append("Brief summary:")
    lines.append(record.brief_summary or "(no summary available)")
    return "\n".join(lines)
