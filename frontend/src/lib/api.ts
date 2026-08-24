import {
  BookMemoryResponse,
  BookSummary,
  ChapterDetail,
  ChapterReviewReport,
  ChapterSummary,
  GlossaryItem,
  GlossaryResponse,
  PipelineStartRequest,
  PreflightResponse,
  PromptItem,
  QueueStatusResponse,
  EnqueueRequest,
  StreamEvent,
  SystemConfig,
  TaskStatusResponse,
} from '../types/api';

const API_BASE = '/api/v1';

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    public readonly detail: string,
    public readonly requestId?: string,
  ) {
    super(detail);
    this.name = 'ApiError';
  }
}

export interface RequestOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
}

function combinedSignal(external: AbortSignal | undefined, timeoutMs: number) {
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort(external?.reason);
  if (external?.aborted) abort();
  else external?.addEventListener('abort', abort, { once: true });
  const timeout = window.setTimeout(() => {
    timedOut = true;
    controller.abort(new DOMException('请求超时', 'TimeoutError'));
  }, timeoutMs);
  return {
    signal: controller.signal,
    didTimeOut: () => timedOut,
    cleanup: () => {
      window.clearTimeout(timeout);
      external?.removeEventListener('abort', abort);
    },
  };
}

export async function request<T>(path: string, options?: RequestInit, requestOptions: RequestOptions = {}): Promise<T> {
  const url = `${API_BASE}${path}`;
  const { signal, didTimeOut, cleanup } = combinedSignal(requestOptions.signal, requestOptions.timeoutMs ?? 30_000);
  try {
    const response = await fetch(url, {
      ...options,
      signal,
      headers: options?.body instanceof FormData ? options?.headers : {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    });

    if (!response.ok) {
      let envelope: any = {};
      try {
        envelope = await response.json();
      } catch {
        // Preserve the HTTP status text for non-JSON failures.
      }
      const nested = typeof envelope.detail === 'object' ? envelope.detail : envelope;
      const detail = typeof envelope.detail === 'string'
        ? envelope.detail
        : nested?.detail || nested?.message || response.statusText || '请求失败';
      throw new ApiError(
        response.status,
        nested?.code || envelope.code || `HTTP_${response.status}`,
        detail,
        response.headers.get('x-request-id') || nested?.request_id || envelope.request_id || undefined,
      );
    }

    if (response.status === 204) return undefined as T;
    const text = await response.text();
    return (text ? JSON.parse(text) : undefined) as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (signal.aborted) {
      throw new ApiError(0, didTimeOut() ? 'REQUEST_TIMEOUT' : 'REQUEST_ABORTED', didTimeOut() ? '请求超时' : '请求已取消');
    }
    throw new ApiError(0, 'NETWORK_ERROR', error instanceof Error ? error.message : '网络请求失败');
  } finally {
    cleanup();
  }
}

export const api = {
  // Books
  getBooks: (options?: RequestOptions) => request<BookSummary[]>('/books', undefined, options),
  getBook: (id: string, options?: RequestOptions) => request<BookSummary>(`/books/${id}`, undefined, options),
  uploadBook: (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return request<BookSummary>('/books/upload', {
      method: 'POST',
      body: formData,
    }, { timeoutMs: 120_000 });
  },
  getChapters: (bookId: string, options?: RequestOptions) => request<ChapterSummary[]>(`/books/${bookId}/chapters`, undefined, options),
  getChapterDetail: (bookId: string, chapterId: string, options?: RequestOptions) =>
    request<ChapterDetail>(`/books/${bookId}/chapters/${chapterId}`, undefined, options),
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
  getTaskStatus: (bookId: string, options?: RequestOptions) =>
    request<TaskStatusResponse>(`/tasks/status/${encodeURIComponent(bookId)}`, undefined, options),
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
  getGlossary: (bookId: string, options?: RequestOptions) => request<GlossaryResponse>(`/knowledge/${bookId}/glossary`, undefined, options),
  updateGlossary: (bookId: string, terms: GlossaryItem[]) =>
    request<GlossaryResponse>(`/knowledge/${bookId}/glossary`, {
      method: 'POST',
      body: JSON.stringify({ terms }),
    }),
  getMemory: (bookId: string, options?: RequestOptions) => request<BookMemoryResponse>(`/knowledge/${bookId}/memory`, undefined, options),
  getReports: (bookId: string, options?: RequestOptions) => request<ChapterReviewReport[]>(`/knowledge/${encodeURIComponent(bookId)}/reports`, undefined, options),
  getChapterReview: (bookId: string, chapterId: string, options?: RequestOptions) =>
    request<any>(`/knowledge/${encodeURIComponent(bookId)}/reviews/${encodeURIComponent(chapterId)}`, undefined, options),

  // System & Preflight & Prompts
  getConfig: (options?: RequestOptions) => request<SystemConfig>('/system/config', undefined, options),
  saveConfig: (config: SystemConfig) =>
    request<{ status: string }>('/system/config', {
      method: 'POST',
      body: JSON.stringify(config),
    }),
  runPreflight: () => request<PreflightResponse>('/system/preflight', { method: 'POST' }),
  getPrompts: (options?: RequestOptions) => request<PromptItem[]>('/system/prompts', undefined, options),
  getPromptDetail: (promptId: string, options?: RequestOptions) => request<PromptItem>(`/system/prompts/${encodeURIComponent(promptId)}`, undefined, options),
  savePrompt: (data: { filename: string; content: string }) =>
    request<{ status: string; id: string; message: string }>('/system/prompts', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  deletePrompt: (promptId: string) =>
    request<{ status: string; message: string }>(`/system/prompts/${encodeURIComponent(promptId)}`, {
      method: 'DELETE',
    }),

  // Queue Management
  getQueue: (options?: RequestOptions) => request<QueueStatusResponse>('/queue', undefined, options),
  enqueueBooks: (data: EnqueueRequest) =>
    request<QueueStatusResponse>('/queue/items', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  cancelQueueItem: (itemId: string) =>
    request<QueueStatusResponse>(`/queue/items/${encodeURIComponent(itemId)}`, {
      method: 'DELETE',
    }),
  retryQueueItem: (itemId: string) =>
    request<QueueStatusResponse>(`/queue/items/${encodeURIComponent(itemId)}/retry`, {
      method: 'POST',
    }),
  moveQueueItem: (itemId: string, direction: 'up' | 'down' | 'top') =>
    request<QueueStatusResponse>(`/queue/items/${encodeURIComponent(itemId)}/move`, {
      method: 'POST',
      body: JSON.stringify({ direction }),
    }),
  reorderQueue: (itemIds: string[]) =>
    request<QueueStatusResponse>('/queue/reorder', {
      method: 'POST',
      body: JSON.stringify({ item_ids: itemIds }),
    }),
  pauseQueue: () => request<QueueStatusResponse>('/queue/pause', { method: 'POST' }),
  resumeQueue: () => request<QueueStatusResponse>('/queue/resume', { method: 'POST' }),
  clearQueue: (scope: 'completed' | 'failed' | 'all_finished' = 'completed') =>
    request<QueueStatusResponse>('/queue/clear', {
      method: 'POST',
      body: JSON.stringify({ scope }),
    }),
  updateQueueConfig: (data: { concurrency?: number; stop_on_error?: boolean }) =>
    request<QueueStatusResponse>('/queue/config', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // SSE Stream
  subscribeEvents: (
    onEvent: (event: StreamEvent) => void,
    onState?: (state: 'live' | 'reconnecting' | 'offline') => void,
  ): (() => void) => {
    const eventSource = new EventSource(`${API_BASE}/events/stream`);

    const handleAnyEvent = (e: MessageEvent, eventName: string) => {
      try {
        const parsed = JSON.parse(e.data);
        const envelope = parsed?.event && parsed?.data ? parsed : {
          event: eventName,
          data: parsed,
          book_id: parsed?.book_id ?? null,
          timestamp: parsed?.timestamp || new Date().toISOString(),
          event_id: e.lastEventId || parsed?.event_id || '',
        };
        onEvent(envelope as StreamEvent);
      } catch (err) {
        console.warn('Error parsing SSE data:', err);
      }
    };

    const registeredEvents = [
      'connect',
      'pipeline_started',
      'pipeline_progress',
      'pipeline_phase_changed',
      'chapter_started',
      'batch_completed',
      'chapter_completed',
      'fallback_triggered',
      'review_completed',
      'finalizing',
      'pipeline_completed',
      'pipeline_failed',
      'pipeline_paused',
      'pipeline_resumed',
      'pipeline_stopped',
      'queue_updated',
      'queue_item_started',
      'queue_item_completed',
      'queue_item_failed',
      'queue_paused',
      'queue_resumed',
    ];

    registeredEvents.forEach((evt) => {
      eventSource.addEventListener(evt, (e) => handleAnyEvent(e as MessageEvent, evt));
    });

    eventSource.onmessage = (e) => handleAnyEvent(e, 'message');
    eventSource.onopen = () => onState?.('live');
    eventSource.onerror = (err) => {
      console.warn('SSE connection error:', err);
      onState?.(eventSource.readyState === EventSource.CLOSED ? 'offline' : 'reconnecting');
    };

    return () => {
      eventSource.close();
    };
  },
};
