import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { KnowledgeView } from './KnowledgeView';

describe('Knowledge loading states', () => {
  it('shows a network failure separately from an empty knowledge base', async () => {
    vi.spyOn(api, 'getGlossary').mockRejectedValue(new Error('network down'));
    vi.spyOn(api, 'getMemory').mockResolvedValue({ book_id: 'book', characters: [], world_settings: [] });
    vi.spyOn(api, 'getReports').mockResolvedValue([]);
    render(<KnowledgeView book={{ id: 'book', name: 'Book' } as any} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('network down');
  });

  it('uses the incremental response without dropping glossary provenance', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'getGlossary').mockResolvedValue({ book_id: 'book', terms: [], conflicts: [] });
    vi.spyOn(api, 'getMemory').mockResolvedValue({ book_id: 'book', characters: [], world_settings: [] });
    vi.spyOn(api, 'getReports').mockResolvedValue([]);
    const update = vi.spyOn(api, 'updateGlossary').mockResolvedValue({
      book_id: 'book', conflicts: [], terms: [{
        source: '名前', target: '名字', category: 'character', confidence: 1,
        note: '', first_seen_chunk: 'c1', last_seen_chunk: 'c4', occurrences: 9, sample_ids: ['p1'],
      }],
    });
    render(<KnowledgeView book={{ id: 'book', name: 'Book' } as any} />);
    await user.click(await screen.findByRole('button', { name: /术语表/ }));
    await user.click(screen.getByRole('button', { name: '添加自定义术语' }));
    await user.type(screen.getByLabelText(/日文原文/), '名前');
    await user.type(screen.getByLabelText(/中文统一译名/), '名字');
    await user.click(screen.getByRole('button', { name: '保存术语' }));
    expect(update).toHaveBeenCalledWith('book', [expect.objectContaining({ source: '名前', target: '名字' })]);
    expect(await screen.findByText('名字')).toBeInTheDocument();
    await expect(update.mock.results[0].value).resolves.toEqual(expect.objectContaining({
      terms: [expect.objectContaining({ occurrences: 9, sample_ids: ['p1'], first_seen_chunk: 'c1' })],
    }));
  });

  it('renders every review issue with its reason and written replacement', async () => {
    vi.spyOn(api, 'getGlossary').mockResolvedValue({ book_id: 'book', terms: [], conflicts: [] });
    vi.spyOn(api, 'getMemory').mockResolvedValue({ book_id: 'book', characters: [], world_settings: [] });
    vi.spyOn(api, 'getReports').mockResolvedValue([{
      chapter_id: 'c0002', reviewed_at: '2026-08-24T05:08:37Z', checked_paragraphs: 11,
      reported_issues: 3, applied_fixes: 2,
      fixes: [{
        id: 'c0002-p00005', category: 'terminology', severity: 'major', confidence: 0.95,
        reason: '原译改变了原文语义强度。', replacement: '第四章 章子 惨烈的侵入',
        auto_apply: true, consensus: false, reporters: ['primary'], applied: true,
      }, {
        id: 'c0002-p00010', category: 'terminology', severity: 'major', confidence: 0.95,
        reason: '原译用词存在歧义。', replacement: '第九章 芙由子 后庭的锐痛',
        auto_apply: true, consensus: true, reporters: ['primary', 'secondary'], applied: true,
      }, {
        id: 'c0002-p00011', category: 'style', severity: 'minor', confidence: 0.8,
        reason: '仅为风格偏好。', replacement: '建议风格译文', auto_apply: false,
        consensus: false, reporters: ['secondary'], applied: false,
        not_applied_reason: '问题分类 style 不在客观缺陷自动修正白名单',
      }],
      glossary_delta: { add: [], update: [], conflicts: [] },
      memory_delta: { add: [], update: [], conflicts: [] },
      chapter_state: { summary: '本章为目录页。' },
      dual_review: { enabled: true, primary_fixes_count: 2, secondary_fixes_count: 1, consensus_fixes_count: 1 },
    }]);

    render(<KnowledgeView book={{ id: 'book', name: 'Book' } as any} />);

    expect(await screen.findByText('客观缺陷与修正记录 (3 处)')).toBeInTheDocument();
    expect(screen.getByText('#c0002-p00005')).toBeInTheDocument();
    expect(screen.getByText(/原译改变了原文语义强度/)).toBeInTheDocument();
    expect(screen.getByText(/第四章 章子 惨烈的侵入/)).toBeInTheDocument();
    expect(screen.getByText('双审共识')).toBeInTheDocument();
    expect(screen.getAllByText('已自动修正')).toHaveLength(2);
    expect(screen.getByText('未修正')).toBeInTheDocument();
    expect(screen.getByText(/问题分类 style 不在客观缺陷自动修正白名单/)).toBeInTheDocument();
    expect(screen.getByText(/建议风格译文/)).toBeInTheDocument();
  });
});
