import { expect, Page, Route, test } from '@playwright/test';

const book = {
  id: 'book-1', name: 'Fixture Book', source_type: 'txt', total_chapters: 1, translated_chapters: 0,
  total_paragraphs: 1, translated_paragraphs: 0, progress_percentage: 0, status: 'pending', has_output_epub: false,
};
const queueItem = (id = 'item-1') => ({
  id, book_id: 'book-1', book_name: 'Fixture Book', source_type: 'txt', options: { book_id: 'book-1' },
  status: 'pending', order_index: 1, priority: 0, overall_progress: 0, current_chapter: '',
  current_chapter_index: 0, total_chapters: 1, message: 'waiting', enqueued_at: 'now', retry_count: 0,
  checkpoint: {}, process_id: 'fixture', recovery_reason: null,
});
const task = (status = 'idle') => ({
  task_id: 'task-1', book_id: 'book-1', status, overall_progress: status === 'completed' ? 1 : 0,
  current_chapter: 'c1', current_chapter_index: 0, total_chapters: 1, current_batch: 0,
  total_batches: 1, recovered_paragraphs: 0, message: status,
});

type FixtureState = {
  books: any[];
  queue: any;
  task: any;
  glossary: any[];
  deleted: boolean;
};

async function installFixture(page: Page, initial?: Partial<FixtureState>) {
  const state: FixtureState = {
    books: [{ ...book }],
    queue: { is_paused: true, concurrency: 1, total_items: 0, running_count: 0, pending_count: 0, completed_count: 0, failed_count: 0, items: [] },
    task: task(),
    glossary: [],
    deleted: false,
    ...initial,
  };
  await page.addInitScript(() => {
    class FixtureEventSource {
      static CLOSED = 2;
      readyState = 1;
      onopen: (() => void) | null = null;
      onerror: (() => void) | null = null;
      onmessage: (() => void) | null = null;
      constructor() { setTimeout(() => this.onopen?.(), 0); }
      addEventListener() {}
      close() { this.readyState = FixtureEventSource.CLOSED; }
    }
    Object.defineProperty(window, 'EventSource', { value: FixtureEventSource });
  });
  const reply = (route: Route, body: unknown, status = 200) => route.fulfill({
    status, contentType: 'application/json', body: JSON.stringify(body),
  });
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace('/api/v1', '');
    const method = request.method();
    if (path === '/books' && method === 'GET') return reply(route, state.books);
    if (path === '/books/upload' && method === 'POST') {
      const uploaded = { ...book, id: 'uploaded', name: 'Uploaded Fixture' };
      state.books.push(uploaded);
      return reply(route, uploaded);
    }
    if (path === '/queue' && method === 'GET') return reply(route, state.queue);
    if (path === '/queue/items' && method === 'POST') {
      const requestBody = request.postDataJSON();
      const ids: string[] = requestBody.book_ids;
      state.queue.items = ids.map((id, index) => ({ ...queueItem(`item-${id}`), id: `item-${id}`, book_id: id, book_name: state.books.find((entry) => entry.id === id)?.name || id, order_index: index + 1 }));
      state.queue.total_items = ids.length;
      state.queue.pending_count = ids.length;
      return reply(route, state.queue);
    }
    if (path === '/queue/resume' && method === 'POST') {
      state.queue.is_paused = false;
      state.queue.items = state.queue.items.map((entry: any) => ({ ...entry, status: 'running' }));
      state.queue.running_count = state.queue.items.length;
      state.queue.pending_count = 0;
      return reply(route, state.queue);
    }
    if (path === '/queue/pause' && method === 'POST') {
      state.queue.is_paused = true;
      return reply(route, state.queue);
    }
    if (path.startsWith('/tasks/status/')) return reply(route, state.task);
    if (path === '/tasks/pipeline/start') { state.task = task('running'); return reply(route, state.task); }
    if (path === '/tasks/pipeline/pause') { state.task = task('paused'); return reply(route, state.task); }
    if (path === '/tasks/pipeline/resume') { state.task = task('running'); return reply(route, state.task); }
    if (path === '/books/book-1/chapters') return reply(route, [{ id: 'c1', index: 1, title: 'Chapter One', total_paragraphs: 1, translated_paragraphs: 1, status: 'reviewed', auto_fixed_count: 0 }]);
    if (path === '/books/book-1/chapters/c1') return reply(route, {
      id: 'c1', index: 1, title: 'Chapter One', total_paragraphs: 1, translated_paragraphs: 1,
      status: 'completed', chapter_summary: 'done', auto_fixed_count: 0,
      paragraphs: [{ id: 'p1', index: 0, chapter_id: 'c1', source: '原文', translated: '旧译', status: 'translated' }],
    });
    if (path === '/knowledge/book-1/reviews/c1') return reply(route, { status: 'not_found', fixes: [] });
    if (path === '/books/book-1/paragraphs/p1' && method === 'PUT') return reply(route, { status: 'ok' });
    if (path === '/knowledge/book-1/glossary' && method === 'GET') return reply(route, { book_id: 'book-1', terms: state.glossary, conflicts: [] });
    if (path === '/knowledge/book-1/glossary' && method === 'POST') {
      const item = request.postDataJSON().terms[0];
      state.glossary = [{ ...item, first_seen_chunk: 'c1', last_seen_chunk: 'c1', occurrences: 1, sample_ids: ['p1'] }];
      return reply(route, { book_id: 'book-1', terms: state.glossary, conflicts: [] });
    }
    if (path === '/knowledge/book-1/memory') return reply(route, { book_id: 'book-1', characters: [], world_settings: [], timeline: [], chapter_states: [] });
    if (path === '/knowledge/book-1/reports') return reply(route, []);
    if (path === '/system/config' && method === 'GET') return reply(route, { roles: { primary_translator: 'mock' }, providers: { mock: { type: 'openai', model: 'fixture', api_key_configured: true, api_key_preview: '••••test' } }, paths: {} });
    if (path === '/system/prompts') return reply(route, []);
    if (path === '/books/book-1/export') return reply(route, { status: 'exported', download_url: '/download' });
    if (path === '/books/book-1' && method === 'DELETE') {
      state.deleted = true;
      state.books = [];
      return reply(route, { status: 'ok', message: 'deleted' });
    }
    return reply(route, { detail: 'fixture route missing' }, 404);
  });
  return state;
}

