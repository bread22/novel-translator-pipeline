import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { api } from './lib/api';
import { ReaderView } from './views/ReaderView';

describe('QA regression: Reader review partial failure', () => {
  it('shows a review error when chapter detail remains available', async () => {
    vi.spyOn(api, 'getChapters').mockResolvedValue([{
      id: 'c1', index: 1, title: 'Chapter 1', total_paragraphs: 1, translated_paragraphs: 1,
      status: 'translated', auto_fixed_count: 0,
    }] as any);
    vi.spyOn(api, 'getChapterDetail').mockResolvedValue({
      id: 'c1', index: 1, title: 'Chapter 1', total_paragraphs: 1, translated_paragraphs: 1,
      status: 'completed', chapter_summary: '', auto_fixed_count: 0,
      paragraphs: [{ id: 'p1', index: 0, chapter_id: 'c1', source: '源文', translated: '译文', status: 'translated' }],
    } as any);
    vi.spyOn(api, 'getChapterReview').mockRejectedValue(new Error('review down'));

    render(<ReaderView book={{ id: 'book-1', name: 'Book 1' } as any} />);

    expect(await screen.findByText('译文')).toBeInTheDocument();
    expect(await screen.findByRole('alert')).toHaveTextContent('review down');
  });
});
