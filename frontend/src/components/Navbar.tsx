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
  sseState?: 'live' | 'reconnecting' | 'offline';
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
  sseState = sseConnected ? 'live' : 'reconnecting',
  queueCount = 0,
  isQueueRunning = false,
}) => {
  const isTaskRunning = (activeTask && activeTask.status === 'running') || isQueueRunning;
  const state = sseState || (sseConnected ? 'live' : 'offline');

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

        {/* Navigation Tabs */}
        <nav className="flex items-center gap-1 bg-[#EBE7DF]/80 p-1 rounded-sm border border-[#E5E0D8]">
          <button
            onClick={() => onSelectTab('queue')}
            className={`flex items-center gap-2 px-3 py-1.5 text-xs font-mono tracking-wider uppercase transition-all rounded-xs ${
              currentTab === 'queue'
                ? 'bg-[#1A1A1A] text-[#FAF9F6] shadow-xs'
                : 'text-[#4A4A4A] hover:bg-[#FAF9F6]/60'
            }`}
          >
            <Layers className="w-3.5 h-3.5" />
            <span>队列枢纽 (QUEUE)</span>
            {queueCount > 0 && (
              <span
                className={`ml-1 px-1.5 py-0.2 rounded-full text-[10px] font-bold ${
                  currentTab === 'queue' ? 'bg-amber-400 text-black' : 'bg-[#D5D0C7] text-neutral-800'
                }`}
              >
                {queueCount}
              </span>
            )}
          </button>

          <button
            onClick={() => onSelectTab('studio')}
            className={`flex items-center gap-2 px-3 py-1.5 text-xs font-mono tracking-wider uppercase transition-all rounded-xs ${
              currentTab === 'studio'
                ? 'bg-[#1A1A1A] text-[#FAF9F6] shadow-xs'
                : 'text-[#4A4A4A] hover:bg-[#FAF9F6]/60'
            }`}
          >
            <Activity className="w-3.5 h-3.5" />
            <span>流水车间 (STUDIO)</span>
            {isTaskRunning && (
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse ml-0.5" />
            )}
          </button>

          <button
            onClick={() => onSelectTab('reader')}
            className={`flex items-center gap-2 px-3 py-1.5 text-xs font-mono tracking-wider uppercase transition-all rounded-xs ${
              currentTab === 'reader'
                ? 'bg-[#1A1A1A] text-[#FAF9F6] shadow-xs'
                : 'text-[#4A4A4A] hover:bg-[#FAF9F6]/60'
            }`}
          >
            <Compass className="w-3.5 h-3.5" />
            <span>双语对齐 (READER)</span>
          </button>

          <button
            onClick={() => onSelectTab('knowledge')}
            className={`flex items-center gap-2 px-3 py-1.5 text-xs font-mono tracking-wider uppercase transition-all rounded-xs ${
              currentTab === 'knowledge'
                ? 'bg-[#1A1A1A] text-[#FAF9F6] shadow-xs'
                : 'text-[#4A4A4A] hover:bg-[#FAF9F6]/60'
            }`}
          >
            <BookMarked className="w-3.5 h-3.5" />
            <span>设定与质检 (KNOWLEDGE)</span>
          </button>

          <button
            onClick={() => onSelectTab('settings')}
            className={`flex items-center gap-2 px-3 py-1.5 text-xs font-mono tracking-wider uppercase transition-all rounded-xs ${
              currentTab === 'settings'
                ? 'bg-[#1A1A1A] text-[#FAF9F6] shadow-xs'
                : 'text-[#4A4A4A] hover:bg-[#FAF9F6]/60'
            }`}
          >
            <Cpu className="w-3.5 h-3.5" />
            <span>系统设置 (SYSTEM)</span>
          </button>
        </nav>

        {/* Book Selector & Status Indicator */}
        <div className="flex items-center gap-3">
          {books.length > 0 && (
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-mono uppercase text-[#666666]">当前书籍:</span>
              <select
                value={selectedBookId || ''}
                onChange={(e) => onSelectBookId(e.target.value)}
                className="text-xs bg-white border border-[#D5D0C7] rounded-sm px-2.5 py-1 text-[#1A1A1A] focus:outline-none focus:border-[#1A1A1A] max-w-[260px] truncate shadow-2xs font-serif"
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
              state === 'live'
                ? 'bg-emerald-50 border-emerald-300 text-emerald-800'
                : state === 'reconnecting'
                ? 'bg-amber-50 border-amber-300 text-amber-800'
                : 'bg-rose-50 border-rose-300 text-rose-800'
            }`}
            title={
              state === 'live'
                ? 'SSE 实时流水线长连接正常'
                : state === 'reconnecting'
                ? '正在同步连接实时流水线...'
                : 'SSE 连接断开，正在尝试重连'
            }
          >
            <Radio
              className={`w-3 h-3 ${
                state === 'live'
                  ? 'animate-pulse text-emerald-600'
                  : state === 'reconnecting'
                  ? 'animate-spin text-amber-600'
                  : 'text-rose-600'
              }`}
            />
            <span>
              {state === 'live' ? 'LIVE' : state === 'reconnecting' ? 'SYNCING...' : 'OFFLINE'}
            </span>
          </div>
        </div>

      </div>
    </header>
  );
};
