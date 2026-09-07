import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { api } from './lib/api';
import { ReaderView } from './views/ReaderView';

const deferred = () => {
  let resolve!: (value: any) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<any>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
};

const chapter = (book: string, id: string, index: number) => ({
  id, index, title: `${book} ${id} chapter`, total_paragraphs: 1, translated_paragraphs: 1, status: 'translated',
});

function mockReading() {
  vi.spyOn(api, 'getChapters').mockImplementation(async (book) => [chapter(book, 'c1', 1), chapter(book, 'c2', 2)] as any);
  vi.spyOn(api, 'getChapterDetail').mockImplementation(async (book, id) => ({
    ...chapter(book, id, id === 'c1' ? 1 : 2), chapter_summary: '',
    paragraphs: [{ id: 'p1', index: 0, chapter_id: id, source: `${book} ${id} SOURCE`, translated: `${book} ${id} OLD`, status: 'translated' }],
  }) as any);
  vi.spyOn(api, 'getChapterReview').mockResolvedValue(null);
}

function startMutation(kind: 'save' | 'retranslate') {
  if (kind === 'retranslate') {
    fireEvent.click(screen.getByText('重译'));
  } else {
    fireEvent.click(screen.getByText('编辑'));
    fireEvent.change(screen.getByDisplayValue('A c1 OLD'), { target: { value: 'A LATE TRANSLATION' } });
    fireEvent.click(screen.getByText('保存修改'));
  }
}

describe.each(['save', 'retranslate'] as const)('late %s response', (kind) => {
  it.each([
    ['book', 'success'], ['book', 'failure'], ['chapter', 'success'], ['chapter', 'failure'],
  ] as const)('ignores %s-switch stale %s', async (navigation, outcome) => {
    mockReading();
    const pending = deferred();
    vi.spyOn(api, 'retranslateParagraph').mockReturnValue(pending.promise);
    vi.spyOn(api, 'updateParagraph').mockReturnValue(pending.promise);
    const alert = vi.spyOn(window, 'alert').mockImplementation(() => {});
    const view = render(<ReaderView book={{ id: 'A', name: 'A' } as any} />);
    await screen.findByText('A c1 OLD');
    startMutation(kind);
    let expected: string;
    if (navigation === 'book') {
      view.rerender(<ReaderView book={{ id: 'B', name: 'B' } as any} />);
      expected = 'B c1 OLD';
    } else {
      fireEvent.click(screen.getByRole('button', { name: /A c2 chapter/ }));
      expected = 'A c2 OLD';
    }
    await screen.findByText(expected);
    await act(async () => {
      if (outcome === 'success') pending.resolve({ status: 'ok', translated: 'A LATE TRANSLATION' });
      else pending.reject(new Error('old request failure'));
    });
    expect(screen.queryByText('A LATE TRANSLATION')).not.toBeInTheDocument();
    expect(screen.getByText(expected)).toBeInTheDocument();
    expect(alert).not.toHaveBeenCalled();
  });

  it('ignores an old response after navigating away and back to the same book', async () => {
    mockReading();
    const pending = deferred();
    vi.spyOn(api, 'retranslateParagraph').mockReturnValue(pending.promise);
    vi.spyOn(api, 'updateParagraph').mockReturnValue(pending.promise);
    const view = render(<ReaderView book={{ id: 'A', name: 'A' } as any} />);
    await screen.findByText('A c1 OLD');
    startMutation(kind);
    view.rerender(<ReaderView book={{ id: 'B', name: 'B' } as any} />);
    await screen.findByText('B c1 OLD');
    view.rerender(<ReaderView book={{ id: 'A', name: 'A' } as any} />);
    await screen.findByText('A c1 OLD');
    await act(async () => pending.resolve({ status: 'ok', translated: 'A LATE TRANSLATION' }));
    expect(screen.queryByText('A LATE TRANSLATION')).not.toBeInTheDocument();
    expect(screen.getByText('A c1 OLD')).toBeInTheDocument();
  });
});
