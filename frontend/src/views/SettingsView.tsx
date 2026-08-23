import React, { useState, useEffect } from 'react';
import {
  Zap,
  CheckCircle2,
  XCircle,
  RotateCw,
  Save,
  Layers,
  Sparkles,
  Sliders,
  Server,
  Plus,
  Trash2,
  Eye,
  EyeOff,
  Key,
} from 'lucide-react';
import { PreflightProviderResult, PreflightResponse, SystemConfig } from '../types/api';
import { api } from '../lib/api';

export const SettingsView: React.FC = () => {
  const [config, setConfig] = useState<SystemConfig | null>(null);
  const [preflightData, setPreflightData] = useState<PreflightResponse | null>(null);
  const [isRunningPreflight, setIsRunningPreflight] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);

  // Show/Hide API Key toggles by provider name
  const [showKeyMap, setShowKeyMap] = useState<Record<string, boolean>>({});

  // Add Provider Modal State
  const [showAddProviderModal, setShowAddProviderModal] = useState(false);
  const [newProviderId, setNewProviderId] = useState('');
  const [newProviderType, setNewProviderType] = useState('openai');
  const [newBaseUrl, setNewBaseUrl] = useState('https://api.deepseek.com/v1');
  const [newModel, setNewModel] = useState('deepseek-chat');
  const [newApiKey, setNewApiKey] = useState('');
  const [newTemperature, setNewTemperature] = useState(0.3);
  const [newContextTokens, setNewContextTokens] = useState(32768);

  useEffect(() => {
    loadConfig();
  }, []);

  const loadConfig = async () => {
    try {
      const cfg = await api.getConfig();
      setConfig(cfg);
    } catch (err: any) {
      console.error('Failed to load config:', err);
    }
  };

  const runPreflightTest = async () => {
    setIsRunningPreflight(true);
    try {
      const res = await api.runPreflight();
      setPreflightData(res);
    } catch (err: any) {
      console.error('Preflight error:', err);
      alert(`预检请求异常: ${err.message || err}`);
    } finally {
      setIsRunningPreflight(false);
    }
  };

  const handleSaveConfig = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!config) return;
    setIsSaving(true);
    try {
      await api.saveConfig(config);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
      runPreflightTest();
    } catch (err: any) {
      alert(`保存配置失败: ${err.message}`);
    } finally {
      setIsSaving(false);
    }
  };

  const providersList = config ? Object.keys(config.providers || {}) : [];

  // Helper for fallbacks
  const currentFallbacks = config?.roles?.fallback_translators || [
    config?.roles?.fallback_translator || '',
    config?.roles?.secondary_fallback_translator || '',
  ].filter(Boolean);

  const fb1 = currentFallbacks[0] || '';
  const fb2 = currentFallbacks[1] || '';

  const handleSetFallback = (index: number, providerName: string) => {
    if (!config) return;
    const newFallbacks = [...currentFallbacks];
    if (providerName) {
      newFallbacks[index] = providerName;
    } else {
      newFallbacks.splice(index, 1);
    }
    const cleanFallbacks = newFallbacks.filter(Boolean);
    setConfig({
      ...config,
      roles: {
        ...config.roles,
        fallback_translators: cleanFallbacks,
        fallback_translator: cleanFallbacks[0] || '',
        secondary_fallback_translator: cleanFallbacks[1] || '',
      },
    });
  };

  // Provider modification
  const handleUpdateProvider = (providerId: string, field: string, value: any) => {
    if (!config || !config.providers || !config.providers[providerId]) return;
    setConfig({
      ...config,
      providers: {
        ...config.providers,
        [providerId]: {
          ...config.providers[providerId],
          [field]: value,
        },
      },
    });
  };

  // Provider deletion
  const handleDeleteProvider = (providerId: string) => {
    if (!config || !config.providers) return;
    if (config.roles?.primary_translator === providerId) {
      alert(`无法删除 "${providerId}"，因为它当前被设为主译模型。请先更改主译。`);
      return;
    }
    if (config.roles?.reviewer === providerId) {
      alert(`无法删除 "${providerId}"，因为它当前被设为一致性审阅模型。请先更改主审。`);
      return;
    }
    if (!confirm(`确定要删除 Provider "${providerId}" 吗？`)) return;

    const newProviders = { ...config.providers };
    delete newProviders[providerId];

    // Clean from fallbacks
    const newFallbacks = (config.roles?.fallback_translators || []).filter((id) => id !== providerId);

    setConfig({
      ...config,
      roles: {
        ...config.roles,
        fallback_translators: newFallbacks,
        fallback_translator: newFallbacks[0] || '',
        secondary_fallback_translator: newFallbacks[1] || '',
        secondary_reviewer:
          config.roles?.secondary_reviewer === providerId ? '' : config.roles?.secondary_reviewer,
      },
      providers: newProviders,
    });
  };

  // Add new provider submit
  const handleAddProviderSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const id = newProviderId.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '_');
    if (!id) {
      alert('请输入有效的 Provider ID');
      return;
    }
    if (config?.providers && config.providers[id]) {
      alert(`Provider "${id}" 已存在，请使用其他名称`);
      return;
    }

    const providerObj: any = {
      type: newProviderType,
      model: newModel.trim(),
    };

    if (newProviderType === 'openai') {
      providerObj.base_url = newBaseUrl.trim();
      providerObj.api_key = newApiKey.trim();
      providerObj.temperature = Number(newTemperature);
      providerObj.context_tokens = Number(newContextTokens);
      providerObj.timeout = 600;
    } else if (newProviderType === 'antigravity') {
      providerObj.agy = 'agy';
      providerObj.effort = 'low';
      providerObj.timeout = 600;
    } else if (newProviderType === 'opencode') {
      providerObj.binary = 'opencode';
      providerObj.agent = '';
      providerObj.timeout = 600;
    } else if (newProviderType === 'codex') {
      providerObj.timeout = 600;
    }

    setConfig({
      ...config,
      providers: {
        ...(config?.providers || {}),
        [id]: providerObj,
      },
    });

    setShowAddProviderModal(false);
    setNewProviderId('');
    setNewApiKey('');
  };

  return (
    <div className="space-y-8 max-w-5xl mx-auto pb-20">
      {/* Top Banner */}
      <div className="bg-slate-900/90 border border-slate-800 p-6 rounded-2xl flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 shadow-xl">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-indigo-950/80 border border-indigo-800/50 text-indigo-300">
              System & Preflight
            </span>
            <h2 className="text-xl font-bold text-white tracking-tight">AI 模型路由、API Key 与双审阅配置</h2>
          </div>
          <p className="text-xs text-slate-400 mt-1">
            管理所有 OpenAI 兼容 API、本地模型与 CLI 后端，配置两级容灾备用（Fallback）与双模型一致性审阅。
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowAddProviderModal(true)}
            className="flex items-center gap-1.5 px-4 py-2.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold border border-slate-700 transition-all cursor-pointer"
          >
            <Plus className="w-4 h-4 text-indigo-400" />
            添加 Provider
          </button>

          <button
            onClick={runPreflightTest}
            disabled={isRunningPreflight}
            className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-lg shadow-indigo-600/30 transition-all hover:scale-105 active:scale-95 disabled:opacity-50 cursor-pointer"
          >
            <RotateCw className={`w-4 h-4 ${isRunningPreflight ? 'animate-spin' : ''}`} />
            {isRunningPreflight ? '正在并发探测 (5s)...' : '一键连通性测试'}
          </button>
        </div>
      </div>

      {/* Preflight Diagnostics Section */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-bold text-slate-200 flex items-center gap-2">
            <Zap className="w-4 h-4 text-amber-400" />
            Provider 连通性预检指标
          </h3>
          {preflightData && (
            <span
              className={`text-xs font-mono px-2.5 py-0.5 rounded-full border ${
                preflightData.all_passed
                  ? 'bg-emerald-950/60 border-emerald-800/60 text-emerald-400'
                  : 'bg-amber-950/60 border-amber-800/60 text-amber-400'
              }`}
            >
              {preflightData.all_passed ? '✓ ACTIVE ROLES READY' : 'SOME PROVIDERS NOT READY'}
            </span>
          )}
        </div>

        {!preflightData && !isRunningPreflight ? (
          <div className="bg-slate-900/60 border border-slate-800/80 rounded-2xl p-5 text-center text-xs text-slate-400 space-y-1">
            <Server className="w-6 h-6 text-slate-600 mx-auto mb-1" />
            <p>点击上方【一键连通性测试】，将并发探测已配置各 AI 模型的网络延迟与响应契约。</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {preflightData?.results.map((res: PreflightProviderResult, idx: number) => {
              const isOk = res.status === 'ok';

              return (
                <div
                  key={idx}
                  className={`p-4 rounded-2xl border transition-all ${
                    isOk
                      ? 'bg-slate-900/80 border-slate-800 hover:border-emerald-800/50'
                      : 'bg-slate-900/80 border-slate-800/60 opacity-85'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2 mb-1.5">
                    <span className="font-bold text-slate-100 text-xs font-mono">{res.provider}</span>
                    {isOk ? (
                      <span className="flex items-center gap-1 text-[10px] font-mono font-bold text-emerald-400 bg-emerald-950 px-2 py-0.5 rounded-full border border-emerald-800/50">
                        <CheckCircle2 className="w-3 h-3" />
                        {res.latency_ms} ms
                      </span>
                    ) : (
                      <span className="flex items-center gap-1 text-[10px] font-mono font-medium text-rose-400 bg-rose-950/60 px-2 py-0.5 rounded-full border border-rose-800/50">
                        <XCircle className="w-3 h-3" />
                        FAILED
                      </span>
                    )}
                  </div>

                  <div className="text-[11px] space-y-1 text-slate-400">
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-indigo-300 uppercase">{res.type}</span>
                      <span className={`font-medium ${res.role !== '未分配' ? 'text-amber-300' : 'text-slate-500'}`}>
                        {res.role}
                      </span>
                    </div>
                    {res.model && <div className="truncate text-slate-300 font-mono">{res.model}</div>}
                    <p className={`text-[10px] pt-1.5 border-t border-slate-800/80 truncate ${isOk ? 'text-slate-400' : 'text-rose-300'}`} title={res.message}>
                      {res.message}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Main Configuration Form */}
      {config && (
        <form onSubmit={handleSaveConfig} className="space-y-8">
          {/* Section 1: Translation Routing & 2-Level Fallback */}
          <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 space-y-4 shadow-xl">
            <div className="flex items-center gap-2 text-xs font-bold text-indigo-300 uppercase tracking-wider">
              <Layers className="w-4 h-4" />
              1. 翻译模型主备容灾拓扑 (Translation & Fallback Topology)
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {/* Primary Translator */}
              <div className="bg-slate-950/60 p-4 rounded-xl border border-indigo-900/40">
                <label className="text-xs font-bold text-slate-200 block mb-1">
                  主译模型 (Primary)
                </label>
                <p className="text-[11px] text-slate-500 mb-2">首选翻译主力模型</p>
                <select
                  value={config.roles?.primary_translator || ''}
                  onChange={(e) =>
                    setConfig({
                      ...config,
                      roles: { ...config.roles, primary_translator: e.target.value },
                    })
                  }
                  className="w-full bg-slate-900 border border-slate-700 rounded-xl p-2.5 text-xs text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
                >
                  {providersList.map((p) => (
                    <option key={p} value={p}>
                      {p} ({config.providers?.[p]?.model || config.providers?.[p]?.type})
                    </option>
                  ))}
                </select>
              </div>

              {/* Fallback #1 */}
              <div className="bg-slate-950/60 p-4 rounded-xl border border-slate-800">
                <label className="text-xs font-bold text-slate-200 block mb-1">
                  一级备用 (Fallback #1)
                </label>
                <p className="text-[11px] text-slate-500 mb-2">主译拦截时无缝接管</p>
                <select
                  value={fb1}
                  onChange={(e) => handleSetFallback(0, e.target.value)}
                  className="w-full bg-slate-900 border border-slate-700 rounded-xl p-2.5 text-xs text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
                >
                  <option value="">-- 未设置 --</option>
                  {providersList.map((p) => (
                    <option key={p} value={p}>
                      {p} ({config.providers?.[p]?.model || config.providers?.[p]?.type})
                    </option>
                  ))}
                </select>
              </div>

              {/* Fallback #2 */}
              <div className="bg-slate-950/60 p-4 rounded-xl border border-slate-800">
                <label className="text-xs font-bold text-slate-200 block mb-1">
                  二级备用 (Fallback #2)
                </label>
                <p className="text-[11px] text-slate-500 mb-2">推荐本地无审查模型 (LM Studio)</p>
                <select
                  value={fb2}
                  onChange={(e) => handleSetFallback(1, e.target.value)}
                  className="w-full bg-slate-900 border border-slate-700 rounded-xl p-2.5 text-xs text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
                >
                  <option value="">-- 未设置 --</option>
                  {providersList.map((p) => (
                    <option key={p} value={p}>
                      {p} ({config.providers?.[p]?.model || config.providers?.[p]?.type})
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          {/* Section 2: Consistency Review & Dual Review */}
          <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 space-y-4 shadow-xl">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-xs font-bold text-purple-300 uppercase tracking-wider">
                <Sparkles className="w-4 h-4" />
                2. 章节长程一致性审阅与双审阅机制 (Consistency & Dual Review)
              </div>

              {/* Dual Review Toggle */}
              <label className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer bg-purple-950/40 border border-purple-800/50 px-3 py-1.5 rounded-xl">
                <input
                  type="checkbox"
                  checked={config.roles?.dual_review ?? false}
                  onChange={(e) =>
                    setConfig({
                      ...config,
                      roles: { ...config.roles, dual_review: e.target.checked },
                    })
                  }
                  className="rounded bg-slate-800 border-purple-700 text-purple-600 focus:ring-0"
                />
                <span className="font-semibold text-purple-200">启用双模型一致性仲裁 (Dual Review)</span>
              </label>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Primary Reviewer */}
              <div className="bg-slate-950/60 p-4 rounded-xl border border-slate-800">
                <label className="text-xs font-bold text-slate-200 block mb-1">
                  一致性主审模型 (Primary Reviewer)
                </label>
                <p className="text-[11px] text-slate-500 mb-2">负责提取术语、人物档案并分析客观缺陷</p>
                <select
                  value={config.roles?.reviewer || ''}
                  onChange={(e) =>
                    setConfig({
                      ...config,
                      roles: { ...config.roles, reviewer: e.target.value },
                    })
                  }
                  className="w-full bg-slate-900 border border-slate-700 rounded-xl p-2.5 text-xs text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
                >
                  {providersList.map((p) => (
                    <option key={p} value={p}>
                      {p} ({config.providers?.[p]?.model || config.providers?.[p]?.type})
                    </option>
                  ))}
                </select>
              </div>

              {/* Secondary Reviewer */}
              <div className={`bg-slate-950/60 p-4 rounded-xl border transition-all ${
                config.roles?.dual_review ? 'border-purple-800/60 opacity-100' : 'border-slate-800 opacity-40'
              }`}>
                <label className="text-xs font-bold text-slate-200 block mb-1">
                  仲裁副审模型 (Secondary Reviewer)
                </label>
                <p className="text-[11px] text-slate-500 mb-2">双审模式下参与交叉校验，出现冲突时仲裁</p>
                <select
                  disabled={!config.roles?.dual_review}
                  value={config.roles?.secondary_reviewer || ''}
                  onChange={(e) =>
                    setConfig({
                      ...config,
                      roles: { ...config.roles, secondary_reviewer: e.target.value },
                    })
                  }
                  className="w-full bg-slate-900 border border-slate-700 rounded-xl p-2.5 text-xs text-slate-100 font-mono focus:outline-none focus:border-purple-500 disabled:cursor-not-allowed"
                >
                  <option value="">-- 未设置 --</option>
                  {providersList.map((p) => (
                    <option key={p} value={p}>
                      {p} ({config.providers?.[p]?.model || config.providers?.[p]?.type})
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          {/* Section 3: AI Provider & API Key Manager */}
          <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 space-y-6 shadow-xl">
            <div className="flex items-center justify-between border-b border-slate-800 pb-4">
              <div>
                <div className="flex items-center gap-2 text-xs font-bold text-emerald-300 uppercase tracking-wider">
                  <Key className="w-4 h-4" />
                  3. AI Provider 与 API Key 密钥管理中心
                </div>
                <p className="text-xs text-slate-400 mt-1">
                  支持直接填入 API Key 明文（如 `sk-...`），或填入环境变量占位符（如 `$DEEPSEEK_API_KEY`）。
                </p>
              </div>

              <button
                type="button"
                onClick={() => setShowAddProviderModal(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold shadow-md shadow-emerald-600/20 transition-all cursor-pointer"
              >
                <Plus className="w-3.5 h-3.5" />
                新增 Provider
              </button>
            </div>

            {/* Providers List Grid */}
            <div className="space-y-4">
              {providersList.map((pId) => {
                const p = config.providers?.[pId] || { type: 'openai' };
                const isShowingKey = showKeyMap[pId] || false;
                const isPrimary = config.roles?.primary_translator === pId;
                const isReviewer = config.roles?.reviewer === pId;
                const isFb1 = fb1 === pId;
                const isFb2 = fb2 === pId;

                return (
                  <div
                    key={pId}
                    className="bg-slate-950/70 border border-slate-800 hover:border-slate-700 p-5 rounded-2xl space-y-4 transition-all"
                  >
                    {/* Provider Item Header */}
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2.5">
                        <span className="font-bold text-slate-100 text-sm font-mono">{pId}</span>
                        <span className="px-2 py-0.5 rounded text-[10px] font-mono uppercase bg-slate-800 text-slate-300">
                          {p.type}
                        </span>
                        {isPrimary && (
                          <span className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-indigo-950 text-indigo-300 border border-indigo-800/50">
                            ★ 主译主力
                          </span>
                        )}
                        {isFb1 && (
                          <span className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-950 text-amber-300 border border-amber-800/50">
                            备用 #1
                          </span>
                        )}
                        {isFb2 && (
                          <span className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-950 text-amber-300 border border-amber-800/50">
                            备用 #2
                          </span>
                        )}
                        {isReviewer && (
                          <span className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-purple-950 text-purple-300 border border-purple-800/50">
                            一致性主审
                          </span>
                        )}
                      </div>

                      <button
                        type="button"
                        onClick={() => handleDeleteProvider(pId)}
                        className="text-slate-500 hover:text-rose-400 p-1.5 rounded-lg hover:bg-slate-800 transition-colors"
                        title="删除该 Provider"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>

                    {/* Provider Input Fields */}
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 text-xs">
                      {/* Model Name */}
                      <div>
                        <label className="text-slate-400 block mb-1 font-medium">模型名称 (Model)</label>
                        <input
                          type="text"
                          value={p.model || ''}
                          onChange={(e) => handleUpdateProvider(pId, 'model', e.target.value)}
                          placeholder="如: deepseek-chat / gpt-4o"
                          className="w-full bg-slate-900 border border-slate-800 rounded-xl p-2 text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
                        />
                      </div>

                      {/* Base URL (if openai type) */}
                      {p.type === 'openai' && (
                        <div>
                          <label className="text-slate-400 block mb-1 font-medium">Base URL</label>
                          <input
                            type="text"
                            value={p.base_url || ''}
                            onChange={(e) => handleUpdateProvider(pId, 'base_url', e.target.value)}
                            placeholder="如: https://api.deepseek.com/v1"
                            className="w-full bg-slate-900 border border-slate-800 rounded-xl p-2 text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
                          />
                        </div>
                      )}

                      {/* API Key (if openai type) */}
                      {p.type === 'openai' && (
                        <div>
                          <label className="text-slate-400 block mb-1 font-medium flex items-center justify-between">
                            <span>API Key (密钥)</span>
                            <button
                              type="button"
                              onClick={() =>
                                setShowKeyMap((prev) => ({ ...prev, [pId]: !isShowingKey }))
                              }
                              className="text-slate-400 hover:text-slate-200 flex items-center gap-1 text-[10px]"
                            >
                              {isShowingKey ? <EyeOff className="w-3 h-3" /> : <Eye className="w-3 h-3" />}
                              {isShowingKey ? '隐藏' : '显示'}
                            </button>
                          </label>
                          <div className="relative">
                            <input
                              type={isShowingKey ? 'text' : 'password'}
                              value={p.api_key || ''}
                              onChange={(e) => handleUpdateProvider(pId, 'api_key', e.target.value)}
                              placeholder="sk-xxxxxxxx 或 $ENV_VAR"
                              className="w-full bg-slate-900 border border-slate-800 rounded-xl p-2 text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
                            />
                          </div>
                        </div>
                      )}

                      {/* Temperature */}
                      {p.type === 'openai' && (
                        <div>
                          <label className="text-slate-400 block mb-1 font-medium">采样温度 (Temperature)</label>
                          <input
                            type="number"
                            step="0.1"
                            min="0.0"
                            max="2.0"
                            value={p.temperature ?? 0.3}
                            onChange={(e) =>
                              handleUpdateProvider(pId, 'temperature', parseFloat(e.target.value))
                            }
                            className="w-full bg-slate-900 border border-slate-800 rounded-xl p-2 text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
                          />
                        </div>
                      )}

                      {/* Context Tokens */}
                      {p.type === 'openai' && (
                        <div>
                          <label className="text-slate-400 block mb-1 font-medium">上下文窗口 (Tokens)</label>
                          <input
                            type="number"
                            step="1024"
                            min="1024"
                            max="1048576"
                            value={p.context_tokens ?? 32768}
                            onChange={(e) =>
                              handleUpdateProvider(pId, 'context_tokens', parseInt(e.target.value, 10))
                            }
                            className="w-full bg-slate-900 border border-slate-800 rounded-xl p-2 text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
                          />
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Section 4: Pipeline Parameters */}
          <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 space-y-4 shadow-xl">
            <div className="flex items-center gap-2 text-xs font-bold text-slate-300 uppercase tracking-wider">
              <Sliders className="w-4 h-4" />
              4. 流水线批次与版式参数 (Pipeline Parameters)
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div>
                <label className="text-xs font-medium text-slate-300 block mb-1.5">
                  单批最大字符数 (Batch Chars)
                </label>
                <input
                  type="number"
                  value={config.pipeline?.primary_batch_max_chars || 4000}
                  onChange={(e) =>
                    setConfig({
                      ...config,
                      pipeline: {
                        ...config.pipeline,
                        primary_batch_max_chars: parseInt(e.target.value, 10),
                      },
                    })
                  }
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl p-2.5 text-xs text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="text-xs font-medium text-slate-300 block mb-1.5">
                  二分递归拆解最大深度 (Max Split Depth)
                </label>
                <input
                  type="number"
                  value={config.pipeline?.max_provider_split_depth ?? 3}
                  onChange={(e) =>
                    setConfig({
                      ...config,
                      pipeline: {
                        ...config.pipeline,
                        max_provider_split_depth: parseInt(e.target.value, 10),
                      },
                    })
                  }
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl p-2.5 text-xs text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="text-xs font-medium text-slate-300 block mb-1.5">
                  默认版式处理 (EPUB Layout)
                </label>
                <select
                  value={config.pipeline?.layout || 'horizontal'}
                  onChange={(e) =>
                    setConfig({
                      ...config,
                      pipeline: { ...config.pipeline, layout: e.target.value },
                    })
                  }
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl p-2.5 text-xs text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
                >
                  <option value="horizontal">重构为中文横排版式 (Horizontal)</option>
                  <option value="preserve">保留原版竖排版式 (Preserve)</option>
                </select>
              </div>
            </div>
          </div>

          {/* Sticky Bottom Save Bar */}
          <div className="sticky bottom-6 z-40 bg-slate-950/90 backdrop-blur-md p-4 rounded-2xl border border-slate-700 flex items-center justify-between shadow-2xl">
            <div className="flex items-center gap-2">
              {saveSuccess ? (
                <span className="text-xs text-emerald-400 flex items-center gap-1.5 font-bold animate-fade-in">
                  <CheckCircle2 className="w-4 h-4" />
                  配置已保存并写入 config.toml！
                </span>
              ) : (
                <span className="text-xs text-slate-400">
                  修改后点击右侧按钮保存，将即刻应用于下一次翻译任务
                </span>
              )}
            </div>

            <button
              type="submit"
              disabled={isSaving}
              className="flex items-center gap-2 px-8 py-3 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold shadow-lg shadow-indigo-600/30 transition-all hover:scale-105 active:scale-95 disabled:opacity-50 cursor-pointer"
            >
              <Save className="w-4 h-4" />
              {isSaving ? '正在保存...' : '保存配置 (Save)'}
            </button>
          </div>
        </form>
      )}

      {/* Add Provider Modal */}
      {showAddProviderModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
          <div className="bg-slate-900 border border-slate-700 rounded-2xl p-6 max-w-lg w-full shadow-2xl space-y-5">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <h3 className="text-base font-bold text-white flex items-center gap-2">
                <Plus className="w-4 h-4 text-emerald-400" />
                新增 AI Provider
              </h3>
              <button
                type="button"
                onClick={() => setShowAddProviderModal(false)}
                className="text-slate-400 hover:text-white"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleAddProviderSubmit} className="space-y-4 text-xs">
              <div>
                <label className="text-slate-300 block mb-1 font-medium">
                  Provider 唯一标识 (ID)
                </label>
                <input
                  type="text"
                  required
                  placeholder="例如: siliconflow / deepseek_custom / ollama"
                  value={newProviderId}
                  onChange={(e) => setNewProviderId(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl p-2.5 text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="text-slate-300 block mb-1 font-medium">Provider 类型</label>
                <select
                  value={newProviderType}
                  onChange={(e) => setNewProviderType(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl p-2.5 text-slate-100 focus:outline-none focus:border-indigo-500"
                >
                  <option value="openai">OpenAI 兼容 API (DeepSeek / SiliconFlow / LM Studio / Ollama 等)</option>
                  <option value="antigravity">Antigravity CLI (Gemini 3.7 / Claude 3.7)</option>
                  <option value="opencode">OpenCode CLI (Muse / MiMo / HY3)</option>
                  <option value="codex">Codex CLI</option>
                </select>
              </div>

              {newProviderType === 'openai' && (
                <>
                  <div>
                    <label className="text-slate-300 block mb-1 font-medium">Base URL</label>
                    <input
                      type="text"
                      required
                      placeholder="例如: https://api.deepseek.com/v1"
                      value={newBaseUrl}
                      onChange={(e) => setNewBaseUrl(e.target.value)}
                      className="w-full bg-slate-950 border border-slate-800 rounded-xl p-2.5 text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
                    />
                  </div>

                  <div>
                    <label className="text-slate-300 block mb-1 font-medium">
                      API Key (密钥)
                    </label>
                    <input
                      type="text"
                      placeholder="sk-xxxxxxxx 或 $ENV_VAR"
                      value={newApiKey}
                      onChange={(e) => setNewApiKey(e.target.value)}
                      className="w-full bg-slate-950 border border-slate-800 rounded-xl p-2.5 text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
                    />
                  </div>
                </>
              )}

              <div>
                <label className="text-slate-300 block mb-1 font-medium">模型名称 (Model)</label>
                <input
                  type="text"
                  required
                  placeholder="例如: deepseek-chat 或 gpt-4o-mini"
                  value={newModel}
                  onChange={(e) => setNewModel(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl p-2.5 text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
                />
              </div>

              {newProviderType === 'openai' && (
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-slate-300 block mb-1 font-medium">采样温度</label>
                    <input
                      type="number"
                      step="0.1"
                      min="0.0"
                      max="2.0"
                      value={newTemperature}
                      onChange={(e) => setNewTemperature(parseFloat(e.target.value))}
                      className="w-full bg-slate-950 border border-slate-800 rounded-xl p-2.5 text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
                    />
                  </div>
                  <div>
                    <label className="text-slate-300 block mb-1 font-medium">上下文窗口 (Tokens)</label>
                    <input
                      type="number"
                      step="1024"
                      value={newContextTokens}
                      onChange={(e) => setNewContextTokens(parseInt(e.target.value, 10))}
                      className="w-full bg-slate-950 border border-slate-800 rounded-xl p-2.5 text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
                    />
                  </div>
                </div>
              )}

              <div className="flex items-center justify-end gap-2 pt-3 border-t border-slate-800">
                <button
                  type="button"
                  onClick={() => setShowAddProviderModal(false)}
                  className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 font-medium"
                >
                  取消
                </button>
                <button
                  type="submit"
                  className="px-5 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-semibold shadow-md shadow-emerald-600/30"
                >
                  添加此 Provider
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
