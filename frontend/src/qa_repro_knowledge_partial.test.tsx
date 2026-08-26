import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { api } from './lib/api';
import { KnowledgeView } from './views/KnowledgeView';

describe('QA regression: Knowledge partial loading', () => {
  it('keeps available glossary data visible when memory fails', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'getGlossary').mockResolvedValue({
      book_id: 'book', terms: [{
        source: '名前', target: '名字', category: 'character', confidence: 1,
        note: '', first_seen_chunk: null, last_seen_chunk: null, occurrences: 1, sample_ids: [],
      }], conflicts: [],
    });
    vi.spyOn(api, 'getMemory').mockRejectedValue(new Error('memory down'));
    vi.spyOn(api, 'getReports').mockResolvedValue([]);

    render(<KnowledgeView book={{ id: 'book', name: 'Book' } as any} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('memory down');
    await user.click(screen.getByRole('button', { name: /术语表/ }));

    expect(screen.getByText('名字')).toBeInTheDocument();
  });
});
