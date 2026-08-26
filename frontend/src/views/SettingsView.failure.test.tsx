import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { SettingsView } from './SettingsView';

describe('settings initial-load recovery', () => {
  it.fails('shows a retryable error when the initial config load fails', async () => {
    vi.spyOn(api, 'getConfig').mockRejectedValue(new Error('config offline'));
    vi.spyOn(api, 'getPrompts').mockResolvedValue([]);

    render(<SettingsView />);
    await waitFor(() => expect(api.getConfig).toHaveBeenCalled());

    expect(screen.getByRole('button', { name: /重试|retry/i })).toBeInTheDocument();
    expect(screen.getByText(/配置加载失败|failed to load config/i)).toBeInTheDocument();
  });
});
