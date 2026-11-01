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
