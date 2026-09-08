from copy import deepcopy

from translator.review.knowledge_extractor import ground_candidates_with_text, _evidence_stats


def candidate(source='美樹'):
    return {'kind': 'glossary', 'candidate_id': 'name', 'source': source, 'target': '美树',
            'category': 'person', 'confidence': 0.0, 'evidence_ids': []}


def test_structured_evidence_excludes_metadata_and_translated_only_mentions():
    value = candidate()
    original = deepcopy(value)
    corpus = {
        'paragraph-a': {'source': '美樹登场', 'chapter_id': 'chapter-actual'},
        'paragraph-b': {'source': '封面美樹', 'source_scope': 'cover', 'chapter_id': 'chapter-actual'},
        'paragraph-c': {'source': '别人登场', 'translated': '美樹登场', 'chapter_id': 'chapter-actual'},
    }
    result = ground_candidates_with_text([value], corpus, 'wrong-default')[0]
    assert result['evidence_ids'] == ['paragraph-a']
    assert result['evidence_provenance'][0]['chapter_id'] == 'chapter-actual'
    assert result['evidence_provenance'][0]['confidence'] == 0.0
    assert _evidence_stats(result) == (1, 1)
    assert value == original


def test_latin_alias_does_not_match_inside_another_name():
    result = ground_candidates_with_text([candidate('Ann')], {
        'c1-p1': 'Anna and JoAnn are here', 'c1-p2': 'Ann is here',
    }, 'c1')[0]
    assert result['evidence_ids'] == ['c1-p2']


def test_grounding_is_bounded_idempotent_and_revalidates_existing_evidence():
    value = {**candidate(), 'evidence_ids': ['unknown'], 'source_paragraph_ids': ['unknown'],
             'evidence_provenance': [{'paragraph_id': 'unknown', 'chapter_id': 'fabricated'}]}
    corpus = {f'c1-p{i}': '美樹来了' for i in range(50)}
    first = ground_candidates_with_text([value], corpus, 'c1', max_grounded_evidence=3)
    second = ground_candidates_with_text(first, corpus, 'c1', max_grounded_evidence=3)
    assert first == second
    assert len(first[0]['evidence_ids']) == 3
    assert 'unknown' not in first[0]['evidence_ids']
    assert {e['chapter_id'] for e in first[0]['evidence_provenance']} == {'c1'}


def test_reporters_do_not_multiply_independent_paragraphs():
    value = candidate()
    value['evidence_provenance'] = [
        {'chapter_id': 'c1', 'paragraph_id': 'p1', 'reporter': reporter}
        for reporter in ['primary', 'secondary', 'deterministic_grounding']
    ]
    assert _evidence_stats(value) == (1, 1)


def test_pipeline_promotes_existing_candidate_without_reextraction_and_ignores_future(tmp_path):
    from translator.core.execution_context import BookExecutionContext
    from translator.core.workspace import BookWorkspace, read_json, write_json
    from translator.glossary.lifecycle import merge_term_candidates
    from translator.glossary.projection import select_relevant_terms
    from translator.glossary.service import persist_glossary
    from translator.pipeline.chapter_pipeline import IterativePipeline

    workspace = BookWorkspace.at(tmp_path, 'book')
    workspace.initialize()
    manifest = tmp_path / 'manifest.json'
    # Deliberately use paragraph IDs that cannot supply a chapter implicitly.
    chapters = [
        {'id': 'z-first', 'paragraphs': [{'id': 'first', 'source': '美樹来了', 'translated': '美树来了'}]},
        {'id': 'a-next', 'paragraphs': [{'id': 'second', 'source': '美樹笑了', 'translated': '美树笑了'}]},
    ]
    write_json(manifest, {'chapters': chapters})
    glossary, _ = merge_term_candidates({'schema_version': '3.0', 'terms': [], 'conflicts': [], 'revisions': []},
                                       [{**candidate(), 'confidence': .88, 'evidence_ids': ['first']}],
                                       chapter_id='z-first', reporter='knowledge_extractor',
                                       evidence_texts={'first': '美樹来了'})
    persist_glossary(workspace, glossary)
    calls = []

    def finalize(kind, payload):
        calls.append((kind, payload))
        return {'decisions': [{'candidate_id': item['candidate_id'], 'action': 'active'}
                              for item in payload['candidates']]}

    context = BookExecutionContext.create(book_id='book', manifest=manifest, workspace=workspace,
                                          novel_root=tmp_path, config_path=tmp_path / 'config.toml',
                                          config={'pipeline': {}, 'roles': {'primary_translator': 'fake', 'reviewer': 'fake'}, 'knowledge_extractor': {'enabled': True}})
    pipeline = IterativePipeline(book='book', workspace=workspace, manifest=manifest,
                                 execution_context=context, knowledge_extractor=finalize)
    first = pipeline._finalize_chapter_knowledge('z-first', chapters[0]['paragraphs'])
    assert first['model_candidates'] == 0
    assert not calls  # The second chapter must not manufacture early recurrence.
    assert read_json(workspace.glossary_path)['terms'][0]['status'] == 'candidate'
    second = pipeline._finalize_chapter_knowledge('a-next', chapters[1]['paragraphs'])
    assert second['model_candidates'] == 1
    assert len(calls) == 1
    glossary = read_json(workspace.glossary_path)
    term = glossary['terms'][0]
    assert term['status'] == 'active'
    assert term['confidence'] == .88
    assert {e['chapter_id'] for e in term['evidence']} == {'z-first', 'a-next'}
    assert read_json(workspace.novel_translator_terms_path)['terms'][0]['target'] == '美树'
    assert select_relevant_terms(glossary, items=[{'source': '美樹回来了'}])[0]['target'] == '美树'
