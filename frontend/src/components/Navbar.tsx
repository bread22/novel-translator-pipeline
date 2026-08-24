import React from 'react';
import { Layers, Activity, Compass, Cpu, BookMarked, Radio } from 'lucide-react';
import { BookSummary, TaskStatusResponse } from '../types/api';

interface NavbarProps {
  currentTab: string;
  onSelectTab: (tab: string) => void;
  books: BookSummary[];
  selectedBookId: string | null;
  onSelectBookId: (id: string) => void;
  activeTask: TaskStatusResponse | null;
  sseConnected: boolean;
  queueCount?: number;
  isQueueRunning?: boolean;
}

export const Navbar: React.FC<NavbarProps> = ({
  currentTab,
  onSelectTab,
  books,
  selectedBookId,
  onSelectBookId,
  activeTask,
  sseConnected,
  queueCount = 0,
  isQueueRunning = false,
}) => {
  const isTaskRunning = (activeTask && activeTask.status === 'running') || isQueueRunning;

  return (
    <header className="sticky top-0 z-50 border-b border-slate-800 bg-slate-950/85 backdrop-blur-md px-6 py-3">
      <div className="max-w-[1600px] mx-auto flex items-center justify-between gap-4">
        {/* Logo & Title */}
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-indigo-500 via-purple-500 to-pink-500 flex items-center justify-center shadow-lg shadow-indigo-500/20 text-white font-bold text-lg">
            NT
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-bold tracking-tight text-slate-100 text-lg">Novel Translator Studio</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-950 border border-indigo-700/50 text-indigo-300 font-mono font-medium">
                v1.0 AI
              </span>
            </div>
            <p className="text-xs text-slate-400">全自动小说翻译与长程一致性审阅工作台</p>
          </div>
        </div>

        {/* Navigation Tabs */}
        <nav className="flex items-center gap-1 bg-slate-900/90 p-1 rounded-xl border border-slate-800/80">
          <button
            onClick={() => onSelectTab('queue')}
            className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-medium transition-all relative ${
              currentTab === 'queue' || currentTab === 'library'
                ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
            }`}
          >
            <Layers className="w-4 h-4" />
            任务调度
            {queueCount > 0 && (
              <span
                className={`px-1.5 py-0.5 rounded-full text-[9px] font-mono font-bold leading-none ${
                  isQueueRunning
                    ? 'bg-emerald-500 text-white shadow-sm animate-pulse'
                    : 'bg-slate-800 text-indigo-300 border border-slate-700'
                }`}
              >
                {queueCount}
              </span>
            )}
          </button>

          <button
            onClick={() => onSelectTab('studio')}
            className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-medium transition-all relative ${
              currentTab === 'studio'
                ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
            }`}
          >
            <Activity className="w-4 h-4" />
            翻译控制台
            {isTaskRunning && (
              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-ping absolute top-1 right-1" />
            )}
          </button>

          <button
            onClick={() => onSelectTab('reader')}
            className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-medium transition-all ${
              currentTab === 'reader'
                ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
            }`}
          >
            <Compass className="w-4 h-4" />
            双语阅读器
          </button>

          <button
            onClick={() => onSelectTab('knowledge')}
            className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-medium transition-all ${
              currentTab === 'knowledge'
                ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
            }`}
          >
            <BookMarked className="w-4 h-4" />
            记忆与术语库
          </button>

          <button
            onClick={() => onSelectTab('settings')}
            className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-medium transition-all ${
              currentTab === 'settings'
                ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
            }`}
          >
            <Cpu className="w-4 h-4" />
            模型路由与预检
          </button>
        </nav>

        {/* Global Controls & Status */}
        <div className="flex items-center gap-3">
          {/* Active Book Selector */}
          {books.length > 0 && (
            <div className="relative">
              <select
                value={selectedBookId || ''}
                onChange={(e) => onSelectBookId(e.target.value)}
                className="bg-slate-900 border border-slate-700/70 text-slate-200 text-xs rounded-lg px-3 py-1.5 pr-8 focus:outline-none focus:ring-1 focus:ring-indigo-500 appearance-none font-medium max-w-[200px] truncate"
              >
                {books.map((b) => (
                  <option key={b.id} value={b.id}>
                    📖 {b.name}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* SSE Live Pulse Indicator */}
          <div
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium border ${
              sseConnected
                ? 'bg-emerald-950/60 border-emerald-800/60 text-emerald-400'
                : 'bg-rose-950/60 border-rose-800/60 text-rose-400'
            }`}
            title={sseConnected ? 'SSE 实时推送已连接' : 'SSE 连接断开，尝试重连中'}
          >
            <Radio className={`w-3 h-3 ${sseConnected ? 'animate-pulse text-emerald-400' : 'text-rose-400'}`} />
            <span>{sseConnected ? 'LIVE' : 'OFFLINE'}</span>
          </div>
        </div>
      </div>
    </header>
  );
};

