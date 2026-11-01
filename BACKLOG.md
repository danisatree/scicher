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
