from unittest.mock import Mock, patch

from ctgov_lookup import build_trial_record, fetch_trial_info, normalize_nct_id, render_trial_detail

STUDY_PAYLOAD = {
    "protocolSection": {
        "identificationModule": {
            "nctId": "NCT01286116",
            "briefTitle": "A Study of Things",
            "officialTitle": "A Randomized Study of Things in Patients",
        },
        "statusModule": {
            "overallStatus": "COMPLETED",
            "startDateStruct": {"date": "2011-02"},
            "completionDateStruct": {"date": "2015-06"},
        },
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Example Sponsor"}},
        "designModule": {
            "studyType": "INTERVENTIONAL",
            "phases": ["PHASE3"],
            "enrollmentInfo": {"count": 250},
        },
        "conditionsModule": {"conditions": ["Example Disease"]},
        "armsInterventionsModule": {
            "interventions": [{"type": "DRUG", "name": "Exampletinib"}],
        },
        "descriptionModule": {"briefSummary": "A brief summary of the study."},
        "eligibilityModule": {"eligibilityCriteria": "Inclusion: adults. Exclusion: none."},
    }
}


def test_build_trial_record_flattens_study():
    record = build_trial_record(STUDY_PAYLOAD)
    assert record.nct_id == "NCT01286116"
    assert record.title == "A Study of Things"
    assert record.official_title == "A Randomized Study of Things in Patients"
    assert record.status == "COMPLETED"
    assert record.phase == "PHASE3"
    assert record.study_type == "INTERVENTIONAL"
    assert record.sponsor == "Example Sponsor"
    assert record.conditions == ["Example Disease"]
    assert record.interventions == ["DRUG: Exampletinib"]
    assert record.enrollment == 250
    assert record.start_date == "2011-02"
    assert record.completion_date == "2015-06"
    assert record.brief_summary == "A brief summary of the study."
    assert record.eligibility_criteria == "Inclusion: adults. Exclusion: none."
    assert record.year == "2011"
    assert record.url == "https://clinicaltrials.gov/study/NCT01286116"
    assert record.doi == ""
    assert record.pmc is None
    assert record.keywords == record.conditions


def test_build_trial_record_handles_missing_optional_modules():
    record = build_trial_record({"protocolSection": {"identificationModule": {"nctId": "NCT00000001"}}})
    assert record.nct_id == "NCT00000001"
    assert record.title == ""
    assert record.conditions == []
    assert record.interventions == []
    assert record.enrollment is None
    assert record.start_date == ""
    assert record.eligibility_criteria == ""


def test_build_trial_record_includes_why_stopped_when_present():
    data = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT02", "briefTitle": "Halted Study"},
            "statusModule": {"overallStatus": "TERMINATED", "whyStopped": "Low enrollment"},
        }
    }
    record = build_trial_record(data)
    assert record.why_stopped == "Low enrollment"
    assert "why stopped: Low enrollment" in render_trial_detail(record)


def test_render_trial_detail_includes_key_fields():
    record = build_trial_record(STUDY_PAYLOAD)
    detail = render_trial_detail(record)
    assert "NCT ID: NCT01286116" in detail
    assert "Title: A Study of Things" in detail
    assert "Status: COMPLETED" in detail
    assert "Phase: PHASE3" in detail
    assert "Sponsor: Example Sponsor" in detail
    assert "Enrollment: 250" in detail
    assert "Inclusion: adults. Exclusion: none." in detail


def test_fetch_trial_info_returns_none_on_404():
    fake_response = Mock(status_code=404)
    with patch("ctgov_lookup.requests.get", return_value=fake_response):
        assert fetch_trial_info("NCT99999999") is None


def test_fetch_trial_info_returns_payload_on_success():
    fake_response = Mock(status_code=200)
    fake_response.raise_for_status = Mock()
    fake_response.json.return_value = STUDY_PAYLOAD
    with patch("ctgov_lookup.requests.get", return_value=fake_response):
        data = fetch_trial_info("NCT01286116")
    assert data == STUDY_PAYLOAD


def test_fetch_trial_info_returns_none_on_network_error():
    with patch("ctgov_lookup.requests.get", side_effect=ConnectionError("boom")):
        assert fetch_trial_info("NCT01286116") is None


def test_normalize_nct_id_prepends_prefix_and_uppercases():
    assert normalize_nct_id("01286116") == "NCT01286116"
    assert normalize_nct_id(" nct01286116 ") == "NCT01286116"
    assert normalize_nct_id("NCT01286116") == "NCT01286116"
