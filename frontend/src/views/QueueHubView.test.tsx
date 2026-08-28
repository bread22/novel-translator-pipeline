import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { QueueHubView } from './QueueHubView';

describe('queue keyboard ordering', () => {
  it('moves a pending item with the keyboard control', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'moveQueueItem').mockResolvedValue({} as never);
    const refresh = vi.fn(async () => undefined);
    const item = (id: string, name: string) => ({
      id, book_id: id, book_name: name, source_type: 'epub', options: { book_id: id }, status: 'pending',
      order_index: 1, priority: 0, overall_progress: 0, current_chapter: '', current_chapter_index: 0,
      total_chapters: 0, message: 'waiting', enqueued_at: 'now', retry_count: 0, checkpoint: {},
    } as const);
    render(<QueueHubView
      books={[]}
      queueStatus={{ is_paused: true, concurrency: 1, total_items: 2, running_count: 0, pending_count: 2, completed_count: 0, failed_count: 0, items: [item('a', 'Alpha'), item('b', 'Beta')] }}
      onRefreshBooks={refresh}
      onRefreshQueue={refresh}
      onSelectBook={vi.fn()}
    />);
    const button = screen.getByRole('button', { name: '将 Beta 上移一位' });
    button.focus();
    await user.keyboard('{Enter}');
    expect(api.moveQueueItem).toHaveBeenCalledWith('b', 'up');
    expect(refresh).toHaveBeenCalled();
  });
});

describe('paused pipeline visibility', () => {
  it('keeps a paused worker in the queue and resumes it instead of duplicating it', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'resumePipeline').mockResolvedValue({} as never);
    const refresh = vi.fn(async () => undefined);
    const book = {
      id: 'book-paused', name: 'Paused Book', source_type: 'epub', total_chapters: 1,
      translated_chapters: 0, total_paragraphs: 1, translated_paragraphs: 0,
      progress_percentage: 0, status: 'pending', has_output_epub: false,
    } as never;
    const item = {
      id: 'q-paused', book_id: 'book-paused', book_name: 'Paused Book', source_type: 'epub',
      options: { book_id: 'book-paused' }, status: 'paused', order_index: 0, priority: 0,
      overall_progress: 0, current_chapter: '', current_chapter_index: 0, total_chapters: 1,
      message: '已暂停', enqueued_at: 'now', retry_count: 0, checkpoint: {},
    } as never;
    render(<QueueHubView
      books={[book]}
      queueStatus={{ is_paused: false, concurrency: 1, total_items: 1, running_count: 1, pending_count: 0,
        completed_count: 0, failed_count: 0, items: [item] }}
      onRefreshBooks={refresh}
      onRefreshQueue={refresh}
      onSelectBook={vi.fn()}
    />);

    expect(screen.getAllByText('已暂停').length).toBeGreaterThanOrEqual(1);
    await user.click(screen.getByRole('button', { name: '继续流水线' }));
    expect(api.resumePipeline).toHaveBeenCalledWith('book-paused');
    expect(refresh).toHaveBeenCalled();
  });
});

describe('batch book upload', () => {
  it('selects multiple files, uploads each, and enqueues successful imports together', async () => {
    const user = userEvent.setup();
    const book = (id: string) => ({
      id, name: id, source_type: 'txt', total_chapters: 1, translated_chapters: 0,
      total_paragraphs: 1, translated_paragraphs: 0, progress_percentage: 0,
      status: 'pending', has_output_epub: false,
    } as const);
    vi.spyOn(api, 'uploadBook')
      .mockResolvedValueOnce(book('book-a'))
      .mockResolvedValueOnce(book('book-b'));
    vi.spyOn(api, 'enqueueBooks').mockResolvedValue({} as never);
    const refreshBooks = vi.fn(async () => undefined);
    const refreshQueue = vi.fn(async () => undefined);
    const { container } = render(<QueueHubView
      books={[]}
      queueStatus={null}
      onRefreshBooks={refreshBooks}
      onRefreshQueue={refreshQueue}
      onSelectBook={vi.fn()}
    />);

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input.multiple).toBe(true);
    await user.upload(input, [
      new File(['a'], '第一本.txt', { type: 'text/plain' }),
      new File(['b'], '第二本.txt', { type: 'text/plain' }),
    ]);

    await waitFor(() => expect(api.uploadBook).toHaveBeenCalledTimes(2));
    expect(api.enqueueBooks).toHaveBeenCalledWith({ book_ids: ['book-a', 'book-b'] });
    expect(refreshBooks).toHaveBeenCalledOnce();
    expect(refreshQueue).toHaveBeenCalledOnce();
    expect(screen.getByText('成功导入 2/2 本，已加入队列。')).toBeInTheDocument();
  });
});
