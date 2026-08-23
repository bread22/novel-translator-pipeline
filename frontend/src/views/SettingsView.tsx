import React, { useState, useEffect } from 'react';
import {
  Zap,
  CheckCircle2,
  XCircle,
  RotateCw,
  Save,
  Settings,
  Layers,
  Sparkles,
  Sliders,
  Server,
} from 'lucide-react';
import { PreflightProviderResult, PreflightResponse, SystemConfig } from '../types/api';
import { api } from '../lib/api';

export const SettingsView: React.FC = () => {
  const [config, setConfig] = useState<SystemConfig | null>(null);
  const [preflightData, setPreflightData] = useState<PreflightResponse | null>(null);
  const [isRunningPreflight, setIsRunningPreflight] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);

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
    } finally {
      setIsRunningPreflight(false);
    }
  };

  const handleSaveConfig = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!config) return;
    setIsSaving(true);
    try {
      await api.saveConfig(config);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
      // Re-run preflight after saving config to update role badges
      runPreflightTest();
    } catch (err: any) {
      alert(`保存配置失败: ${err.message}`);
    } finally {
      setIsSaving(false);
    }
  };

  const providersList = config ? Object.keys(config.providers || {}) : [];

  // Helper to get fallback array
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

  return (
    <div className="space-y-8 max-w-5xl mx-auto pb-16">
      {/* Top Banner */}
      <div className="bg-slate-900/90 border border-slate-800 p-6 rounded-2xl flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 shadow-xl">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-indigo-950/80 border border-indigo-800/50 text-indigo-300">
              System & Preflight
            </span>
            <h2 className="text-xl font-bold text-white tracking-tight">模型路由、降级拓扑与双审阅配置</h2>
          </div>
          <p className="text-xs text-slate-400 mt-1">
            配置主译模型、两级敏感词容灾备用（Fallback）、双模型一致性审阅（Dual Review）及连通性探测。
          </p>
        </div>

        <button
          onClick={runPreflightTest}
          disabled={isRunningPreflight}
          className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-lg shadow-indigo-600/30 transition-all hover:scale-105 active:scale-95 disabled:opacity-50 cursor-pointer"
        >
          <RotateCw className={`w-4 h-4 ${isRunningPreflight ? 'animate-spin' : ''}`} />
          {isRunningPreflight ? '正在并发探测 (5s)...' : '一键连通性测试'}
        </button>
      </div>

      {/* Preflight Diagnostics Section */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-bold text-slate-200 flex items-center gap-2">
            <Zap className="w-4 h-4 text-amber-400" />
            Provider 实时健康状态与毫秒延迟
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
          <div className="bg-slate-900/60 border border-slate-800/80 rounded-2xl p-6 text-center text-xs text-slate-400 space-y-2">
            <Server className="w-8 h-8 text-slate-600 mx-auto" />
            <p>点击上方【一键连通性测试】按钮，将并发探测已配置各 AI 模型的网络延迟与响应契约。</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {preflightData?.results.map((res: PreflightProviderResult, idx: number) => {
              const isOk = res.status === 'ok';

              return (
                <div
                  key={idx}
                  className={`p-5 rounded-2xl border transition-all ${
                    isOk
                      ? 'bg-slate-900/80 border-slate-800 hover:border-emerald-800/50'
                      : 'bg-slate-900/80 border-slate-800/60 opacity-80'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <span className="font-bold text-slate-100 text-sm font-mono">{res.provider}</span>
                    {isOk ? (
                      <span className="flex items-center gap-1 text-[11px] font-mono font-bold text-emerald-400 bg-emerald-950 px-2 py-0.5 rounded-full border border-emerald-800/50">
                        <CheckCircle2 className="w-3.5 h-3.5" />
                        {res.latency_ms} ms
                      </span>
                    ) : (
                      <span className="flex items-center gap-1 text-[11px] font-mono font-medium text-rose-400 bg-rose-950/60 px-2 py-0.5 rounded-full border border-rose-800/50">
                        <XCircle className="w-3.5 h-3.5" />
                        FAILED
                      </span>
                    )}
                  </div>

                  <div className="text-xs space-y-1">
                    <div className="flex items-center gap-1 text-slate-400">
                      <span>类型:</span>
                      <span className="font-mono text-indigo-300 uppercase">{res.type}</span>
                    </div>
                    {res.model && (
                      <div className="flex items-center gap-1 text-slate-400">
                        <span>模型:</span>
                        <span className="font-mono text-slate-200 truncate">{res.model}</span>
                      </div>
                    )}
                    {res.role && (
                      <div className="flex items-center gap-1 text-slate-400">
                        <span>角色:</span>
                        <span className={`font-medium ${res.role !== '未分配' ? 'text-amber-300' : 'text-slate-500'}`}>
                          {res.role}
                        </span>
                      </div>
                    )}
                    <p className={`text-[11px] pt-2 border-t border-slate-800/80 truncate ${isOk ? 'text-slate-400' : 'text-rose-300'}`} title={res.message}>
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
        <form onSubmit={handleSaveConfig} className="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 space-y-8 shadow-xl">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-slate-800 pb-4">
            <div>
              <h3 className="text-sm font-bold text-slate-200 flex items-center gap-2">
                <Settings className="w-4 h-4 text-indigo-400" />
                模型角色路由与双审阅拓扑编排 (config.toml)
              </h3>
              <p className="text-xs text-slate-400 mt-0.5">所有改动保存后即刻热生效于下一次翻译批次</p>
            </div>
            {saveSuccess && (
              <span className="text-xs text-emerald-400 flex items-center gap-1 animate-fade-in font-medium">
                <CheckCircle2 className="w-4 h-4" />
                配置已保存并生效
              </span>
            )}
          </div>

          {/* Section 1: Translation Routing & 2-Level Fallback */}
          <div className="space-y-4">
            <div className="flex items-center gap-2 text-xs font-bold text-indigo-300 uppercase tracking-wider">
              <Layers className="w-4 h-4" />
              1. 翻译模型主备容灾拓扑 (Translation & Fallback)
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {/* Primary Translator */}
              <div className="bg-slate-950/60 p-4 rounded-xl border border-indigo-900/40">
                <label className="text-xs font-bold text-slate-200 block mb-1">
                  主译模型 (Primary)
                </label>
                <p className="text-[11px] text-slate-500 mb-2">默认首选翻译后端</p>
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
          <div className="space-y-4 pt-4 border-t border-slate-800">
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
                <p className="text-[11px] text-slate-500 mb-2">负责提取术语、长程记忆并分析客观缺陷</p>
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

              {/* Secondary Reviewer (Active when dual_review is enabled) */}
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

          {/* Section 3: Advanced Pipeline Parameters */}
          <div className="space-y-4 pt-4 border-t border-slate-800">
            <div className="flex items-center gap-2 text-xs font-bold text-slate-300 uppercase tracking-wider">
              <Sliders className="w-4 h-4" />
              3. 流水线批次与版式参数 (Pipeline Parameters)
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

          {/* Submit */}
          <div className="flex justify-end pt-4 border-t border-slate-800">
            <button
              type="submit"
              disabled={isSaving}
              className="flex items-center gap-2 px-7 py-3 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-lg shadow-indigo-600/30 transition-all disabled:opacity-50 cursor-pointer"
            >
              <Save className="w-4 h-4" />
              {isSaving ? '正在保存配置...' : '保存配置 (Save to config.toml)'}
            </button>
          </div>
        </form>
      )}
    </div>
  );
};
