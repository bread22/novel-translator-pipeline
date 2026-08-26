import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { api } from './lib/api';
import { ReaderView } from './views/ReaderView';

describe('QA regression: Reader count after clearing a translation', () => {
  it('decrements translated counts when an edited paragraph is cleared', async () => {
    vi.spyOn(api, 'getChapters').mockResolvedValue([{
      id: 'c1', index: 1, title: 'Chapter 1', total_paragraphs: 1, translated_paragraphs: 1,
      status: 'translated', auto_fixed_count: 0,
    }] as any);
    vi.spyOn(api, 'getChapterDetail').mockResolvedValue({
      id: 'c1', index: 1, title: 'Chapter 1', total_paragraphs: 1, translated_paragraphs: 1,
      status: 'completed', chapter_summary: '', auto_fixed_count: 0,
      paragraphs: [{ id: 'p1', index: 0, chapter_id: 'c1', source: '源文', translated: '旧译', status: 'translated' }],
    } as any);
    vi.spyOn(api, 'getChapterReview').mockResolvedValue(null);
    vi.spyOn(api, 'updateParagraph').mockResolvedValue({ status: 'ok' });

    render(<ReaderView book={{ id: 'book-1', name: 'Book 1' } as any} />);
    await screen.findByText('旧译');
    expect(screen.getByText(/已翻译 1 段/)).toBeInTheDocument();

    fireEvent.click(screen.getByText('编辑'));
    fireEvent.change(screen.getByDisplayValue('旧译'), { target: { value: '' } });
    fireEvent.click(screen.getByText('保存修改'));

    await waitFor(() => expect(api.updateParagraph).toHaveBeenCalledWith('book-1', 'p1', ''));
    expect(screen.getByText(/已翻译 0 段/)).toBeInTheDocument();
  });
});
