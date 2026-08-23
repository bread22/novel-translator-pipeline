import React, { useState, useEffect, useCallback } from 'react';
import { Navbar } from './components/Navbar';
import { LibraryView } from './views/LibraryView';
import { LiveStudioView } from './views/LiveStudioView';
import { ReaderView } from './views/ReaderView';
import { KnowledgeView } from './views/KnowledgeView';
import { SettingsView } from './views/SettingsView';
import { BookSummary, StreamEvent, TaskStatusResponse } from './types/api';
import { api } from './lib/api';

export const App: React.FC = () => {
  const [currentTab, setCurrentTab] = useState<string>('library');
  const [books, setBooks] = useState<BookSummary[]>([]);
  const [selectedBookId, setSelectedBookId] = useState<string | null>(null);
  const [activeTask, setActiveTask] = useState<TaskStatusResponse | null>(null);
  const [streamEvents, setStreamEvents] = useState<StreamEvent[]>([]);
  const [sseConnected, setSseConnected] = useState(false);

  // Refresh Books
  const refreshBooks = useCallback(async () => {
    try {
      const data = await api.getBooks();
      setBooks(data);
      if (!selectedBookId && data.length > 0) {
        setSelectedBookId(data[0].id);
      }
    } catch (err) {
      console.error('Failed to fetch books:', err);
    }
  }, [selectedBookId]);

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

  useEffect(() => {
    refreshBooks();
  }, [refreshBooks]);

  useEffect(() => {
    if (selectedBookId) {
      refreshTask();
    }
  }, [selectedBookId, refreshTask]);

  // Global SSE Subscription
  useEffect(() => {
    const unsubscribe = api.subscribeEvents((evt) => {
      if (evt.event === 'connect') {
        setSseConnected(true);
      }
      setStreamEvents((prev) => [...prev.slice(-200), evt]);

      // 1. Direct activeTask state update from event payload if present
      if (evt.data && typeof evt.data === 'object' && evt.data.task_id && evt.data.status) {
        if (!selectedBookId || evt.data.book_id === selectedBookId) {
          setActiveTask(evt.data as TaskStatusResponse);
        }
      }

      // 2. If pipeline state changed or chapter completed, sync task and books
      const pipelineEvents = [
        'pipeline_started',
        'chapter_started',
        'pipeline_progress',
        'chapter_completed',
        'pipeline_completed',
        'pipeline_paused',
        'pipeline_stopped',
      ];
      if (pipelineEvents.includes(evt.event)) {
        refreshTask();
        refreshBooks();
      }
    }, selectedBookId || undefined);

    return () => {
      unsubscribe();
    };
  }, [selectedBookId, refreshTask, refreshBooks]);

  const selectedBook = books.find((b) => b.id === selectedBookId) || null;

  const handleSelectBook = (bookId: string, targetTab?: string) => {
    setSelectedBookId(bookId);
    if (targetTab) {
      setCurrentTab(targetTab);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col selection:bg-indigo-500 selection:text-white">
      {/* Top Navbar */}
      <Navbar
        currentTab={currentTab}
        onSelectTab={setCurrentTab}
        books={books}
        selectedBookId={selectedBookId}
        onSelectBookId={setSelectedBookId}
        activeTask={activeTask}
        sseConnected={sseConnected}
      />

      {/* Main View Area */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-6">
        {currentTab === 'library' && (
          <LibraryView
            books={books}
            onRefreshBooks={refreshBooks}
            onSelectBook={handleSelectBook}
          />
        )}

        {currentTab === 'studio' && (
          <LiveStudioView
            book={selectedBook}
            activeTask={activeTask}
            streamEvents={streamEvents}
            onRefreshTask={refreshTask}
            onRefreshBooks={refreshBooks}
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

