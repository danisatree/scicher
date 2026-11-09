import csv
import os
import stat

import pytest

import openaccess
import persistence
from persistence import (
    Settings,
    apply_settings,
    append_query_log,
    load_settings,
    load_watch_snapshots,
    read_query_log,
    save_settings,
    save_watch_snapshot,
    write_results_csv,
)
from pubmed_source import CSV_FIELDS


@pytest.fixture(autouse=True)
def _restore_openaccess_globals():
    """apply_settings mutates openaccess module globals; isolate tests from each other."""
    original_email = openaccess.UNPAYWALL_EMAIL
    original_mirror = openaccess.SCI_HUB_BASE_URL
    yield
    openaccess.UNPAYWALL_EMAIL = original_email
    openaccess.SCI_HUB_BASE_URL = original_mirror


def test_write_results_csv_round_trips_rows(tmp_path):
    path = tmp_path / "results.csv"
    rows = [
        {field_name: "" for field_name in CSV_FIELDS} | {"pmid": "1", "source": "PubMed", "title": "A"},
        {field_name: "" for field_name in CSV_FIELDS} | {"pmid": "2", "source": "arXiv", "title": "B"},
    ]
    write_results_csv(str(path), rows)

    with open(path, newline="", encoding="utf-8") as fh:
        read_rows = list(csv.DictReader(fh))
    assert [r["pmid"] for r in read_rows] == ["1", "2"]
    assert read_rows[0]["title"] == "A"


def test_append_query_log_writes_header_once(tmp_path):
    path = tmp_path / "log.csv"
    append_query_log(str(path), "migraine[title]", "5")
    append_query_log(str(path), "headache[title]", "3")

    with open(path, newline="", encoding="utf-8") as fh:
        lines = fh.readlines()
    assert lines[0].strip() == "timestamp,query,result_count"
    assert sum(1 for line in lines if line.startswith("timestamp,query")) == 1


def test_read_query_log_returns_rows_newest_first(tmp_path):
    path = tmp_path / "log.csv"
    append_query_log(str(path), "first query", "1")
    append_query_log(str(path), "second query", "2")

    rows = read_query_log(str(path))
    assert [r["query"] for r in rows] == ["second query", "first query"]


def test_read_query_log_skips_blank_query_rows(tmp_path):
    path = tmp_path / "log.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["timestamp", "query", "result_count"])
        writer.writeheader()
        writer.writerow({"timestamp": "t", "query": "", "result_count": ""})
        writer.writerow({"timestamp": "t", "query": "real query", "result_count": "1"})

    rows = read_query_log(str(path))
    assert len(rows) == 1
    assert rows[0]["query"] == "real query"


def test_read_query_log_missing_file_returns_empty_list(tmp_path):
    assert read_query_log(str(tmp_path / "missing.csv")) == []


def test_watch_snapshots_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "WATCH_SNAPSHOTS_PATH", tmp_path / "snapshots.json")

    assert load_watch_snapshots() == {}
    save_watch_snapshot("migraine[title]", ["pubmed:1", "pubmed:2"])
    save_watch_snapshot("headache[title]", ["pubmed:3"])

    snapshots = load_watch_snapshots()
    assert snapshots == {
        "migraine[title]": ["pubmed:1", "pubmed:2"],
        "headache[title]": ["pubmed:3"],
    }


def test_load_watch_snapshots_tolerates_corrupt_json(tmp_path, monkeypatch):
    path = tmp_path / "snapshots.json"
    path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(persistence, "WATCH_SNAPSHOTS_PATH", path)

    assert load_watch_snapshots() == {}


def test_load_settings_returns_defaults_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "SETTINGS_PATH", tmp_path / "settings.json")
    assert load_settings() == Settings()


def test_load_settings_tolerates_corrupt_json(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(persistence, "SETTINGS_PATH", path)
    assert load_settings() == Settings()


def test_save_and_load_settings_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(persistence, "SETTINGS_PATH", path)

    settings = Settings(ncbi_api_key="abc123", unpaywall_email="me@example.com", theme="science-light")
    save_settings(settings)

    assert load_settings() == settings


def test_save_settings_restricts_file_permissions(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(persistence, "SETTINGS_PATH", path)

    save_settings(Settings(ncbi_api_key="secret"))

    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_apply_settings_sets_ncbi_env_var(monkeypatch):
    monkeypatch.delenv("NCBI_API_KEY", raising=False)
    apply_settings(Settings(ncbi_api_key="my-key"))
    assert os.environ["NCBI_API_KEY"] == "my-key"


def test_apply_settings_updates_openaccess_module_globals():
    apply_settings(Settings(unpaywall_email="me@example.com", scihub_mirror="https://mirror.example/"))
    assert openaccess.UNPAYWALL_EMAIL == "me@example.com"
    assert openaccess.SCI_HUB_BASE_URL == "https://mirror.example/"


def test_apply_settings_leaves_openaccess_globals_untouched_when_blank():
    openaccess.UNPAYWALL_EMAIL = "existing@example.com"
    apply_settings(Settings(unpaywall_email="", scihub_mirror=""))
    assert openaccess.UNPAYWALL_EMAIL == "existing@example.com"
