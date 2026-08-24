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
});
