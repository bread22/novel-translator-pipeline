import { render, screen } from '@testing-library/react';
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
});
