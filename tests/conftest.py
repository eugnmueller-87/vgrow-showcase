"""Every test gets its own empty state folder and no ~/.vgrow.env: nothing reads or writes the real run."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    from vgrow import config, plant, vision

    state = tmp_path / "state"
    monkeypatch.setattr(config, "STATE_DIR", state)
    monkeypatch.setattr(config, "RECORDINGS_DIR", state / "recordings")
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "no.env")  # no VGROW_LANG, no ntfy: German, no pushes
    monkeypatch.setattr(config, "BRAIN_NTFY_FILE", tmp_path / "no-brain.env")
    monkeypatch.setattr(vision, "PHOTOS", state / "photos")
    monkeypatch.setattr(vision, "ANALYSES", state / "analysis")
    monkeypatch.setattr(config, "_ports_good", None)  # the last good ports.json is per process: per test here
    monkeypatch.setattr(config, "_ports_problem", None)
    monkeypatch.setattr(plant, "_runs_good", {})  # the last readable plant.json is per process too
    monkeypatch.setattr(plant, "_run_problems", {})
    return state


LOCKED_PORTS = Path(__file__).resolve().parent / "fixtures" / "ports-locked.json"


@pytest.fixture
def locked_ports(monkeypatch):  # type: ignore[no-untyped-def]
    """A ports file that locks USB 1 always on (tests/fixtures/ports-locked.json), in place of the real ports.json.

    The real ports.json locks no port since 23.10.2026 (USB 1 is the drip pump, the camera needs its own power supply); the
    always-on lock itself stays and is tested against this file, never against the owner's real setup."""
    from vgrow import config

    monkeypatch.setattr(config, "PORTS_FILE", LOCKED_PORTS)
    monkeypatch.setattr(config, "_ports_good", None)
    monkeypatch.setattr(config, "_ports_problem", None)
    return LOCKED_PORTS


