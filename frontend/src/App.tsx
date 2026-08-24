import React, { useState, useEffect, useCallback, useReducer, useRef } from 'react';
import { Navbar } from './components/Navbar';
import { QueueHubView } from './views/QueueHubView';
import { LiveStudioView } from './views/LiveStudioView';
import { ReaderView } from './views/ReaderView';
import { KnowledgeView } from './views/KnowledgeView';
import { SettingsView } from './views/SettingsView';
import { QueueStatusResponse, StreamEvent, TaskStatusResponse } from './types/api';
import { api } from './lib/api';
import { appServerReducer, initialAppServerState } from './appState';
import { createRequestCache } from './lib/requestCache';

const VALID_TABS = ['queue', 'studio', 'reader', 'knowledge', 'settings'];
const STREAM_EVENTS_STORAGE_KEY = 'stream_events_by_book_v1';
const MAX_STREAM_EVENTS_PER_BOOK = 300;

function loadPersistedEvents(): Record<string, StreamEvent[]> {
  try {
    const parsed = JSON.parse(localStorage.getItem(STREAM_EVENTS_STORAGE_KEY) || '{}');
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    return Object.fromEntries(
      Object.entries(parsed).flatMap(([bookId, events]) => (
        Array.isArray(events)
          ? [[bookId, events.filter((event): event is StreamEvent => (
            event !== null
            && typeof event === 'object'
            && typeof event.event === 'string'
            && typeof event.timestamp === 'string'
            && typeof event.event_id === 'string'
          )).slice(-MAX_STREAM_EVENTS_PER_BOOK)]]
          : []
      )),
    );
  } catch {
    return {};
  }
}

