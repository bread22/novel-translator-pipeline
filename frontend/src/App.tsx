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
const MAX_STREAM_EVENTS_PER_BOOK = 30;
const MAX_EVENT_BOOKS = 20;

function boundedEvents(events: Record<string, StreamEvent[]>): Record<string, StreamEvent[]> {
  return Object.fromEntries(Object.entries(events).slice(-MAX_EVENT_BOOKS));
}

function isStreamEvent(value: unknown): value is StreamEvent {
  return Boolean(
    value
    && typeof value === 'object'
    && typeof (value as StreamEvent).event === 'string'
    && typeof (value as StreamEvent).timestamp === 'string'
    && typeof (value as StreamEvent).event_id === 'string'
  );
}

function eventKey(event: StreamEvent): string {
  return event.event_id || `${event.timestamp}_${event.event}`;
}

function mergeEventHistory(...batches: unknown[][]): StreamEvent[] {
  const seen = new Set<string>();
  const merged: StreamEvent[] = [];
  for (const event of batches.flat()) {
    if (!isStreamEvent(event)) continue;
    const key = eventKey(event);
    if (!seen.has(key)) {
      seen.add(key);
      merged.push(event);
    }
  }
  return merged.slice(-MAX_STREAM_EVENTS_PER_BOOK);
}

function loadPersistedEvents(): Record<string, StreamEvent[]> {
  try {
    const parsed = JSON.parse(localStorage.getItem(STREAM_EVENTS_STORAGE_KEY) || '{}');
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    return boundedEvents(Object.fromEntries(
      Object.entries(parsed).flatMap(([bookId, events]) => (
        Array.isArray(events)
          ? [[bookId, mergeEventHistory(events)]]
          : []
      )),
    ));
  } catch {
    return {};
  }
}

