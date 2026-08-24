import { BookSummary, QueueStatusResponse, TaskStatusResponse } from './types/api';

export interface AppServerState {
  books: BookSummary[];
  queue: QueueStatusResponse | null;
  task: TaskStatusResponse | null;
}

export type AppServerAction =
  | { type: 'books'; value: BookSummary[] }
  | { type: 'queue'; value: QueueStatusResponse | null }
  | { type: 'task'; value: TaskStatusResponse | null };

export const initialAppServerState: AppServerState = { books: [], queue: null, task: null };

export const appServerReducer = (state: AppServerState, action: AppServerAction): AppServerState => {
  switch (action.type) {
    case 'books': return { ...state, books: action.value };
    case 'queue': return { ...state, queue: action.value };
    case 'task': return { ...state, task: action.value };
  }
};
