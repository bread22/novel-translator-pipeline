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
const AUTH_TOKEN_STORAGE_KEY = 'web_auth_token';

export function getAuthToken(): string | null {
  if (typeof window === 'undefined') return null;

  const queryToken = new URLSearchParams(window.location.search).get('access_token');
  if (queryToken) {
    try {
      sessionStorage.setItem(AUTH_TOKEN_STORAGE_KEY, queryToken);
      const cleanUrl = new URL(window.location.href);
      cleanUrl.searchParams.delete('access_token');
      window.history.replaceState(null, '', `${cleanUrl.pathname}${cleanUrl.search}${cleanUrl.hash}`);
    } catch {
      // Storage/history can be unavailable in locked-down browser contexts.
    }
    return queryToken;
  }

  try {
    return sessionStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function withAuthQuery(path: string): string {
  const token = getAuthToken();
  if (!token || typeof window === 'undefined') return path;

  const url = new URL(path, window.location.origin);
  if (url.origin !== window.location.origin) return path;
  url.searchParams.set('access_token', token);
  return url.href;
}

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
    const headers = new Headers(options?.headers);
    if (!(options?.body instanceof FormData) && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }
    const token = getAuthToken();
    if (token && !headers.has('Authorization')) {
      headers.set('Authorization', `Bearer ${token}`);
    }
    const response = await fetch(url, {
      ...options,
      signal,
      headers,
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
    }, { timeoutMs: 120_000 }),
  resetBook: (bookId: string) =>
    request<{ status: string; message: string }>(`/books/${bookId}/reset`, {
      method: 'POST',
    }),
  deleteBook: (bookId: string) =>
    request<{ status: string; message: string }>(`/books/${bookId}`, {
      method: 'DELETE',
    }),
  getBookEvents: (bookId: string, limit: number = 500, options?: RequestOptions) =>
    request<StreamEvent[]>(`/books/${bookId}/events?limit=${limit}`, undefined, options),

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
  // Provider probes may take up to two minutes (remote health checks enforce
  // a 120s floor), so keep the browser request alive long enough to collect
  // the complete result instead of reporting a client-side timeout at 30s.
  runPreflight: () => request<PreflightResponse>('/system/preflight', { method: 'POST' }, { timeoutMs: 180_000 }),
  testKnowledgeExtractor: () => request<any>('/system/knowledge-extractor/test', { method: 'POST' }, { timeoutMs: 180_000 }),
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
    let eventSource: EventSource | null = null;
    let isSubscribed = true;
    let reconnectTimeout: any = null;

    const setupConnection = () => {
      if (!isSubscribed) return;
      try {
        eventSource = new EventSource(withAuthQuery(`${API_BASE}/events/stream`));

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
          'pipeline_reviewer_status',
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
          eventSource!.addEventListener(evt, (e) => handleAnyEvent(e as MessageEvent, evt));
        });

        eventSource.onmessage = (e) => handleAnyEvent(e, 'message');
        eventSource.onopen = () => onState?.('live');
        eventSource.onerror = (err) => {
          console.warn('SSE connection error:', err);
          if (eventSource?.readyState === EventSource.CLOSED) {
            onState?.('offline');
            if (isSubscribed) {
              clearTimeout(reconnectTimeout);
              reconnectTimeout = setTimeout(setupConnection, 2000);
            }
          } else {
            onState?.('reconnecting');
          }
        };
      } catch (err) {
        console.error('Failed to initialize EventSource:', err);
        onState?.('offline');
        if (isSubscribed) {
          clearTimeout(reconnectTimeout);
          reconnectTimeout = setTimeout(setupConnection, 3000);
        }
      }
    };

    setupConnection();

    return () => {
      isSubscribed = false;
      clearTimeout(reconnectTimeout);
      eventSource?.close();
    };
  },
};
