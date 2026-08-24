import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from './lib/api';
import { App } from './App';

vi.mock('./components/Navbar', () => ({ Navbar: () => <div>navbar</div> }));
vi.mock('./views/QueueHubView', () => ({
  QueueHubView: ({ queueStatus }: { queueStatus: { pending_count: number } | null }) => (
    <div>pending:{queueStatus?.pending_count ?? 'none'}</div>
  ),
}));
vi.mock('./views/LiveStudioView', () => ({
  LiveStudioView: ({ streamEvents, onClearEvents }: { streamEvents: any[]; onClearEvents: () => void }) => (
    <div>
      <span>events:{streamEvents.length}</span>
      <button onClick={onClearEvents}>clear events</button>
    </div>
  ),
}));
vi.mock('./views/ReaderView', () => ({ ReaderView: () => <div>reader</div> }));
vi.mock('./views/KnowledgeView', () => ({ KnowledgeView: () => <div>knowledge</div> }));
vi.mock('./views/SettingsView', () => ({ SettingsView: () => <div>settings</div> }));

const emptyQueue = {
  is_paused: true, concurrency: 1, total_items: 0, running_count: 0, pending_count: 0,
  completed_count: 0, failed_count: 0, items: [],
};

describe('global application state', () => {
  beforeEach(() => {
    localStorage.clear();
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

  it('restores a book waterfall after the page is remounted and persists manual clearing', async () => {
    window.location.hash = '#/studio';
    let emit!: (event: any) => void;
    vi.spyOn(api, 'getBooks').mockResolvedValue([{ id: 'selected-book' }] as any);
    vi.spyOn(api, 'getQueue').mockResolvedValue(emptyQueue);
    vi.spyOn(api, 'getTaskStatus').mockRejectedValue(new Error('not found'));
    vi.spyOn(api, 'subscribeEvents').mockImplementation((handler) => {
      emit = handler;
      return () => undefined;
    });

    const firstPage = render(<App />);
    expect(await screen.findByText('events:0')).toBeInTheDocument();
    act(() => emit({
      event: 'pipeline_stopped', book_id: 'selected-book', timestamp: 'now', event_id: 'evt-stop',
      data: { book_id: 'selected-book', status: 'cancelled' },
    }));
    expect(screen.getByText('events:1')).toBeInTheDocument();
    firstPage.unmount();

    const refreshedPage = render(<App />);
    expect(await screen.findByText('events:1')).toBeInTheDocument();
    fireEvent.click(screen.getByText('clear events'));
    expect(screen.getByText('events:0')).toBeInTheDocument();
    refreshedPage.unmount();

    render(<App />);
    expect(await screen.findByText('events:0')).toBeInTheDocument();
  });
});
