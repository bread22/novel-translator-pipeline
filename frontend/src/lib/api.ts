import {
  BookMemoryResponse,
  BookSummary,
  ChapterDetail,
  ChapterSummary,
  GlossaryItem,
  GlossaryResponse,
  PipelineStartRequest,
  PreflightResponse,
  PromptItem,
  StreamEvent,
  SystemConfig,
  TaskStatusResponse,
} from '../types/api';

const API_BASE = '/api/v1';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`;
  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });

  if (!response.ok) {
    let errorDetail = response.statusText;
    try {
      const err = await response.json();
      errorDetail = err.detail || errorDetail;
    } catch {
      // ignore
    }
    throw new Error(errorDetail);
  }

  return response.json();
}

export const api = {
  // Books
  getBooks: () => request<BookSummary[]>('/books'),
  getBook: (id: string) => request<BookSummary>(`/books/${id}`),
  uploadBook: async (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch(`${API_BASE}/books/upload`, {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || '上传文件失败');
    }
    return res.json() as Promise<BookSummary>;
  },
  getChapters: (bookId: string) => request<ChapterSummary[]>(`/books/${bookId}/chapters`),
  getChapterDetail: (bookId: string, chapterId: string) =>
    request<ChapterDetail>(`/books/${bookId}/chapters/${chapterId}`),
  updateParagraph: (bookId: string, paragraphId: string, translated: string) =>
    request<{ status: string }>(`/books/${bookId}/paragraphs/${paragraphId}`, {
      method: 'PUT',
      body: JSON.stringify({ translated }),
    }),
  exportBook: (bookId: string, layout: 'horizontal' | 'preserve' = 'horizontal') =>
    request<{ status: string; download_url: string }>(`/books/${bookId}/export?layout=${layout}`, {
      method: 'POST',
    }),
  resetBook: (bookId: string) =>
    request<{ status: string; message: string }>(`/books/${bookId}/reset`, {
      method: 'POST',
    }),
  deleteBook: (bookId: string) =>
    request<{ status: string; message: string }>(`/books/${bookId}`, {
      method: 'DELETE',
    }),

  // Tasks
  startPipeline: (data: PipelineStartRequest) =>
    request<TaskStatusResponse>('/tasks/pipeline/start', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  pausePipeline: (bookId: string) =>
    request<TaskStatusResponse>(`/tasks/pipeline/pause?task_or_book_id=${encodeURIComponent(bookId)}`, {
      method: 'POST',
    }),
  resumePipeline: (bookId: string) =>
    request<TaskStatusResponse>(`/tasks/pipeline/resume?task_or_book_id=${encodeURIComponent(bookId)}`, {
      method: 'POST',
    }),
  stopPipeline: (bookId: string) =>
    request<TaskStatusResponse>(`/tasks/pipeline/stop?task_or_book_id=${encodeURIComponent(bookId)}`, {
      method: 'POST',
    }),
  getTaskStatus: (bookId: string) =>
    request<TaskStatusResponse>(`/tasks/status/${encodeURIComponent(bookId)}`),
  retranslateParagraph: (bookId: string, chapterId: string, paragraphId: string, provider?: string) =>
    request<{ status: string; translated: string }>('/tasks/retranslate-paragraph', {
      method: 'POST',
      body: JSON.stringify({
        book_id: bookId,
        chapter_id: chapterId,
        paragraph_id: paragraphId,
        provider,
      }),
    }),

  // Knowledge & Memory
  getGlossary: (bookId: string) => request<GlossaryResponse>(`/knowledge/${bookId}/glossary`),
  updateGlossary: (bookId: string, terms: GlossaryItem[]) =>
    request<GlossaryResponse>(`/knowledge/${bookId}/glossary`, {
      method: 'POST',
      body: JSON.stringify({ terms }),
    }),
  getMemory: (bookId: string) => request<BookMemoryResponse>(`/knowledge/${bookId}/memory`),
  getChapterReview: (bookId: string, chapterId: string) =>
    request<any>(`/knowledge/${bookId}/reviews/${chapterId}`),

  // System & Preflight & Prompts
  getConfig: () => request<SystemConfig>('/system/config'),
  saveConfig: (config: SystemConfig) =>
    request<{ status: string }>('/system/config', {
      method: 'POST',
      body: JSON.stringify(config),
    }),
  runPreflight: () => request<PreflightResponse>('/system/preflight', { method: 'POST' }),
  getPrompts: () => request<PromptItem[]>('/system/prompts'),
  getPromptDetail: (promptId: string) => request<PromptItem>(`/system/prompts/${encodeURIComponent(promptId)}`),
  savePrompt: (data: { filename: string; content: string }) =>
    request<{ status: string; id: string; message: string }>('/system/prompts', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  deletePrompt: (promptId: string) =>
    request<{ status: string; message: string }>(`/system/prompts/${encodeURIComponent(promptId)}`, {
      method: 'DELETE',
    }),

  // SSE Stream
  subscribeEvents: (onEvent: (event: StreamEvent) => void, bookId?: string): (() => void) => {
    const url = bookId ? `${API_BASE}/events/stream?book_id=${encodeURIComponent(bookId)}` : `${API_BASE}/events/stream`;
    const eventSource = new EventSource(url);

    const handleAnyEvent = (e: MessageEvent, eventName: string) => {
      try {
        const parsed = JSON.parse(e.data);
        onEvent({
          event: eventName,
          data: parsed,
          book_id: bookId,
          timestamp: new Date().toISOString(),
        });
      } catch (err) {
        console.warn('Error parsing SSE data:', err);
      }
    };

    const registeredEvents = [
      'connect',
      'pipeline_started',
      'pipeline_progress',
      'chapter_started',
      'chapter_completed',
      'fallback_triggered',
      'review_completed',
      'finalizing',
      'pipeline_completed',
      'pipeline_failed',
      'pipeline_paused',
      'pipeline_resumed',
      'pipeline_stopped',
    ];

    registeredEvents.forEach((evt) => {
      eventSource.addEventListener(evt, (e) => handleAnyEvent(e as MessageEvent, evt));
    });

    eventSource.onmessage = (e) => handleAnyEvent(e, 'message');
    eventSource.onerror = (err) => {
      console.warn('SSE connection error:', err);
    };

    return () => {
      eventSource.close();
    };
  },
};

