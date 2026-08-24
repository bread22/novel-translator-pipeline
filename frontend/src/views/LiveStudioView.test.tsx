import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { LiveStudioView } from './LiveStudioView';

describe('live model topology', () => {
  it('puts translators on standby while the reviewer is working', async () => {
    Element.prototype.scrollIntoView = vi.fn();
    vi.spyOn(api, 'getConfig').mockResolvedValue({
      roles: {
        primary_translator: 'primary',
        fallback_translators: ['fallback-1', 'fallback-2'],
        reviewer: 'reviewer',
        dual_review: false,
      },
      providers: {},
    } as never);
    vi.spyOn(api, 'getPrompts').mockResolvedValue([]);

    render(<LiveStudioView
      book={{
        id: 'book', name: 'Book', source_type: 'epub', total_chapters: 1,
        translated_chapters: 0, total_paragraphs: 2, translated_paragraphs: 2,
        progress_percentage: 1,
      } as never}
      activeTask={{
        task_id: 'task', book_id: 'book', status: 'running', phase: 'reviewing',
        overall_progress: 1, current_chapter: 'c0001', current_chapter_index: 1,
        total_chapters: 1, current_batch: 1, total_batches: 1,
        recovered_paragraphs: 0, message: '正在审阅第 1/1 章：c0001',
      }}
      streamEvents={[]}
      onRefreshTask={vi.fn(async () => undefined)}
      onRefreshBooks={vi.fn(async () => undefined)}
    />);

    const primary = screen.getByText('PRIMARY (主译)').parentElement;
    expect(primary).not.toBeNull();
    expect(within(primary!).getByText('STANDBY')).toBeInTheDocument();
    expect(screen.queryByText('● TRANSLATING')).not.toBeInTheDocument();
    expect(screen.getByText('● 正在执行错译与术语审计')).toBeInTheDocument();
  });
});