export const App: React.FC = () => {
  const [currentTab, setCurrentTab] = useState<string>(() => {
    const tab = window.location.hash.replace(/^#\/?/, '');
    return VALID_TABS.includes(tab) ? tab : 'queue';
  });
  const [{ books, queue: queueStatus, task: activeTask }, dispatchServer] = useReducer(appServerReducer, initialAppServerState);
  const [selectedBookId, setSelectedBookId] = useState<string | null>(() => {
    return localStorage.getItem('selected_book_id') || null;
  });
  const [eventsByBook, setEventsByBook] = useState<Record<string, StreamEvent[]>>(loadPersistedEvents);
  const [sseConnected, setSseConnected] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isInitialLoading, setIsInitialLoading] = useState(true);
  const selectedBookRef = useRef(selectedBookId);
  const requestCache = useRef(createRequestCache()).current;

  useEffect(() => {
    selectedBookRef.current = selectedBookId;
  }, [selectedBookId]);

  useEffect(() => {
    try {
      localStorage.setItem(STREAM_EVENTS_STORAGE_KEY, JSON.stringify(eventsByBook));
    } catch (err) {
      console.warn('Failed to persist stream events:', err);
    }
  }, [eventsByBook]);

  // Refresh Books
  const refreshBooks = useCallback(async () => {
    try {
      const data = await requestCache('books', () => api.getBooks());
      setLoadError(null);
      dispatchServer({ type: 'books', value: data });
      if (data.length > 0) {
        setSelectedBookId((prev) => {
          if (prev && data.some((b) => b.id === prev)) {
            return prev;
          }
          const saved = localStorage.getItem('selected_book_id');
          if (saved && data.some((b) => b.id === saved)) {
            return saved;
          }
          return data[0].id;
        });
      }
    } catch (err) {
      console.error('Failed to fetch books:', err);
      setLoadError(err instanceof Error ? err.message : '书籍列表加载失败');
    }
  }, [requestCache]);

  // Refresh active task status
  const refreshTask = useCallback(async () => {
    if (!selectedBookId) return;
    try {
      const task = await requestCache(`task:${selectedBookId}`, () => api.getTaskStatus(selectedBookId)).catch(() => null);
      dispatchServer({ type: 'task', value: task });
    } catch (err) {
      console.error('Failed to fetch task status:', err);
    }
  }, [requestCache, selectedBookId]);

  // Refresh Queue
  const refreshQueue = useCallback(async () => {
    try {
      const q = await requestCache('queue', () => api.getQueue());
      dispatchServer({ type: 'queue', value: q });
    } catch (err) {
      console.error('Failed to fetch queue:', err);
    }
  }, [requestCache]);

  useEffect(() => {
    Promise.allSettled([refreshBooks(), refreshQueue()]).finally(() => setIsInitialLoading(false));
  }, [refreshBooks, refreshQueue]);

  useEffect(() => {
    const handleHash = () => {
      const tab = window.location.hash.replace(/^#\/?/, '');
      if (VALID_TABS.includes(tab)) setCurrentTab(tab);
    };
    window.addEventListener('hashchange', handleHash);
    return () => window.removeEventListener('hashchange', handleHash);
  }, []);

  const selectTab = (tab: string) => {
    setCurrentTab(tab);
    window.history.replaceState(null, '', `#/${tab}`);
  };

  useEffect(() => {
    if (selectedBookId) {
      localStorage.setItem('selected_book_id', selectedBookId);
      refreshTask();
    }
  }, [selectedBookId, refreshTask]);

  // Global SSE Subscription
  useEffect(() => {
    const unsubscribe = api.subscribeEvents((evt) => {
      // Record event under target book ID
      const targetBookId = evt.book_id || evt.data?.book_id;
      if (targetBookId) {
        setEventsByBook((prev) => {
          const existing = prev[targetBookId] || [];
          return {
            ...prev,
            [targetBookId]: [...existing.slice(-(MAX_STREAM_EVENTS_PER_BOOK - 1)), evt],
          };
        });
      }

      // 1. Queue event updates
      if (evt.event === 'queue_updated' && evt.data && typeof evt.data === 'object') {
        dispatchServer({ type: 'queue', value: evt.data as QueueStatusResponse });
        refreshBooks();
      } else if (evt.event.startsWith('queue_')) {
        refreshQueue();
        refreshBooks();
      }

      // 2. Direct activeTask state update from event payload if present
      if (evt.data && typeof evt.data === 'object' && evt.data.task_id && evt.data.status) {
        if (!selectedBookRef.current || evt.data.book_id === selectedBookRef.current) {
          dispatchServer({ type: 'task', value: evt.data as TaskStatusResponse });
        }
      }

      // 3. If pipeline state changed or chapter completed, sync task, books, and queue
      const pipelineEvents = [
        'pipeline_started',
        'chapter_started',
        'batch_completed',
        'pipeline_progress',
        'pipeline_phase_changed',
        'pipeline_reviewer_status',
        'chapter_completed',
        'pipeline_completed',
        'pipeline_paused',
        'pipeline_resumed',
        'pipeline_stopped',
      ];
      if (pipelineEvents.includes(evt.event)) {
        if (selectedBookRef.current) {
          requestCache(`task:${selectedBookRef.current}`, () => api.getTaskStatus(selectedBookRef.current!))
            .then((value) => dispatchServer({ type: 'task', value }))
            .catch(() => dispatchServer({ type: 'task', value: null }));
        }
        refreshBooks();
        refreshQueue();
      }
    }, (state) => {
      setSseConnected(state === 'live');
      if (state === 'live') {
        refreshBooks();
        refreshQueue();
        if (selectedBookRef.current) {
          requestCache(`task:${selectedBookRef.current}`, () => api.getTaskStatus(selectedBookRef.current!))
            .then((value) => dispatchServer({ type: 'task', value }))
            .catch(() => dispatchServer({ type: 'task', value: null }));
        }
      }
    });

    return () => {
      unsubscribe();
    };
  }, [refreshBooks, refreshQueue, requestCache]);

  const selectedBook = books.find((b) => b.id === selectedBookId) || null;

  const handleSelectBook = (bookId: string, targetTab?: string) => {
    setSelectedBookId(bookId);
    localStorage.setItem('selected_book_id', bookId);
    if (targetTab) {
      selectTab(targetTab);
    }
  };

  const handleClearEvents = (bookId: string) => {
    setEventsByBook((prev) => ({
      ...prev,
      [bookId]: [],
    }));
  };

  const queueCount = (queueStatus?.pending_count || 0) + (queueStatus?.running_count || 0);
  const isQueueRunning = (queueStatus?.running_count || 0) > 0;
  const currentStreamEvents = (selectedBookId && eventsByBook[selectedBookId]) || [];

  return (
    <div className="min-h-screen bg-[#FAF9F6] text-[#1A1A1A] flex flex-col selection:bg-[#1D4ED8] selection:text-white font-sans">
      {/* Top Navbar */}
      <Navbar
        currentTab={currentTab}
        onSelectTab={selectTab}
        books={books}
        selectedBookId={selectedBookId}
        onSelectBookId={setSelectedBookId}
        activeTask={activeTask}
        sseConnected={sseConnected}
        queueCount={queueCount}
        isQueueRunning={isQueueRunning}
      />

      {/* Main View Area */}
      <main className="flex-1 max-w-[1600px] w-full mx-auto p-6">
        {isInitialLoading && <p role="status" className="text-sm text-[#666666]">正在加载工作区…</p>}
        {loadError && (
          <div role="alert" className="mb-4 border border-red-300 bg-red-50 p-3 text-sm text-red-800">
            {loadError} <button className="underline" onClick={() => void refreshBooks()}>重试</button>
          </div>
        )}
        {(currentTab === 'queue' || currentTab === 'library') && (
          <QueueHubView
            books={books}
            queueStatus={queueStatus}
            onRefreshBooks={refreshBooks}
            onRefreshQueue={refreshQueue}
            onSelectBook={handleSelectBook}
          />
        )}

        {currentTab === 'studio' && (
          <LiveStudioView
            book={selectedBook}
            activeTask={activeTask}
            streamEvents={currentStreamEvents}
            onRefreshTask={refreshTask}
            onRefreshBooks={refreshBooks}
            onClearEvents={() => selectedBookId && handleClearEvents(selectedBookId)}
          />
        )}

        {currentTab === 'reader' && <ReaderView book={selectedBook} />}

        {currentTab === 'knowledge' && <KnowledgeView book={selectedBook} />}

        {currentTab === 'settings' && <SettingsView />}
      </main>
    </div>
  );
};
export default App;
