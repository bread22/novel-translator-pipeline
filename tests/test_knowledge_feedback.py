import pytest

from translator.core.workspace import BookWorkspace, read_json, write_json
from translator.pipeline.chapter_pipeline import IterativePipeline


def test_provisional_context_isolated_and_cached_until_accepted_text_changes(tmp_path):
    workspace = BookWorkspace.at(tmp_path, 'book')
    workspace.initialize()
    manifest = tmp_path / 'manifest.json'
    paragraph = {'id': 'p1', 'source': '他来了', 'translated': '他来了'}
    write_json(manifest, {'chapters': [{'id': 'c1', 'paragraphs': [paragraph]}]})
    calls = []

    def extract(kind, payload):
        calls.append(payload)
        return {'rolling_context_delta': {'active_entities': ['甲']}}

    pipeline = IterativePipeline(book='book', workspace=workspace, manifest=manifest,
                                 knowledge_extractor=extract, apply=True, review_apply_mode='hard_fix')
    window = {'items': [paragraph], 'context_before': [], 'context_after': []}
    result = pipeline._extract_window_knowledge('c1', window, {}, 1, 1, provisional=True)
    assert result['rolling_context_delta']['active_entities'] == ['甲']
    assert result['provisional'] is True
    assert not pipeline._knowledge_windows
    assert not pipeline._knowledge_candidates
    pipeline._extract_window_knowledge('c1', window, {}, 1, 1)
    assert len(calls) == 1  # unchanged final inputs reuse provisional extraction
    assert len(pipeline._knowledge_windows['c1']) == 1
    pipeline._knowledge_windows['c1'] = []
    data = read_json(manifest)
    data['chapters'][0]['paragraphs'][0]['translated'] = '他终于来了'
    write_json(manifest, data)
    pipeline._extract_window_knowledge('c1', window, {}, 1, 1)
    assert len(calls) == 2
    assert calls[-1]['items'][0]['translated'] == '他终于来了'


def test_hard_fix_review_forwards_temporary_context_to_next_window(monkeypatch, tmp_path):
    from translator.review import reviewer

    workspace = BookWorkspace.at(tmp_path, 'book')
    workspace.initialize()
    manifest = tmp_path / 'manifest.json'
    write_json(manifest, {'chapters': [{'id': 'c1', 'paragraphs': [
        {'id': f'p{i}', 'source': '甲来了', 'translated': '甲来了'} for i in range(2)
    ]}]})
    seen = []

    def execute(payload, items, *args, **kwargs):
        seen.append(payload)
        return {'checked_ids': [item['id'] for item in items], 'fixes': []}

    monkeypatch.setattr(reviewer, '_execute_segment_with_adaptive_split', execute)

    def run(source, output, **kwargs):
        reviewer.run_chapter_review(source, output, chunk_size=1, backtrack_enabled=False,
                                    config={'roles': {'reviewer': 'fake'}}, **kwargs)

    run._uses_window_knowledge = True

    def extract(kind, payload):
        return {'rolling_context_delta': {'active_entities': ['甲']}} if kind == 'window' else {}

    pipeline = IterativePipeline(book='book', workspace=workspace, manifest=manifest,
                                 chapter_reviewer=run, knowledge_extractor=extract,
                                 apply=True, review_apply_mode='hard_fix')
    pipeline._review_chapter('c1')
    assert len(seen) == 2
    assert seen[1]['current_chapter_review_context']['active_entities'] == ['甲']
    assert len(pipeline._knowledge_windows['c1']) == 2


@pytest.fixture(autouse=True)
def isolated_execution_config(monkeypatch):
    # These regressions must run from a clean checkout without local config.toml.
    monkeypatch.setattr('translator.pipeline.chapter_pipeline.load_config', lambda: {
        'roles': {'primary_translator': 'fake', 'reviewer': 'fake'},
        'pipeline': {}, 'paths': {},
    })
