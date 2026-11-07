"""Reference provenance, honest unknowns, package boundaries and safe images."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

import test_wiki as tw
from vgrow import wiki


def test_reference_catalogue_is_complete_sourced_and_distinct_from_breeder_products() -> None:
    status, data = tw.get('/api/wiki/varieties', lang='en')
    assert status == 200 and data['count'] == 157
    rows = data['varieties']
    assert len({r['key'] for r in rows}) == 157
    # Leafly's 100 and the rare, heirloom and classic ones added on the owner's request (01.11.2026)
    assert Counter(r['selection'] for r in rows) == {'leafly100': 100, 'rare': 57}
    assert {'laughing-buddha', 'malawi', 'thai', 'kali-mist', 'nevilles-haze'} <= {r['key'] for r in rows}
    assert [r['label'].casefold() for r in rows] == sorted(r['label'].casefold() for r in rows)
    assert all(r['reference']['thc_percent'] is None or 0 < r['reference']['thc_percent'] <= 100 for r in rows)
    assert all(r['reference']['thc_basis'] == 'source_median' for r in rows)
    assert all(r['reference']['indica_percent'] is None and r['reference']['sativa_percent'] is None for r in rows)
    assert sum(r['image']['external']['representative'] for r in rows) == 5
    assert all(r['image']['external']['src'].startswith('https://') for r in rows)
    landrace_sources = {'https://www.leafly.com/news/growing/is-cannabis-cultivar-same-as-strain',
                        'https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5296298/'}
    for row in rows:
        status, card = tw.get('/api/wiki/card/varieties/' + row['key'], lang='en')
        profiles = [s for s in card['sources'] if (s['url'] or '').startswith('https://www.leafly.com/strains/')
                    or s['id'] == 'L0']
        assert status == 200 and len(profiles) == (2 if row['selection'] == 'leafly100' else 1)
        seeds = [s for s in card['sources'] if s['id'].startswith('Z')]
        # a seed version's page is a shop or carries shop offers: its address and day, never a link
        assert all(s['url'] is None and s['host'] and s['retrieved'] for s in seeds)
        landrace = [s for s in card['sources'] if s['url'] in landrace_sources]
        assert len(profiles) + len(seeds) + len(landrace) == len(card['sources'])
        assert not {'effects', 'medical', 'growInfo', 'yield', 'watering'}.intersection(card['card'])
    assert tw.get('/api/wiki/strains')[1]['count'] == 205


def test_grow_scores_follow_the_sources_three_steps_and_never_fill_a_gap() -> None:
    assert [wiki.speed_score(d) for d in (43, 49, 56, 58, 63, 70, 85, 98)] == [10, 10, 9, 8, 7, 6, 3, 1]
    full = wiki.grow_view({'difficulty': 'difficult', 'height': 'tall', 'yield': 'medium', 'flowering_days': 85,
                           'source_id': 'L1'})
    assert full['scores'] == {'ease': 3, 'compact': 3, 'speed': 3, 'yield': 6}
    assert full['verdict'] == 'later' and full['cons'] == ['difficult', 'tall', 'slow'] and full['weeks'] == 12
    easy = wiki.grow_view({'difficulty': 'easy', 'height': 'average', 'yield': 'medium', 'flowering_days': 60,
                           'source_id': 'L1'})
    assert easy['verdict'] == 'good' and easy['pros'] == ['easy', 'fast'] and not easy['cons']
    catch = wiki.grow_view({'difficulty': 'moderate', 'height': 'tall', 'yield': 'high', 'flowering_days': 60,
                            'source_id': 'L1'})
    assert catch['verdict'] == 'catch' and catch['cons'] == ['tall']
    assert wiki.grow_view({'difficulty': 'moderate', 'height': 'average', 'source_id': 'L1'})['verdict'] == 'fine'
    partial = wiki.grow_view({'height': 'short', 'flowering_days': 50, 'source_id': 'L1'})
    # how hard stays open; a known plus or catch is said (owner, 01.11.2026: the information cannot be grey)
    assert partial['verdict'] == 'partial_fine' and partial['scores']['ease'] is None and partial['scores']['yield'] is None
    assert wiki.grow_view({'height': 'tall', 'source_id': 'L1'})['verdict'] == 'partial_catch'
    assert wiki.grow_view({'height': 'average', 'source_id': 'L1'})['verdict'] == 'partial'
    # nothing, nothing valid, or nothing with its source is no score at all, never a middle one
    for raw in (None, {}, {'difficulty': 'hard', 'height': 'huge', 'flowering_days': 9000, 'source_id': 'L1'},
                {'flowering_days': True, 'source_id': 'L1'}, {'difficulty': 'easy', 'height': 'short'}):
        assert wiki.grow_view(raw) is None


def test_a_seed_version_fills_only_what_leafly_leaves_open_and_keeps_its_source() -> None:
    seed = {'source_id': 'Z7', 'maker': "Barney's Farm", 'product': 'Wedding Cake', 'via': 'maker',
            'flowering_days': [55, 60], 'height_indoor_cm': [90, 100], 'yield_indoor_g_m2': [600, 700],
            'difficulty': 'difficult'}
    leafly = {'difficulty': 'easy', 'flowering_days': 63, 'source_id': 'L5'}
    g = wiki.grow_view(leafly, seed, box_cm=92)
    assert g['difficulty'] == 'easy' and g['flowering_days'] == 63  # Leafly first, never overwritten
    assert g['from']['difficulty']['id'] == 'L5' and g['from']['height'] == {
        'id': 'Z7', 'by': "Barney's Farm", 'via': 'maker', 'cm': [90, 100], 'upto': False}
    assert g['height'] == 'average' and g['yield'] == 'high' and g['source_ids'] == ['L5', 'Z7']
    # the maker's centimetres against the box: the upper figure fits, fits up to half again, or does not
    assert [wiki._height_step(cm, 92) for cm in ([60, 90], [90, 92], [90, 100], [120, 138], [120, 140], [200, 250])] == [
        'short', 'short', 'average', 'average', 'tall', 'tall']
    assert [wiki._yield_step(y) for y in ([300, 380], [300, 500], [450, 550], [600, 700])] == ['low', 'medium', 'medium', 'high']
    only = wiki.grow_view(None, {**seed, 'difficulty': None}, box_cm=92)
    assert only['flowering_days'] == 58 and only['verdict'] == 'partial_fine' and only['source_id'] == 'Z7'
    upto = wiki.grow_view(None, {**seed, 'yield_indoor_g_m2': [900, 900], 'upto': ['yield']}, box_cm=92)
    assert upto['from']['yield']['upto'] is True
    for bad in ({**seed, 'source_id': 'L5'}, {**seed, 'maker': ' '}, {'source_id': 'Z1', 'maker': 'x', 'product': 'y'},
                {**seed, 'flowering_days': [70, 50], 'height_indoor_cm': [0, 9999], 'yield_indoor_g_m2': None,
                 'difficulty': 'hard'}):
        assert wiki.seed_view(bad) is None


def test_popularity_counts_leafly_reviews_on_a_tenfold_scale() -> None:
    def view(n: int) -> dict:
        return wiki.popularity_view({'reviews': n, 'source_id': 'L1', 'retrieved': '2026-11-01'})

    assert [(view(n)['score'], view(n)['level']) for n in (13, 99, 100, 500, 1000, 2000, 5000, 14937)] == [
        (3, 'rare'), (5, 'rare'), (5, 'uncommon'), (7, 'known'), (8, 'known'), (8, 'very'), (9, 'top'), (10, 'top')]
    assert wiki.popularity_view({'reviews': 0, 'source_id': 'L1'}) is None
    assert wiki.popularity_view({'reviews': 50, 'source_id': 'Z1'}) is None
    _, data = tw.get('/api/wiki/varieties', lang='en')
    assert all(r['reference']['popularity'] for r in data['varieties'])
    assert sum(r['reference']['popularity']['level'] == 'rare' for r in data['varieties']) == 18


def test_every_seed_figure_is_quoted_and_the_gaps_are_named() -> None:
    data = json.loads((wiki.LIBRARY / 'varieties/popular.json').read_text(encoding='utf-8'))
    seeds = {e['key']: e['reference']['seed_facts'] for e in data['entries'] if 'seed_facts' in e['reference']}
    assert len(seeds) == 35
    for key, seed in seeds.items():
        figures = {f for f in ('flowering_days', 'height_indoor_cm', 'yield_indoor_g_m2', 'height_word', 'yield_word',
                               'difficulty') if f in seed}
        assert figures and figures == set(seed['quotes']), key
        assert all(isinstance(q, str) and q.strip() for q in seed['quotes'].values()), key
        source = data['sources'][seed['source_id']]
        assert source['url'].startswith('https://') and source['retrieved'], key
    assert seeds['nevilles-haze']['difficulty'] == 'difficult'
    assert seeds['nevilles-haze']['quotes']['difficulty'] == 'A must for the experienced grower'
    no_data = [e['key'] for e in data['entries'] if not e['reference'].get('grow') and 'seed_facts' not in e['reference']]
    assert len(no_data) == 19 and 'gelonade' not in no_data


def test_the_fact_sheet_answers_is_it_for_me_and_where_it_comes_from() -> None:
    card = wiki.card_view(wiki.find('varieties', 'laughing-buddha'), 'en', ['cannabis'])
    ref = card['card']['reference']
    assert ref['grow']['verdict'] == 'later' and ref['grow']['source_id'] == 'L2773'
    assert ref['origin']['breeder'] == "Barney's Farm"
    parents = ref['family']['parents']
    assert [(p['name'], p['key']) for p in parents] == [('Thai', 'thai'), ('Jamaican', 'jamaican')]
    assert all(p['origin']['landrace'] for p in parents)
    assert {'LR1', 'LR2'} <= {s['id'] for s in card['sources']}  # the landrace explanation cites its two sources
    assert ref['box_cm'] == 92
    photo = card['image']['external']
    assert photo['src'].endswith('?w=640&auto=format&fit=crop') and photo['large'].endswith('?w=1280&auto=format&fit=max')
    assert photo['large'].startswith('https://leafly-public.imgix.net/strains/reviews/photos/laughing-buddha')
    cherry = wiki.card_view(wiki.find('varieties', 'cherry-pie'), 'en', ['cannabis'])
    gdp = cherry['card']['reference']['family']['parents'][0]
    assert gdp['key'] == 'granddaddy-purple' and gdp['parents'], 'a parent with a page shows its own parents'
    assert not {'LR1', 'LR2'} & {s['id'] for s in cherry['sources']}


def test_every_origin_fact_is_a_quote_of_its_profile() -> None:
    data = json.loads((wiki.LIBRARY / 'varieties/popular.json').read_text(encoding='utf-8'))
    origins = [e['reference']['origin'] for e in data['entries'] if 'origin' in e['reference']]
    assert len(origins) == 81
    for origin in origins:
        assert origin['source_id'] in data['sources'] and origin['quotes']
        if origin['breeder']:
            assert origin['quotes']['breeder'] == origin['breeder']
        assert bool(origin['place']) == ('place' in origin['quotes'])
        assert bool(origin['era']) == ('era' in origin['quotes'])
    assert wiki.origin_view({'breeder': ' ', 'place': {'en': 'x'}, 'era': {'decade': '1990'}}) is None


def test_metadata_unknowns_never_turn_into_claims_and_invalid_numbers_are_rejected() -> None:
    for value in (None, 0, -2, 101, True, '30', float('nan'), float('inf')):
        result = wiki.variety_reference({'thc_percent': value, 'indica_percent': 100, 'clone_only': True})
        assert result['thc_percent'] is None
        assert result['indica_percent'] is None and result['clone_only'] == 'unverified'
    assert wiki.variety_reference({'thc_percent': 21.5})['thc_percent'] == 21.5


def test_package_and_public_boundaries_are_preserved(tmp_path: Path) -> None:
    assert wiki.varieties_view('en', []) is None
    assert wiki.varieties_view('en', ['cannabis'], public=True) is None
    assert not any(a['id'] == 'varieties' for a in wiki.areas_view(['cannabis'], public=True))
    assert wiki.varieties_view('en', ['cannabis'], library=tmp_path) is None
    off = wiki.index_view('en', [])
    assert any(c['id'] == 'health' for c in off['categories'])


@pytest.mark.parametrize('url', [
    'javascript:alert(1)', 'https://evil.example/photo.jpg',
    'https://leafly-public.imgix.net.evil.example/strains/reviews/photos/a.jpg',
    'https://leafly-public.imgix.net/strains/reviews/photos/a.jpg?redirect=https://evil.example',
    'http://leafly-public.imgix.net/strains/reviews/photos/a.jpg',
])
def test_reference_images_reject_untrusted_hosts_and_queries(url: str) -> None:
    out = wiki.image_view({'reference_url': url, 'source_url': 'https://www.leafly.com/strains/blue-dream'}, 'en', ['cannabis'], kind='variety')
    assert 'external' not in out


def test_health_expansion_is_cited_general_botany_and_has_no_duplicate_keys() -> None:
    data = json.loads((wiki.LIBRARY / 'health/conditions.json').read_text(encoding='utf-8'))
    assert len(data['entries']) == 22 and len({r['key'] for r in data['entries']}) == 22
    assert all(row['sources'] and row['facts'] and row['facts_en'] for row in data['entries'])
    index = wiki.index_view('en', ['cannabis'])
    counts = Counter(c.get('group') for cat in index['categories'] for c in cat['cards'])
    assert counts['krankheiten'] == 11 and counts['schaedlinge'] == 15 and counts['stress'] == 13
    assert counts['naehrstoffe'] == 10


def test_namesake_products_do_not_establish_original_clone_identity() -> None:
    data = wiki.card_view(wiki.find('varieties', 'apple-fritter'), 'en', ['cannabis'])
    reference = data['card']['reference']
    assert reference['clone_only'] == 'unverified'
    assert any(v['flowering'] == 'auto' for v in reference['named_variants'])
    assert all(v['key'] and v['brand'] for v in reference['named_variants'])
