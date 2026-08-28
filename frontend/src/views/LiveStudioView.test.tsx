import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
        secondary_reviewer: 'reviewer-2',
        dual_review: true,
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
        reviewer_states: { primary: 'completed', secondary: 'reviewing' },
        reviewer_details: {
          primary: { status: 'completed', backend: 'reviewer', attempt: 1, chunk_index: 1, total_chunks: 2 },
          secondary: {
            status: 'reviewing', backend: 'reviewer-fallback', attempt: 3,
            candidate_index: 2, candidate_total: 2, chunk_index: 1, total_chunks: 2,
            split_depth: 1, split_path: 'root.L', timeout_seconds: 360,
          },
        },
      }}
      streamEvents={[{
        event: 'pipeline_reviewer_status', timestamp: '2026-08-24T12:00:00Z', event_id: 'evt-1',
        data: {
          task_id: 'task', message: '副审 reviewer-fallback 审阅中', reviewer_role: 'secondary',
          reviewer_backend: 'reviewer-fallback', reviewer_status: 'reviewing', attempt: 3,
          candidate_index: 2, candidate_total: 2, chunk_index: 1, total_chunks: 2,
          split_path: 'root.L', timeout_seconds: 360,
        },
      }]}
      onRefreshTask={vi.fn(async () => undefined)}
      onRefreshBooks={vi.fn(async () => undefined)}
    />);

    const primary = screen.getByText('PRIMARY (主译)').parentElement;
    expect(primary).not.toBeNull();
    expect(within(primary!).getByText('STANDBY')).toBeInTheDocument();
    expect(screen.queryByText('● TRANSLATING')).not.toBeInTheDocument();
    const primaryReviewer = screen.getByText('REVIEWER #1 (主审)').parentElement;
    const secondaryReviewer = screen.getByText('REVIEWER #2 (副审)').parentElement;
    expect(primaryReviewer).not.toBeNull();
    expect(secondaryReviewer).not.toBeNull();
    expect(within(primaryReviewer!).getByText('✓ COMPLETED')).toBeInTheDocument();
    expect(within(secondaryReviewer!).getByText('● REVIEWING')).toBeInTheDocument();
    const secondaryReviewerCard = secondaryReviewer!.parentElement;
    expect(within(secondaryReviewerCard!).getAllByText('reviewer-fallback').length).toBeGreaterThan(0);
    expect(within(secondaryReviewerCard!).getByText('分块 1/2 · 尝试 #3 · 路由 2/2 · 子段 root.L')).toBeInTheDocument();
    expect(screen.getByText(/后端/).textContent).toContain('reviewer-fallback');
    expect(screen.getByText(/后端/).textContent).toContain('尝试 #3');
    expect(screen.getByText(/后端/).textContent).toContain('超时 360s');
  });

  it('labels a reviewer that has not started the current chapter as pending', async () => {
    Element.prototype.scrollIntoView = vi.fn();
    vi.spyOn(api, 'getConfig').mockResolvedValue({
      roles: { reviewer: 'reviewer-1', secondary_reviewer: 'reviewer-2', dual_review: true },
      providers: {},
    } as never);
    vi.spyOn(api, 'getPrompts').mockResolvedValue([]);

    render(<LiveStudioView
      book={{ id: 'book', name: 'Book', source_type: 'epub', total_chapters: 1,
        translated_chapters: 0, total_paragraphs: 1, translated_paragraphs: 1,
        progress_percentage: 1 } as never}
      activeTask={{ task_id: 'task', book_id: 'book', status: 'running', phase: 'reviewing',
        overall_progress: 1, current_chapter: 'c0001', current_chapter_index: 1,
        total_chapters: 1, current_batch: 0, total_batches: 0, recovered_paragraphs: 0,
        message: '正在审阅', reviewer_states: { primary: 'reviewing', secondary: 'standby' } }}
      streamEvents={[]}
      onRefreshTask={vi.fn(async () => undefined)}
      onRefreshBooks={vi.fn(async () => undefined)}
    />);

    const secondaryReviewer = screen.getByText('REVIEWER #2 (副审)').parentElement;
    expect(secondaryReviewer).not.toBeNull();
    expect(within(secondaryReviewer!).getByText('PENDING')).toBeInTheDocument();
  });

  it('does not start with a policy that failed to persist globally', async () => {
    Element.prototype.scrollIntoView = vi.fn();
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.spyOn(api, 'getConfig').mockResolvedValue({
      paths: { translation_policy: 'old-policy.md' },
      providers: {},
    } as never);
    vi.spyOn(api, 'getPrompts').mockResolvedValue([
      { path: 'old-policy.md', name: 'Old policy', type: 'translation' },
      { path: 'new-policy.md', name: 'New policy', type: 'translation' },
    ] as never);
    vi.spyOn(api, 'saveConfig').mockRejectedValue(new Error('config write failed'));
    const startPipeline = vi.spyOn(api, 'startPipeline').mockResolvedValue({} as never);
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => undefined);

    render(<LiveStudioView
      book={{ id: 'book', name: 'Book', source_type: 'epub', total_chapters: 1,
        translated_chapters: 0, total_paragraphs: 1, translated_paragraphs: 0,
        progress_percentage: 0 } as never}
      activeTask={null}
      streamEvents={[]}
      onRefreshTask={vi.fn(async () => undefined)}
      onRefreshBooks={vi.fn(async () => undefined)}
    />);

    const selects = await screen.findAllByRole('combobox');
    const policySelect = selects[1];
    fireEvent.change(policySelect, { target: { value: 'new-policy.md' } });
    await waitFor(() => expect(api.saveConfig).toHaveBeenCalled());

    await waitFor(() => expect(policySelect).toHaveValue('old-policy.md'));
    expect(consoleError).toHaveBeenCalledWith(
      'Failed to sync policy to server config:',
      expect.objectContaining({ message: 'config write failed' }),
    );
    fireEvent.click(screen.getByRole('button', { name: '启动全自动流水线' }));
    await waitFor(() => expect(startPipeline).toHaveBeenCalled());
    expect(startPipeline.mock.calls[0][0]).toMatchObject({ translation_policy: 'old-policy.md' });
    expect(alertSpy).not.toHaveBeenCalled();
  });
});
