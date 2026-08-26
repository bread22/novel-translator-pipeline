import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { SettingsView } from './SettingsView';

describe('settings initial-load recovery', () => {
  it('shows a retryable error when the initial config load fails', async () => {
    const getConfig = vi.spyOn(api, 'getConfig')
      .mockRejectedValueOnce(new Error('config offline'))
      .mockResolvedValueOnce({ paths: {}, providers: {}, roles: {} } as never);
    vi.spyOn(api, 'getPrompts').mockResolvedValue([]);

    render(<SettingsView />);
    await waitFor(() => expect(api.getConfig).toHaveBeenCalled());

    expect(screen.getByRole('button', { name: /重试|retry/i })).toBeInTheDocument();
    expect(screen.getByText(/配置加载失败|failed to load config/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /重试|retry/i }));
    await waitFor(() => expect(getConfig).toHaveBeenCalledTimes(2));
  });
});
