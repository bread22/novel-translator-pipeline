import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { SettingsView } from './SettingsView';

describe('local agent provider executable paths', () => {
  afterEach(() => vi.restoreAllMocks());

  it('shows and edits the configured path for every local CLI provider type', async () => {
    vi.spyOn(api, 'getConfig').mockResolvedValue({
      paths: {},
      roles: {},
      providers: {
        ag: { type: 'antigravity', model: 'gemini', agy: '/opt/bin/agy' },
        cx: { type: 'codex', model: '', binary: '/opt/bin/codex' },
        oc: { type: 'opencode', model: 'muse', binary: '/opt/bin/opencode' },
      },
    });
    vi.spyOn(api, 'getPrompts').mockResolvedValue([]);

    render(<SettingsView />);

    await waitFor(() => expect(screen.getByDisplayValue('/opt/bin/agy')).toBeInTheDocument());
    expect(screen.getByDisplayValue('/opt/bin/codex')).toHaveAttribute('pattern', '/.+');
    expect(screen.getByDisplayValue('/opt/bin/opencode')).toHaveAttribute('required');
  });

  it('requires an absolute executable path when adding a local provider', async () => {
    vi.spyOn(api, 'getConfig').mockResolvedValue({ paths: {}, roles: {}, providers: {} });
    vi.spyOn(api, 'getPrompts').mockResolvedValue([]);

    render(<SettingsView />);
    await waitFor(() => expect(api.getConfig).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: '添加 Provider' }));
    fireEvent.change(screen.getByLabelText('Provider 类型'), { target: { value: 'codex' } });

    const pathInput = screen.getByLabelText('可执行文件绝对路径 (binary)');
    expect(pathInput).toBeRequired();
    expect(pathInput).toHaveAttribute('pattern', '/.+');
    expect(pathInput).toHaveAttribute('placeholder', '/absolute/path/to/codex');
  });
});
