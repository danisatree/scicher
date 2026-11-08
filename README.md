# Scicher

A terminal UI for building PubMed advanced-search queries, running them (optionally
alongside arXiv), and browsing the results — without leaving the keyboard.

Built with [Textual](https://github.com/Textualize/textual).

## Why

PubMed's advanced search builder works, but it's a web form: slow to iterate on, easy
to lose track of what you've built, and separate from wherever you're keeping notes on
what you found. Scicher keeps the query builder, the live query preview, the results
table, and per-article detail all on one screen, with everything driven from the
keyboard.

## Features

- **Visual query builder** — add terms scoped to a field (Title/Abstract, MeSH,
  Title, Text Word, Author, Journal, or raw/unparsed), grouped with AND/OR, rendered
  live as the actual PubMed query string you're about to run.
- **MeSH autocomplete** — typing a MeSH term shows a ranked dropdown of matches
  (prefix matches first, then substring matches anywhere in the term) instead of
  guessing the exact descriptor name.
- **Filters** — publication year range, publication type, species (human/animal),
  free-full-text-only, and max results, folded into the same query.
- **arXiv as a second source** — run the compatible part of the same query against
  arXiv alongside PubMed, with results merged into one table.
- **Results table** — sortable by year, with per-row KPIs (total, sources, year
  range, missing abstracts, open access count) and a detail pane for the
  highlighted row.
- **DOI lookup** — look up any DOI directly (independent of a search), pulling
  Crossref metadata and resolving open-access/PMC full text.
- **Per-article actions** — open the article/DOI page, open a free copy if one is
  available, open a Sci-Hub link, read PMC full text inline, or copy the citation,
  DOI, or URL.
- **CSV export and query log** — export the current result set to CSV, or append the
  current query to a running CSV log for later reference.
- **Save/load queries** — persist a query (terms, groups, and filters) and reload it
  later.
- **Tokyo Night theme** — dark and light variants, each color traced to the official
  [Tokyo Night VS Code theme](https://github.com/tokyo-night/tokyo-night-vscode-theme).

## Requirements

- Python 3.13+
- A PubMed/NCBI API key is recommended (set via Settings in-app) to avoid rate limits
  on [E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25497/).

## Installation

Using [uv](https://docs.astral.sh/uv/) (recommended — this repo ships a `uv.lock`):

```sh
uv sync
uv run main.py
```

Or with plain `pip`:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python main.py
```

## Usage

Run the app, then use the number keys to jump between panes and the listed single-key
actions to drive everything else. The in-app footer always shows the bindings
available in the current context; the key ones are:

| Key | Action |
| --- | --- |
| `1` / `2` / `3` / `4` | Focus Terms / Query / Results / Detail |
| `5` | DOI Lookup |
| `a` | Add term |
| `n` | New group (AND) |
| `m` | Edit selected term |
| `d` | Delete selected term |
| `f` | Edit filters |
| `s` | Run search |
| `y` | Sort results by year |
| `e` | Export results to CSV |
| `l` | Log current query to CSV |
| `o` | Load a saved query |
| `u` | Undo last removed term |
| `p` | Settings |
| `t` | Toggle light/dark theme |
| `q` | Quit |

`Ctrl+P` opens the command palette for everything else.

## Project layout

- `main.py` — the app: query model, PubMed/arXiv/Crossref fetchers, and all Textual
  screens/widgets.
- `theme.py` — the Tokyo Night theme definitions.
- `style.tcss` — Textual CSS for the app's layout and widgets.
- `data/mesh_terms.txt` — a static list of MeSH descriptor names used for
  autocomplete.

## Status

This is a personal tool under active development.

## License

[MIT](LICENSE)
