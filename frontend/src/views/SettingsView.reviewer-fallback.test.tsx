import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { SettingsView } from './SettingsView';

describe('reviewer fallback routing', () => {
  afterEach(() => vi.restoreAllMocks());

  it('edits reviewer fallbacks while dual review is disabled', async () => {
    vi.spyOn(api, 'getConfig').mockResolvedValue({
      paths: {},
      roles: {
        reviewer: 'reviewer-primary',
        dual_review: false,
        fallback_reviewers: [],
      },
      providers: {
        'reviewer-primary': { type: 'openai', model: 'primary-model' },
        'reviewer-fallback': { type: 'openai', model: 'fallback-model' },
      },
    });
    vi.spyOn(api, 'getPrompts').mockResolvedValue([]);
    const saveConfig = vi.spyOn(api, 'saveConfig').mockResolvedValue({ status: 'ok' });

    render(<SettingsView />);

    const fallbackSelect = await screen.findByLabelText('备用审阅 #1');
    expect(screen.queryByLabelText('副审模型 (Secondary Reviewer)')).not.toBeInTheDocument();
    fireEvent.change(fallbackSelect, { target: { value: 'reviewer-fallback' } });
    fireEvent.click(screen.getByRole('button', { name: /保存配置/ }));

    await waitFor(() => expect(saveConfig).toHaveBeenCalledTimes(1));
    expect(saveConfig.mock.calls[0][0].roles?.fallback_reviewers).toEqual(['reviewer-fallback']);
  });

  it('edits knowledge extractor fallback providers', async () => {
    vi.spyOn(api, 'getConfig').mockResolvedValue({
      paths: {},
      roles: { reviewer: 'reviewer-primary', dual_review: false, fallback_reviewers: [] },
      providers: {
        'extractor-primary': { type: 'openai', model: 'primary-model' },
        'extractor-fallback': { type: 'openai', model: 'fallback-model' },
      },
      knowledge_extractor: {
        enabled: true,
        provider: 'extractor-primary',
        fallback_providers: [],
      },
    });
    vi.spyOn(api, 'getPrompts').mockResolvedValue([]);
    const saveConfig = vi.spyOn(api, 'saveConfig').mockResolvedValue({ status: 'ok' });

    render(<SettingsView />);

    const fallbackSelect = await screen.findByLabelText('备用提取器 #1');
    fireEvent.change(fallbackSelect, { target: { value: 'extractor-fallback' } });
    fireEvent.click(screen.getByRole('button', { name: /保存配置/ }));

    await waitFor(() => expect(saveConfig).toHaveBeenCalledTimes(1));
    expect(saveConfig.mock.calls[0][0].knowledge_extractor?.fallback_providers).toEqual(['extractor-fallback']);
  });
});