test('upload, enqueue, keyboard controls, and queue lifecycle use the mock server state flow', async ({ page }) => {
  await installFixture(page);
  await page.goto('/#/queue');
  await expect(page.getByText('Fixture Book', { exact: true })).toBeVisible();
  await page.locator('input[type=file]').setInputFiles({ name: 'fixture.txt', mimeType: 'text/plain', buffer: Buffer.from('chapter') });
  await expect(page.getByText(/Uploaded Fixture.*导入成功/)).toBeVisible();
  await page.getByRole('button', { name: '加入队列', exact: true }).first().press('Enter');
  await expect(page.getByText(/排队 #1/).first()).toBeVisible();
  await page.getByRole('button', { name: '启动队列' }).first().click();
  await expect(page.getByText('正在翻译中').first()).toBeVisible();
  await page.getByRole('button', { name: /暂停调度/ }).click();
  await expect(page.getByText(/待命暂停/).first()).toBeVisible();
});

test('pipeline start, pause, and resume render their confirmed task state', async ({ page }) => {
  await installFixture(page);
  await page.goto('/#/studio');
  await page.getByRole('button', { name: '启动全自动流水线' }).click();
  await expect(page.getByRole('button', { name: '暂停' })).toBeVisible();
  await page.getByRole('button', { name: '暂停' }).click();
  await expect(page.getByRole('button', { name: '继续流水线' })).toBeVisible();
  await page.getByRole('button', { name: '继续流水线' }).click();
  await expect(page.getByRole('button', { name: '终止' })).toBeVisible();
});

test('reader edit and glossary increment complete without losing server metadata', async ({ page }) => {
  await installFixture(page);
  await page.goto('/#/reader');
  await expect(page.getByText('Chapter One').first()).toBeVisible();
  await page.getByRole('button', { name: '编辑' }).click();
  await page.locator('textarea').fill('新译');
  await page.getByRole('button', { name: '保存修改' }).click();
  await expect(page.getByText('新译')).toBeVisible();
  await page.goto('/#/knowledge');
  await page.getByRole('button', { name: /术语表/ }).click();
  await page.getByRole('button', { name: '添加自定义术语' }).click();
  await page.getByLabel(/日文原文/).fill('名前');
  await page.getByLabel(/中文统一译名/).fill('名字');
  await page.getByRole('button', { name: '保存术语' }).click();
  await expect(page.getByText('名字')).toBeVisible();
});

test('completed book exports and is removed after confirmation', async ({ page }) => {
  const completed = { ...book, status: 'completed', translated_chapters: 1, translated_paragraphs: 1, progress_percentage: 1, has_output_epub: true };
  const state = await installFixture(page, { books: [completed] });
  await page.goto('/#/queue');
  await page.getByRole('button', { name: '导出EPUB' }).click();
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: '删除' }).click();
  await expect(page.getByText('Fixture Book', { exact: true })).toHaveCount(0);
  expect(state.deleted).toBe(true);
});
