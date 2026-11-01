# Backlog

## Add arXiv and bioRxiv as additional search sources

Currently search only hits PubMed (via `metapub`). Add arXiv and bioRxiv
as alternative/additional sources, with a way to pick which engine(s) a
search runs against (so if one of them turns out to be redundant or
unreliable, it can just be left unselected rather than ripped out).

Open questions to resolve when picking this up:
- UI: a selector in §1/§2 (query terms/compiled query area) vs. a new
  settings toggle, since query syntax differs per source (PubMed field
  tags vs. arXiv/bioRxiv search params).
- Each source has its own result schema — need a shared internal
  article model so §3/§4 and CSV export stay source-agnostic.
- Filters (year, publication type, species, full text) are PubMed-specific;
  decide what subset applies per source.

## Add a Language column to the Found Articles table (§3)

PubMed's efetch XML has a reliable `<Language>` element (ISO 639-2/B code,
e.g. `eng`, `fre`, `rus`) on essentially every record; confirmed via direct
efetch calls that it's populated for both recent and old entries (only a
small number of obscure/very old records come back `und` — undetermined).
`metapub.PubMedArticle` doesn't expose it as an attribute, but it's on the
underlying `article.content` lxml tree: `article.content.find(".//Language")`.
arXiv has no equivalent per-article language field (near-universally English,
not confidently per-record), so leave that column blank for arXiv rows
rather than guessing.

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

## Saved queries as a "watch" (diff since last run)

The query log (`l`/`o`) already stores query text + result count per
run, but not which specific articles matched. Extend it to snapshot the
full ID list per run (PMIDs + arXiv ids), so re-running a saved query can
report "N new since last run" instead of just the new total count.

- Storage: probably only keep the latest ID snapshot per saved query
  (not full history) to avoid the log growing unbounded.
- UI: an explicit "re-run and diff" action from `LoadQueryScreen`, separate
  from a plain reload, since a diff needs the previous snapshot kept around
  specifically for comparison rather than just restoring the query text.

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
