import React, { useState, useEffect } from 'react';
import {
  Zap,
  CheckCircle2,
  XCircle,
  RotateCw,
  Save,
  Settings,
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
    runPreflightTest();
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
    } catch (err: any) {
      alert(`保存配置失败: ${err.message}`);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="space-y-8 max-w-5xl mx-auto pb-16">
      {/* Top Banner */}
      <div className="bg-slate-900/90 border border-slate-800 p-6 rounded-2xl flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-indigo-950/80 border border-indigo-800/50 text-indigo-300">
              System & Preflight
            </span>
            <h2 className="text-xl font-bold text-white tracking-tight">模型路由与连通性预检</h2>
          </div>
          <p className="text-xs text-slate-400 mt-1">
            测试各 AI Provider 的网络连通性、实时毫秒级延迟与响应契约，配置主译与容灾降级拓扑。
          </p>
        </div>

        <button
          onClick={runPreflightTest}
          disabled={isRunningPreflight}
          className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-lg shadow-indigo-600/30 transition-all hover:scale-105 active:scale-95 disabled:opacity-50 cursor-pointer"
        >
          <RotateCw className={`w-4 h-4 ${isRunningPreflight ? 'animate-spin' : ''}`} />
          {isRunningPreflight ? '正在探测连通性...' : '一键连通性测试'}
        </button>
      </div>

      {/* Preflight Results Grid */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-bold text-slate-200 flex items-center gap-2">
            <Zap className="w-4 h-4 text-amber-400" />
            Provider 健康状态与延迟指标
          </h3>
          {preflightData && (
            <span
              className={`text-xs font-mono px-2.5 py-0.5 rounded-full border ${
                preflightData.all_passed
                  ? 'bg-emerald-950/60 border-emerald-800/60 text-emerald-400'
                  : 'bg-rose-950/60 border-rose-800/60 text-rose-400'
              }`}
            >
              {preflightData.all_passed ? 'ALL PROVIDERS READY' : 'SOME PROVIDERS FAILED'}
            </span>
          )}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {!preflightData ? (
            <div className="col-span-3 text-center py-12 text-slate-500 text-xs border border-dashed border-slate-800 rounded-2xl">
              正在运行预检探测...
            </div>
          ) : (
            preflightData.results.map((res: PreflightProviderResult, idx: number) => {
              const isOk = res.status === 'ok';

              return (
                <div
                  key={idx}
                  className={`p-5 rounded-2xl border transition-all ${
                    isOk
                      ? 'bg-slate-900/80 border-slate-800 hover:border-emerald-800/50'
                      : 'bg-rose-950/20 border-rose-900/50'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <span className="font-bold text-slate-100 text-sm">{res.provider}</span>
                    {isOk ? (
                      <span className="flex items-center gap-1 text-[11px] font-mono font-bold text-emerald-400 bg-emerald-950 px-2 py-0.5 rounded-full border border-emerald-800/50">
                        <CheckCircle2 className="w-3.5 h-3.5" />
                        {res.latency_ms} ms
                      </span>
                    ) : (
                      <span className="flex items-center gap-1 text-[11px] font-mono font-bold text-rose-400 bg-rose-950 px-2 py-0.5 rounded-full border border-rose-800/50">
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
                        <span className="text-amber-300 font-medium">{res.role}</span>
                      </div>
                    )}
                    <p className={`text-[11px] pt-2 border-t border-slate-800/80 ${isOk ? 'text-slate-400' : 'text-rose-300'}`}>
                      {res.message}
                    </p>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Role Routing Configuration Form */}
      {config && (
        <form onSubmit={handleSaveConfig} className="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 space-y-6">
          <div className="flex items-center justify-between border-b border-slate-800 pb-4">
            <h3 className="text-sm font-bold text-slate-200 flex items-center gap-2">
              <Settings className="w-4 h-4 text-indigo-400" />
              模型角色路由与拓扑编排 (config.toml)
            </h3>
            {saveSuccess && (
              <span className="text-xs text-emerald-400 flex items-center gap-1 animate-fade-in">
                <CheckCircle2 className="w-4 h-4" />
                配置已保存并生效
              </span>
            )}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            {/* Primary Translator */}
            <div>
              <label className="text-xs font-medium text-slate-300 block mb-1.5">
                主译模型 (Primary Translator)
              </label>
              <select
                value={config.roles?.primary_translator || ''}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    roles: { ...config.roles, primary_translator: e.target.value },
                  })
                }
                className="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-xs text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
              >
                {Object.keys(config.providers || {}).map((p) => (
                  <option key={p} value={p}>
                    {p} ({config.providers?.[p]?.model || config.providers?.[p]?.type})
                  </option>
                ))}
              </select>
            </div>

            {/* Consistency Reviewer */}
            <div>
              <label className="text-xs font-medium text-slate-300 block mb-1.5">
                章节一致性审阅模型 (Consistency Reviewer)
              </label>
              <select
                value={config.roles?.reviewer || ''}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    roles: { ...config.roles, reviewer: e.target.value },
                  })
                }
                className="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-xs text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
              >
                {Object.keys(config.providers || {}).map((p) => (
                  <option key={p} value={p}>
                    {p} ({config.providers?.[p]?.model || config.providers?.[p]?.type})
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Pipeline params */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5 pt-4 border-t border-slate-800">
            <div>
              <label className="text-xs font-medium text-slate-300 block mb-1.5">
                单批翻译最大字符数 (Batch Max Chars)
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
                className="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-xs text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
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
                className="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-xs text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
              >
                <option value="horizontal">重构为中文横排版式 (Horizontal)</option>
                <option value="preserve">保留原版竖排版式 (Preserve)</option>
              </select>
            </div>
          </div>

          <div className="flex justify-end pt-4 border-t border-slate-800">
            <button
              type="submit"
              disabled={isSaving}
              className="flex items-center gap-2 px-6 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-lg shadow-indigo-600/30 transition-all disabled:opacity-50 cursor-pointer"
            >
              <Save className="w-4 h-4" />
              {isSaving ? '正在保存...' : '保存配置'}
            </button>
          </div>
        </form>
      )}
    </div>
  );
};
