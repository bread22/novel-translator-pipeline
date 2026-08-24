import { act, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from './lib/api';
import { App } from './App';

vi.mock('./components/Navbar', () => ({ Navbar: () => <div>navbar</div> }));
vi.mock('./views/QueueHubView', () => ({
  QueueHubView: ({ queueStatus }: { queueStatus: { pending_count: number } | null }) => (
    <div>pending:{queueStatus?.pending_count ?? 'none'}</div>
  ),
}));
vi.mock('./views/LiveStudioView', () => ({ LiveStudioView: () => <div>studio</div> }));
vi.mock('./views/ReaderView', () => ({ ReaderView: () => <div>reader</div> }));
vi.mock('./views/KnowledgeView', () => ({ KnowledgeView: () => <div>knowledge</div> }));
vi.mock('./views/SettingsView', () => ({ SettingsView: () => <div>settings</div> }));

const emptyQueue = {
  is_paused: true, concurrency: 1, total_items: 0, running_count: 0, pending_count: 0,
  completed_count: 0, failed_count: 0, items: [],
};

describe('global application state', () => {
  beforeEach(() => {
    window.location.hash = '#/queue';
    localStorage.setItem('selected_book_id', 'selected-book');
  });

  it('applies a global queue event even when it belongs to another book', async () => {
    let emit!: (event: any) => void;
    vi.spyOn(api, 'getBooks').mockResolvedValue([]);
    vi.spyOn(api, 'getQueue').mockResolvedValue(emptyQueue);
    vi.spyOn(api, 'getTaskStatus').mockRejectedValue(new Error('not found'));
    vi.spyOn(api, 'subscribeEvents').mockImplementation((handler) => {
      emit = handler;
      return () => undefined;
    });
    render(<App />);
    expect(await screen.findByText('pending:0')).toBeInTheDocument();
    act(() => emit({
      event: 'queue_updated', book_id: 'different-book', timestamp: 'now', event_id: '1',
      data: { ...emptyQueue, pending_count: 3, total_items: 3 },
    }));
    expect(screen.getByText('pending:3')).toBeInTheDocument();
  });
});
