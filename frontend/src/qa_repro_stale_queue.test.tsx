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

describe('QA regression: stale REST snapshot after SSE queue update', () => {
  beforeEach(() => {
    localStorage.clear();
    window.location.hash = '#/queue';
  });

  it('must keep the newer SSE queue snapshot when the older initial GET resolves later', async () => {
    let resolveInitialQueue!: (value: typeof emptyQueue) => void;
    let emit!: (event: any) => void;
    const initialQueue = new Promise<typeof emptyQueue>((resolve) => { resolveInitialQueue = resolve; });

    vi.spyOn(api, 'getBooks').mockResolvedValue([]);
    vi.spyOn(api, 'getQueue').mockReturnValue(initialQueue);
    vi.spyOn(api, 'subscribeEvents').mockImplementation((handler) => {
      emit = handler;
      return () => undefined;
    });

    render(<App />);
    await act(async () => { await Promise.resolve(); });

    act(() => emit({
      event: 'queue_updated', book_id: 'book-new', timestamp: 'new', event_id: 'new-event',
      data: { ...emptyQueue, pending_count: 3, total_items: 3 },
    }));
    expect(screen.getByText('pending:3')).toBeInTheDocument();

    await act(async () => resolveInitialQueue(emptyQueue));
    expect(screen.getByText('pending:3')).toBeInTheDocument();
  });
});
