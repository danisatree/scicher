# Backlog

## Add bioRxiv as an additional search source

arXiv is already implemented as a second source alongside PubMed (shared
term builder, source toggles in §1, merged into §3/§4/CSV export via the
same article model). bioRxiv is the remaining piece: it has no keyword
search of its own (its native API only supports date/category browsing),
so it likely needs to go through Europe PMC instead of bioRxiv's own API —
noted during an earlier feasibility pass but not yet implemented.

## Abstract fallback when PubMed has none: tested, not worth building yet

Some PubMed records come back with no abstract (`article.abstract` empty) —
mostly old (1950s-80s) letters/case reports, often non-English, from before
structured abstracts were standard. Tested two free fallback APIs against
15 such rows from a real search (`scicher_results_20261101_155051.csv`):

- **Semantic Scholar** (`api.semanticscholar.org`, by `PMID:`/`DOI:`): only
  3/15 requests succeeded even with backoff retries up to 20s — unauthenticated
  rate limit is too aggressive/shared to rely on without an API key. Of those
  3, 0 had an abstract.
- **OpenAlex** (`api.openalex.org`, by `pmid:`/`doi:`, with `mailto=` for the
  polite pool): found all 15/15 records reliably (docs: 100k requests/day,
  10 req/sec polite pool), but 0/15 had `abstract_inverted_index` either.

Conclusion: for this dataset, the abstract genuinely doesn't exist anywhere
digitized, not just missing from PubMed — no API fallback would have helped.
OpenAlex is the more promising mechanism if this gets revisited (reliable
record coverage, generous polite-pool limit, `mailto` already available from
the Unpaywall email setting) but there's no evidence yet it would recover
abstracts for newer papers either. Re-test on a sample of *recent* (post-2015)
PubMed rows with a missing abstract before investing in building this —
the current test sample skews old/non-English, which may not be
representative of when PubMed usually omits an abstract.

## Deduplicate results across sources

A preprint searched via arXiv can also show up as its own PubMed row once
published (or vice versa for a journal article that was also deposited as
a preprint), so the same work can appear twice in §3/export.

- Match by DOI when both rows have one; arXiv preprints often don't get a
  DOI until the journal version is indexed, so also need a normalized
  title fallback (lowercase, strip punctuation/whitespace) for the common
  case where only one side has a DOI.
- Open question: merge into one row (which metadata wins — PubMed's is
  usually richer) or keep both but visually link them (e.g. a "see also"
  note)? Silently dropping one loses the preprint's earlier-access info.

## Export to reference-manager formats (BibTeX/RIS)

CSV is good for spreadsheets but researchers importing into Zotero/
EndNote/Mendeley want BibTeX or RIS. The article model already has every
field those formats need (`article_to_row` in `main.py`), so this is
mostly a second `ExportCsvScreen`-style modal with a different writer
function, not a new data path.

- Needs a citation key generator (e.g. `AuthorYear`, de-duplicated with
  a/b/c suffixes for collisions).
- Needs an entry-type mapping: PubMed journal articles -> `@article`,
  arXiv preprints -> `@misc` (or `@article` with an `eprint` field, per
  BibTeX convention) — don't just hardcode `@article` for everything.

## Flag retracted articles

A risk signal worth surfacing before someone cites a paper: Crossref's
`/works/{doi}` response includes an `update-to` relation with type
`retraction` when a work has been retracted (backed by Retraction Watch
data as of their 2023 merger with Crossref). Checked that Crossref is the
mechanism to use — haven't yet tested call volume/latency against a real
batch of DOIs from this app's result sets.

- Same lazy/on-demand pattern as Unpaywall: don't bulk-check the whole
  table on search (adds a network round-trip per row); check when the
  user opens §4/the article actions modal, or add an explicit opt-in
  "check retractions" bulk action with its own rate-limit handling.
- Surface as a loud flag in §4 detail and the article actions modal, not
  just a quiet CSV column — this is the kind of thing a researcher needs
  to actually notice.

## Snowballing: find related/citing papers from an article

Add "Find related" / "Find citing papers" to the article actions modal
(alongside Copy citation etc.), backed by OpenAlex's `related_works` /
`cited_by_api_url` or Semantic Scholar's recommendations endpoint — both
already evaluated this session for the abstract-fallback investigation
(OpenAlex: reliable by `pmid:`/`doi:`, 100k/day + 10 req/sec polite pool
with `mailto`; Semantic Scholar: free tier too rate-limited to trust for
bulk/background use).

- Results should merge into §3 as an additional mini-search (same
  `self._articles`/`self._last_result_keys` path as a normal search),
  not a separate screen, so export/filters/KPIs keep working on them.
- OpenAlex is the safer default given Semantic Scholar's observed 429s.

## Publish to PyPI, so it's installable via `uvx`/`pipx`/Homebrew tap

Currently only runnable from a cloned repo (`python main.py`). Publishing
to PyPI would let users run it with `uvx scicher` / `uv tool install
scicher` without cloning anything; a Homebrew tap (separate maintenance
burden, see below) could wrap that same PyPI package rather than vendoring
dependencies itself. Homebrew core is unrealistic for a project this size/
young — a personal tap is the realistic Homebrew path if that's wanted later.

What's needed:
- **Restructure into a proper package.** Everything is currently flat in
  the repo root (`main.py`, `style.tcss`, `theme.py`, `data/mesh_terms.txt`),
  not a `src/scicher/` package. `style.tcss` (`CSS_PATH = "style.tcss"`)
  and `data/mesh_terms.txt` (`Path(__file__).parent / "data" / ...`) are
  loaded relative to the module file, so they need to actually ship inside
  the package (declared as package data), or the installed app will fail
  to find them at startup. `main()` / `if __name__ == "__main__":` already
  exist at the bottom of `main.py` — the entry point logic itself doesn't
  need to change, just where the file lives.
- **`pyproject.toml` metadata**: add `[project.scripts] scicher = "scicher.app:main"`
  (or equivalent post-restructure path); add `requests` to `dependencies`
  (used directly in `main.py` but not currently declared — presumably
  pulled in transitively via `metapub`); replace the placeholder
  `description = "Add your description here"`; add a `license` (no
  `LICENSE` file or license field exists in the repo at all right now).
- **Account/naming**: a PyPI account + API token, and confirm the name
  `scicher` isn't already taken.
- **Build/publish tooling**: `uv build` (wheel + sdist) then `uv publish`
  — no need for a separate `twine` dependency, `uv` does both natively.
