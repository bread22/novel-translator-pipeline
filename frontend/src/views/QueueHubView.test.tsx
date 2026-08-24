import { render, screen } from '@testing-library/react';
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
