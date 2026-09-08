import pytest

from pathlib import Path

from translator.core.workspace import BookWorkspace, read_json, write_json
from translator.pipeline.chapter_pipeline import IterativePipeline
from translator.review import reviewer


def test_missing_targets_keep_original_context(monkeypatch, tmp_path):
    seen = []

    def execute(payload, items, *args, **kwargs):
        seen.append(payload)
        return {"checked_ids": [i["id"] for i in items], "fixes": []}

    monkeypatch.setattr(reviewer, "_execute_segment_with_adaptive_split", execute)
    source, output = tmp_path / 'input.json', tmp_path / 'output.json'
    write_json(source, {"items": [
        {"id": f"p{i}", "source": "原文", "translated": "译文"} for i in range(3)
    ], "review_target_ids": ["p1"]})
    reviewer.run_chapter_review(source, output, config={"roles": {"reviewer": "fake"}},
                                context_before=2, context_after=2)
    assert len(seen) == 1
    assert [i['id'] for i in seen[0]['items']] == ['p1']
    assert [i['id'] for i in seen[0]['context_before']] == ['p0']
    assert [i['id'] for i in seen[0]['context_after']] == ['p2']
    assert read_json(output)['checked_ids'] == ['p1']


def test_pipeline_accumulates_partial_coverage_and_preserves_polish(tmp_path: Path):
    workspace = BookWorkspace.at(tmp_path, 'book')
    workspace.initialize()
    manifest = tmp_path / 'manifest.json'
    write_json(manifest, {'chapters': [{'id': 'c1', 'paragraphs': [
        {'id': f'p{i}', 'source': '原文', 'translated': '译文'} for i in range(3)
    ]}]})
    calls = []
    polish = {'id': 'p0', 'category': 'mistranslation', 'severity': 'major',
              'confidence': .99, 'replacement': '更自然的译文', 'auto_apply': True}

    def review(source, output):
        payload = read_json(source)
        calls.append(payload.get('review_target_ids'))
        if len(calls) == 1:
            write_json(output, {'checked_ids': ['p0', 'p1'], 'fixes': [polish]})
        else:
            write_json(output, {'checked_ids': ['p2'], 'fixes': []})

    pipeline = IterativePipeline(book='book', workspace=workspace, manifest=manifest,
                                 chapter_reviewer=review, knowledge_extractor=lambda *a: {}, apply=False)
    pipeline._review_chapter('c1')
    result = read_json(workspace.reviews_dir / 'c1-output.json')
    assert calls == [None, ['p2']]
    assert set(result['checked_ids']) == {'p0', 'p1', 'p2'}
    assert result['fixes'][0]['replacement'] == polish['replacement']


@pytest.fixture(autouse=True)
def isolated_execution_config(monkeypatch):
    # These regressions must run from a clean checkout without local config.toml.
    monkeypatch.setattr('translator.pipeline.chapter_pipeline.load_config', lambda: {
        'roles': {'primary_translator': 'fake', 'reviewer': 'fake'},
        'pipeline': {}, 'paths': {},
    })
