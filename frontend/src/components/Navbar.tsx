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
    <header className="sticky top-0 z-50 border-b border-[#E5E0D8] bg-[#FAF9F6]/95 backdrop-blur-md px-6 py-3">
      <div className="max-w-[1600px] mx-auto flex items-center justify-between gap-4">
        
        {/* Editorial Masthead & Branding */}
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-sm bg-[#1A1A1A] text-white flex items-center justify-center font-serif font-black text-base shadow-sm">
            NT
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-serif font-bold tracking-tight text-[#1A1A1A] text-lg">
                Novel Translator Studio
              </span>
              <span className="text-[10px] px-1.5 py-0.5 border border-[#1A1A1A] bg-white text-[#1A1A1A] font-serif italic">
                EDITION · 2026
              </span>
            </div>
            <p className="text-xs text-[#666666] font-sans">全自动日文小说翻译与长程一致性审阅工坊</p>
          </div>
        </div>

        {/* Navigation Tabs (Editorial Paper Tab Bar) */}
        <nav className="flex items-center gap-1 bg-[#F2EFE9] p-1 rounded-sm border border-[#E5E0D8]">
          <button
            onClick={() => onSelectTab('queue')}
            className={`flex items-center gap-2 px-3.5 py-1.5 rounded-sm text-xs font-medium transition-all relative cursor-pointer ${
              currentTab === 'queue' || currentTab === 'library'
                ? 'bg-[#1D4ED8] text-white font-semibold shadow-sm'
                : 'text-[#4A4A4A] hover:text-[#1A1A1A] hover:bg-white/80'
            }`}
          >
            <Layers className="w-3.5 h-3.5" />
            <span>任务调度</span>
            {queueCount > 0 && (
              <span
                className={`px-1.5 py-0.2 rounded text-[10px] font-mono font-bold leading-none ${
                  isQueueRunning
                    ? 'bg-emerald-600 text-white animate-pulse'
                    : currentTab === 'queue' ? 'bg-white text-[#1D4ED8]' : 'bg-[#E5E0D8] text-[#1A1A1A]'
                }`}
              >
                {queueCount}
              </span>
            )}
          </button>

          <button
            onClick={() => onSelectTab('studio')}
            className={`flex items-center gap-2 px-3.5 py-1.5 rounded-sm text-xs font-medium transition-all relative cursor-pointer ${
              currentTab === 'studio'
                ? 'bg-[#1D4ED8] text-white font-semibold shadow-sm'
                : 'text-[#4A4A4A] hover:text-[#1A1A1A] hover:bg-white/80'
            }`}
          >
            <Activity className="w-3.5 h-3.5" />
            <span>翻译控制台</span>
            {isTaskRunning && (
              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-ping absolute top-1 right-1" />
            )}
          </button>

          <button
            onClick={() => onSelectTab('reader')}
            className={`flex items-center gap-2 px-3.5 py-1.5 rounded-sm text-xs font-medium transition-all cursor-pointer ${
              currentTab === 'reader'
                ? 'bg-[#1D4ED8] text-white font-semibold shadow-sm'
                : 'text-[#4A4A4A] hover:text-[#1A1A1A] hover:bg-white/80'
            }`}
          >
            <Compass className="w-3.5 h-3.5" />
            <span>双语阅读器</span>
          </button>

          <button
            onClick={() => onSelectTab('knowledge')}
            className={`flex items-center gap-2 px-3.5 py-1.5 rounded-sm text-xs font-medium transition-all cursor-pointer ${
              currentTab === 'knowledge'
                ? 'bg-[#1D4ED8] text-white font-semibold shadow-sm'
                : 'text-[#4A4A4A] hover:text-[#1A1A1A] hover:bg-white/80'
            }`}
          >
            <BookMarked className="w-3.5 h-3.5" />
            <span>记忆与术语库</span>
          </button>

          <button
            onClick={() => onSelectTab('settings')}
            className={`flex items-center gap-2 px-3.5 py-1.5 rounded-sm text-xs font-medium transition-all cursor-pointer ${
              currentTab === 'settings'
                ? 'bg-[#1D4ED8] text-white font-semibold shadow-sm'
                : 'text-[#4A4A4A] hover:text-[#1A1A1A] hover:bg-white/80'
            }`}
          >
            <Cpu className="w-3.5 h-3.5" />
            <span>模型路由与预检</span>
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
                className="bg-white border border-[#E5E0D8] text-[#1A1A1A] text-xs rounded-sm px-3 py-1.5 pr-8 focus:outline-none focus:border-[#1D4ED8] appearance-none font-medium max-w-[200px] truncate shadow-sm cursor-pointer"
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
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-sm text-[11px] font-mono font-medium border ${
              sseConnected
                ? 'bg-emerald-50 border-emerald-300 text-emerald-800'
                : 'bg-rose-50 border-rose-300 text-rose-800'
            }`}
            title={sseConnected ? 'SSE 实时流水线连接正常' : 'SSE 连接断开，正在尝试重连'}
          >
            <Radio className={`w-3 h-3 ${sseConnected ? 'animate-pulse text-emerald-600' : 'text-rose-600'}`} />
            <span>{sseConnected ? 'LIVE' : 'OFFLINE'}</span>
          </div>
        </div>

      </div>
    </header>
  );
};
