from translator.core.workspace import merge_memory_delta, memory_for_chapter


def test_temporal_updates_preserve_history_and_do_not_leak_into_earlier_chapters():
    memory = {}
    # Deliberately non-lexical chapter order.
    for chapter, value in [('z', '王都'), ('a', '边城')]:
        memory, summary = merge_memory_delta(memory, {'add': [
            {'key': '甲：位置', 'value': value, 'category': 'state', 'confidence': .99}
        ]}, chapter)
        assert summary['conflicted'] == 0
    order = ['z', 'a', 'b']
    assert memory_for_chapter(memory, 'z', order)['entries'] == []
    prior = memory_for_chapter(memory, 'a', order)['entries'][0]
    assert prior['value'] == '王都'
    assert 'state_versions' not in prior
    assert memory_for_chapter(memory, 'b', order)['entries'][0]['value'] == '边城'
    memory, _ = merge_memory_delta(memory, {'update': [
        {'key': '甲：位置', 'value': '旧都', 'category': 'state', 'confidence': .99}
    ]}, 'z')
    assert len(memory['entries'][0]['state_versions']) == 2
    assert memory_for_chapter(memory, 'b', order)['entries'][0]['value'] == '边城'
    assert memory_for_chapter(memory, 'a', order)['entries'][0]['value'] == '旧都'


def test_timeless_fact_changes_remain_conflicts():
    memory, _ = merge_memory_delta({}, {'add': [
        {'key': '世界规则', 'value': '有魔法', 'category': 'fact', 'confidence': .99}
    ]}, 'c1')
    memory, summary = merge_memory_delta(memory, {'update': [
        {'key': '世界规则', 'value': '没有魔法', 'category': 'fact', 'confidence': .99}
    ]}, 'c2')
    assert summary['conflicted'] == 1
    assert memory['entries'][0]['value'] == '有魔法'


def test_extracted_state_survives_finalization_and_disk_roundtrip(tmp_path):
    from translator.core.workspace import BookWorkspace, read_json
    from translator.review.knowledge_extractor import apply_knowledge_delta, normalize_window_output

    workspace = BookWorkspace.at(tmp_path, 'book')
    workspace.initialize()
    for chapter, value in [('c1', '王都'), ('c2', '边城')]:
        items = [{'id': f'{chapter}-p1', 'source': f'甲在{value}', 'translated': f'甲在{value}'}]
        normalized = normalize_window_output({'knowledge_candidates': [{
            'candidate_id': 'state', 'kind': 'memory', 'category': 'state',
            'key': '甲：位置', 'value': value, 'confidence': .99,
            'source_paragraph_ids': [items[0]['id']], 'evidence_ids': [items[0]['id']],
        }]}, window_id=f'{chapter}:window:0001', items=items)
        candidates = normalized['knowledge_candidates']
        assert len(candidates) == 1
        apply_knowledge_delta(workspace, chapter, candidates,
                              [{'candidate_id': candidates[0]['candidate_id'], 'action': 'active'}])
    memory = read_json(workspace.book_memory_path)
    assert len(memory['entries'][0]['state_versions']) == 2
    assert memory_for_chapter(memory, 'c3', ['c1', 'c2', 'c3'])['entries'][0]['value'] == '边城'
