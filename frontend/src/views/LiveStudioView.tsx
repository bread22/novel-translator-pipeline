import React, { useState, useEffect, useRef } from 'react';
import {
  Play,
  Pause,
  Square,
  Zap,
  Activity,
  ArrowRight,
  Split,
  Terminal,
  CheckCircle2,
} from 'lucide-react';
import { BookSummary, PromptItem, ReviewerExecutionDetail, StreamEvent, SystemConfig, TaskStatusResponse } from '../types/api';
import { api } from '../lib/api';

interface LiveStudioViewProps {
  book: BookSummary | null;
  activeTask: TaskStatusResponse | null;
  streamEvents: StreamEvent[];
  onRefreshTask: () => Promise<void>;
  onRefreshBooks: () => Promise<void>;
  onClearEvents?: () => void;
}

export const LiveStudioView: React.FC<LiveStudioViewProps> = ({
  book,
  activeTask,
  streamEvents,
  onRefreshTask,
  onRefreshBooks,
  onClearEvents,
}) => {
  const [config, setConfig] = useState<SystemConfig | null>(null);
  const [isStarting, setIsStarting] = useState(false);
  const [applyFixes, setApplyFixes] = useState(true);
  const [autonomous, setAutonomous] = useState(true);
  const [layout, setLayout] = useState<'horizontal' | 'preserve'>('horizontal');
  const [eventFilter, setEventFilter] = useState<'all' | 'pipeline' | 'fallback'>('all');
  const [prompts, setPrompts] = useState<PromptItem[]>([]);
  const [selectedPolicy, setSelectedPolicy] = useState<string>('docs/prompts/erotic-novel-policy.md');
  const feedBottomRef = useRef<HTMLDivElement>(null);

  const isRunning = activeTask && activeTask.status === 'running';
  const isPaused = activeTask && activeTask.status === 'paused';

  useEffect(() => {
    feedBottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [streamEvents]);

  useEffect(() => {
    api.getConfig().then(setConfig).catch(() => {});
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
      <div className="text-center py-24 bg-white border border-dashed border-[#E5E0D8] rounded-sm p-12 max-w-xl mx-auto shadow-sm">
        <Activity className="w-12 h-12 text-[#888888] mx-auto mb-3" />
        <h3 className="text-[#1A1A1A] font-serif font-bold text-base">未选择任何书籍</h3>
        <p className="text-[#666666] text-xs mt-1">请在顶部下拉列表或任务调度中心选择一部小说进入翻译控制台</p>
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
    if (eventFilter === 'pipeline') {
      return ['pipeline_started', 'chapter_started', 'batch_completed', 'pipeline_progress', 'pipeline_phase_changed', 'pipeline_reviewer_status', 'chapter_completed', 'pipeline_completed'].includes(evt.event);
    }
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
    (isRunning ? '流水线推进中...' : '就绪');

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-12">
      
      {/* Header & Controls Toolbar (Editorial Editorial Suite) */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 bg-white border border-[#E5E0D8] p-6 rounded-sm shadow-sm">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-mono px-2 py-0.5 border border-[#1A1A1A] bg-[#FAF9F6] text-[#1A1A1A] font-bold rounded-sm">
              LIVE TRANSLATION SUITE
            </span>
            <h2 className="text-xl font-serif font-bold text-[#1A1A1A] tracking-tight">{book.name}</h2>
          </div>
          <p className="text-xs text-[#666666] mt-1 flex items-center gap-2 font-mono">
            <span>总章节: {book.total_chapters} 章 ({book.translated_chapters} 章已完成)</span>
            <span>·</span>
            <span>总段落: {book.total_paragraphs} 段 ({book.translated_paragraphs} 段已译)</span>
            <span>·</span>
            <span className="text-[#1D4ED8] font-medium font-sans">
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
              className="flex items-center gap-2 px-5 py-2.5 rounded-sm bg-[#1D4ED8] hover:bg-[#1E40AF] text-white text-xs font-semibold shadow-sm transition-all disabled:opacity-50 cursor-pointer"
            >
              <Play className="w-4 h-4 fill-white" />
              {isStarting ? '启动中...' : '启动全自动流水线'}
            </button>
          ) : isRunning ? (
            <>
              <button
                onClick={handlePause}
                className="flex items-center gap-1.5 px-4 py-2.5 rounded-sm bg-amber-50 border border-amber-300 text-amber-900 hover:bg-amber-100 text-xs font-semibold shadow-sm transition-all cursor-pointer"
              >
                <Pause className="w-4 h-4 fill-current" />
                暂停
              </button>
              <button
                onClick={handleStop}
                className="flex items-center gap-1.5 px-4 py-2.5 rounded-sm bg-rose-600 hover:bg-rose-500 text-white text-xs font-semibold shadow-sm transition-all cursor-pointer"
              >
                <Square className="w-4 h-4 fill-white" />
                终止
              </button>
            </>
          ) : (
            <>
              <button
                onClick={handleResume}
                className="flex items-center gap-1.5 px-5 py-2.5 rounded-sm bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold shadow-sm transition-all cursor-pointer"
              >
                <Play className="w-4 h-4 fill-white" />
                继续流水线
              </button>
              <button
                onClick={handleStop}
                className="flex items-center gap-1.5 px-4 py-2.5 rounded-sm bg-rose-600 hover:bg-rose-500 text-white text-xs font-semibold shadow-sm transition-all cursor-pointer"
              >
                <Square className="w-4 h-4 fill-white" />
                终止
              </button>
            </>
          )}
        </div>
      </div>

      {(() => {
        // Dynamic Role & Provider Resolution from real config
        const primaryName = config?.roles?.primary_translator || 'nemotron';
        const primaryProvider = config?.providers?.[primaryName];
        const primaryModel = primaryProvider?.model || primaryName;

        const fallbackList = config?.roles?.fallback_translators || ['gemini_lite', 'deepseek'];
        const fb1Name = fallbackList[0] || 'gemini_lite';
        const fb1Provider = config?.providers?.[fb1Name];
        const fb1Model = fb1Provider?.model || fb1Name;

        const fb2Name = fallbackList[1] || (fallbackList.length > 1 ? fallbackList[1] : 'deepseek');
        const fb2Provider = config?.providers?.[fb2Name];
        const fb2Model = fb2Provider?.model || fb2Name;

        const rev1Name = config?.roles?.reviewer || primaryName;
        const rev1Model = config?.providers?.[rev1Name]?.model || rev1Name;
        const isDualReview = config?.roles?.dual_review ?? true;
        const rev2Name = config?.roles?.secondary_reviewer || fb1Name;
        const rev2Model = config?.providers?.[rev2Name]?.model || rev2Name;

        const latestEvent = streamEvents[streamEvents.length - 1];
        const isFallbackActive = latestEvent?.event === 'fallback_triggered' || latestEvent?.event?.includes('fallback');
        const isReviewActive = activeTask?.phase === 'reviewing'
          || (!activeTask?.phase && (latestEvent?.event?.includes('review') || activeTask?.message?.includes('审阅') || activeTask?.message?.includes('一致性')));
        const isTranslationActive = activeTask?.phase === 'translating'
          || (!activeTask?.phase && !isFallbackActive && !isReviewActive);
        const hasRecovered = (activeTask?.recovered_paragraphs || 0) > 0;
        const rev1RawStatus = activeTask?.reviewer_states?.primary;
        const rev1Status = isReviewActive && rev1RawStatus === 'standby'
          ? 'pending'
          : rev1RawStatus || (isReviewActive ? 'pending' : 'standby');
        const rev2RawStatus = activeTask?.reviewer_states?.secondary;
        const rev2Status = !isDualReview
          ? 'disabled'
          : isReviewActive && rev2RawStatus === 'standby'
          ? 'pending'
          : rev2RawStatus || (isReviewActive ? 'pending' : 'standby');
        const reviewerBadge = (status: string) => ({
          reviewing: '● REVIEWING',
          completed: '✓ COMPLETED',
          failed: '× FAILED',
          cancelled: 'CANCELLED',
          disabled: 'DISABLED',
          pending: 'PENDING',
          standby: 'STANDBY',
        }[status] || 'STANDBY');
        const reviewerActive = (status: string) => status === 'reviewing';
        const reviewerDetailText = (detail: ReviewerExecutionDetail | undefined) => {
          if (!detail) return '';
          const parts: string[] = [];
          if (detail.chunk_index && detail.total_chunks) parts.push(`分块 ${detail.chunk_index}/${detail.total_chunks}`);
          if (detail.attempt) parts.push(`尝试 #${detail.attempt}`);
          if (detail.candidate_index && (detail.candidate_total || 0) > 1) {
            parts.push(`路由 ${detail.candidate_index}/${detail.candidate_total}`);
          }
          if (detail.split_path && detail.split_path !== 'root') parts.push(`子段 ${detail.split_path}`);
          return parts.join(' · ');
        };
        const reviewerCard = (
          role: 'primary' | 'secondary',
          label: string,
          configuredName: string,
          configuredModel: string,
          status: string,
        ) => {
          const detail = activeTask?.reviewer_details?.[role];
          const actualName = detail?.backend || configuredName;
          const actualModel = config?.providers?.[actualName]?.model || (actualName === configuredName ? configuredModel : actualName);
          return { role: label, name: actualName, model: actualModel, status, detail: reviewerDetailText(detail) };
        };

        return (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            
            {/* Left 2 Cols: Fallback Topology Graphic Widget */}
            <div className="lg:col-span-2 bg-white border border-[#E5E0D8] p-6 rounded-sm flex flex-col justify-between shadow-sm">
              <div className="flex items-center justify-between gap-2 mb-4">
                <div className="flex items-center gap-2">
                  <Zap className="w-4 h-4 text-amber-600" />
                  <h3 className="text-sm font-serif font-bold text-[#1A1A1A]">模型路由与多级降级拓扑</h3>
                </div>
                <span className="text-[11px] text-emerald-700 font-mono flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-emerald-500 animate-ping" />
                  自动两级闭环容灾
                </span>
              </div>

              {/* Visual Topology DAG */}
              <div className="grid grid-cols-1 sm:grid-cols-5 gap-3 items-center py-4 text-center">
                {/* Node 1: Primary */}
                <div
                  className={`p-3.5 rounded-sm border transition-all text-left ${
                    isRunning && isTranslationActive && !isFallbackActive
                      ? 'bg-[#EFF6FF] border-[#1D4ED8] shadow-sm'
                      : isRunning && isFallbackActive
                      ? 'bg-amber-50 border-amber-400'
                      : 'bg-[#FAF9F6] border-[#E5E0D8]'
                  }`}
                >
                  <div className="flex items-center justify-between text-[10px] font-mono mb-1">
                    <span className="text-[#1D4ED8] font-bold">PRIMARY (主译)</span>
                    <span
                      className={`px-1.5 py-0.5 rounded-sm text-[9px] ${
                        isRunning && isTranslationActive && !isFallbackActive
                          ? 'bg-[#1D4ED8] text-white font-bold animate-pulse'
                          : 'bg-[#E5E0D8] text-[#666666]'
                      }`}
                    >
                      {isRunning && isTranslationActive && !isFallbackActive ? '● TRANSLATING' : 'STANDBY'}
                    </span>
                  </div>
                  <div className="font-serif font-bold text-xs text-[#1A1A1A] truncate" title={primaryName}>
                    {primaryName}
                  </div>
                  <div className="text-[10px] text-[#666666] font-mono truncate mt-0.5" title={primaryModel}>
                    {primaryModel}
                  </div>
                </div>

                {/* Split Icon */}
                <div className="flex justify-center text-[#888888]">
                  <div className="flex flex-col items-center">
                    <Split
                      className={`w-4 h-4 transition-colors ${
                        isFallbackActive ? 'text-amber-600 animate-bounce' : 'text-[#888888]'
                      }`}
                    />
                    <span className="text-[9px] text-[#666666] font-mono mt-0.5 text-center">
                      {hasRecovered ? `已救回 ${activeTask?.recovered_paragraphs} 段` : '自动容灾分流'}
                    </span>
                  </div>
                </div>

                {/* Node 2: Fallback 1 */}
                <div
                  className={`p-3.5 rounded-sm border transition-all text-left ${
                    isRunning && isFallbackActive
                      ? 'bg-emerald-50 border-emerald-500 shadow-sm animate-pulse'
                      : hasRecovered
                      ? 'bg-[#FAF9F6] border-emerald-300'
                      : 'bg-[#FAF9F6] border-[#E5E0D8]'
                  }`}
                >
                  <div className="flex items-center justify-between text-[10px] font-mono mb-1">
                    <span className="text-emerald-700 font-bold">FALLBACK #1 (一级备用)</span>
                    <span
                      className={`px-1.5 py-0.5 rounded-sm text-[9px] ${
                        isRunning && isFallbackActive
                          ? 'bg-emerald-600 text-white font-bold'
                          : 'bg-[#E5E0D8] text-[#666666]'
                      }`}
                    >
                      {isRunning && isFallbackActive ? '⚡ RECOVERING' : 'STANDBY'}
                    </span>
                  </div>
                  <div className="font-serif font-bold text-xs text-[#1A1A1A] truncate" title={fb1Name}>
                    {fb1Name}
                  </div>
                  <div className="text-[10px] text-[#666666] font-mono truncate mt-0.5" title={fb1Model}>
                    {fb1Model}
                  </div>
                </div>

                {/* Arrow */}
                <div className="flex justify-center text-[#888888]">
                  <ArrowRight className="w-4 h-4 text-[#888888]" />
                </div>

                {/* Node 3: Fallback 2 */}
                <div className="p-3.5 rounded-sm border bg-[#FAF9F6] border-[#E5E0D8] text-left">
                  <div className="flex items-center justify-between text-[10px] font-mono mb-1">
                    <span className="text-rose-700 font-bold">FALLBACK #2 (二级备用)</span>
                    <span className="px-1.5 py-0.5 rounded-sm text-[9px] bg-[#E5E0D8] text-[#666666] font-mono">
                      STANDBY
                    </span>
                  </div>
                  <div className="font-serif font-bold text-xs text-[#1A1A1A] truncate" title={fb2Name}>
                    {fb2Name}
                  </div>
                  <div className="text-[10px] text-[#666666] font-mono truncate mt-0.5" title={fb2Model}>
                    {fb2Model}
                  </div>
                </div>
              </div>

              {/* Independent Chapter Reviewers */}
              <div className="mt-4">
                <div className="flex items-center gap-2 mb-2 text-xs">
                  <CheckCircle2 className={`w-4 h-4 ${isReviewActive ? 'text-[#1D4ED8]' : 'text-emerald-600'}`} />
                  <span className="font-serif font-bold text-[#1A1A1A]">章节一致性双盲审阅器</span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {[
                    reviewerCard('primary', 'REVIEWER #1 (主审)', rev1Name, rev1Model, rev1Status),
                    reviewerCard('secondary', 'REVIEWER #2 (副审)', rev2Name, rev2Model, rev2Status),
                  ].map((reviewer) => (
                    <div
                      key={reviewer.role}
                      className={`p-3.5 rounded-sm border transition-all text-left ${
                        reviewerActive(reviewer.status)
                          ? 'bg-[#EFF6FF] border-[#1D4ED8] shadow-sm'
                          : reviewer.status === 'failed' || reviewer.status === 'cancelled'
                          ? 'bg-rose-50 border-rose-400'
                          : reviewer.status === 'completed'
                          ? 'bg-emerald-50 border-emerald-300'
                          : reviewer.status === 'pending'
                          ? 'bg-amber-50 border-amber-300'
                          : 'bg-[#FAF9F6] border-[#E5E0D8]'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2 text-[10px] font-mono mb-1">
                        <span className="text-violet-700 font-bold">{reviewer.role}</span>
                        <span className={`px-1.5 py-0.5 rounded-sm text-[9px] ${
                          reviewerActive(reviewer.status)
                            ? 'bg-[#1D4ED8] text-white font-bold animate-pulse'
                            : reviewer.status === 'failed' || reviewer.status === 'cancelled'
                            ? 'bg-rose-600 text-white font-bold'
                            : reviewer.status === 'completed'
                            ? 'bg-emerald-600 text-white font-bold'
                            : reviewer.status === 'pending'
                            ? 'bg-amber-100 text-amber-800 font-bold'
                            : 'bg-[#E5E0D8] text-[#666666]'
                        }`}>
                          {reviewerBadge(reviewer.status)}
                        </span>
                      </div>
                      <div className="font-serif font-bold text-xs text-[#1A1A1A] truncate" title={reviewer.name}>
                        {reviewer.name}
                      </div>
                      <div className="text-[10px] text-[#666666] font-mono truncate mt-0.5" title={reviewer.model}>
                        {reviewer.model}
                      </div>
                      {reviewer.detail && (
                        <div className="text-[10px] text-[#1D4ED8] font-mono mt-1" title={reviewer.detail}>
                          {reviewer.detail}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* Right Col: Live Progress & Options Panel */}
            <div className="bg-white border border-[#E5E0D8] p-6 rounded-sm flex flex-col justify-between shadow-sm">
              <div>
                <h3 className="text-sm font-serif font-bold text-[#1A1A1A] mb-3">全书推进概览</h3>
                <div className="space-y-4">
                  <div>
                    <div className="flex justify-between text-xs text-[#4A4A4A] mb-1 font-mono">
                      <span>段落翻译进度</span>
                      <span className="font-bold text-[#1D4ED8]">{progressPercent}%</span>
                    </div>
                    <div className="w-full bg-[#F2EFE9] rounded-sm h-2 overflow-hidden border border-[#E5E0D8]">
                      <div
                        className="bg-[#1D4ED8] h-2 rounded-sm transition-all duration-500"
                        style={{ width: `${progressPercent}%` }}
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-3 text-xs">
                    <div className="p-3 bg-[#FAF9F6] border border-[#E5E0D8] rounded-sm">
                      <div className="text-[#888888] text-[11px] font-sans">已翻译段落</div>
                      <div className="text-base font-serif font-bold text-[#1A1A1A] mt-0.5">
                        {book.translated_paragraphs} / {book.total_paragraphs}
                      </div>
                    </div>
                    <div className="p-3 bg-[#FAF9F6] border border-[#E5E0D8] rounded-sm">
                      <div className="text-[#888888] text-[11px] font-sans">已审阅章节</div>
                      <div className="text-base font-serif font-bold text-[#1A1A1A] mt-0.5">
                        {book.translated_chapters} / {book.total_chapters}
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              {/* Config options */}
              <div className="mt-4 pt-4 border-t border-[#E5E0D8] space-y-2 text-xs">
                <label className="flex items-center gap-2 text-[#4A4A4A] cursor-pointer">
                  <input
                    type="checkbox"
                    checked={applyFixes}
                    onChange={(e) => setApplyFixes(e.target.checked)}
                    className="rounded-sm border-[#E5E0D8] text-[#1D4ED8] focus:ring-0 cursor-pointer"
                  />
                  <span>自动写回高置信度客观审阅修复</span>
                </label>
                <label className="flex items-center gap-2 text-[#4A4A4A] cursor-pointer">
                  <input
                    type="checkbox"
                    checked={autonomous}
                    onChange={(e) => setAutonomous(e.target.checked)}
                    className="rounded-sm border-[#E5E0D8] text-[#1D4ED8] focus:ring-0 cursor-pointer"
                  />
                  <span>纯自主模式 (遇到冲突保持原样不阻塞)</span>
                </label>
                <div className="flex items-center justify-between pt-1 text-[#666666]">
                  <span>导出排版:</span>
                  <select
                    value={layout}
                    onChange={(e) => setLayout(e.target.value as 'horizontal' | 'preserve')}
                    className="bg-[#FAF9F6] border border-[#E5E0D8] rounded-sm px-2 py-0.5 text-[11px] text-[#1A1A1A] focus:outline-none cursor-pointer"
                  >
                    <option value="horizontal">重构为中文横排</option>
                    <option value="preserve">保留原版竖排</option>
                  </select>
                </div>
                
                {/* Prompt Policy Selector */}
                <div className="pt-2 border-t border-[#E5E0D8] space-y-1">
                  <span className="text-[11px] font-medium text-[#4A4A4A] block font-serif">
                    翻译提示词规范 (Policy Prompt):
                  </span>
                  <select
                    value={selectedPolicy}
                    onChange={(e) => setSelectedPolicy(e.target.value)}
                    className="w-full bg-[#FAF9F6] border border-[#E5E0D8] rounded-sm px-2 py-1.5 text-xs text-[#1D4ED8] font-medium focus:outline-none focus:border-[#1D4ED8] cursor-pointer"
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
        );
      })()}

      {/* Live SSE Stream Waterfall Feed */}
      <div className="bg-white border border-[#E5E0D8] rounded-sm overflow-hidden shadow-sm">
        <div className="px-6 py-4 border-b border-[#E5E0D8] flex items-center justify-between gap-4 bg-[#FAF9F6]">
          <div className="flex items-center gap-2.5">
            <Terminal className="w-4 h-4 text-[#1D4ED8]" />
            <h3 className="text-sm font-serif font-bold text-[#1A1A1A]">实时事件瀑布流 (SSE Stream)</h3>
            <span className="text-[10px] font-mono px-2 py-0.5 rounded-sm bg-[#E5E0D8] text-[#1A1A1A]">
              {streamEvents.length} events
            </span>
          </div>

          <div className="flex items-center gap-1.5">
            <button
              onClick={() => setEventFilter('all')}
              className={`px-2.5 py-1 rounded-sm text-xs transition-all cursor-pointer ${
                eventFilter === 'all' ? 'bg-[#1D4ED8] text-white font-semibold' : 'text-[#666666] hover:text-[#1A1A1A]'
              }`}
            >
              全部事件
            </button>
            <button
              onClick={() => setEventFilter('pipeline')}
              className={`px-2.5 py-1 rounded-sm text-xs transition-all cursor-pointer ${
                eventFilter === 'pipeline' ? 'bg-[#1D4ED8] text-white font-semibold' : 'text-[#666666] hover:text-[#1A1A1A]'
              }`}
            >
              流水线推进
            </button>
            <button
              onClick={() => setEventFilter('fallback')}
              className={`px-2.5 py-1 rounded-sm text-xs transition-all cursor-pointer ${
                eventFilter === 'fallback' ? 'bg-amber-600 text-white font-semibold' : 'text-[#666666] hover:text-[#1A1A1A]'
              }`}
            >
              降级容灾
            </button>

            {onClearEvents && streamEvents.length > 0 && (
              <button
                onClick={onClearEvents}
                className="px-2 py-1 rounded-sm text-xs text-[#888888] hover:text-[#1A1A1A] hover:bg-[#E5E0D8] transition-colors ml-1 cursor-pointer"
                title="清空当前日志流"
              >
                清空
              </button>
            )}
          </div>
        </div>

        {/* Scrollable event list */}
        <div className="p-4 max-h-[420px] overflow-y-auto space-y-2 font-mono text-xs bg-[#FAF9F6]">
          {filteredEvents.length === 0 ? (
            <div className="text-center py-12 text-[#888888]">
              <Activity className="w-8 h-8 mx-auto mb-2 opacity-40 animate-pulse text-[#888888]" />
              <p>等待流水线实时事件推送中...</p>
            </div>
          ) : (
            filteredEvents.map((evt, idx) => {
              const isFallback = evt.event === 'fallback_triggered';
              const isCompleted = evt.event === 'pipeline_completed';
              const isChapterDone = evt.event === 'chapter_completed';
              const isBatchDone = evt.event === 'batch_completed';

              let content = null;
              if (evt.event === 'connect') {
                content = <span className="text-emerald-700 font-sans">🟢 SSE 实时事件通道已连接</span>;
              } else if (evt.event === 'pipeline_started') {
                content = <span className="text-[#1D4ED8] font-sans">🚀 {evt.data?.message || '流水线已启动'}</span>;
              } else if (evt.event === 'chapter_started') {
                const chIndex = evt.data?.current_chapter_index ?? evt.data?.chapter_index;
                const chId = evt.data?.current_chapter ?? evt.data?.chapter_id;
                content = (
                  <span className="text-[#1A1A1A] font-sans">
                    📖 {chIndex ? <><strong className="text-[#1D4ED8]">第 {chIndex} 章</strong> {chId ? `(${chId})` : ''} · </> : ''}{evt.data?.message || '开始翻译与一致性审阅...'}
                  </span>
                );
              } else if (evt.event === 'batch_completed') {
                const chIndex = evt.data?.chapter_index ?? evt.data?.current_chapter_index;
                const chId = evt.data?.chapter_id ?? evt.data?.current_chapter;
                const bIdx = evt.data?.batch_index;
                const bParas = evt.data?.batch_paragraphs || 0;
                const remP = evt.data?.chapter_pending_paragraphs;
                const prog = Math.round((evt.data?.overall_progress || 0) * 100);
                content = (
                  <span className="text-[#1D4ED8] font-sans">
                    📦 {chIndex ? <><strong className="text-[#1A1A1A]">第 {chIndex} 章</strong> {chId ? `(${chId})` : ''} · </> : ''}
                    批次 #{bIdx} 翻译完成 (本批已译 {bParas} 段{remP !== undefined ? `，本章剩余 ${remP} 段` : ''} · 全书进度 <strong className="text-[#1D4ED8]">{prog}%</strong>)
                  </span>
                );
              } else if (evt.event === 'pipeline_progress') {
                content = (
                  <span className="text-[#4A4A4A] font-sans">
                    📊 全书进度: <strong className="text-[#1D4ED8]">{Math.round((evt.data?.overall_progress || 0) * 100)}%</strong> · {evt.data?.message || ''}
                  </span>
                );
              } else if (evt.event === 'pipeline_reviewer_status') {
                const role = evt.data?.reviewer_role === 'secondary' ? '副审' : '主审';
                const backend = evt.data?.reviewer_backend || '-';
                const status = ({ reviewing: '审阅中', completed: '已完成', failed: '调用失败', cancelled: '已取消' } as Record<string, string>)[evt.data?.reviewer_status] || evt.data?.reviewer_status;
                const details = [
                  evt.data?.attempt ? `尝试 #${evt.data.attempt}` : '',
                  evt.data?.candidate_index && evt.data?.candidate_total > 1 ? `路由 ${evt.data.candidate_index}/${evt.data.candidate_total}` : '',
                  evt.data?.chunk_index && evt.data?.total_chunks ? `分块 ${evt.data.chunk_index}/${evt.data.total_chunks}` : '',
                  evt.data?.split_path && evt.data.split_path !== 'root' ? `子段 ${evt.data.split_path}` : '',
                  evt.data?.timeout_seconds ? `超时 ${evt.data.timeout_seconds}s` : '',
                ].filter(Boolean).join(' · ');
                content = (
                  <span className="text-violet-800 font-sans">
                    🔎 <strong>{role}</strong> · 后端 <strong>{backend}</strong> · {status}{details ? ` · ${details}` : ''}
                  </span>
                );
              } else if (evt.event === 'chapter_completed') {
                const chIndex = evt.data?.chapter_index ?? evt.data?.current_chapter_index;
                const chId = evt.data?.chapter_id ?? evt.data?.current_chapter;
                const issues =
                  evt.data?.issues ??
                  evt.data?.result?.issues ??
                  evt.data?.result?.review?.issues ??
                  evt.data?.result?.review?.reported_issues ??
                  evt.data?.result?.reported_issues ??
                  0;
                const fixes =
                  evt.data?.fixes ??
                  evt.data?.result?.fixes ??
                  evt.data?.result?.review?.fixes ??
                  evt.data?.result?.review?.applied_fixes ??
                  evt.data?.result?.applied_fixes ??
                  0;
                content = (
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 font-sans">
                    <span className="text-emerald-800 font-medium">
                      ✅ {chIndex ? <><strong className="text-emerald-900">第 {chIndex} 章</strong> {chId ? `(${chId})` : ''} </> : ''}处理完成 (发现 {issues} 处问题，写回 {fixes} 处修复)
                    </span>
                    <span className="text-[11px] text-[#1D4ED8] bg-[#EFF6FF] px-2 py-0.5 rounded-sm border border-[#BFDBFE]">
                      详细质检报告已沉淀至「记忆与术语库」
                    </span>
                  </div>
                );
              } else if (evt.event === 'fallback_triggered') {
                content = (
                  <span className="text-amber-800 font-sans">
                    ⚡ 触发模型降级: <strong className="text-amber-900">{evt.data?.from_provider}</strong> 发生异常 ({evt.data?.reason || '阻塞'}) ➔ 自动切换至 <strong className="text-emerald-800">{evt.data?.to_provider}</strong>
                  </span>
                );
              } else if (evt.event === 'pipeline_completed') {
                content = <span className="text-emerald-800 font-bold font-sans">🎉 {evt.data?.message || '全书翻译完成，中文 EPUB 已就绪！'}</span>;
              } else {
                content = (
                  <span className="text-[#4A4A4A] font-sans">
                    {evt.data?.message || (typeof evt.data === 'string' ? evt.data : JSON.stringify(evt.data))}
                  </span>
                );
              }

              return (
                <div
                  key={idx}
                  className={`p-3 rounded-sm border transition-all ${
                    isFallback
                      ? 'bg-amber-50 border-amber-300 text-amber-900'
                      : isChapterDone
                      ? 'bg-emerald-50 border-emerald-300 text-emerald-900'
                      : isBatchDone
                      ? 'bg-[#EFF6FF] border-[#BFDBFE] text-[#1D4ED8]'
                      : isCompleted
                      ? 'bg-emerald-50 border-emerald-300 text-emerald-900'
                      : 'bg-white border-[#E5E0D8] text-[#1A1A1A]'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2 text-[11px] mb-1">
                    <span className="font-bold uppercase tracking-wider text-[#1D4ED8]">
                      [{evt.event}]
                    </span>
                    <span className="text-[#888888]">{new Date(evt.timestamp).toLocaleTimeString()}</span>
                  </div>

                  <div className="text-xs break-all">
                    {content}
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
