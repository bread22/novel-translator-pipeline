import React, { useState, useEffect, useRef } from 'react';
import {
  Play,
  Pause,
  Square,
  Sparkles,
  Zap,
  Activity,
  ArrowRight,
  Split,
  Terminal,
} from 'lucide-react';
import { BookSummary, PromptItem, StreamEvent, TaskStatusResponse } from '../types/api';
import { api } from '../lib/api';

interface LiveStudioViewProps {
  book: BookSummary | null;
  activeTask: TaskStatusResponse | null;
  streamEvents: StreamEvent[];
  onRefreshTask: () => Promise<void>;
  onRefreshBooks: () => Promise<void>;
}

export const LiveStudioView: React.FC<LiveStudioViewProps> = ({
  book,
  activeTask,
  streamEvents,
  onRefreshTask,
  onRefreshBooks,
}) => {
  const [isStarting, setIsStarting] = useState(false);
  const [applyFixes, setApplyFixes] = useState(true);
  const [autonomous, setAutonomous] = useState(true);
  const [layout, setLayout] = useState<'horizontal' | 'preserve'>('horizontal');
  const [eventFilter, setEventFilter] = useState<'all' | 'fallback' | 'review'>('all');
  const [prompts, setPrompts] = useState<PromptItem[]>([]);
  const [selectedPolicy, setSelectedPolicy] = useState<string>('docs/prompts/erotic-novel-policy.md');
  const feedBottomRef = useRef<HTMLDivElement>(null);

  const isRunning = activeTask && activeTask.status === 'running';
  const isPaused = activeTask && activeTask.status === 'paused';

  useEffect(() => {
    feedBottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [streamEvents]);

  useEffect(() => {
    api.getPrompts().then((list) => {
      setPrompts(list);
      const defaultTranslation = list.find((p) => p.type === 'translation');
      if (defaultTranslation) {
        setSelectedPolicy(defaultTranslation.path);
      }
    }).catch(() => {});
  }, []);

  if (!book) {
    return (
      <div className="text-center py-24 border border-dashed border-slate-800 rounded-2xl p-12 max-w-xl mx-auto">
        <Activity className="w-12 h-12 text-slate-600 mx-auto mb-3" />
        <h3 className="text-slate-300 font-medium">未选择任何书籍</h3>
        <p className="text-slate-500 text-xs mt-1">请在顶部下拉列表或书架中心选择一部小说进入翻译控制台</p>
      </div>
    );
  }

  const handleStart = async () => {
    setIsStarting(true);
    try {
      await api.startPipeline({
        book_id: book.id,
        apply: applyFixes,
        autonomous: autonomous,
        finalize: true,
        layout: layout,
        translation_policy: selectedPolicy,
      });
      await onRefreshTask();
    } catch (err: any) {
      alert(`启动流水线失败: ${err.message}`);
    } finally {
      setIsStarting(false);
    }
  };

  const handlePause = async () => {
    try {
      await api.pausePipeline(book.id);
      await onRefreshTask();
    } catch (err: any) {
      alert(`暂停失败: ${err.message}`);
    }
  };

  const handleResume = async () => {
    try {
      await api.resumePipeline(book.id);
      await onRefreshTask();
    } catch (err: any) {
      alert(`恢复失败: ${err.message}`);
    }
  };

  const handleStop = async () => {
    if (!confirm('确定要终止当前流水线吗？已翻译的段落会自动保存。')) return;
    try {
      await api.stopPipeline(book.id);
      await onRefreshTask();
      await onRefreshBooks();
    } catch (err: any) {
      alert(`终止失败: ${err.message}`);
    }
  };

  const filteredEvents = streamEvents.filter((evt) => {
    if (eventFilter === 'fallback') return evt.event === 'fallback_triggered';
    if (eventFilter === 'review') return evt.event === 'review_completed';
    return true;
  });

  const progressPercent = activeTask
    ? Math.round(activeTask.overall_progress * 100)
    : Math.round((book.progress_percentage || 0) * 100);

  const latestStatusEvent = [...streamEvents].reverse().find(
    (e) => e.data && typeof e.data === 'object' && e.data.task_id && e.data.message
  );
  const liveMessage =
    latestStatusEvent?.data?.message ||
    activeTask?.message ||
    (isRunning ? '流水线推进中...' : '准备就绪');

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-12">
      {/* Header & Controls Toolbar */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 bg-slate-900/90 border border-slate-800 p-6 rounded-2xl shadow-xl backdrop-blur-md">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-xs font-mono px-2 py-0.5 rounded bg-indigo-950 border border-indigo-700/50 text-indigo-300">
              Live Pipeline
            </span>
            <h2 className="text-xl font-bold text-white tracking-tight">{book.name}</h2>
          </div>
          <p className="text-xs text-slate-400 mt-1 flex items-center gap-2">
            <span>总章节: {book.total_chapters} 章</span>
            <span>·</span>
            <span>总段落: {book.total_paragraphs} 段</span>
            <span>·</span>
            <span className="text-indigo-400 font-medium">
              {liveMessage}
            </span>
          </p>
        </div>

        {/* Action Buttons */}
        <div className="flex items-center gap-2.5 flex-wrap">
          {!isRunning && !isPaused ? (
            <button
              onClick={handleStart}
              disabled={isStarting}
              className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white text-xs font-semibold shadow-lg shadow-indigo-600/30 transition-all hover:scale-105 active:scale-95 disabled:opacity-50 cursor-pointer"
            >
              <Play className="w-4 h-4 fill-white" />
              {isStarting ? '启动中...' : '启动全自动流水线'}
            </button>
          ) : isRunning ? (
            <>
              <button
                onClick={handlePause}
                className="flex items-center gap-1.5 px-4 py-2.5 rounded-xl bg-amber-600 hover:bg-amber-500 text-white text-xs font-semibold shadow-md shadow-amber-600/20 transition-all cursor-pointer"
              >
                <Pause className="w-4 h-4 fill-white" />
                暂停
              </button>
              <button
                onClick={handleStop}
                className="flex items-center gap-1.5 px-4 py-2.5 rounded-xl bg-rose-600/90 hover:bg-rose-500 text-white text-xs font-semibold shadow-md shadow-rose-600/20 transition-all cursor-pointer"
              >
                <Square className="w-4 h-4 fill-white" />
                终止
              </button>
            </>
          ) : (
            <>
              <button
                onClick={handleResume}
                className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold shadow-md shadow-emerald-600/20 transition-all cursor-pointer"
              >
                <Play className="w-4 h-4 fill-white" />
                继续流水线
              </button>
              <button
                onClick={handleStop}
                className="flex items-center gap-1.5 px-4 py-2.5 rounded-xl bg-rose-600/90 hover:bg-rose-500 text-white text-xs font-semibold shadow-md shadow-rose-600/20 transition-all cursor-pointer"
              >
                <Square className="w-4 h-4 fill-white" />
                终止
              </button>
            </>
          )}
        </div>
      </div>

      {/* Progress & Live Topology */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left 2 Cols: Fallback Topology Graphic Widget */}
        <div className="lg:col-span-2 bg-slate-900/80 border border-slate-800 p-6 rounded-2xl flex flex-col justify-between">
          <div className="flex items-center justify-between gap-2 mb-4">
            <div className="flex items-center gap-2">
              <Zap className="w-4 h-4 text-amber-400" />
              <h3 className="text-sm font-bold text-slate-200">两级降级容灾流向拓扑 (Fallback Topology)</h3>
            </div>
            <span className="text-[11px] text-slate-500 font-mono">100% 自动闭环调度</span>
          </div>

          {/* Visual Topology DAG */}
          <div className="grid grid-cols-1 sm:grid-cols-5 gap-3 items-center py-4 text-center">
            {/* Node 1: Primary */}
            <div className={`p-4 rounded-xl border transition-all ${
              isRunning ? 'bg-indigo-950/60 border-indigo-500 glow-primary' : 'bg-slate-950/60 border-slate-800'
            }`}>
              <div className="text-[10px] text-indigo-400 font-mono font-medium">PRIMARY</div>
              <div className="font-bold text-xs text-slate-100 mt-1">主译模型</div>
              <div className="text-[10px] text-slate-400 mt-0.5">Gemini / Nemotron</div>
            </div>

            {/* Split Icon */}
            <div className="flex justify-center text-slate-600">
              <div className="flex flex-col items-center">
                <Split className="w-4 h-4 text-amber-500 animate-pulse" />
                <span className="text-[9px] text-amber-500/80 font-mono mt-0.5">敏感词拦截</span>
              </div>
            </div>

            {/* Node 2: Fallback 1 */}
            <div className="p-4 rounded-xl border bg-slate-950/60 border-slate-800">
              <div className="text-[10px] text-emerald-400 font-mono font-medium">FALLBACK #1</div>
              <div className="font-bold text-xs text-slate-100 mt-1">一级备用</div>
              <div className="text-[10px] text-slate-400 mt-0.5">OpenCode / Muse</div>
            </div>

            {/* Arrow */}
            <div className="flex justify-center text-slate-600">
              <ArrowRight className="w-4 h-4 text-slate-500" />
            </div>

            {/* Node 3: Fallback 2 */}
            <div className="p-4 rounded-xl border bg-slate-950/60 border-slate-800">
              <div className="text-[10px] text-rose-400 font-mono font-medium">FALLBACK #2</div>
              <div className="font-bold text-xs text-slate-100 mt-1">二级备用</div>
              <div className="text-[10px] text-slate-400 mt-0.5">LM Studio 本地无审查</div>
            </div>
          </div>

          {/* Chapter Consistency Reviewer Bar */}
          <div className="mt-4 p-3 rounded-xl bg-purple-950/30 border border-purple-800/40 flex items-center justify-between text-xs">
            <div className="flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-purple-400" />
              <span className="font-semibold text-purple-200">章节长程一致性审阅 (Consistency Reviewer)</span>
            </div>
            <span className="text-[11px] text-purple-300 font-mono">100% ID 校验 · 动态术语记忆提取 · 客观缺陷自动写回</span>
          </div>
        </div>

        {/* Right Col: Progress Gauge & Summary */}
        <div className="bg-slate-900/80 border border-slate-800 p-6 rounded-2xl flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-bold text-slate-200">执行进度指标</h3>
              <span className={`text-xs font-mono px-2 py-0.5 rounded-full ${
                isRunning ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' : 'bg-slate-800 text-slate-400'
              }`}>
                {isRunning ? 'RUNNING' : isPaused ? 'PAUSED' : 'IDLE'}
              </span>
            </div>

            <div className="space-y-4">
              <div>
                <div className="flex justify-between text-xs mb-1.5">
                  <span className="text-slate-400">全书翻译完成度</span>
                  <span className="font-bold text-slate-200 font-mono">{progressPercent}%</span>
                </div>
                <div className="w-full bg-slate-800 rounded-full h-2 overflow-hidden">
                  <div
                    className="bg-gradient-to-r from-indigo-500 via-purple-500 to-emerald-400 h-full rounded-full transition-all duration-500"
                    style={{ width: `${progressPercent}%` }}
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2 pt-2 border-t border-slate-800 text-xs">
                <div className="bg-slate-950/60 p-2.5 rounded-lg border border-slate-800/80">
                  <span className="text-slate-500 text-[10px]">当前章节</span>
                  <p className="font-mono font-bold text-slate-200 mt-0.5">
                    {activeTask?.current_chapter_index || 0} / {activeTask?.total_chapters || book.total_chapters}
                  </p>
                </div>
                <div className="bg-slate-950/60 p-2.5 rounded-lg border border-slate-800/80">
                  <span className="text-slate-500 text-[10px]">容灾救回段落</span>
                  <p className="font-mono font-bold text-emerald-400 mt-0.5">
                    {activeTask?.recovered_paragraphs || 0} 段
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* Config options */}
          <div className="mt-4 pt-4 border-t border-slate-800/80 space-y-2 text-xs">
            <label className="flex items-center gap-2 text-slate-300 cursor-pointer">
              <input
                type="checkbox"
                checked={applyFixes}
                onChange={(e) => setApplyFixes(e.target.checked)}
                className="rounded bg-slate-800 border-slate-700 text-indigo-600 focus:ring-0"
              />
              <span>自动写回高置信度客观审阅修复</span>
            </label>
            <label className="flex items-center gap-2 text-slate-300 cursor-pointer">
              <input
                type="checkbox"
                checked={autonomous}
                onChange={(e) => setAutonomous(e.target.checked)}
                className="rounded bg-slate-800 border-slate-700 text-indigo-600 focus:ring-0"
              />
              <span>纯自主模式 (遇到冲突保持原样不阻塞)</span>
            </label>
            <div className="flex items-center justify-between pt-1 text-slate-400">
              <span>导出排版:</span>
              <select
                value={layout}
                onChange={(e) => setLayout(e.target.value as 'horizontal' | 'preserve')}
                className="bg-slate-950 border border-slate-800 rounded px-2 py-0.5 text-[11px] text-slate-200"
              >
                <option value="horizontal">重构为中文横排</option>
                <option value="preserve">保留原版竖排</option>
              </select>
            </div>
            {/* Prompt Policy Selector */}
            <div className="pt-2 border-t border-slate-800/80 space-y-1">
              <span className="text-[11px] font-medium text-slate-300 block">
                选择翻译提示词规范 (Policy Prompt):
              </span>
              <select
                value={selectedPolicy}
                onChange={(e) => setSelectedPolicy(e.target.value)}
                className="w-full bg-slate-950 border border-slate-700/70 rounded-lg px-2 py-1.5 text-xs text-indigo-300 font-medium focus:outline-none focus:border-indigo-500"
              >
                {prompts.filter(p => p.type === 'translation').map((p) => (
                  <option key={p.path} value={p.path}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>
      </div>

      {/* Live SSE Stream Waterfall Feed */}
      <div className="bg-slate-900/80 border border-slate-800 rounded-2xl overflow-hidden shadow-xl">
        <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between gap-4 bg-slate-950/50">
          <div className="flex items-center gap-2.5">
            <Terminal className="w-4 h-4 text-indigo-400" />
            <h3 className="text-sm font-bold text-slate-200">实时事件瀑布流 (SSE Event Stream)</h3>
            <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-400">
              {streamEvents.length} events
            </span>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => setEventFilter('all')}
              className={`px-2.5 py-1 rounded text-xs transition-all ${
                eventFilter === 'all' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              全部
            </button>
            <button
              onClick={() => setEventFilter('fallback')}
              className={`px-2.5 py-1 rounded text-xs transition-all ${
                eventFilter === 'fallback' ? 'bg-amber-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              降级事件
            </button>
            <button
              onClick={() => setEventFilter('review')}
              className={`px-2.5 py-1 rounded text-xs transition-all ${
                eventFilter === 'review' ? 'bg-purple-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              审阅报告
            </button>
          </div>
        </div>

        {/* Scrollable event list */}
        <div className="p-4 max-h-[420px] overflow-y-auto space-y-2.5 font-mono text-xs">
          {filteredEvents.length === 0 ? (
            <div className="text-center py-12 text-slate-500">
              <Activity className="w-8 h-8 mx-auto mb-2 opacity-40 animate-pulse" />
              <p>等待流水线事件推送中...</p>
            </div>
          ) : (
            filteredEvents.map((evt, idx) => {
              const isFallback = evt.event === 'fallback_triggered';
              const isReview = evt.event === 'review_completed';
              const isCompleted = evt.event === 'pipeline_completed';

              return (
                <div
                  key={idx}
                  className={`p-3 rounded-xl border transition-all ${
                    isFallback
                      ? 'bg-amber-950/40 border-amber-800/60 text-amber-200'
                      : isReview
                      ? 'bg-purple-950/40 border-purple-800/60 text-purple-200'
                      : isCompleted
                      ? 'bg-emerald-950/40 border-emerald-800/60 text-emerald-200'
                      : 'bg-slate-950/60 border-slate-800/70 text-slate-300'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2 text-[11px] mb-1">
                    <span className="font-bold uppercase tracking-wider text-indigo-400">
                      [{evt.event}]
                    </span>
                    <span className="text-slate-500">{new Date(evt.timestamp).toLocaleTimeString()}</span>
                  </div>

                  <div className="text-xs text-slate-200 break-all">
                    {typeof evt.data === 'string' ? evt.data : JSON.stringify(evt.data, null, 2)}
                  </div>
                </div>
              );
            })
          )}
          <div ref={feedBottomRef} />
        </div>
      </div>
    </div>
  );
};
