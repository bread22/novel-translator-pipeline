import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { ReaderView } from './ReaderView';

const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
};

const book = (id: string) => ({ id, name: id } as any);
const chapter = (id: string, title: string, role = 'chapter') => ({
  id, index: 1, title, role, total_paragraphs: 0, translated_paragraphs: 0, status: 'pending', auto_fixed_count: 0,
} as any);

describe('Reader request sequencing', () => {
  it('does not let a late response from the previous book overwrite the current book', async () => {
    const first = deferred<any[]>();
    const second = deferred<any[]>();
    vi.spyOn(api, 'getChapters').mockImplementation((id) => id === 'A' ? first.promise : second.promise);
    vi.spyOn(api, 'getChapterDetail').mockImplementation(async (_bookId, chapterId) => ({
      ...chapter(chapterId, 'Book B chapter'), paragraphs: [], chapter_summary: '',
    }));
    vi.spyOn(api, 'getChapterReview').mockResolvedValue(null);

    const view = render(<ReaderView book={book('A')} />);
    view.rerender(<ReaderView book={book('B')} />);
    second.resolve([chapter('b-1', 'Book B chapter')]);
    await screen.findAllByText('Book B chapter');
    first.resolve([chapter('a-1', 'Stale A chapter')]);
    await waitFor(() => expect(screen.queryByText('Stale A chapter')).not.toBeInTheDocument());
  });

  it('shows cover and TOC units as translatable reader items', async () => {
    vi.spyOn(api, 'getChapters').mockResolvedValue([chapter('cover-1', '封面', 'cover')]);
    vi.spyOn(api, 'getChapterDetail').mockResolvedValue({
      ...chapter('cover-1', '封面', 'cover'), paragraphs: [], chapter_summary: '',
    });
    vi.spyOn(api, 'getChapterReview').mockResolvedValue(null);

    render(<ReaderView book={book('fixture')} />);

    expect(await screen.findByText('COVER 1')).toBeInTheDocument();
    expect(screen.getByText(/目录 \/ 内容索引/)).toBeInTheDocument();
  });
});
