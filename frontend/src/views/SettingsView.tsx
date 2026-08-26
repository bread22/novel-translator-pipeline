import React, { useState, useEffect, useRef } from 'react';
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
  Key,
  FileText,
  Edit3,
  Check,
} from 'lucide-react';
import { PreflightProviderResult, PreflightResponse, PromptItem, SystemConfig } from '../types/api';
import { api } from '../lib/api';
import { Modal } from '../components/Modal';
import { migrateProviderRoleReferences, providerRoleReferences } from './settingsUtils';

export const SettingsView: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'routing' | 'prompts'>('routing');
  const [config, setConfig] = useState<SystemConfig | null>(null);
  const [configLoadError, setConfigLoadError] = useState<string | null>(null);
  const [preflightData, setPreflightData] = useState<PreflightResponse | null>(null);
  const [isRunningPreflight, setIsRunningPreflight] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [providerPendingDeletion, setProviderPendingDeletion] = useState<string | null>(null);
  const [providerReplacementId, setProviderReplacementId] = useState('');

  const lastSavedConfig = useRef<SystemConfig | null>(null);

  // Add Provider Modal State
  const [showAddProviderModal, setShowAddProviderModal] = useState(false);
  const [newProviderId, setNewProviderId] = useState('');
  const [newProviderType, setNewProviderType] = useState('openai');
  const [newBaseUrl, setNewBaseUrl] = useState('https://api.deepseek.com/v1');
  const [newModel, setNewModel] = useState('deepseek-chat');
  const [newApiKey, setNewApiKey] = useState('');
  const [newTemperature, setNewTemperature] = useState(0.3);
  const [newContextTokens, setNewContextTokens] = useState(32768);

  // Prompt Management State
  const [prompts, setPrompts] = useState<PromptItem[]>([]);
  const [selectedPromptId, setSelectedPromptId] = useState<string>('');
  const [editingPromptContent, setEditingPromptContent] = useState<string>('');
  const [isSavingPrompt, setIsSavingPrompt] = useState(false);
  const [promptSaveSuccess, setPromptSaveSuccess] = useState(false);

  // Add Prompt Modal State
  const [showAddPromptModal, setShowAddPromptModal] = useState(false);
  const [newPromptFilename, setNewPromptFilename] = useState('');
  const [newPromptContent, setNewPromptContent] = useState('');

  useEffect(() => {
    loadConfig();
    loadPrompts();
  }, []);

  const loadConfig = async () => {
    setConfigLoadError(null);
    try {
      const cfg = await api.getConfig();
      setConfig(cfg);
      lastSavedConfig.current = structuredClone(cfg);
    } catch (err: any) {
      console.error('Failed to load config:', err);
      setConfigLoadError(err instanceof Error ? err.message : '配置加载失败');
    }
  };

  const loadPrompts = async () => {
    try {
      const list = await api.getPrompts();
      setPrompts(list);
      if (list.length > 0) {
        setSelectedPromptId((prev) => {
          const current = list.find((p) => p.id === prev) || list[0];
          setEditingPromptContent(current.content);
          return current.id;
        });
      }
    } catch (err: any) {
      console.error('Failed to load prompts:', err);
    }
  };

  const handleSelectPrompt = (pId: string) => {
    setSelectedPromptId(pId);
    const found = prompts.find((p) => p.id === pId);
    if (found) {
      setEditingPromptContent(found.content);
    }
  };

  const handleSavePrompt = async () => {
    if (!selectedPromptId || !editingPromptContent.trim()) return;
    setIsSavingPrompt(true);
    try {
      await api.savePrompt({
        filename: selectedPromptId,
        content: editingPromptContent,
      });
      setPromptSaveSuccess(true);
      setTimeout(() => setPromptSaveSuccess(false), 3000);
      await loadPrompts();
    } catch (err: any) {
      alert(`保存提示词失败: ${err.message}`);
    } finally {
      setIsSavingPrompt(false);
    }
  };

  const handleDeletePrompt = async (pId: string) => {
    if (!confirm(`确定要删除提示词规范 '${pId}' 吗？`)) return;
    try {
      await api.deletePrompt(pId);
      await loadPrompts();
    } catch (err: any) {
      alert(`删除提示词失败: ${err.message}`);
    }
  };

  const handleCreatePromptSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newPromptFilename.trim() || !newPromptContent.trim()) return;
    try {
      const saved = await api.savePrompt({
        filename: newPromptFilename,
        content: newPromptContent,
      });
      setShowAddPromptModal(false);
      setNewPromptFilename('');
      setNewPromptContent('');
      const list = await api.getPrompts();
      setPrompts(list);
      const selected = list.find((prompt) => prompt.id === saved.id);
      if (selected) {
        setSelectedPromptId(selected.id);
        setEditingPromptContent(selected.content);
      }
    } catch (err: any) {
      alert(`创建提示词失败: ${err.message}`);
    }
  };

  const handleSetAsDefaultPolicy = async (policyPath: string) => {
    if (!config) return;
    const updated = {
      ...config,
      paths: {
        ...config.paths,
        translation_policy: policyPath,
      },
    };
    setConfig(updated);
    try {
      await api.saveConfig(updated);
      lastSavedConfig.current = structuredClone(updated);
      alert(`已将 '${policyPath}' 设为系统全局默认翻译规范！`);
    } catch (err: any) {
      if (lastSavedConfig.current) setConfig(structuredClone(lastSavedConfig.current));
      alert(`设置默认规范失败: ${err.message}`);
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
      lastSavedConfig.current = structuredClone(config);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
    } catch (err: any) {
      if (lastSavedConfig.current) setConfig(structuredClone(lastSavedConfig.current));
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

  const handleDeleteProvider = (providerId: string) => {
    if (!config || !config.providers) return;
    const references = providerRoleReferences(config, providerId);
    if (references.length) {
      const replacement = Object.keys(config.providers).find((id) => id !== providerId) || '';
      setProviderPendingDeletion(providerId);
      setProviderReplacementId(replacement);
      return;
    }
    if (!confirm(`确定要删除 Provider [${providerId}] 吗？`)) return;
    const { [providerId]: _, ...remaining } = config.providers;
    setConfig({
      ...config,
      providers: remaining,
    });
  };

  const handleMigrateAndDeleteProvider = () => {
    if (!config?.providers || !providerPendingDeletion || !providerReplacementId) return;
    const migrated = migrateProviderRoleReferences(config, providerPendingDeletion, providerReplacementId);
    const { [providerPendingDeletion]: _removed, ...remaining } = migrated.providers || {};
    setConfig({ ...migrated, providers: remaining });
    setProviderPendingDeletion(null);
    setProviderReplacementId('');
  };

  const handleAddProviderSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newProviderId.trim()) return;

    const id = newProviderId.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '_');
    const providerObj: any = {
      type: newProviderType,
      model: newModel.trim(),
    };

    if (newProviderType === 'openai') {
      providerObj.base_url = newBaseUrl.trim();
      providerObj.api_key = newApiKey.trim() || '$API_KEY';
      providerObj.temperature = newTemperature;
      providerObj.context_tokens = newContextTokens;
      providerObj.timeout = 600;
    } else if (newProviderType === 'antigravity') {
      providerObj.agy = 'agy';
      providerObj.effort = 'low';
      providerObj.timeout = 600;
      providerObj.concurrency = 1;
      providerObj.context_tokens = 1048576;
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

  const selectedPrompt = prompts.find((p) => p.id === selectedPromptId);
  const isDefaultPolicy = selectedPrompt && config?.paths?.translation_policy === selectedPrompt.path;

  return (
    <div className="space-y-6 max-w-5xl mx-auto pb-20">
      {/* Top Banner */}
      <div className="bg-white border border-[#E5E0D8] p-6 rounded-sm flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 shadow-sm">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-mono px-2 py-0.5 rounded-sm bg-[#FAF9F6] border border-[#1A1A1A] text-[#1A1A1A] font-bold">
              SYSTEM & MODEL CONFIG
            </span>
            <h2 className="text-xl font-serif font-bold text-[#1A1A1A] tracking-tight">AI 模型路由、API Key 与提示词规范管理</h2>
          </div>
          <p className="text-xs text-[#666666] mt-1 font-sans">
            管理 OpenAI 兼容 API、两级容灾备用（Fallback）、双审阅及多风格文学翻译提示词规范。
          </p>
        </div>

        <div className="flex items-center gap-2">
          {activeTab === 'routing' ? (
            <>
              <button
                onClick={() => setShowAddProviderModal(true)}
                className="flex items-center gap-1.5 px-4 py-2.5 rounded-sm bg-white hover:bg-[#FAF9F6] text-[#1A1A1A] text-xs font-medium border border-[#E5E0D8] transition-all cursor-pointer shadow-sm"
              >
                <Plus className="w-4 h-4 text-[#1D4ED8]" />
                添加 Provider
              </button>

              <button
                onClick={runPreflightTest}
                disabled={isRunningPreflight}
                className="flex items-center gap-2 px-5 py-2.5 rounded-sm bg-[#1D4ED8] hover:bg-[#1E40AF] text-white text-xs font-semibold shadow-sm transition-all disabled:opacity-50 cursor-pointer"
              >
                <RotateCw className={`w-4 h-4 ${isRunningPreflight ? 'animate-spin' : ''}`} />
                {isRunningPreflight ? '正在并发探测...' : '一键连通性测试'}
              </button>
            </>
          ) : (
            <button
              onClick={() => setShowAddPromptModal(true)}
              className="flex items-center gap-1.5 px-4 py-2.5 rounded-sm bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold shadow-sm transition-all cursor-pointer"
            >
              <Plus className="w-4 h-4 text-white" />
              新建自定义 Prompt
            </button>
          )}
        </div>
      </div>

      {/* Sub Navigation Tabs */}
      <div className="flex items-center gap-2 border-b border-[#E5E0D8] pb-2">
        <button
          type="button"
          onClick={() => setActiveTab('routing')}
          className={`flex items-center gap-2 px-4 py-2 rounded-sm text-xs font-medium transition-all cursor-pointer ${
            activeTab === 'routing'
              ? 'bg-[#1D4ED8] text-white font-semibold shadow-sm'
              : 'text-[#4A4A4A] hover:text-[#1A1A1A] hover:bg-[#FAF9F6]'
          }`}
        >
          <Sliders className="w-4 h-4" />
          AI 模型路由与 API Key (Model Routing)
        </button>

        <button
          type="button"
          onClick={() => setActiveTab('prompts')}
          className={`flex items-center gap-2 px-4 py-2 rounded-sm text-xs font-medium transition-all cursor-pointer ${
            activeTab === 'prompts'
              ? 'bg-[#1D4ED8] text-white font-semibold shadow-sm'
              : 'text-[#4A4A4A] hover:text-[#1A1A1A] hover:bg-[#FAF9F6]'
          }`}
        >
          <FileText className="w-4 h-4" />
          翻译与审阅提示词规范 (Prompt Manager)
          <span className="px-1.5 py-0.2 rounded-sm text-[10px] bg-[#FAF9F6] border border-[#E5E0D8] text-[#1A1A1A] font-mono">
            {prompts.length}
          </span>
        </button>
      </div>

      {configLoadError && (
        <div role="alert" className="flex items-center justify-between gap-4 border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800">
          <span>配置加载失败：{configLoadError}</span>
          <button type="button" className="shrink-0 underline" onClick={() => void loadConfig()}>重试</button>
        </div>
      )}

      {/* TAB 1: MODEL ROUTING & API KEY */}
      {activeTab === 'routing' && (
        <div className="space-y-8">
          {/* Preflight Diagnostics Section */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-serif font-bold text-[#1A1A1A] flex items-center gap-2">
                <Zap className="w-4 h-4 text-amber-600" />
                Provider 连通性预检指标
              </h3>
              {preflightData && (
                <span
                  className={`text-xs font-mono px-2.5 py-0.5 rounded-sm border ${
                    preflightData.all_passed
                      ? 'bg-emerald-50 border-emerald-300 text-emerald-800'
                      : 'bg-amber-50 border-amber-300 text-amber-800'
                  }`}
                >
                  {preflightData.all_passed ? '✓ ACTIVE ROLES READY' : 'SOME PROVIDERS NOT READY'}
                </span>
              )}
            </div>

            {!preflightData && !isRunningPreflight ? (
              <div className="bg-white border border-[#E5E0D8] rounded-sm p-5 text-center text-xs text-[#666666] space-y-1 shadow-sm font-serif">
                <Server className="w-6 h-6 text-[#888888] mx-auto mb-1" />
                <p>点击上方【一键连通性测试】，将并发探测已配置各 AI 模型的网络延迟与响应契约。</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                {preflightData?.results.map((res: PreflightProviderResult, idx: number) => {
                  const isOk = res.status === 'ok';

                  return (
                    <div
                      key={idx}
                      className={`p-4 rounded-sm border transition-all ${
                        isOk
                          ? 'bg-white border-[#E5E0D8] shadow-sm hover:border-emerald-300'
                          : 'bg-[#FAF9F6] border-[#E5E0D8] opacity-85'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2 mb-1.5">
                        <span className="font-bold text-[#1A1A1A] text-xs font-mono">{res.provider}</span>
                        {isOk ? (
                          <span className="flex items-center gap-1 text-[10px] font-mono font-bold text-emerald-800 bg-emerald-50 px-2 py-0.5 rounded-sm border border-emerald-300">
                            <CheckCircle2 className="w-3 h-3 text-emerald-600" />
                            {res.latency_ms} ms
                          </span>
                        ) : (
                          <span className="flex items-center gap-1 text-[10px] font-mono font-medium text-rose-800 bg-rose-50 px-2 py-0.5 rounded-sm border border-rose-300">
                            <XCircle className="w-3 h-3 text-rose-600" />
                            FAILED
                          </span>
                        )}
                      </div>

                      <div className="text-[11px] space-y-1 text-[#666666]">
                        <div className="flex items-center justify-between">
                          <span className="font-mono text-[#1D4ED8] uppercase">{res.type}</span>
                          <span className={`font-medium ${res.role !== '未分配' ? 'text-amber-800' : 'text-[#888888]'}`}>
                            {res.role}
                          </span>
                        </div>
                        {res.model && <div className="truncate text-[#1A1A1A] font-mono">{res.model}</div>}
                        <p className={`text-[10px] pt-1.5 border-t border-[#E5E0D8] truncate ${isOk ? 'text-[#666666]' : 'text-rose-700'}`} title={res.message}>
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
            <form onSubmit={handleSaveConfig} className="space-y-6">
              {/* Section 1: Translation Routing & 2-Level Fallback */}
              <div className="bg-white border border-[#E5E0D8] rounded-sm p-6 space-y-4 shadow-sm">
                <div className="flex items-center gap-2 text-xs font-serif font-bold text-[#1D4ED8] uppercase tracking-wider">
                  <Layers className="w-4 h-4" />
                  1. 翻译模型主备容灾拓扑 (Translation & Fallback Topology)
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  {/* Primary Translator */}
                  <div className="bg-[#FAF9F6] p-4 rounded-sm border border-[#E5E0D8]">
                    <label className="text-xs font-serif font-bold text-[#1A1A1A] block mb-1">
                      主译模型 (Primary)
                    </label>
                    <p className="text-[11px] text-[#666666] mb-2 font-sans">首选翻译主力模型</p>
                    <select
                      value={config.roles?.primary_translator || ''}
                      onChange={(e) =>
                        setConfig({
                          ...config,
                          roles: { ...config.roles, primary_translator: e.target.value },
                        })
                      }
                      className="w-full bg-white border border-[#E5E0D8] rounded-sm p-2.5 text-xs text-[#1A1A1A] font-mono focus:outline-none focus:border-[#1D4ED8]"
                    >
                      {providersList.map((p) => (
                        <option key={p} value={p}>
                          {p} ({config.providers?.[p]?.model || config.providers?.[p]?.type})
                        </option>
                      ))}
                    </select>
                  </div>

                  {/* Fallback #1 */}
                  <div className="bg-[#FAF9F6] p-4 rounded-sm border border-[#E5E0D8]">
                    <label className="text-xs font-serif font-bold text-[#1A1A1A] block mb-1">
                      一级备用 (Fallback #1)
                    </label>
                    <p className="text-[11px] text-[#666666] mb-2 font-sans">主译拦截时无缝接管</p>
                    <select
                      value={fb1}
                      onChange={(e) => handleSetFallback(0, e.target.value)}
                      className="w-full bg-white border border-[#E5E0D8] rounded-sm p-2.5 text-xs text-[#1A1A1A] font-mono focus:outline-none focus:border-[#1D4ED8]"
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
                  <div className="bg-[#FAF9F6] p-4 rounded-sm border border-[#E5E0D8]">
                    <label className="text-xs font-serif font-bold text-[#1A1A1A] block mb-1">
                      二级备用 (Fallback #2)
                    </label>
                    <p className="text-[11px] text-[#666666] mb-2 font-sans">一级备用亦受阻时底线救灾</p>
                    <select
                      value={fb2}
                      onChange={(e) => handleSetFallback(1, e.target.value)}
                      className="w-full bg-white border border-[#E5E0D8] rounded-sm p-2.5 text-xs text-[#1A1A1A] font-mono focus:outline-none focus:border-[#1D4ED8]"
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

                {/* Default Translation Policy Selector */}
                <div className="pt-2 border-t border-[#E5E0D8]">
                  <label className="text-xs font-serif font-bold text-[#1A1A1A] block mb-1.5 flex items-center justify-between">
                    <span>默认翻译提示词规范 (Default Policy Prompt)</span>
                    <button
                      type="button"
                      onClick={() => setActiveTab('prompts')}
                      className="text-[#1D4ED8] hover:underline text-[11px] font-normal cursor-pointer"
                    >
                      前往提示词管理器编辑规范 →
                    </button>
                  </label>
                  <select
                    value={config.paths?.translation_policy || 'docs/prompts/erotic-novel-policy.md'}
                    onChange={(e) =>
                      setConfig({
                        ...config,
                        paths: {
                          ...config.paths,
                          translation_policy: e.target.value,
                        },
                      })
                    }
                    className="w-full bg-[#FAF9F6] border border-[#E5E0D8] rounded-sm p-3 text-xs text-[#1D4ED8] font-mono focus:outline-none focus:border-[#1D4ED8]"
                  >
                    {prompts.filter(p => p.type === 'translation').map((p) => (
                      <option key={p.path} value={p.path}>
                        {p.name} ({p.filename})
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Section 2: Dual Review & Consistency Routing */}
              <div className="bg-white border border-[#E5E0D8] rounded-sm p-6 space-y-4 shadow-sm">
                <div className="flex items-center gap-2 text-xs font-serif font-bold text-[#1D4ED8] uppercase tracking-wider">
                  <Sparkles className="w-4 h-4" />
                  2. 双模型一致性审阅配置 (Dual Review & Consistency)
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {/* Primary Reviewer */}
                  <div className="bg-[#FAF9F6] p-4 rounded-sm border border-[#E5E0D8]">
                    <label className="text-xs font-serif font-bold text-[#1A1A1A] block mb-1">
                      一致性主审 (Primary Reviewer)
                    </label>
                    <p className="text-[11px] text-[#666666] mb-2 font-sans">负责客观错译、漏译与实体提取</p>
                    <select
                      value={config.roles?.reviewer || ''}
                      onChange={(e) =>
                        setConfig({
                          ...config,
                          roles: { ...config.roles, reviewer: e.target.value },
                        })
                      }
                      className="w-full bg-white border border-[#E5E0D8] rounded-sm p-2.5 text-xs text-[#1A1A1A] font-mono focus:outline-none focus:border-[#1D4ED8]"
                    >
                      {providersList.map((p) => (
                        <option key={p} value={p}>
                          {p} ({config.providers?.[p]?.model || config.providers?.[p]?.type})
                        </option>
                      ))}
                    </select>
                  </div>

                  {/* Dual Review Toggle & Secondary Reviewer */}
                  <div className="bg-[#FAF9F6] p-4 rounded-sm border border-[#E5E0D8] space-y-3">
                    <div className="flex items-center justify-between">
                      <div>
                        <label className="text-xs font-serif font-bold text-[#1A1A1A] block">
                          双审阅模式 (Dual Review)
                        </label>
                        <p className="text-[11px] text-[#666666] font-sans">双模型交叉检验，消除幻觉修改</p>
                      </div>
                      <input
                        type="checkbox"
                        checked={config.roles?.dual_review ?? true}
                        onChange={(e) =>
                          setConfig({
                            ...config,
                            roles: { ...config.roles, dual_review: e.target.checked },
                          })
                        }
                        className="rounded-sm border-[#E5E0D8] text-[#1D4ED8] focus:ring-0 w-4 h-4 cursor-pointer"
                      />
                    </div>

                    {config.roles?.dual_review && (
                      <div>
                        <label className="text-[11px] text-[#666666] block mb-1 font-serif">
                          副审模型 (Secondary Reviewer)
                        </label>
                        <select
                          value={config.roles?.secondary_reviewer || ''}
                          onChange={(e) =>
                            setConfig({
                              ...config,
                              roles: { ...config.roles, secondary_reviewer: e.target.value },
                            })
                          }
                          className="w-full bg-white border border-[#E5E0D8] rounded-sm p-2.5 text-xs text-[#1A1A1A] font-mono focus:outline-none focus:border-[#1D4ED8]"
                        >
                          <option value="">-- 未设置 --</option>
                          {providersList.map((p) => (
                            <option key={p} value={p}>
                              {p} ({config.providers?.[p]?.model || config.providers?.[p]?.type})
                            </option>
                          ))}
                        </select>
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {/* Section 3: AI Provider Manager & API Key Configurator */}
              <div className="bg-white border border-[#E5E0D8] rounded-sm p-6 space-y-5 shadow-sm">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-xs font-serif font-bold text-[#1A1A1A] uppercase tracking-wider">
                    <Server className="w-4 h-4 text-[#1D4ED8]" />
                    3. AI Provider 与 API Key 管理器 ({providersList.length} 个配置)
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowAddProviderModal(true)}
                    className="flex items-center gap-1 text-xs text-[#1D4ED8] hover:underline font-medium cursor-pointer"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    添加新 Provider
                  </button>
                </div>

                {/* Providers List Grid */}
                <div className="space-y-4">
                  {providersList.map((pId) => {
                    const p = config.providers?.[pId] || { type: 'openai' };
                    const isPrimary = config.roles?.primary_translator === pId;
                    const isReviewer = config.roles?.reviewer === pId;
                    const isFb1 = fb1 === pId;
                    const isFb2 = fb2 === pId;

                    return (
                      <div
                        key={pId}
                        className="bg-[#FAF9F6] border border-[#E5E0D8] p-5 rounded-sm space-y-4 shadow-sm"
                      >
                        {/* Provider Item Header */}
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-center gap-2.5">
                            <span className="font-bold text-[#1A1A1A] text-sm font-mono">{pId}</span>
                            <span className="px-2 py-0.5 rounded-sm text-[10px] font-mono uppercase bg-white border border-[#E5E0D8] text-[#4A4A4A]">
                              {p.type}
                            </span>
                            {isPrimary && (
                              <span className="px-2 py-0.5 rounded-sm text-[10px] font-medium bg-[#EFF6FF] text-[#1D4ED8] border border-[#BFDBFE]">
                                ★ 主译主力
                              </span>
                            )}
                            {isFb1 && (
                              <span className="px-2 py-0.5 rounded-sm text-[10px] font-medium bg-amber-50 text-amber-800 border border-amber-300">
                                备用 #1
                              </span>
                            )}
                            {isFb2 && (
                              <span className="px-2 py-0.5 rounded-sm text-[10px] font-medium bg-amber-50 text-amber-800 border border-amber-300">
                                备用 #2
                              </span>
                            )}
                            {isReviewer && (
                              <span className="px-2 py-0.5 rounded-sm text-[10px] font-medium bg-purple-50 text-purple-800 border border-purple-200">
                                一致性主审
                              </span>
                            )}
                          </div>

                          <button
                            type="button"
                            onClick={() => handleDeleteProvider(pId)}
                            className="text-[#888888] hover:text-rose-600 p-1.5 rounded-sm hover:bg-white transition-colors cursor-pointer"
                            title="删除该 Provider"
                            aria-label={`删除 Provider ${pId}`}
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>

                        {/* Provider Input Fields */}
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 text-xs">
                          {/* Model Name */}
                          <div>
                            <label className="text-[#666666] block mb-1 font-medium font-serif">模型名称 (Model)</label>
                            <input
                              type="text"
                              value={p.model || ''}
                              onChange={(e) => handleUpdateProvider(pId, 'model', e.target.value)}
                              placeholder="如: deepseek-chat / gpt-4o"
                              className="w-full bg-white border border-[#E5E0D8] rounded-sm p-2 text-[#1A1A1A] font-mono focus:outline-none focus:border-[#1D4ED8]"
                            />
                          </div>

                          {/* Base URL (if openai type) */}
                          {p.type === 'openai' && (
                            <div>
                              <label className="text-[#666666] block mb-1 font-medium font-serif">Base URL</label>
                              <input
                                type="text"
                                value={p.base_url || ''}
                                onChange={(e) => handleUpdateProvider(pId, 'base_url', e.target.value)}
                                placeholder="如: https://api.deepseek.com/v1"
                                className="w-full bg-white border border-[#E5E0D8] rounded-sm p-2 text-[#1A1A1A] font-mono focus:outline-none focus:border-[#1D4ED8]"
                              />
                            </div>
                          )}

                          {/* API Key (if openai type) */}
                          {p.type === 'openai' && (
                            <div>
                              <label className="text-[#666666] block mb-1 font-medium font-serif flex items-center justify-between">
                                <span>API Key (密钥)</span>
                                <span className="text-[10px] font-mono text-[#888888]">
                                  {p.api_key_configured ? `已配置 ${p.api_key_preview || ''}` : '未配置'}
                                </span>
                              </label>
                              <div className="relative">
                                <input
                                  type="password"
                                  value={p.api_key || ''}
                                  onChange={(e) => handleUpdateProvider(pId, 'api_key', e.target.value)}
                                  placeholder={p.api_key_ref || '$ENV_VAR 或新密钥（留空则保持）'}
                                  className="w-full bg-white border border-[#E5E0D8] rounded-sm p-2 text-[#1A1A1A] font-mono focus:outline-none focus:border-[#1D4ED8] pr-8"
                                />
                                <Key className="w-3.5 h-3.5 text-[#888888] absolute right-2.5 top-3 pointer-events-none" />
                              </div>
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Section 4: Pipeline Parameters */}
              <div className="bg-white border border-[#E5E0D8] rounded-sm p-6 space-y-4 shadow-sm">
                <div className="flex items-center gap-2 text-xs font-serif font-bold text-[#1A1A1A] uppercase tracking-wider">
                  <Sliders className="w-4 h-4" />
                  4. 流水线批次与版式参数 (Pipeline Parameters)
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                  <div>
                    <label className="text-xs font-medium text-[#4A4A4A] block mb-1.5 font-serif">
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
                      className="w-full bg-[#FAF9F6] border border-[#E5E0D8] rounded-sm p-2.5 text-xs text-[#1A1A1A] font-mono focus:outline-none focus:border-[#1D4ED8]"
                    />
                  </div>

                  <div>
                    <label className="text-xs font-medium text-[#4A4A4A] block mb-1.5 font-serif">
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
                      className="w-full bg-[#FAF9F6] border border-[#E5E0D8] rounded-sm p-2.5 text-xs text-[#1A1A1A] font-mono focus:outline-none focus:border-[#1D4ED8]"
                    />
                  </div>

                  <div>
                    <label className="text-xs font-medium text-[#4A4A4A] block mb-1.5 font-serif">
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
                      className="w-full bg-[#FAF9F6] border border-[#E5E0D8] rounded-sm p-2.5 text-xs text-[#1A1A1A] font-mono focus:outline-none focus:border-[#1D4ED8]"
                    >
                      <option value="horizontal">重构为中文横排版式 (Horizontal)</option>
                      <option value="preserve">保留原版竖排版式 (Preserve)</option>
                    </select>
                  </div>
                </div>
              </div>

              {/* Sticky Bottom Save Bar */}
              <div className="sticky bottom-6 z-40 bg-white/95 backdrop-blur-md p-4 rounded-sm border border-[#E5E0D8] flex items-center justify-between shadow-md">
                <div className="flex items-center gap-2">
                  {saveSuccess ? (
                    <span className="text-xs text-emerald-800 flex items-center gap-1.5 font-bold">
                      <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                      配置已保存并写入 config.toml 与 .env！
                    </span>
                  ) : (
                    <span className="text-xs text-[#666666]">
                      修改后点击右侧按钮保存，将即刻应用于下一次翻译任务
                    </span>
                  )}
                </div>

                <button
                  type="submit"
                  disabled={isSaving}
                  className="flex items-center gap-2 px-8 py-3 rounded-sm bg-[#1D4ED8] hover:bg-[#1E40AF] text-white text-xs font-semibold shadow-sm transition-all disabled:opacity-50 cursor-pointer"
                >
                  <Save className="w-4 h-4" />
                  {isSaving ? '正在保存...' : '保存配置 (Save)'}
                </button>
              </div>
            </form>
          )}
        </div>
      )}

      {/* TAB 2: PROMPT & POLICY MANAGER */}
      {activeTab === 'prompts' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Left Column: Prompts List */}
          <div className="space-y-4">
            <div className="bg-white border border-[#E5E0D8] p-4 rounded-sm space-y-3 shadow-sm">
              <div className="flex items-center justify-between">
                <h3 className="text-xs font-serif font-bold text-[#1A1A1A] uppercase tracking-wider flex items-center gap-2">
                  <FileText className="w-4 h-4 text-[#1D4ED8]" />
                  翻译提示词规范 ({prompts.filter(p => p.type === 'translation').length})
                </h3>
              </div>

              <div className="space-y-2">
                {prompts.filter(p => p.type === 'translation').map((p) => {
                  const isSelected = p.id === selectedPromptId;
                  const isDefault = config?.paths?.translation_policy === p.path;

                  return (
                    <div
                      key={p.id}
                      onClick={() => handleSelectPrompt(p.id)}
                      className={`p-3 rounded-sm border transition-all cursor-pointer flex items-center justify-between gap-2 ${
                        isSelected
                          ? 'bg-[#EFF6FF] border-[#1D4ED8] shadow-sm'
                          : 'bg-[#FAF9F6] border-[#E5E0D8] hover:border-[#D4CEBF]'
                      }`}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-serif font-bold text-[#1A1A1A] truncate">{p.name}</span>
                          {isDefault && (
                            <span className="px-1.5 py-0.2 rounded-sm text-[10px] bg-emerald-50 text-emerald-800 border border-emerald-300 shrink-0 font-medium font-mono">
                              默认
                            </span>
                          )}
                        </div>
                        <span className="text-[10px] text-[#666666] font-mono truncate block mt-0.5">
                          {p.filename}
                        </span>
                      </div>

                      {p.id !== 'erotic-novel-policy.md' && p.id !== 'general-novel-policy.md' && p.id !== 'translation-policy.md' && (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDeletePrompt(p.id);
                          }}
                          className="text-[#888888] hover:text-rose-600 p-1 rounded-sm hover:bg-white transition-colors cursor-pointer"
                          title="删除此自定义 Prompt"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Review Prompts Section */}
            <div className="bg-white border border-[#E5E0D8] p-4 rounded-sm space-y-3 shadow-sm">
              <div className="flex items-center justify-between">
                <h3 className="text-xs font-serif font-bold text-[#1A1A1A] uppercase tracking-wider flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-purple-700" />
                  一致性审阅提示词 ({prompts.filter(p => p.type === 'review').length})
                </h3>
              </div>

              <div className="space-y-2">
                {prompts.filter(p => p.type === 'review').map((p) => {
                  const isSelected = p.id === selectedPromptId;

                  return (
                    <div
                      key={p.id}
                      onClick={() => handleSelectPrompt(p.id)}
                      className={`p-3 rounded-sm border transition-all cursor-pointer flex items-center justify-between gap-2 ${
                        isSelected
                          ? 'bg-purple-50 border-purple-400 shadow-sm'
                          : 'bg-[#FAF9F6] border-[#E5E0D8] hover:border-[#D4CEBF]'
                      }`}
                    >
                      <div className="min-w-0 flex-1">
                        <span className="text-xs font-serif font-bold text-[#1A1A1A] truncate block">{p.name}</span>
                        <span className="text-[10px] text-[#666666] font-mono truncate block mt-0.5">
                          {p.filename}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          {/* Right Column: Markdown Prompt Editor */}
          <div className="lg:col-span-2 bg-white border border-[#E5E0D8] rounded-sm p-6 space-y-4 shadow-sm flex flex-col justify-between">
            {selectedPrompt ? (
              <div className="space-y-4 flex-1 flex flex-col">
                {/* Editor Header */}
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-[#E5E0D8]">
                  <div>
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-serif font-bold text-[#1A1A1A]">{selectedPrompt.name}</h3>
                      <span className={`px-2 py-0.5 rounded-sm text-[10px] font-mono font-medium ${
                        selectedPrompt.type === 'translation'
                          ? 'bg-[#EFF6FF] text-[#1D4ED8] border border-[#BFDBFE]'
                          : 'bg-purple-50 text-purple-800 border border-purple-200'
                      }`}>
                        {selectedPrompt.type === 'translation' ? '翻译规范' : '审阅规范'}
                      </span>
                    </div>
                    <span className="text-xs text-[#666666] font-mono mt-0.5 block">{selectedPrompt.path}</span>
                  </div>

                  <div className="flex items-center gap-2">
                    {selectedPrompt.type === 'translation' && (
                      <button
                        type="button"
                        onClick={() => handleSetAsDefaultPolicy(selectedPrompt.path)}
                        disabled={isDefaultPolicy}
                        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-sm text-xs font-medium transition-all cursor-pointer ${
                          isDefaultPolicy
                            ? 'bg-emerald-50 border border-emerald-300 text-emerald-800 cursor-default'
                            : 'bg-white hover:bg-[#FAF9F6] text-[#1A1A1A] border border-[#E5E0D8] shadow-sm'
                        }`}
                      >
                        <Check className="w-3.5 h-3.5" />
                        {isDefaultPolicy ? '当前系统默认' : '设为默认规范'}
                      </button>
                    )}
                  </div>
                </div>

                {/* Editor Body */}
                <div className="space-y-1.5 flex-1 flex flex-col">
                  <div className="flex items-center justify-between text-xs text-[#666666]">
                    <span className="flex items-center gap-1 font-serif">
                      <Edit3 className="w-3.5 h-3.5 text-[#1D4ED8]" />
                      Markdown 提示词内容 (实时生效)
                    </span>
                    <span className="font-mono text-[11px] text-[#888888]">
                      {editingPromptContent.length} 字符
                    </span>
                  </div>

                  <textarea
                    value={editingPromptContent}
                    onChange={(e) => setEditingPromptContent(e.target.value)}
                    rows={18}
                    className="w-full bg-[#FAF9F6] border border-[#E5E0D8] rounded-sm p-4 text-xs text-[#1A1A1A] font-mono leading-relaxed focus:outline-none focus:border-[#1D4ED8] resize-y flex-1"
                    placeholder="在此编辑提示词规范..."
                  />
                </div>

                {/* Editor Actions Footer */}
                <div className="flex items-center justify-between pt-3 border-t border-[#E5E0D8]">
                  <div>
                    {promptSaveSuccess && (
                      <span className="text-xs text-emerald-800 flex items-center gap-1 font-bold">
                        <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                        Prompt 已成功保存！
                      </span>
                    )}
                  </div>

                  <button
                    type="button"
                    onClick={handleSavePrompt}
                    disabled={isSavingPrompt}
                    className="flex items-center gap-2 px-6 py-2.5 rounded-sm bg-[#1D4ED8] hover:bg-[#1E40AF] text-white text-xs font-semibold shadow-sm transition-all cursor-pointer disabled:opacity-50"
                  >
                    <Save className="w-4 h-4" />
                    {isSavingPrompt ? '保存中...' : '保存修改 (Save Prompt)'}
                  </button>
                </div>
              </div>
            ) : (
              <div className="text-center py-20 text-[#888888] text-xs font-serif">
                请在左侧列表中选择一个提示词规范进行查看与编辑
              </div>
            )}
          </div>
        </div>
      )}

      {/* Add Provider Modal */}
      {providerPendingDeletion && config?.providers && (
        <Modal title="迁移 Provider 角色引用" onClose={() => setProviderPendingDeletion(null)} className="p-6 max-w-lg w-full space-y-4">
          <h3 className="font-serif font-bold">删除前迁移角色引用</h3>
          <p className="text-xs text-[#4A4A4A]">
            Provider [{providerPendingDeletion}] 被 {providerRoleReferences(config, providerPendingDeletion).join('、')} 引用。请选择替代 Provider；迁移和删除将在点击「保存配置」时一并提交。
          </p>
          <label className="block text-xs font-medium" htmlFor="provider-replacement">替代 Provider</label>
          <select
            id="provider-replacement"
            value={providerReplacementId}
            onChange={(event) => setProviderReplacementId(event.target.value)}
            className="w-full border border-[#E5E0D8] bg-white p-2 text-xs"
          >
            {Object.keys(config.providers).filter((id) => id !== providerPendingDeletion).map((id) => (
              <option key={id} value={id}>{id}</option>
            ))}
          </select>
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setProviderPendingDeletion(null)} className="border px-4 py-2 text-xs">取消</button>
            <button type="button" disabled={!providerReplacementId} onClick={handleMigrateAndDeleteProvider} className="bg-[#1D4ED8] px-4 py-2 text-xs text-white disabled:opacity-50">
              迁移引用并删除
            </button>
          </div>
        </Modal>
      )}

      {showAddProviderModal && (
        <Modal title="新增 AI Provider" onClose={() => setShowAddProviderModal(false)} className="p-6 max-w-lg w-full space-y-5">
            <div className="flex items-center justify-between border-b border-[#E5E0D8] pb-3">
              <h3 className="text-base font-serif font-bold text-[#1A1A1A] flex items-center gap-2">
                <Plus className="w-4 h-4 text-[#1D4ED8]" />
                新增 AI Provider
              </h3>
              <button
                type="button"
                onClick={() => setShowAddProviderModal(false)}
                aria-label="关闭新增 Provider 对话框"
                className="text-[#888888] hover:text-[#1A1A1A] cursor-pointer"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleAddProviderSubmit} className="space-y-4 text-xs">
              <div>
                <label className="text-[#4A4A4A] block mb-1 font-medium font-serif">
                  Provider 唯一标识 (ID)
                </label>
                <input
                  type="text"
                  required
                  placeholder="例如: siliconflow / deepseek_custom / ollama"
                  value={newProviderId}
                  onChange={(e) => setNewProviderId(e.target.value)}
                  className="w-full bg-[#FAF9F6] border border-[#E5E0D8] rounded-sm p-2.5 text-[#1A1A1A] font-mono focus:outline-none focus:border-[#1D4ED8]"
                />
              </div>

              <div>
                <label className="text-[#4A4A4A] block mb-1 font-medium font-serif">Provider 类型</label>
                <select
                  value={newProviderType}
                  onChange={(e) => setNewProviderType(e.target.value)}
                  className="w-full bg-[#FAF9F6] border border-[#E5E0D8] rounded-sm p-2.5 text-[#1A1A1A] focus:outline-none focus:border-[#1D4ED8]"
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
                    <label className="text-[#4A4A4A] block mb-1 font-medium font-serif">Base URL</label>
                    <input
                      type="text"
                      required
                      placeholder="例如: https://api.deepseek.com/v1"
                      value={newBaseUrl}
                      onChange={(e) => setNewBaseUrl(e.target.value)}
                      className="w-full bg-[#FAF9F6] border border-[#E5E0D8] rounded-sm p-2.5 text-[#1A1A1A] font-mono focus:outline-none focus:border-[#1D4ED8]"
                    />
                  </div>

                  <div>
                    <label className="text-[#4A4A4A] block mb-1 font-medium font-serif">
                      API Key (密钥)
                    </label>
                    <input
                      type="password"
                      placeholder="sk-xxxxxxxx 或 $ENV_VAR"
                      value={newApiKey}
                      onChange={(e) => setNewApiKey(e.target.value)}
                      className="w-full bg-[#FAF9F6] border border-[#E5E0D8] rounded-sm p-2.5 text-[#1A1A1A] font-mono focus:outline-none focus:border-[#1D4ED8]"
                    />
                  </div>
                </>
              )}

              <div>
                <label className="text-[#4A4A4A] block mb-1 font-medium font-serif">模型名称 (Model)</label>
                <input
                  type="text"
                  required
                  placeholder="例如: deepseek-chat 或 gpt-4o-mini"
                  value={newModel}
                  onChange={(e) => setNewModel(e.target.value)}
                  className="w-full bg-[#FAF9F6] border border-[#E5E0D8] rounded-sm p-2.5 text-[#1A1A1A] font-mono focus:outline-none focus:border-[#1D4ED8]"
                />
              </div>

              {newProviderType === 'openai' && (
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-[#4A4A4A] block mb-1 font-medium font-serif">采样温度</label>
                    <input
                      type="number"
                      step="0.1"
                      min="0.0"
                      max="2.0"
                      value={newTemperature}
                      onChange={(e) => setNewTemperature(parseFloat(e.target.value))}
                      className="w-full bg-[#FAF9F6] border border-[#E5E0D8] rounded-sm p-2.5 text-[#1A1A1A] font-mono focus:outline-none focus:border-[#1D4ED8]"
                    />
                  </div>
                  <div>
                    <label className="text-[#4A4A4A] block mb-1 font-medium font-serif">上下文窗口 (Tokens)</label>
                    <input
                      type="number"
                      step="1024"
                      value={newContextTokens}
                      onChange={(e) => setNewContextTokens(parseInt(e.target.value, 10))}
                      className="w-full bg-[#FAF9F6] border border-[#E5E0D8] rounded-sm p-2.5 text-[#1A1A1A] font-mono focus:outline-none focus:border-[#1D4ED8]"
                    />
                  </div>
                </div>
              )}

              <div className="flex items-center justify-end gap-2 pt-3 border-t border-[#E5E0D8]">
                <button
                  type="button"
                  onClick={() => setShowAddProviderModal(false)}
                  className="px-4 py-2 rounded-sm bg-white hover:bg-[#FAF9F6] border border-[#E5E0D8] text-[#4A4A4A] font-medium cursor-pointer"
                >
                  取消
                </button>
                <button
                  type="submit"
                  className="px-5 py-2 rounded-sm bg-[#1D4ED8] hover:bg-[#1E40AF] text-white font-semibold shadow-sm cursor-pointer"
                >
                  添加此 Provider
                </button>
              </div>
            </form>
        </Modal>
      )}

      {/* Add Prompt Modal */}
      {showAddPromptModal && (
        <Modal title="新建自定义 Prompt 规范" onClose={() => setShowAddPromptModal(false)} className="p-6 max-w-xl w-full space-y-5">
            <div className="flex items-center justify-between border-b border-[#E5E0D8] pb-3">
              <h3 className="text-base font-serif font-bold text-[#1A1A1A] flex items-center gap-2">
                <Plus className="w-4 h-4 text-[#1D4ED8]" />
                新建自定义 Prompt 规范
              </h3>
              <button
                type="button"
                onClick={() => setShowAddPromptModal(false)}
                aria-label="关闭新建 Prompt 对话框"
                className="text-[#888888] hover:text-[#1A1A1A] cursor-pointer"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleCreatePromptSubmit} className="space-y-4 text-xs">
              <div>
                <label className="text-[#4A4A4A] block mb-1 font-medium font-serif">
                  规范文件名 (Filename)
                </label>
                <input
                  type="text"
                  required
                  placeholder="例如: wuxia-policy.md 或 strict-translation.md"
                  value={newPromptFilename}
                  onChange={(e) => setNewPromptFilename(e.target.value)}
                  className="w-full bg-[#FAF9F6] border border-[#E5E0D8] rounded-sm p-2.5 text-[#1A1A1A] font-mono focus:outline-none focus:border-[#1D4ED8]"
                />
              </div>

              <div>
                <label className="text-[#4A4A4A] block mb-1 font-medium font-serif">
                  提示词规范内容 (Markdown)
                </label>
                <textarea
                  required
                  rows={10}
                  placeholder="# 自定义翻译规范&#10;&#10;你是一位精通..."
                  value={newPromptContent}
                  onChange={(e) => setNewPromptContent(e.target.value)}
                  className="w-full bg-[#FAF9F6] border border-[#E5E0D8] rounded-sm p-3 text-[#1A1A1A] font-mono leading-relaxed focus:outline-none focus:border-[#1D4ED8]"
                />
              </div>

              <div className="flex items-center justify-end gap-2 pt-3 border-t border-[#E5E0D8]">
                <button
                  type="button"
                  onClick={() => setShowAddPromptModal(false)}
                  className="px-4 py-2 rounded-sm bg-white hover:bg-[#FAF9F6] border border-[#E5E0D8] text-[#4A4A4A] font-medium cursor-pointer"
                >
                  取消
                </button>
                <button
                  type="submit"
                  className="px-5 py-2 rounded-sm bg-[#1D4ED8] hover:bg-[#1E40AF] text-white font-semibold shadow-sm cursor-pointer"
                >
                  创建并保存
                </button>
              </div>
            </form>
        </Modal>
      )}
    </div>
  );
};
