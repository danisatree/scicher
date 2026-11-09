"""Local file persistence: results/query-log CSVs, watch snapshots, and user settings."""

import contextlib
import csv
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import openaccess
from pubmed_source import CSV_FIELDS


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
    """Push settings into the running process: env var for metapub, openaccess module globals."""
    if settings.ncbi_api_key:
        os.environ["NCBI_API_KEY"] = settings.ncbi_api_key
        try:
            import metapub.eutils_common as _eutils_common

            _eutils_common.API_KEY = settings.ncbi_api_key
        except ImportError:
            pass

    if settings.unpaywall_email:
        openaccess.UNPAYWALL_EMAIL = settings.unpaywall_email
    if settings.scihub_mirror:
        openaccess.SCI_HUB_BASE_URL = settings.scihub_mirror
