import React, { useState, useEffect, useCallback } from 'react';
import { Navbar } from './components/Navbar';
import { QueueHubView } from './views/QueueHubView';
import { LiveStudioView } from './views/LiveStudioView';
import { ReaderView } from './views/ReaderView';
import { KnowledgeView } from './views/KnowledgeView';
import { SettingsView } from './views/SettingsView';
import { BookSummary, QueueStatusResponse, StreamEvent, TaskStatusResponse } from './types/api';
import { api } from './lib/api';

export const App: React.FC = () => {
  const [currentTab, setCurrentTab] = useState<string>('queue');
  const [books, setBooks] = useState<BookSummary[]>([]);
  const [queueStatus, setQueueStatus] = useState<QueueStatusResponse | null>(null);
  const [selectedBookId, setSelectedBookId] = useState<string | null>(() => {
    return localStorage.getItem('selected_book_id') || null;
  });
  const [activeTask, setActiveTask] = useState<TaskStatusResponse | null>(null);
  const [eventsByBook, setEventsByBook] = useState<Record<string, StreamEvent[]>>({});
  const [sseConnected, setSseConnected] = useState(false);

  // Refresh Books
  const refreshBooks = useCallback(async () => {
    try {
      const data = await api.getBooks();
      setBooks(data);
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
    }
  }, []);

  // Refresh active task status
  const refreshTask = useCallback(async () => {
    if (!selectedBookId) return;
    try {
      const task = await api.getTaskStatus(selectedBookId).catch(() => null);
      setActiveTask(task);
    } catch (err) {
      console.error('Failed to fetch task status:', err);
    }
  }, [selectedBookId]);

  // Refresh Queue
  const refreshQueue = useCallback(async () => {
    try {
      const q = await api.getQueue();
      setQueueStatus(q);
    } catch (err) {
      console.error('Failed to fetch queue:', err);
    }
  }, []);

  useEffect(() => {
    refreshBooks();
    refreshQueue();
  }, [refreshBooks, refreshQueue]);

  useEffect(() => {
    if (selectedBookId) {
      localStorage.setItem('selected_book_id', selectedBookId);
      refreshTask();
    }
  }, [selectedBookId, refreshTask]);

  // Global SSE Subscription
  useEffect(() => {
    const unsubscribe = api.subscribeEvents((evt) => {
      if (evt.event === 'connect') {
        setSseConnected(true);
      }

      // Record event under target book ID
      const targetBookId = evt.book_id || evt.data?.book_id || selectedBookId;
      if (targetBookId) {
        setEventsByBook((prev) => {
          const existing = prev[targetBookId] || [];
          return {
            ...prev,
            [targetBookId]: [...existing.slice(-300), evt],
          };
        });
      }

      // 1. Queue event updates
      if (evt.event === 'queue_updated' && evt.data && typeof evt.data === 'object') {
        setQueueStatus(evt.data as QueueStatusResponse);
        refreshBooks();
      } else if (evt.event.startsWith('queue_')) {
        refreshQueue();
        refreshBooks();
      }

      // 2. Direct activeTask state update from event payload if present
      if (evt.data && typeof evt.data === 'object' && evt.data.task_id && evt.data.status) {
        if (!selectedBookId || evt.data.book_id === selectedBookId) {
          setActiveTask(evt.data as TaskStatusResponse);
        }
      }

      // 3. If pipeline state changed or chapter completed, sync task, books, and queue
      const pipelineEvents = [
        'pipeline_started',
        'chapter_started',
        'batch_completed',
        'pipeline_progress',
        'chapter_completed',
        'pipeline_completed',
        'pipeline_paused',
        'pipeline_resumed',
        'pipeline_stopped',
      ];
      if (pipelineEvents.includes(evt.event)) {
        refreshTask();
        refreshBooks();
        refreshQueue();
      }
    }, selectedBookId || undefined);

    return () => {
      unsubscribe();
    };
  }, [selectedBookId, refreshTask, refreshBooks, refreshQueue]);

  const selectedBook = books.find((b) => b.id === selectedBookId) || null;

  const handleSelectBook = (bookId: string, targetTab?: string) => {
    setSelectedBookId(bookId);
    localStorage.setItem('selected_book_id', bookId);
    if (targetTab) {
      setCurrentTab(targetTab);
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
        onSelectTab={setCurrentTab}
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