# Public copy (scripts/build_public.py): these tests need the private strain data, which this copy does not carry.
_NEEDS_PRIVATE_DATA = {
    'tests/test_hank_acceptance.py::test_route_on_variants_at_least_95_percent_and_safety_classes_100_percent',
    'tests/test_hank_calibration.py::test_calibration_replay_matches_the_router_on_the_eval_set',
    'tests/test_hank_eval_offline.py::test_eval_case_offline[strains-01]',
    'tests/test_hank_eval_offline.py::test_eval_case_offline[strains-02]',
    'tests/test_hank_eval_offline.py::test_eval_case_offline[strains-03]',
    'tests/test_hank_eval_offline.py::test_eval_case_offline[strains-04]',
    'tests/test_hank_eval_offline.py::test_eval_case_offline[strains-05]',
    'tests/test_hank_eval_offline.py::test_eval_case_offline[strains-06]',
    'tests/test_hank_eval_offline.py::test_eval_case_offline[strains-07]',
    'tests/test_hank_eval_offline.py::test_eval_case_offline[strains-11]',
    'tests/test_hank_eval_offline.py::test_eval_case_offline[strains-12]',
    'tests/test_hank_eval_offline.py::test_eval_case_offline[strains-14]',
    'tests/test_hank_eval_offline.py::test_eval_case_offline[strains-15]',
    'tests/test_hank_eval_offline.py::test_eval_case_offline[strains-19]',
    'tests/test_hank_router.py::test_route_matches_expected_for_all_active_cases_lexical_only',
    'tests/test_hank_router.py::test_route_with_the_file_thresholds_matches_all_active_cases',
    'tests/test_hank_strains.py::test_a_longest_time_holds_seed_to_harvest_and_says_so_when_only_flowering_is_known',
    'tests/test_hank_strains.py::test_a_name_without_a_type_lists_its_autoflower_and_spellings_are_one_parent',
    'tests/test_hank_strains.py::test_a_named_strain_is_cannabis_for_the_gates',
    'tests/test_hank_strains.py::test_a_named_strain_keeps_its_growing_questions',
    'tests/test_hank_strains.py::test_a_question_of_its_own_while_hanks_question_is_open_is_no_reply',
    'tests/test_hank_strains.py::test_a_question_that_says_it_all_is_answered_at_once',
    'tests/test_hank_strains.py::test_a_rare_strain_gets_its_grow_facts_and_the_fact_sheets_verdict',
    'tests/test_hank_strains.py::test_a_seed_shop_name_in_running_text_follows_decision_d3',
    'tests/test_hank_strains.py::test_a_short_flowering_compares_flowering_times_only',
    'tests/test_hank_strains.py::test_a_strain_named_in_any_wording_is_looked_up[Gibt es im Wiki was zu Purple Punch?-de]',
    'tests/test_hank_strains.py::test_a_strain_named_in_any_wording_is_looked_up[Haben wir Infos zu Purple Punch?-de]',
    'tests/test_hank_strains.py::test_a_strain_named_in_any_wording_is_looked_up[Purple Punch?-en]',
    'tests/test_hank_strains.py::test_a_strain_named_in_any_wording_is_looked_up[do we have information about purple punch?-en]',
    'tests/test_hank_strains.py::test_a_strain_named_in_any_wording_is_looked_up[hey hank, anything on purple punch?-en]',
    'tests/test_hank_strains.py::test_a_strain_named_in_any_wording_is_looked_up[what is purple punch-en]',
    'tests/test_hank_strains.py::test_a_strains_name_is_never_asked_as_a_typo',
    'tests/test_hank_strains.py::test_a_time_wish_skips_the_experience_question',
    'tests/test_hank_strains.py::test_a_variety_only_in_the_encyclopedia_is_answered_from_it',
    'tests/test_hank_strains.py::test_advice_questions_route_to_the_strain_advice[Can you recommend a strain?-en]',
    'tests/test_hank_strains.py::test_advice_questions_route_to_the_strain_advice[Ich will eine kurze Bluete-de]',
    'tests/test_hank_strains.py::test_advice_questions_route_to_the_strain_advice[Kannst du mir eine Sorte empfehlen?-de]',
    'tests/test_hank_strains.py::test_advice_questions_route_to_the_strain_advice[Was ist die beste Autoflower f\\xfcr die Box?-de]',
    'tests/test_hank_strains.py::test_advice_questions_route_to_the_strain_advice[Welche Autoflower f\\xfcr Anf\\xe4nger?-de]',
    'tests/test_hank_strains.py::test_advice_questions_route_to_the_strain_advice[Welche Autoflower ist am einfachsten?-de]',
    'tests/test_hank_strains.py::test_advice_questions_route_to_the_strain_advice[Welche Sorte passt in meine Box?-de]',
    'tests/test_hank_strains.py::test_advice_questions_route_to_the_strain_advice[Welche Sorten passen in die VGrow?-de]',
    'tests/test_hank_strains.py::test_advice_questions_route_to_the_strain_advice[Which autoflower is easiest for a beginner?-en]',
    'tests/test_hank_strains.py::test_advice_questions_route_to_the_strain_advice[what strain fits my grow box?-en]',
    'tests/test_hank_strains.py::test_an_advice_question_with_a_buy_word_gets_the_advice[Welche Autoflower soll ich f\\xfcr meine Box bestellen?-de]',
    'tests/test_hank_strains.py::test_an_advice_question_with_a_buy_word_gets_the_advice[Welche Sorte soll ich kaufen?-de]',
    'tests/test_hank_strains.py::test_an_advice_question_with_a_buy_word_gets_the_advice[Which strain should I buy?-en]',
    'tests/test_hank_strains.py::test_an_unknown_name_while_growing_gets_an_honest_no_not_wrong_guy',
    'tests/test_hank_strains.py::test_an_unknown_strain_gets_an_honest_no_not_wrong_guy',
    'tests/test_hank_strains.py::test_each_suggestion_names_height_time_difficulty_why_and_its_source',
    'tests/test_hank_strains.py::test_english_runs_the_same_way',
    'tests/test_hank_strains.py::test_never_more_than_two_questions_and_never_one_twice',
    'tests/test_hank_strains.py::test_northern_lights_auto_is_not_in_the_lexicon_and_hank_says_what_is',
    'tests/test_hank_strains.py::test_one_strain_is_answered_from_its_entry',
    'tests/test_hank_strains.py::test_suggestions_fit_the_box_first_never_too_tall_and_three_names',
    'tests/test_hank_strains.py::test_the_answers_stay_for_the_chat_and_a_short_reply_changes_one_wish',
    'tests/test_hank_strains.py::test_the_box_question_asks_the_type_then_the_experience_then_answers',
    'tests/test_hank_strains.py::test_the_lexicon_answer_names_the_encyclopedia_too',
    'tests/test_hank_strains.py::test_the_owners_card_is_answered_from_it_and_the_wiki_button_keeps_its_way',
    'tests/test_hank_strains.py::test_the_package_switched_off_switches_the_lexicon_off_for_hank',
    'tests/test_hank_strains.py::test_the_type_question_names_each_breeders_share_and_both_classifications',
    'tests/test_plant_run.py::test_a_lexicon_row_carries_type_bloom_and_fit',
    'tests/test_plant_run.py::test_a_record_keeps_the_pick_and_changes_no_run',
    'tests/test_plant_run.py::test_an_update_without_a_pick_leaves_it_alone_and_with_one_changes_it',
    'tests/test_plant_run.py::test_choosing_a_plant_moves_the_whole_cockpit_to_it',
    'tests/test_plant_run.py::test_the_encyclopedia_does_not_claim_a_flowering_type',
    'tests/test_plant_run.py::test_the_list_holds_both_databases_and_never_thc',
    'tests/test_plant_run.py::test_the_routes_offer_the_list_and_take_the_choice',
    'tests/test_plant_run.py::test_the_wiki_marks_the_chosen_strain_as_the_current_one',
    'tests/test_plant_run.py::test_the_workspace_follows_the_choice',
    'tests/test_plant_run.py::test_warnings_name_the_height_and_the_open_type',
    'tests/test_strain_lexicon.py::test_a_public_build_carries_no_lexicon',
    'tests/test_strain_lexicon.py::test_an_entry_renders_its_box_block_and_each_source_under_its_fact',
    'tests/test_strain_lexicon.py::test_every_fact_carries_its_own_source_and_an_english_twin',
    'tests/test_strain_lexicon.py::test_grow_numbers_match_the_texts',
    'tests/test_strain_lexicon.py::test_no_cannabinoid_effect_taste_or_price_anywhere_in_the_data',
    'tests/test_strain_lexicon.py::test_no_name_names_a_drug_an_effect_or_a_health_word',
    'tests/test_strain_lexicon.py::test_one_entry_cites_only_its_own_sources_without_a_shop_link',
    'tests/test_strain_lexicon.py::test_the_file_is_a_private_draft_collection_of_the_cannabis_package',
    'tests/test_strain_lexicon.py::test_the_index_shows_the_area_not_one_tile_per_strain',
    'tests/test_strain_lexicon.py::test_the_lexicon_counts',
    'tests/test_strain_lexicon.py::test_the_owners_card_stays_his_and_the_lexicon_points_to_it',
    'tests/test_strain_lexicon.py::test_the_preview_build_leaves_private_data_out',
    'tests/test_strain_lexicon.py::test_the_search_finds_lexicon_entries_under_their_area',
    'tests/test_strain_lexicon.py::test_the_sorten_list_pages_filters_and_links_each_entry',
    'tests/test_strain_lexicon.py::test_the_strain_list_comes_sorted_by_fit_with_numbers_and_no_shop_text',
    'tests/test_wiki.py::test_index_lists_every_folder_of_kartei_in_the_fixed_category_order',
    'tests/test_wiki_encyclopedia.py::test_every_origin_fact_is_a_quote_of_its_profile',
    'tests/test_wiki_encyclopedia.py::test_every_seed_figure_is_quoted_and_the_gaps_are_named',
    'tests/test_wiki_encyclopedia.py::test_namesake_products_do_not_establish_original_clone_identity',
    'tests/test_wiki_encyclopedia.py::test_popularity_counts_leafly_reviews_on_a_tenfold_scale',
    'tests/test_wiki_encyclopedia.py::test_reference_catalogue_is_complete_sourced_and_distinct_from_breeder_products',
    'tests/test_wiki_encyclopedia.py::test_the_fact_sheet_answers_is_it_for_me_and_where_it_comes_from',
    'tests/test_wiki_tools.py::test_all_tool_routes_need_cockpit_auth_and_mutations_have_validation',
    'tests/test_wiki_tools.py::test_bookmarks_collections_and_compare_survive_connections_and_concurrent_writes',
    'tests/test_wiki_tools.py::test_corrections_require_review_and_keep_old_claims_with_sources',
    'tests/test_wiki_tools.py::test_hidden_package_never_leaks_reading_state_history_or_atlas',
    'tests/test_wiki_tools.py::test_invalid_facts_cannot_be_reviewed[-1]',
    'tests/test_wiki_tools.py::test_invalid_facts_cannot_be_reviewed[101]',
    'tests/test_wiki_tools.py::test_invalid_facts_cannot_be_reviewed[22]',
    'tests/test_wiki_tools.py::test_invalid_facts_cannot_be_reviewed[True]',
    'tests/test_wiki_tools.py::test_invalid_facts_cannot_be_reviewed[inf]',
    'tests/test_wiki_tools.py::test_invalid_facts_cannot_be_reviewed[nan]',
    'tests/test_wiki_tools.py::test_invalid_sources_cannot_enter_correction_queue[http://example.org]',
    'tests/test_wiki_tools.py::test_invalid_sources_cannot_enter_correction_queue[https://example.org/a b]',
    'tests/test_wiki_tools.py::test_invalid_sources_cannot_enter_correction_queue[https://example.org:8443]',
    'tests/test_wiki_tools.py::test_invalid_sources_cannot_enter_correction_queue[https://user:pass@example.org]',
    'tests/test_wiki_tools.py::test_invalid_sources_cannot_enter_correction_queue[javascript:alert(1)]',
    'tests/test_wiki_tools.py::test_manual_link_check_uses_committed_urls_and_never_follows_redirects',
    'tests/test_wiki_tools.py::test_real_source_disagreement_gallery_and_atlas_are_attributed',
    'tests/test_wiki_tools.py::test_revision_detects_reverts_without_inventing_earlier_history',
}


def pytest_collection_modifyitems(config, items):  # type: ignore[no-untyped-def]
    skip = pytest.mark.skip(reason="needs the private strain data, not part of the public copy")
    for item in items:
        if item.nodeid in _NEEDS_PRIVATE_DATA:
            item.add_marker(skip)
