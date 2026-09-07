import { act, fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { api } from './lib/api';
import { ReaderView } from './views/ReaderView';

it('ignores late retranslation after switching books', async () => {
  let resolve!: (value: any) => void;
  const pending = new Promise<any>((done) => { resolve = done; });
  const chapter = (id: string) => ({id: 'c1', index: 1, title: `${id} chapter`, total_paragraphs: 1, translated_paragraphs: 1, status: 'translated'});
  vi.spyOn(api, 'getChapters').mockImplementation(async (id) => [chapter(id)] as any);
  vi.spyOn(api, 'getChapterDetail').mockImplementation(async (id) => ({
    ...chapter(id), chapter_summary: '', paragraphs: [{id: 'p1', index: 0, chapter_id: 'c1', source: `${id} SOURCE`, translated: `${id} OLD`, status: 'translated'}],
  }) as any);
  vi.spyOn(api, 'getChapterReview').mockResolvedValue(null);
  vi.spyOn(api, 'retranslateParagraph').mockReturnValue(pending);
  const view = render(<ReaderView book={{id: 'A', name: 'A'} as any} />);
  await screen.findByText('A OLD');
  fireEvent.click(screen.getByText('重译'));
  view.rerender(<ReaderView book={{id: 'B', name: 'B'} as any} />);
  await screen.findByText('B OLD');
  await act(async () => { resolve({status: 'ok', translated: 'A LATE TRANSLATION'}); });
  expect(screen.queryByText('A LATE TRANSLATION')).not.toBeInTheDocument();
  expect(screen.getByText('B OLD')).toBeInTheDocument();
});
