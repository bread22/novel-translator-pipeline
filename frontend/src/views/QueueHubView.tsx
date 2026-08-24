import React, { useState, useRef } from 'react';
import {
  UploadCloud,
  Play,
  Pause,
  RotateCcw,
  Trash2,
  ArrowUp,
  ArrowDown,
  ArrowUpToLine,
  GripVertical,
  CheckCircle2,
  Clock,
  AlertCircle,
  X,
  Sparkles,
  BookOpen,
  Search,
  Download,
  Share2,
  Layers,
  Cpu,
  RefreshCw,
  Plus,
  Compass,
  FileText,
  Zap,
} from 'lucide-react';
import { BookSummary, QueueItem, QueueStatusResponse } from '../types/api';
import { api } from '../lib/api';

interface QueueHubViewProps {
  books: BookSummary[];
  queueStatus: QueueStatusResponse | null;
  onRefreshBooks: () => Promise<void>;
  onRefreshQueue: () => Promise<void>;
  onSelectBook: (bookId: string, targetTab?: string) => void;
}

export const QueueHubView: React.FC<QueueHubViewProps> = ({
  books,
  queueStatus,
  onRefreshBooks,
  onRefreshQueue,
  onSelectBook,
}) => {
  // Left Pane: Book Pool State
  const [search, setSearch] = useState('');
  const [filterStatus, setFilterStatus] = useState<'all' | 'untranslated' | 'completed'>('all');
  const [isUploading, setIsUploading] = useState(false);
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);
  const [resettingBookId, setResettingBookId] = useState<string | null>(null);
  const [deletingBookId, setDeletingBookId] = useState<string | null>(null);
  const [exportingBookId, setExportingBookId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Right Pane: Drag & Drop State
  const [draggedItemId, setDraggedItemId] = useState<string | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);
  const [isUpdatingQueue, setIsUpdatingQueue] = useState(false);

  const pendingItems = queueStatus?.items.filter((i) => i.status === 'pending') || [];
  const runningItems = queueStatus?.items.filter((i) => i.status === 'running') || [];
  const finishedItems =
    queueStatus?.items.filter((i) => ['completed', 'failed', 'cancelled'].includes(i.status)) || [];

  // Book In-Queue Lookup map
  const bookQueueMap = new Map<string, QueueItem>();
  queueStatus?.items.forEach((item) => {
    if (['running', 'pending'].includes(item.status)) {
      bookQueueMap.set(item.book_id, item);
    }
  });

  // Filtered Books
  const filteredBooks = books.filter((book) => {
    const matchesSearch = book.name.toLowerCase().includes(search.toLowerCase());
    if (!matchesSearch) return false;
    if (filterStatus === 'completed') return book.status === 'completed';
    if (filterStatus === 'untranslated') return book.status !== 'completed';
    return true;
  });

  // Handle file upload
  const handleFileUpload = async (file: File) => {
    setIsUploading(true);
    setUploadMessage(`正在上传并解析 "${file.name}"...`);
    try {
      const newBook = await api.uploadBook(file);
      setUploadMessage(`"${newBook.name}" 导入成功！已加入书籍库。`);
      await onRefreshBooks();
      // Auto enqueue option
      await api.enqueueBooks({ book_ids: [newBook.id] });
      await onRefreshQueue();
    } catch (err: any) {
      setUploadMessage(`导入失败: ${err.message}`);
    } finally {
      setIsUploading(false);
      setTimeout(() => setUploadMessage(null), 4000);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      const file = e.dataTransfer.files[0];
      if (file.name.endsWith('.epub') || file.name.endsWith('.txt')) {
        handleFileUpload(file);
      } else {
        setUploadMessage('请上传 .epub 或 .txt 格式的小说');
      }
    }
  };

  // Enqueue Single Book
  const handleEnqueue = async (bookId: string, e?: React.MouseEvent) => {
    e?.stopPropagation();
    try {
      await api.enqueueBooks({ book_ids: [bookId] });
      await onRefreshQueue();
    } catch (err: any) {
      alert(`加入队列失败: ${err.message}`);
    }
  };

  // Enqueue All Pending Books
  const handleEnqueueAllPending = async () => {
    const uncompletedIds = books
      .filter((b) => b.status !== 'completed' && !bookQueueMap.has(b.id))
      .map((b) => b.id);
    if (uncompletedIds.length === 0) {
      alert('所有未完结书籍均已在队列中或全部完结！');
      return;
    }
    try {
      await api.enqueueBooks({ book_ids: uncompletedIds });
      await onRefreshQueue();
    } catch (err: any) {
      alert(`批量入队失败: ${err.message}`);
    }
  };

  // Cancel / Remove from queue
  const handleCancelItem = async (itemId: string, e?: React.MouseEvent) => {
    e?.stopPropagation();
    try {
      await api.cancelQueueItem(itemId);
      await onRefreshQueue();
    } catch (err: any) {
      alert(`取消失败: ${err.message}`);
    }
  };

  // Retry Item
  const handleRetryItem = async (itemId: string, e?: React.MouseEvent) => {
    e?.stopPropagation();
    try {
      await api.retryQueueItem(itemId);
      await onRefreshQueue();
    } catch (err: any) {
      alert(`重试失败: ${err.message}`);
    }
  };

  // Move Item (Top / Up / Down)
  const handleMoveItem = async (itemId: string, direction: 'up' | 'down' | 'top', e?: React.MouseEvent) => {
    e?.stopPropagation();
    try {
      await api.moveQueueItem(itemId, direction);
      await onRefreshQueue();
    } catch (err: any) {
      alert(`调序失败: ${err.message}`);
    }
  };

  // Drag and drop handlers
  const handleDragStart = (e: React.DragEvent, itemId: string) => {
    setDraggedItemId(itemId);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', itemId);
  };

  const handleDragOver = (e: React.DragEvent, index: number) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    if (dragOverIndex !== index) {
      setDragOverIndex(index);
    }
  };

  const handleDragDrop = async (e: React.DragEvent, targetIndex: number) => {
    e.preventDefault();
    setDragOverIndex(null);
    if (!draggedItemId) return;

    const currentIndex = pendingItems.findIndex((i) => i.id === draggedItemId);
    if (currentIndex === -1 || currentIndex === targetIndex) {
      setDraggedItemId(null);
      return;
    }

    // Optimistic reorder
    const newPending = [...pendingItems];
    const [movedItem] = newPending.splice(currentIndex, 1);
    newPending.splice(targetIndex, 0, movedItem);

    setDraggedItemId(null);
    setIsUpdatingQueue(true);

    try {
      const newIds = newPending.map((i) => i.id);
      await api.reorderQueue(newIds);
      await onRefreshQueue();
    } catch (err: any) {
      console.error('Reorder queue failed:', err);
      await onRefreshQueue();
    } finally {
      setIsUpdatingQueue(false);
    }
  };

  const handleDragEnd = () => {
    setDraggedItemId(null);
    setDragOverIndex(null);
  };

  // Toggle Pause/Resume Queue
  const handleToggleQueuePause = async () => {
    try {
      if (queueStatus?.is_paused) {
        await api.resumeQueue();
      } else {
        await api.pauseQueue();
      }
      await onRefreshQueue();
    } catch (err: any) {
      alert(`操作队列失败: ${err.message}`);
    }
  };

  // Clear completed/failed
  const handleClearQueue = async (scope: 'completed' | 'all_finished') => {
    try {
      await api.clearQueue(scope);
      await onRefreshQueue();
    } catch (err: any) {
      alert(`清理失败: ${err.message}`);
    }
  };

  // Concurrency change
  const handleChangeConcurrency = async (concurrency: number) => {
    try {
      await api.updateQueueConfig({ concurrency });
      await onRefreshQueue();
    } catch (err: any) {
      alert(`调整并发数失败: ${err.message}`);
    }
  };

  // Book Actions (Reset, Delete, Export)
  const handleResetBook = async (bookId: string, bookName: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`确定要重置《${bookName}》的翻译进度吗？\n\n注意：这会清空已翻译的段落文本并重置所有提取的长程记忆，从头开始重新翻译。`)) {
      return;
    }
    setResettingBookId(bookId);
    try {
      await api.resetBook(bookId);
      await onRefreshBooks();
      await onRefreshQueue();
      setUploadMessage(`《${bookName}》翻译进度与长程记忆已重置！`);
    } catch (err: any) {
      alert(`重置失败: ${err.message}`);
    } finally {
      setResettingBookId(null);
      setTimeout(() => setUploadMessage(null), 4000);
    }
  };

  const handleDeleteBook = async (bookId: string, bookName: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`确定要彻底删除《${bookName}》吗？\n\n警告：这将删除该书籍的所有章节、段落工作区、审阅记录和已生成的 EPUB 成品文件，不可恢复！`)) {
      return;
    }
    setDeletingBookId(bookId);
    try {
      await api.deleteBook(bookId);
      await onRefreshBooks();
      await onRefreshQueue();
      setUploadMessage(`《${bookName}》已彻底删除`);
    } catch (err: any) {
      alert(`删除失败: ${err.message}`);
    } finally {
      setDeletingBookId(null);
      setTimeout(() => setUploadMessage(null), 4000);
    }
  };

  const handleExport = async (bookId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setExportingBookId(bookId);
    try {
      await api.exportBook(bookId, 'horizontal');
      await onRefreshBooks();
    } catch (err: any) {
      alert(`导出失败: ${err.message}`);
    } finally {
      setExportingBookId(null);
    }
  };

  return (
    <div className="space-y-6 max-w-[1600px] mx-auto pb-16">
      {/* Upload Drop Zone Banner */}
      <div
        onDragOver={(e) => e.preventDefault()}
        onDrop={handleDrop}
        className="relative overflow-hidden rounded-2xl border border-indigo-900/50 bg-gradient-to-br from-indigo-950/40 via-slate-900/80 to-slate-950 p-6 shadow-xl backdrop-blur-sm"
      >
        <div className="absolute -right-12 -top-12 w-64 h-64 bg-indigo-500/10 rounded-full blur-3xl pointer-events-none" />
        <div className="relative z-10 flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="space-y-1.5 text-center md:text-left">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-indigo-900/60 border border-indigo-700/50 text-indigo-300 text-xs font-medium">
              <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
              任务调度工作台 · 多书籍批量队列
            </div>
            <h1 className="text-xl md:text-2xl font-bold tracking-tight text-white">
              小说批量翻译与队列调度
            </h1>
            <p className="text-slate-400 text-xs max-w-2xl">
              支持多书籍批量排队、自由拖拽调整执行次序、动态并发槽位与断点续译。
            </p>
          </div>
          <div className="flex items-center gap-3">
            <input
              type="file"
              ref={fileInputRef}
              accept=".epub,.txt"
              className="hidden"
              onChange={(e) => {
                if (e.target.files && e.target.files[0]) {
                  handleFileUpload(e.target.files[0]);
                }
              }}
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={isUploading}
              className="flex items-center gap-2 px-5 py-3 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs shadow-lg shadow-indigo-600/30 transition-all hover:scale-105 active:scale-95 disabled:opacity-50 cursor-pointer"
            >
              <UploadCloud className="w-4 h-4" />
              {isUploading ? '正在上传解析...' : '上传并加入队列'}
            </button>

            <button
              onClick={handleEnqueueAllPending}
              className="flex items-center gap-1.5 px-4 py-3 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-medium border border-slate-700/60 transition-all cursor-pointer"
              title="将书库中所有未完结书籍一键添加至队列尾部"
            >
              <Zap className="w-4 h-4 text-amber-400" />
              全部未完结入队
            </button>
          </div>
        </div>

        {uploadMessage && (
          <div className="mt-3 p-2.5 rounded-lg bg-indigo-900/50 border border-indigo-700 text-indigo-200 text-xs flex items-center gap-2 animate-fade-in">
            <FileText className="w-4 h-4 text-indigo-400 shrink-0" />
            {uploadMessage}
          </div>
        )}
      </div>

      {/* Dual-Pane Layout: Left = Book Pool (45%), Right = Execution Queue (55%) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        {/* ================= LEFT PANE: Book Pool (5 cols) ================= */}
        <div className="lg:col-span-5 space-y-4">
          <div className="flex items-center justify-between bg-slate-900/90 border border-slate-800 p-4 rounded-xl shadow-md">
            <div className="flex items-center gap-2">
              <BookOpen className="w-4 h-4 text-indigo-400" />
              <h2 className="text-sm font-bold text-white">已注册书籍资产池</h2>
              <span className="text-xs font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-400">
                {books.length} 本
              </span>
            </div>

            {/* Filter Tabs */}
            <div className="flex items-center gap-1 bg-slate-950 p-1 rounded-lg border border-slate-800 text-[11px]">
              <button
                onClick={() => setFilterStatus('all')}
                className={`px-2.5 py-1 rounded transition-colors ${
                  filterStatus === 'all' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                全部
              </button>
              <button
                onClick={() => setFilterStatus('untranslated')}
                className={`px-2.5 py-1 rounded transition-colors ${
                  filterStatus === 'untranslated'
                    ? 'bg-indigo-600 text-white'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                待处理
              </button>
              <button
                onClick={() => setFilterStatus('completed')}
                className={`px-2.5 py-1 rounded transition-colors ${
                  filterStatus === 'completed' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                已完结
              </button>
            </div>
          </div>

          {/* Search Input */}
          <div className="relative">
            <Search className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder="搜索书籍名称..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full bg-slate-900/90 border border-slate-800 rounded-xl pl-9 pr-4 py-2 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500"
            />
          </div>

          {/* Book List */}
          <div className="space-y-3 max-h-[750px] overflow-y-auto pr-1">
            {filteredBooks.length === 0 ? (
              <div className="text-center py-12 border border-dashed border-slate-800 rounded-xl p-6">
                <BookOpen className="w-8 h-8 text-slate-600 mx-auto mb-2" />
                <p className="text-slate-400 text-xs">暂无匹配书籍，拖入 EPUB 即可开始</p>
              </div>
            ) : (
              filteredBooks.map((book) => {
                const isCompleted = book.status === 'completed';
                const queueItem = bookQueueMap.get(book.id);
                const isRunningInQueue = queueItem?.status === 'running';
                const isPendingInQueue = queueItem?.status === 'pending';
                const progressPercent = Math.round((book.progress_percentage || 0) * 100);

                return (
                  <div
                    key={book.id}
                    className={`bg-slate-900/80 hover:bg-slate-900 border rounded-xl p-4 transition-all duration-200 shadow-sm ${
                      isRunningInQueue
                        ? 'border-indigo-500/80 ring-1 ring-indigo-500/40'
                        : isPendingInQueue
                        ? 'border-amber-500/40'
                        : 'border-slate-800 hover:border-slate-700'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2 mb-2">
                      <div>
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 uppercase">
                            {book.source_type || 'epub'}
                          </span>
                          {isRunningInQueue ? (
                            <span className="inline-flex items-center gap-1 text-[10px] font-medium text-indigo-300 bg-indigo-950 border border-indigo-700/60 px-2 py-0.5 rounded-full animate-pulse">
                              <Play className="w-2.5 h-2.5 fill-indigo-400" />
                              队列翻译中
                            </span>
                          ) : isPendingInQueue ? (
                            <span className="inline-flex items-center gap-1 text-[10px] font-medium text-amber-300 bg-amber-950/80 border border-amber-700/60 px-2 py-0.5 rounded-full">
                              <Clock className="w-2.5 h-2.5" />
                              排队中 #{queueItem?.order_index}
                            </span>
                          ) : isCompleted ? (
                            <span className="inline-flex items-center gap-1 text-[10px] font-medium text-emerald-400 bg-emerald-950/60 border border-emerald-800/60 px-2 py-0.5 rounded-full">
                              <CheckCircle2 className="w-2.5 h-2.5" />
                              已完结
                            </span>
                          ) : (
                            <span className="text-[10px] font-mono text-slate-400">
                              {progressPercent}%
                            </span>
                          )}
                        </div>
                        <h3 className="font-semibold text-slate-100 text-xs line-clamp-1">
                          {book.name}
                        </h3>
                      </div>

                      {/* Top Right Mini Actions */}
                      <div className="flex items-center gap-1 shrink-0">
                        <button
                          type="button"
                          onClick={(e) => handleResetBook(book.id, book.name, e)}
                          disabled={resettingBookId === book.id || isRunningInQueue}
                          className="p-1 rounded bg-slate-800/80 hover:bg-amber-950 text-slate-400 hover:text-amber-300 border border-slate-700/50 transition-colors disabled:opacity-30 cursor-pointer"
                          title="重置全书翻译与记忆"
                        >
                          <RotateCcw className={`w-3 h-3 ${resettingBookId === book.id ? 'animate-spin' : ''}`} />
                        </button>
                        <button
                          type="button"
                          onClick={(e) => handleDeleteBook(book.id, book.name, e)}
                          disabled={deletingBookId === book.id || isRunningInQueue}
                          className="p-1 rounded bg-slate-800/80 hover:bg-rose-950 text-slate-400 hover:text-rose-400 border border-slate-700/50 transition-colors disabled:opacity-30 cursor-pointer"
                          title="彻底删除书籍"
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      </div>
                    </div>

                    {/* Progress Bar & Mini Stats */}
                    <div className="space-y-1.5 my-2">
                      <div className="flex items-center justify-between text-[10px] text-slate-400">
                        <span>{book.translated_chapters} / {book.total_chapters} 章</span>
                        <span>{book.translated_paragraphs} / {book.total_paragraphs} 段</span>
                      </div>
                      <div className="w-full bg-slate-800 rounded-full h-1 overflow-hidden">
                        <div
                          className={`h-full rounded-full transition-all duration-300 ${
                            isCompleted
                              ? 'bg-emerald-500'
                              : isRunningInQueue
                              ? 'bg-indigo-500 animate-pulse'
                              : 'bg-indigo-600'
                          }`}
                          style={{ width: `${progressPercent}%` }}
                        />
                      </div>
                    </div>

                    {/* Bottom Action Bar */}
                    <div className="flex items-center justify-between gap-2 mt-3 pt-2.5 border-t border-slate-800/80">
                      <div className="flex items-center gap-1.5">
                        <button
                          onClick={() => onSelectBook(book.id, 'reader')}
                          className="flex items-center gap-1 px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 text-[11px] transition-all cursor-pointer"
                          title="进入双语对照阅读器"
                        >
                          <Compass className="w-3 h-3" />
                          阅读
                        </button>

                        {book.has_output_epub ? (
                          <a
                            href={book.epub_download_url || `/api/v1/books/${book.id}/download`}
                            download
                            className="flex items-center gap-1 px-2 py-1 rounded bg-emerald-950/80 border border-emerald-700/60 hover:bg-emerald-800 text-emerald-300 text-[11px] transition-all"
                            title="下载中文横排 EPUB"
                          >
                            <Download className="w-3 h-3" />
                            EPUB
                          </a>
                        ) : (
                          <button
                            onClick={(e) => handleExport(book.id, e)}
                            disabled={exportingBookId === book.id}
                            className="flex items-center gap-1 px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-400 text-[11px] transition-all cursor-pointer disabled:opacity-40"
                            title="导出横排 EPUB"
                          >
                            <Share2 className={`w-3 h-3 ${exportingBookId === book.id ? 'animate-spin' : ''}`} />
                            导出
                          </button>
                        )}
                      </div>

                      {/* Enqueue Action Button */}
                      {isRunningInQueue ? (
                        <button
                          onClick={() => onSelectBook(book.id, 'studio')}
                          className="flex items-center gap-1 px-2.5 py-1 rounded bg-indigo-600/30 hover:bg-indigo-600 text-indigo-200 text-[11px] font-medium transition-all cursor-pointer"
                        >
                          <Play className="w-3 h-3 fill-indigo-300" />
                          查看控制台
                        </button>
                      ) : isPendingInQueue ? (
                        <button
                          className="flex items-center gap-1 px-2.5 py-1 rounded bg-amber-950/80 hover:bg-amber-900 border border-amber-700/60 text-amber-300 text-[11px] transition-all cursor-pointer"
                          title="从排队中移出"
                        >
                          <X className="w-3 h-3" />
                          移出队列
                        </button>
                      ) : (
                        <button
                          onClick={(e) => handleEnqueue(book.id, e)}
                          className="flex items-center gap-1 px-2.5 py-1 rounded bg-indigo-600 hover:bg-indigo-500 text-white text-[11px] font-medium shadow-sm transition-all hover:scale-105 active:scale-95 cursor-pointer"
                        >
                          <Plus className="w-3 h-3" />
                          加入队列
                        </button>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* ================= RIGHT PANE: Execution Queue (7 cols) ================= */}
        <div className="lg:col-span-7 space-y-4">
          {/* Queue Global Toolbar */}
          <div className="bg-slate-900/90 border border-slate-800 p-4 rounded-xl shadow-md space-y-3">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="flex items-center gap-2.5">
                <Layers className="w-4 h-4 text-indigo-400" />
                <h2 className="text-sm font-bold text-white flex items-center gap-1.5">
                  动态任务执行队列
                  {isUpdatingQueue && <RefreshCw className="w-3 h-3 text-indigo-400 animate-spin" />}
                </h2>

                {queueStatus?.is_paused ? (
                  <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-amber-950/80 border border-amber-700/70 text-amber-300">
                    <Pause className="w-2.5 h-2.5 fill-amber-400" />
                    已暂停调度
                  </span>
                ) : runningItems.length > 0 ? (
                  <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-emerald-950/80 border border-emerald-700/70 text-emerald-300">
                    <span className="w-2 h-2 rounded-full bg-emerald-400 animate-ping" />
                    队列运行中
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-slate-800 text-slate-400">
                    空闲待命
                  </span>
                )}
              </div>

              {/* Concurrency Selector */}
              <div className="flex items-center gap-2 text-xs">
                <span className="text-slate-400 text-[11px] flex items-center gap-1">
                  <Cpu className="w-3.5 h-3.5 text-indigo-400" />
                  并发槽位:
                </span>
                <div className="flex items-center bg-slate-950 p-0.5 rounded-lg border border-slate-800 font-mono text-xs">
                  {[1, 2, 3, 4].map((slot) => (
                    <button
                      key={slot}
                      onClick={() => handleChangeConcurrency(slot)}
                      className={`px-2.5 py-0.5 rounded transition-all cursor-pointer ${
                        queueStatus?.concurrency === slot
                          ? 'bg-indigo-600 text-white font-bold shadow-sm'
                          : 'text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      {slot}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Quick Actions Bar */}
            <div className="flex items-center justify-between gap-2 pt-2 border-t border-slate-800/80 text-xs flex-wrap">
              <div className="flex items-center gap-2">
                <button
                  onClick={handleToggleQueuePause}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-medium text-xs transition-all cursor-pointer ${
                    queueStatus?.is_paused
                      ? 'bg-emerald-600 hover:bg-emerald-500 text-white shadow-sm'
                      : 'bg-amber-600/90 hover:bg-amber-500 text-white shadow-sm'
                  }`}
                >
                  {queueStatus?.is_paused ? (
                    <>
                      <Play className="w-3.5 h-3.5 fill-white" />
                      恢复队列调度
                    </>
                  ) : (
                    <>
                      <Pause className="w-3.5 h-3.5 fill-white" />
                      暂停队列调度
                    </>
                  )}
                </button>

                <button
                  onClick={() => onRefreshQueue()}
                  className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs transition-all cursor-pointer"
                  title="刷新队列状态"
                >
                  <RefreshCw className="w-3.5 h-3.5" />
                  刷新
                </button>
              </div>

              {finishedItems.length > 0 && (
                <button
                  onClick={() => handleClearQueue('completed')}
                  className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-slate-200 text-xs transition-all cursor-pointer"
                  title="清空所有已完结的历史队列项"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  清空已完成
                </button>
              )}
            </div>
          </div>

          {/* Section 1: RUNNING ITEMS (Active Execution) */}
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs font-semibold text-indigo-300 px-1">
              <span>🚀 正在执行任务 ({runningItems.length})</span>
            </div>

            {runningItems.length === 0 ? (
              <div className="bg-slate-900/40 border border-dashed border-slate-800/80 rounded-xl p-4 text-center text-xs text-slate-500">
                暂无正在运行的书籍任务。添加书籍或点击启动队列即可开始。
              </div>
            ) : (
              runningItems.map((item) => {
                const progressPct = Math.round(item.overall_progress * 100);
                return (
                  <div
                    key={item.id}
                    className="relative overflow-hidden bg-gradient-to-br from-indigo-950/60 via-slate-900/90 to-slate-950 border border-indigo-500/60 rounded-xl p-4 shadow-lg ring-1 ring-indigo-500/30"
                  >
                    <div className="flex items-start justify-between gap-3 mb-2">
                      <div>
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-indigo-900 text-indigo-200 font-bold animate-pulse">
                            ● RUNNING
                          </span>
                          <span className="text-xs text-indigo-400 font-mono">
                            {progressPct}%
                          </span>
                        </div>
                        <h3 className="font-bold text-white text-sm">
                          {item.book_name}
                        </h3>
                      </div>

                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => onSelectBook(item.book_id, 'studio')}
                          className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow transition-all cursor-pointer"
                        >
                          <Play className="w-3.5 h-3.5 fill-white" />
                          查看控制台
                        </button>

                        <button
                          onClick={(e) => handleCancelItem(item.id, e)}
                          title="终止该任务"
                        >
                          <X className="w-4 h-4" />
                        </button>
                      </div>
                    </div>

                    {/* Progress Bar */}
                    <div className="space-y-1.5 my-3">
                      <div className="flex items-center justify-between text-xs text-slate-300 font-mono">
                        <span className="truncate max-w-md text-indigo-200">{item.message}</span>
                        <span>{item.current_chapter || `第 ${item.current_chapter_index}/${item.total_chapters} 章`}</span>
                      </div>
                      <div className="w-full bg-slate-800/80 rounded-full h-2 overflow-hidden">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-indigo-500 via-purple-500 to-emerald-400 transition-all duration-300"
                          style={{ width: `${progressPct}%` }}
                        />
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {/* Section 2: PENDING QUEUE (Drag & Drop Reorderable List) */}
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs font-semibold text-amber-300 px-1">
              <span className="flex items-center gap-1">
                <Clock className="w-3.5 h-3.5" />
                等待排队列表 ({pendingItems.length})
                <span className="text-[10px] font-normal text-slate-400 ml-1">
                  (🖐️ 拖拽左侧抓手或点击上下箭头自由调序)
                </span>
              </span>
            </div>

            {queueStatus?.is_paused && pendingItems.length > 0 && (
              <div className="flex items-center justify-between gap-3 p-3 rounded-xl bg-amber-950/40 border border-amber-800/60 text-amber-200 text-xs">
                <div className="flex items-center gap-2">
                  <Clock className="w-4 h-4 text-amber-400 shrink-0" />
                  <span>队列处于待命暂停状态，已加入 {pendingItems.length} 本书。可任意拖拽调序，准备就绪后点击「启动队列」。</span>
                </div>
                <button
                  onClick={handleToggleQueuePause}
                  className="px-3 py-1 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs shadow transition-all shrink-0 cursor-pointer flex items-center gap-1"
                >
                  <Play className="w-3 h-3 fill-white" />
                  启动队列
                </button>
              </div>
            )}

            {pendingItems.length === 0 ? (
              <div className="bg-slate-900/40 border border-dashed border-slate-800/80 rounded-xl p-6 text-center text-xs text-slate-500">
                等待队列为空。在左侧书籍资产池中点击「加入队列」即可添加。
              </div>
            ) : (
              <div className="space-y-2">
                {pendingItems.map((item, index) => {
                  const isDragging = draggedItemId === item.id;
                  const isOver = dragOverIndex === index;

                  return (
                    <div
                      key={item.id}
                      draggable={true}
                      onDragStart={(e) => handleDragStart(e, item.id)}
                      onDragOver={(e) => handleDragOver(e, index)}
                      onDrop={(e) => handleDragDrop(e, index)}
                      onDragEnd={handleDragEnd}
                      className={`group relative flex items-center justify-between gap-3 bg-slate-900/80 hover:bg-slate-900 border rounded-xl p-3 transition-all select-none ${
                        isDragging
                          ? 'opacity-40 border-dashed border-indigo-400'
                          : isOver
                          ? 'border-t-4 border-t-indigo-500 bg-slate-800/80'
                          : 'border-slate-800 hover:border-slate-700 shadow-sm'
                      }`}
                    >
                      {/* Left: Drag Handle & Order Badge */}
                      <div className="flex items-center gap-2.5 min-w-0 flex-1">
                        <div
                          className="cursor-grab active:cursor-grabbing p-1 text-slate-500 hover:text-slate-200 transition-colors"
                          title="拖动调整顺序"
                        >
                          <GripVertical className="w-4 h-4" />
                        </div>

                        <span className="text-xs font-mono font-bold px-2 py-0.5 rounded bg-amber-950/80 border border-amber-700/60 text-amber-300">
                          #{index + 1}
                        </span>

                        <div className="min-w-0 flex-1">
                          <h4 className="text-xs font-semibold text-slate-100 truncate">
                            {item.book_name}
                          </h4>
                          <p className="text-[10px] text-slate-400 font-mono truncate">
                            {item.message}
                          </p>
                        </div>
                      </div>

                      {/* Right: Auxiliary Up/Down/Top & Remove Buttons */}
                      <div className="flex items-center gap-1 shrink-0">
                        {index > 0 && (
                          <button
                            onClick={(e) => handleMoveItem(item.id, 'top', e)}
                            className="p-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-white transition-colors cursor-pointer"
                            title="置顶"
                          >
                            <ArrowUpToLine className="w-3.5 h-3.5" />
                          </button>
                        )}
                        {index > 0 && (
                          <button
                            onClick={(e) => handleMoveItem(item.id, 'up', e)}
                            className="p-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-white transition-colors cursor-pointer"
                            title="上移一位"
                          >
                            <ArrowUp className="w-3.5 h-3.5" />
                          </button>
                        )}
                        {index < pendingItems.length - 1 && (
                          <button
                            onClick={(e) => handleMoveItem(item.id, 'down', e)}
                            className="p-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-white transition-colors cursor-pointer"
                            title="下移一位"
                          >
                            <ArrowDown className="w-3.5 h-3.5" />
                          </button>
                        )}

                        <button
                          onClick={(e) => handleCancelItem(item.id, e)}
                          className="p-1 rounded bg-slate-800 hover:bg-rose-950 text-slate-400 hover:text-rose-400 transition-colors cursor-pointer ml-1"
                          title="移出等待队列"
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Section 3: FINISHED & FAILED ARCHIVE */}
          {finishedItems.length > 0 && (
            <div className="space-y-2 pt-2">
              <div className="flex items-center justify-between text-xs font-semibold text-slate-400 px-1">
                <span>🏁 历史完成与异常记录 ({finishedItems.length})</span>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => handleClearQueue('all_finished')}
                    className="text-[11px] text-slate-500 hover:text-slate-300 transition-colors cursor-pointer"
                  >
                    全部清空
                  </button>
                </div>
              </div>

              <div className="space-y-2 max-h-[300px] overflow-y-auto pr-1">
                {finishedItems.map((item) => {
                  const isSuccess = item.status === 'completed';
                  const isFailed = item.status === 'failed';

                  return (
                    <div
                      key={item.id}
                      className={`flex items-center justify-between gap-3 rounded-xl p-3 text-xs border ${
                        isSuccess
                          ? 'bg-slate-900/60 border-slate-800/80'
                          : 'bg-rose-950/30 border-rose-900/60'
                      }`}
                    >
                      <div className="flex items-center gap-2 min-w-0 flex-1">
                        {isSuccess ? (
                          <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                        ) : (
                          <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
                        )}

                        <div className="min-w-0 flex-1">
                          <h4 className="font-medium text-slate-200 truncate">
                            {item.book_name}
                          </h4>
                          <p className={`text-[10px] truncate ${isFailed ? 'text-rose-300' : 'text-slate-500'}`}>
                            {item.message.includes('均未完成')
                              ? '所有翻译模型均未返回有效译文（请检查模型 API Key 配置后重试）'
                              : item.message}
                          </p>
                        </div>
                      </div>

                      <div className="flex items-center gap-2 shrink-0">
                        {isFailed && (
                          <button
                            onClick={(e) => handleRetryItem(item.id, e)}
                            className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-rose-900/80 hover:bg-rose-800 text-rose-200 text-[11px] font-medium transition-colors cursor-pointer"
                            title="重新入队执行重试"
                          >
                            <RotateCcw className="w-3 h-3" />
                            重试
                          </button>
                        )}

                        {isSuccess && (
                          <button
                            onClick={() => onSelectBook(item.book_id, 'reader')}
                            className="flex items-center gap-1 px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 text-[11px] transition-colors cursor-pointer"
                          >
                            <BookOpen className="w-3 h-3" />
                            阅读
                          </button>
                        )}

                        <button
                          onClick={(e) => handleCancelItem(item.id, e)}
                          className="p-1 rounded text-slate-500 hover:text-slate-300 transition-colors cursor-pointer"
                          title="删除该记录"
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

