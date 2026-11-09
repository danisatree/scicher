import pytest

from querymodel import (
    Filters,
    Group,
    Term,
    build_query,
    describe_filters,
    match_mesh_terms,
    parse_query,
    quote_if_needed,
    render_filters,
    render_group,
    render_query,
    render_term,
    split_top_level,
)


def test_quote_if_needed_adds_quotes_only_for_multiword_values():
    assert quote_if_needed("migraine") == "migraine"
    assert quote_if_needed("von Willebrand Disease") == '"von Willebrand Disease"'
    assert quote_if_needed('"already quoted value"') == '"already quoted value"'


def test_render_term_all_field_is_raw_unquoted():
    assert render_term(Term(field="ALL", value="migraine AND headache")) == "migraine AND headache"


def test_render_term_quotes_multiword_values():
    assert render_term(Term(field="title", value="von Willebrand Disease")) == (
        '"von Willebrand Disease"[title]'
    )


def test_render_group_single_term_has_no_parens():
    group = Group(terms=[Term(field="title", value="migraine")])
    assert render_group(group) == "migraine[title]"


def test_render_group_multiple_terms_are_ored_in_parens():
    group = Group(terms=[Term(field="title", value="migraine"), Term(field="title", value="headache")])
    assert render_group(group) == "(migraine[title] OR headache[title])"


def test_render_query_ands_non_empty_groups_only():
    groups = [
        Group(terms=[Term(field="title", value="migraine")]),
        Group(),  # empty group is skipped
        Group(terms=[Term(field="au", value="Smith")]),
    ]
    assert render_query(groups) == "migraine[title] and Smith[au]"


def test_filters_is_empty_true_for_defaults():
    assert Filters().is_empty()


@pytest.mark.parametrize(
    "filters",
    [
        Filters(year_from="2015"),
        Filters(year_to="2020"),
        Filters(publication_types=frozenset({"Review"})),
        Filters(species="human"),
        Filters(free_full_text=True),
        Filters(max_results=100),
    ],
)
def test_filters_is_empty_false_when_any_field_set(filters):
    assert not filters.is_empty()


def test_render_filters_year_range_fills_in_missing_bound():
    clauses = render_filters(Filters(year_from="2015"))
    assert len(clauses) == 1
    assert clauses[0].startswith('("2015"[dp] : "')
    assert clauses[0].endswith('"[dp])')


def test_render_filters_species_and_free_full_text():
    clauses = render_filters(Filters(species="human", free_full_text=True))
    assert clauses == ['"humans"[mesh]', '"free full text"[filter]']


def test_render_filters_multiple_publication_types_are_ored_sorted_and_parenthesized():
    clauses = render_filters(Filters(publication_types=frozenset({"Review", "Case Reports"})))
    assert clauses == ['("Case Reports"[pt] OR "Review"[pt])']


def test_render_filters_single_publication_type_has_no_parens():
    clauses = render_filters(Filters(publication_types=frozenset({"Review"})))
    assert clauses == ['"Review"[pt]']


def test_build_query_combines_groups_and_filters():
    groups = [Group(terms=[Term(field="title", value="migraine")])]
    filters = Filters(species="human")
    assert build_query(groups, filters) == 'migraine[title] and "humans"[mesh]'


def test_build_query_empty_groups_and_filters_is_empty_string():
    assert build_query([Group()], Filters()) == ""


@pytest.mark.parametrize(
    ("text", "sep", "expected"),
    [
        ("a and b and c", " and ", ["a", "b", "c"]),
        ('(a and b) and "c and d"', " and ", ['(a and b)', '"c and d"']),
        ("a and  ", " and ", ["a"]),
    ],
)
def test_split_top_level_ignores_separators_inside_parens_and_quotes(text, sep, expected):
    assert split_top_level(text, sep) == expected


@pytest.mark.parametrize(
    "query",
    [
        "migraine[title/abstract]",
        '"von Willebrand Disease"[MeSH]',
        'migraine[title/abstract] and Smith[au]',
        '(migraine[title/abstract] OR headache[title/abstract])',
        '("2015"[dp] : "2025"[dp])',
        '"humans"[mesh]',
        '"animals"[mesh]',
        '"free full text"[filter]',
        '("Case Reports"[pt] OR "Review"[pt])',
    ],
)
def test_parse_query_round_trips_app_generated_clauses(query):
    groups, filters = parse_query(query)
    assert build_query(groups, filters) == query


def test_parse_query_round_trip_for_full_query_with_filters():
    groups = [
        Group(terms=[Term(field="title/abstract", value="migraine")]),
        Group(terms=[Term(field="au", value="Smith"), Term(field="au", value="Jones")]),
    ]
    filters = Filters(
        year_from="2015",
        year_to="2025",
        publication_types=frozenset({"Review", "Meta-Analysis"}),
        species="human",
        free_full_text=True,
    )
    query = build_query(groups, filters)

    parsed_groups, parsed_filters = parse_query(query)

    assert build_query(parsed_groups, parsed_filters) == query
    assert parsed_filters == filters
    assert parsed_groups == groups


def test_parse_query_unrecognized_clause_becomes_raw_all_term():
    groups, filters = parse_query("some free text nobody generated")
    assert filters == Filters()
    assert len(groups) == 1
    [term] = groups[0].terms
    assert term.field == "ALL"
    assert term.value == "some free text nobody generated"


def test_describe_filters_lists_only_set_fields():
    filters = Filters(year_from="2015", species="human", max_results=100)
    description = describe_filters(filters)
    assert "2015" in description
    assert "Human" in description
    assert "Max 100" in description


def test_describe_filters_empty_for_defaults():
    assert describe_filters(Filters()) == ""


def test_match_mesh_terms_empty_query_returns_nothing():
    assert match_mesh_terms(["Migraine Disorders"], "") == []


def test_match_mesh_terms_prefix_matches_rank_before_substring_matches():
    terms = ["Disease Susceptibility", "von Willebrand Disease", "Diseases, Genetic"]
    matches = match_mesh_terms(terms, "disease")
    assert matches[0] in {"Disease Susceptibility", "Diseases, Genetic"}
    assert "von Willebrand Disease" in matches
    assert matches.index("von Willebrand Disease") > 0


def test_match_mesh_terms_is_case_insensitive_and_respects_limit():
    terms = [f"Term {i}" for i in range(20)]
    matches = match_mesh_terms(terms, "TERM", limit=5)
    assert len(matches) == 5