function persistEvents(eventsByBook: Record<string, StreamEvent[]>): void {
  try {
    localStorage.setItem(STREAM_EVENTS_STORAGE_KEY, JSON.stringify(eventsByBook));
  } catch (err) {
    // History is durable on the server. Do not repeatedly serialize/prune on
    // the UI thread when storage is unavailable or its quota is exhausted.
    console.warn('Failed to persist stream events:', err);
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
  const [sseState, setSseState] = useState<'live' | 'reconnecting' | 'offline'>('reconnecting');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isInitialLoading, setIsInitialLoading] = useState(true);
  const selectedBookRef = useRef(selectedBookId);
  const requestCache = useRef(createRequestCache()).current;
  const booksRevision = useRef(0);
  const queueRevision = useRef(0);
  const taskRevision = useRef(0);
  const eventsRef = useRef(eventsByBook);
  const eventsDirty = useRef(false);
  const taskRefreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    selectedBookRef.current = selectedBookId;
    taskRevision.current += 1;
    dispatchServer({ type: 'task', value: null });
  }, [selectedBookId]);

  useEffect(() => {
    eventsRef.current = eventsByBook;
    eventsDirty.current = true;
  }, [eventsByBook]);

  useEffect(() => {
    const flush = () => {
      if (!eventsDirty.current) return;
      eventsDirty.current = false;
      persistEvents(eventsRef.current);
    };
    const timer = setInterval(flush, 500);
    window.addEventListener('pagehide', flush);
    return () => {
      clearInterval(timer);
      window.removeEventListener('pagehide', flush);
      flush();
    };
  }, []);

  const refreshBooks = useCallback(async () => {
    const revision = ++booksRevision.current;
    try {
      const data = await requestCache('books', () => api.getBooks());
      if (revision !== booksRevision.current) return;
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

  // Debounced Refresh Books
  const refreshBooksTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const debouncedRefreshBooks = useCallback(() => {
    if (refreshBooksTimeoutRef.current) {
      clearTimeout(refreshBooksTimeoutRef.current);
    }
    refreshBooksTimeoutRef.current = setTimeout(() => {
      refreshBooks();
    }, 600);
  }, [refreshBooks]);

  // Refresh active task status
  const refreshTask = useCallback(async () => {
    const bookId = selectedBookRef.current;
    if (!bookId) return;
    const revision = ++taskRevision.current;
    try {
      const task = await requestCache(`task:${bookId}`, () => api.getTaskStatus(bookId));
      if (bookId === selectedBookRef.current && revision === taskRevision.current) {
        dispatchServer({ type: 'task', value: task });
      }
    } catch (err) {
      // A transient fetch failure must not turn a running task into "idle".
      console.debug('Task status refresh failed:', err);
    }
  }, [requestCache]);

  const scheduleTaskRefresh = useCallback(() => {
    if (taskRefreshTimer.current) return;
    taskRefreshTimer.current = setTimeout(() => {
      taskRefreshTimer.current = null;
      void refreshTask();
    }, 600);
  }, [refreshTask]);

  useEffect(() => () => {
    if (taskRefreshTimer.current) clearTimeout(taskRefreshTimer.current);
  }, []);

  // Refresh Queue
  const refreshQueue = useCallback(async () => {
    const revision = ++queueRevision.current;
    try {
      const q = await requestCache('queue', () => api.getQueue());
      if (revision !== queueRevision.current) return;
      dispatchServer({ type: 'queue', value: q });
    } catch (err) {
      console.error('Failed to fetch queue:', err);
    }
  }, [requestCache]);

  // Debounced Refresh Queue
  const refreshQueueTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const debouncedRefreshQueue = useCallback(() => {
    if (refreshQueueTimeoutRef.current) {
      clearTimeout(refreshQueueTimeoutRef.current);
    }
    refreshQueueTimeoutRef.current = setTimeout(() => {
      refreshQueue();
    }, 600);
  }, [refreshQueue]);

  useEffect(() => {
    return () => {
      if (refreshBooksTimeoutRef.current) clearTimeout(refreshBooksTimeoutRef.current);
      if (refreshQueueTimeoutRef.current) clearTimeout(refreshQueueTimeoutRef.current);
    };
  }, []);

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

  // Fetch persistent historical events from server
  const fetchBookEvents = useCallback(async (bookId: string) => {
    if (!bookId) return;
    try {
      const serverEvents = await api.getBookEvents(bookId, MAX_STREAM_EVENTS_PER_BOOK);
      if (Array.isArray(serverEvents) && serverEvents.length > 0) {
        setEventsByBook((prev) => {
          const combined = mergeEventHistory(serverEvents, prev[bookId] || []);
          const next = { ...prev };
          delete next[bookId];
          return boundedEvents({ ...next, [bookId]: combined });
        });
      }
    } catch (err) {
      console.debug('Failed to fetch server events for book:', err);
    }
  }, []);

  useEffect(() => {
    if (selectedBookId) {
      localStorage.setItem('selected_book_id', selectedBookId);
      refreshTask();
      fetchBookEvents(selectedBookId);
    }
  }, [selectedBookId, refreshTask, fetchBookEvents]);

  // Global SSE Subscription
  useEffect(() => {
    const unsubscribe = api.subscribeEvents((evt) => {
      // Record event under target book ID
      const explicitBookId = evt.book_id || evt.data?.book_id;
      const targetBookId = explicitBookId || (
        selectedBookRef.current
        && evt.event !== 'connect'
        && !evt.event.startsWith('queue_')
          ? selectedBookRef.current
          : null
      );
      if (targetBookId) {
        setEventsByBook((prev) => {
          const existing = prev[targetBookId] || [];
          const next = mergeEventHistory(existing, [evt]);
          if (next.length === existing.length && next.every((event, index) => event === existing[index])) {
            return prev;
          }
          const history = { ...prev };
          delete history[targetBookId];
          return boundedEvents({ ...history, [targetBookId]: next });
        });
      }

      // 1. Queue event updates
      if (evt.event === 'queue_updated' && evt.data && typeof evt.data === 'object') {
        queueRevision.current += 1;
        dispatchServer({ type: 'queue', value: evt.data as QueueStatusResponse });
        debouncedRefreshBooks();
      } else if (evt.event.startsWith('queue_')) {
        debouncedRefreshQueue();
        debouncedRefreshBooks();
      }

      // Use complete task snapshots directly; partial events trigger one
      // bounded refresh rather than one HTTP request per progress event.
      const hasTaskSnapshot = Boolean(evt.data?.task_id && evt.data?.status
        && typeof evt.data?.overall_progress === 'number'
        && typeof evt.data?.total_chapters === 'number');
      if (hasTaskSnapshot && targetBookId === selectedBookRef.current) {
        taskRevision.current += 1;
        if (taskRefreshTimer.current) {
          clearTimeout(taskRefreshTimer.current);
          taskRefreshTimer.current = null;
        }
        dispatchServer({ type: 'task', value: { ...evt.data, book_id: targetBookId } as TaskStatusResponse });
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
        if (!hasTaskSnapshot && targetBookId === selectedBookRef.current) scheduleTaskRefresh();
        const boundaryEvents = [
          'chapter_started',
          'chapter_completed',
          'pipeline_completed',
          'pipeline_paused',
          'pipeline_resumed',
          'pipeline_stopped',
        ];
        if (boundaryEvents.includes(evt.event)) {
          debouncedRefreshBooks();
          debouncedRefreshQueue();
        }
      }
    }, (state) => {
      setSseState(state);
      setSseConnected(state === 'live');
      if (state === 'live') {
        refreshBooks();
        refreshQueue();
        void refreshTask();
      }
    });

    return () => {
      unsubscribe();
    };
  }, [refreshBooks, refreshQueue, debouncedRefreshBooks, debouncedRefreshQueue, refreshTask, scheduleTaskRefresh]);

  useEffect(() => {
    if (sseState === 'live') return;
    const timer = setInterval(() => {
      if (document.visibilityState === 'hidden') return;
      void refreshBooks();
      void refreshQueue();
      void refreshTask();
    }, 15000);
    return () => clearInterval(timer);
  }, [sseState, refreshBooks, refreshQueue, refreshTask]);

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
        sseState={sseState}
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
