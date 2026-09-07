import json
from pathlib import Path
from unittest.mock import patch

from translator.core.execution_context import BookExecutionContext
from translator.core.workspace import BookWorkspace, write_json
from translator.providers.translator import ProviderTranslator
from translator.review.reviewer import run_chapter_review
from translator.web.models import RetranslateParagraphRequest
from translator.web.routes import tasks


def test_context_uses_config_relative_policy_and_isolated_snapshots(tmp_path):
    policy = tmp_path / 'policies' / 'custom.md'
    policy.parent.mkdir()
    policy.write_text('CUSTOM BOOK POLICY')
    config = {'paths': {'translation_policy': 'policies/custom.md'}, 'providers': {'fixture': {'type': 'openai'}}}
    ws = BookWorkspace.at(tmp_path / 'output', 'book')
    context = BookExecutionContext.create(book_id='book', manifest=tmp_path / 'manifest.json', workspace=ws,
        novel_root=tmp_path / 'vendor', config=config, config_path=tmp_path / 'config.toml')
    config['providers']['fixture']['type'] = 'changed'
    first = context.translator()
    second = context.translator()
    first.config['providers']['fixture']['type'] = 'mutated'
    assert second.config['providers']['fixture']['type'] == 'openai'
    assert second._system_prompt('fixture') == 'CUSTOM BOOK POLICY'
    assert second.glossary_path == ws.glossary_path


def test_dual_review_threads_use_supplied_config_without_global_reload(tmp_path):
    source = tmp_path / 'input.json'
    output = tmp_path / 'output.json'
    write_json(source, {'items': [{'id': 'p1', 'source': '原文', 'translated': '译文'}]})
    cfg = {'roles': {'reviewer': 'primary', 'secondary_reviewer': 'secondary', 'dual_review': True},
           'pipeline': {'review_context': {'enabled': False}, 'review_backtrack_enabled': False}}
    seen = []
    class Provider:
        def review(self, kind, payload, schema, **kwargs):
            return {'checked_ids': ['p1'], 'fixes': []}
    def factory(name, config):
        seen.append((name, config))
        return Provider()
    with patch('translator.review.reviewer.load_config', side_effect=AssertionError('global config read')), \
         patch('translator.review.reviewer.get_provider', side_effect=factory):
        run_chapter_review(source, output, config=cfg)
    assert {name for name, _ in seen} == {'primary', 'secondary'}
    assert all(config == cfg for _, config in seen)
    assert json.loads(output.read_text())['checked_ids'] == ['p1']


def test_retranslation_receives_workspace_glossary_and_config(tmp_path, monkeypatch):
    path = tmp_path / 'manifest.json'
    write_json(path, {'title': 'My Book', 'chapters': [{'id': 'c1', 'paragraphs': [{'id': 'p1', 'source': 'source'}]}]})
    cfg = {'paths': {'output_root': str(tmp_path / 'output'), 'translation_policy': 'policy.md'}}
    monkeypatch.setattr(tasks, 'manifest_path', lambda _: path)
    monkeypatch.setattr(tasks, 'load_config', lambda: cfg)
    with patch.object(tasks, 'ProviderTranslator') as factory:
        factory.return_value.return_value = {'status': 'ok'}
        tasks.retranslate_paragraph(RetranslateParagraphRequest(book_id='book', chapter_id='c1', paragraph_id='p1', provider='fixture'))
    options = factory.call_args.kwargs
    assert options['glossary_path'] == BookWorkspace.at(tmp_path / 'output', 'My Book').glossary_path
    assert options['config'] == cfg
    assert options['manifest'] == path


def test_job_managers_accept_independent_config_without_global_reload(tmp_path):
    from translator.core.job_manager import JobManager
    one = {'paths': {'output_root': 'one'}, 'queue': {'concurrency': 1}}
    two = {'paths': {'output_root': 'two'}, 'queue': {'concurrency': 2}}
    with patch('translator.core.job_manager.load_config', side_effect=AssertionError('global read')):
        first = JobManager(config=one, config_path=tmp_path / 'config.toml')
        second = JobManager(config=two, config_path=tmp_path / 'config.toml')
        one['queue']['concurrency'] = 4
        assert first.execution_config()['queue']['concurrency'] == 1
        assert second.execution_config()['queue']['concurrency'] == 2
    assert first.output_root == tmp_path / 'one'
    assert second.output_root == tmp_path / 'two'
