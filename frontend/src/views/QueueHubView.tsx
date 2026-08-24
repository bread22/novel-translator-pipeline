import React, { useState, useRef } from 'react';
import {
  UploadCloud,
  Play,
  Pause,
  RotateCcw,
  ArrowUp,
  ArrowDown,
  ArrowUpToLine,
  GripVertical,
  CheckCircle2,
  Clock,
  AlertCircle,
  X,
  BookOpen,
  Search,
  Plus,
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

    const newPending = [...pendingItems];
    const [movedItem] = newPending.splice(currentIndex, 1);
    newPending.splice(targetIndex, 0, movedItem);

    setDraggedItemId(null);

    try {
      const newIds = newPending.map((i) => i.id);
      await api.reorderQueue(newIds);
      await onRefreshQueue();
    } catch (err: any) {
      console.error('Reorder queue failed:', err);
      await onRefreshQueue();
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

  // Book Actions
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
      
      {/* Upload Drop Zone Banner (Editorial Publication Desk) */}
      <div
        onDragOver={(e) => e.preventDefault()}
        onDrop={handleDrop}
        className="bg-white border border-[#E5E0D8] rounded-sm p-6 shadow-sm"
      >
        <div className="flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="space-y-1 text-center md:text-left">
            <div className="inline-flex items-center gap-2 px-2.5 py-0.5 border border-[#1A1A1A] bg-white text-[#1A1A1A] text-[11px] font-serif italic">
              MANUSCRIPT INTAKE · 稿件排版与排队中心
            </div>
            <h1 className="text-xl md:text-2xl font-serif font-bold tracking-tight text-[#1A1A1A]">
              日文小说批量翻译与队列调度
            </h1>
            <p className="text-[#666666] text-xs font-sans max-w-2xl">
              支持多书籍批量排队、自由拖拽调整执行次序、动态并发槽位与断点自愈。
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
              className="flex items-center gap-2 px-5 py-2.5 bg-[#1D4ED8] hover:bg-[#1E40AF] text-white font-semibold text-xs shadow-sm transition-all cursor-pointer rounded-sm disabled:opacity-50"
            >
              <UploadCloud className="w-4 h-4" />
              {isUploading ? '正在上传解析...' : '上传并加入队列'}
            </button>

            <button
              onClick={handleEnqueueAllPending}
              className="flex items-center gap-1.5 px-4 py-2.5 bg-white hover:bg-[#FAF9F6] text-[#1A1A1A] text-xs font-medium border border-[#E5E0D8] transition-all cursor-pointer rounded-sm shadow-sm"
              title="将书库中所有未完结书籍一键添加至队列尾部"
            >
              <Zap className="w-4 h-4 text-amber-600" />
              全部未完结入队
            </button>
          </div>
        </div>

        {uploadMessage && (
          <div className="mt-4 p-3 bg-[#EFF6FF] border border-[#BFDBFE] text-[#1D4ED8] text-xs flex items-center gap-2 rounded-sm">
            <FileText className="w-4 h-4 shrink-0" />
            <span>{uploadMessage}</span>
          </div>
        )}
      </div>

      {/* Dual-Pane Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        
        {/* ================= LEFT PANE: Book Pool (5 cols) ================= */}
        <div className="lg:col-span-5 space-y-4">
          
          <div className="flex items-center justify-between bg-white border border-[#E5E0D8] p-4 rounded-sm shadow-sm">
            <div className="flex items-center gap-2">
              <BookOpen className="w-4 h-4 text-[#1D4ED8]" />
              <h2 className="text-sm font-serif font-bold text-[#1A1A1A]">已注册书籍资产池</h2>
              <span className="text-xs font-mono px-2 py-0.5 bg-[#F2EFE9] border border-[#E5E0D8] text-[#4A4A4A]">
                {books.length} 卷
              </span>
            </div>

            <div className="flex items-center gap-1.5">
              <button
                onClick={() => setFilterStatus('all')}
                className={`px-2.5 py-1 text-xs transition-colors rounded-sm cursor-pointer ${
                  filterStatus === 'all'
                    ? 'bg-[#1A1A1A] text-white font-semibold'
                    : 'text-[#666666] hover:text-[#1A1A1A]'
                }`}
              >
                全部
              </button>
              <button
                onClick={() => setFilterStatus('untranslated')}
                className={`px-2.5 py-1 text-xs transition-colors rounded-sm cursor-pointer ${
                  filterStatus === 'untranslated'
                    ? 'bg-[#1A1A1A] text-white font-semibold'
                    : 'text-[#666666] hover:text-[#1A1A1A]'
                }`}
              >
                未完结
              </button>
              <button
                onClick={() => setFilterStatus('completed')}
                className={`px-2.5 py-1 text-xs transition-colors rounded-sm cursor-pointer ${
                  filterStatus === 'completed'
                    ? 'bg-[#1A1A1A] text-white font-semibold'
                    : 'text-[#666666] hover:text-[#1A1A1A]'
                }`}
              >
                已完成
              </button>
            </div>
          </div>

          {/* Search Box */}
          <div className="relative">
            <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-[#888888]" />
            <input
              type="text"
              placeholder="按书名或作者检索..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full bg-white border border-[#E5E0D8] rounded-sm pl-9 pr-4 py-2 text-xs text-[#1A1A1A] focus:outline-none focus:border-[#1D4ED8] shadow-sm font-sans placeholder-[#999999]"
            />
          </div>

          {/* Book Cards List */}
          <div className="space-y-3 max-h-[640px] overflow-y-auto pr-1">
            {filteredBooks.length === 0 ? (
              <div className="bg-white border border-dashed border-[#E5E0D8] rounded-sm p-8 text-center text-xs text-[#888888]">
                暂无符合条件的小说。请在上方拖拽上传 .epub 或 .txt
              </div>
            ) : (
              filteredBooks.map((book) => {
                const queueItem = bookQueueMap.get(book.id);
                const isRunningInQueue = queueItem?.status === 'running';
                const isPendingInQueue = queueItem?.status === 'pending';
                const isCompleted = book.status === 'completed';
                const progressPct =
                  book.total_paragraphs > 0
                    ? Math.min(100, Math.round((book.translated_paragraphs / book.total_paragraphs) * 100))
                    : 0;

                return (
                  <div
                    key={book.id}
                    className="bg-white hover:bg-[#FAF9F6] border border-[#E5E0D8] rounded-sm p-4 space-y-3 shadow-sm transition-all"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="space-y-1 min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <h3 className="font-serif font-bold text-sm text-[#1A1A1A] truncate">
                            {book.name}
                          </h3>
                        </div>
                        <p className="text-[11px] text-[#666666] font-mono">
                          {book.source_type?.toUpperCase() || 'EPUB'} · {book.total_chapters} 章节 · 共 {book.total_paragraphs.toLocaleString()} 段
                        </p>
                      </div>

                      {/* In-Queue Status Badges */}
                      <div className="shrink-0">
                        {isRunningInQueue ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-mono font-bold bg-emerald-50 border border-emerald-300 text-emerald-800 rounded-sm">
                            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-ping" />
                            正在翻译中
                          </span>
                        ) : isPendingInQueue ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-mono font-semibold bg-amber-50 border border-amber-300 text-amber-800 rounded-sm">
                            <Clock className="w-3 h-3 text-amber-600" />
                            排队 #{queueItem?.order_index}
                          </span>
                        ) : isCompleted ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-mono font-bold bg-[#FAF9F6] border border-[#E5E0D8] text-emerald-700 rounded-sm">
                            <CheckCircle2 className="w-3 h-3 text-emerald-600" />
                            已完结
                          </span>
                        ) : (
                          <span className="inline-flex items-center px-2 py-0.5 text-[10px] font-mono bg-[#F2EFE9] text-[#666666] rounded-sm">
                            待翻译
                          </span>
                        )}
                      </div>
                    </div>

                    {/* Progress Bar */}
                    <div className="space-y-1">
                      <div className="flex items-center justify-between text-[11px] text-[#666666] font-mono">
                        <span>进度 {book.translated_chapters}/{book.total_chapters} 章</span>
                        <span>{progressPct}% ({book.translated_paragraphs}/{book.total_paragraphs} 段)</span>
                      </div>
                      <div className="w-full bg-[#F2EFE9] h-1.5 overflow-hidden rounded-sm border border-[#E5E0D8]">
                        <div
                          className={`h-full transition-all duration-300 ${
                            isCompleted ? 'bg-emerald-600' : isRunningInQueue ? 'bg-[#1D4ED8]' : 'bg-[#4A4A4A]'
                          }`}
                          style={{ width: `${progressPct}%` }}
                        />
                      </div>
                    </div>

                    {/* Action Toolbar */}
                    <div className="flex items-center justify-between pt-2 border-t border-[#E5E0D8] text-xs">
                      <div className="flex items-center gap-2">
                        {isCompleted ? (
                          <>
                            <button
                              onClick={() => onSelectBook(book.id, 'reader')}
                              className="px-2 py-1 text-xs text-[#1D4ED8] hover:underline font-medium cursor-pointer"
                            >
                              阅读
                            </button>
                            <button
                              onClick={(e) => handleExport(book.id, e)}
                              disabled={exportingBookId === book.id}
                              className="px-2 py-1 text-xs text-[#4A4A4A] hover:text-[#1A1A1A] cursor-pointer"
                            >
                              {exportingBookId === book.id ? '导出中...' : '导出EPUB'}
                            </button>
                          </>
                        ) : (
                          <button
                            onClick={() => onSelectBook(book.id, 'studio')}
                            className="px-2 py-1 text-xs text-[#4A4A4A] hover:text-[#1A1A1A] cursor-pointer"
                          >
                            详情
                          </button>
                        )}

                        <button
                          onClick={(e) => handleResetBook(book.id, book.name, e)}
                          disabled={resettingBookId === book.id || isRunningInQueue}
                          className="px-2 py-1 text-xs text-[#666666] hover:text-[#1A1A1A] disabled:opacity-30 cursor-pointer"
                          title="清空翻译历史与长程记忆"
                        >
                          {resettingBookId === book.id ? '重置中...' : '重置'}
                        </button>

                        <button
                          onClick={(e) => handleDeleteBook(book.id, book.name, e)}
                          disabled={deletingBookId === book.id || isRunningInQueue}
                          className="px-2 py-1 text-xs text-[#888888] hover:text-rose-600 disabled:opacity-30 cursor-pointer"
                          title="彻底删除该书籍"
                        >
                          删除
                        </button>
                      </div>

                      {/* Enqueue Action Button */}
                      {isRunningInQueue ? (
                        <button
                          onClick={() => onSelectBook(book.id, 'studio')}
                          className="flex items-center gap-1 px-3 py-1 bg-[#1D4ED8] text-white text-xs font-semibold rounded-sm shadow-sm cursor-pointer"
                        >
                          <Play className="w-3 h-3 fill-white" />
                          查看控制台
                        </button>
                      ) : isPendingInQueue ? (
                        <button
                          onClick={(e) => handleCancelItem(queueItem!.id, e)}
                          className="flex items-center gap-1 px-2.5 py-1 bg-amber-50 hover:bg-amber-100 border border-amber-300 text-amber-800 text-xs rounded-sm transition-all cursor-pointer"
                          title="从排队中移出"
                        >
                          <X className="w-3 h-3" />
                          移出队列
                        </button>
                      ) : (
                        <button
                          onClick={(e) => handleEnqueue(book.id, e)}
                          className="flex items-center gap-1 px-3 py-1 bg-white hover:bg-[#FAF9F6] border border-[#1A1A1A] text-[#1A1A1A] text-xs font-serif font-bold rounded-sm transition-all cursor-pointer shadow-sm"
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
          
          {/* Queue Status Control Bar */}
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-white border border-[#E5E0D8] p-4 rounded-sm shadow-sm">
            <div className="flex items-center gap-3">
              <span
                className={`w-3 h-3 rounded-full ${
                  queueStatus?.is_paused
                    ? 'bg-amber-500'
                    : runningItems.length > 0
                    ? 'bg-emerald-500 animate-pulse'
                    : 'bg-[#888888]'
                }`}
              />
              <div>
                <h2 className="text-sm font-serif font-bold text-[#1A1A1A]">
                  执行队列调度中心
                </h2>
                <p className="text-[11px] text-[#666666] font-mono">
                  {queueStatus?.is_paused
                    ? '状态: 待命暂停 (点击启动开始处理)'
                    : runningItems.length > 0
                    ? `正在翻译 ${runningItems.length} 部 · 排队等待 ${pendingItems.length} 部`
                    : '就绪中 (等待书籍加入)'}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-3">
              {/* Concurrency Selector */}
              <div className="flex items-center gap-1 text-xs text-[#666666]">
                <span>并发槽位:</span>
                <select
                  value={queueStatus?.concurrency || 1}
                  onChange={(e) => handleChangeConcurrency(Number(e.target.value))}
                  className="bg-[#FAF9F6] border border-[#E5E0D8] text-[#1A1A1A] rounded-sm px-2 py-1 text-xs font-mono focus:outline-none cursor-pointer"
                >
                  <option value={1}>1 本 (推荐)</option>
                  <option value={2}>2 本并行</option>
                  <option value={3}>3 本并行</option>
                  <option value={4}>4 本最大</option>
                </select>
              </div>

              {/* Pause / Resume Button */}
              <button
                onClick={handleToggleQueuePause}
                className={`flex items-center gap-1.5 px-4 py-1.5 rounded-sm text-xs font-semibold shadow-sm transition-all cursor-pointer ${
                  queueStatus?.is_paused
                    ? 'bg-emerald-600 hover:bg-emerald-500 text-white font-bold'
                    : 'bg-amber-50 border border-amber-300 text-amber-800 hover:bg-amber-100'
                }`}
              >
                {queueStatus?.is_paused ? (
                  <>
                    <Play className="w-3.5 h-3.5 fill-white" />
                    <span>启动队列</span>
                  </>
                ) : (
                  <>
                    <Pause className="w-3.5 h-3.5 fill-current" />
                    <span>暂停调度</span>
                  </>
                )}
              </button>
            </div>
          </div>

          {/* Section 1: RUNNING ITEMS */}
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs font-serif font-bold text-[#1A1A1A] px-1">
              <span className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-emerald-500 animate-ping" />
                正在执行的翻译任务 ({runningItems.length})
              </span>
            </div>

            {runningItems.length === 0 ? (
              <div className="bg-[#FAF9F6] border border-dashed border-[#E5E0D8] rounded-sm p-6 text-center text-xs text-[#888888]">
                当前无正在执行的书籍。
              </div>
            ) : (
              runningItems.map((item) => {
                const progressPct = Math.min(100, Math.round((item.overall_progress || 0) * 100));

                return (
                  <div
                    key={item.id}
                    className="bg-white border-2 border-[#1D4ED8] rounded-sm p-4 shadow-sm space-y-3"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="px-1.5 py-0.5 bg-[#EFF6FF] border border-[#BFDBFE] text-[#1D4ED8] text-[10px] font-mono font-bold rounded-sm">
                            RUNNING
                          </span>
                          <h3 className="font-serif font-bold text-[#1A1A1A] text-sm truncate">
                            {item.book_name}
                          </h3>
                        </div>
                      </div>

                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => onSelectBook(item.book_id, 'studio')}
                          className="flex items-center gap-1 px-3 py-1.5 bg-[#1D4ED8] hover:bg-[#1E40AF] text-white text-xs font-semibold rounded-sm shadow-sm cursor-pointer"
                        >
                          <Play className="w-3.5 h-3.5 fill-white" />
                          查看控制台
                        </button>

                        <button
                          onClick={(e) => handleCancelItem(item.id, e)}
                          className="p-1.5 bg-rose-50 hover:bg-rose-100 text-rose-700 border border-rose-200 rounded-sm cursor-pointer"
                          title="终止该任务"
                        >
                          <X className="w-4 h-4" />
                        </button>
                      </div>
                    </div>

                    {/* Progress Bar */}
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between text-xs text-[#4A4A4A] font-mono">
                        <span className="truncate max-w-md text-[#1D4ED8]">{item.message}</span>
                        <span>{item.current_chapter || `第 ${item.current_chapter_index}/${item.total_chapters} 章`}</span>
                      </div>
                      <div className="w-full bg-[#F2EFE9] h-2 overflow-hidden rounded-sm border border-[#E5E0D8]">
                        <div
                          className="h-full bg-[#1D4ED8] transition-all duration-300"
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
            <div className="flex items-center justify-between text-xs font-serif font-bold text-[#1A1A1A] px-1">
              <span className="flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5 text-amber-600" />
                等待排队列表 ({pendingItems.length})
                <span className="text-[11px] font-normal text-[#888888] ml-1 font-sans">
                  (按住左侧 ⠿ 抓手或点击上下箭头调序)
                </span>
              </span>
            </div>

            {queueStatus?.is_paused && pendingItems.length > 0 && (
              <div className="flex items-center justify-between gap-3 p-3 rounded-sm bg-amber-50 border border-amber-300 text-amber-900 text-xs">
                <div className="flex items-center gap-2">
                  <Clock className="w-4 h-4 text-amber-600 shrink-0" />
                  <span>队列处于待命暂停状态，已排队 {pendingItems.length} 部小说。调序完成后点击「启动队列」。</span>
                </div>
                <button
                  onClick={handleToggleQueuePause}
                  className="px-3 py-1 rounded-sm bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs shadow-sm cursor-pointer flex items-center gap-1 shrink-0"
                >
                  <Play className="w-3 h-3 fill-white" />
                  启动队列
                </button>
              </div>
            )}

            {pendingItems.length === 0 ? (
              <div className="bg-white border border-dashed border-[#E5E0D8] rounded-sm p-6 text-center text-xs text-[#888888]">
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
                      className={`group relative flex items-center justify-between gap-3 bg-white hover:bg-[#FAF9F6] border rounded-sm p-3 transition-all select-none ${
                        isDragging
                          ? 'opacity-40 border-dashed border-[#1D4ED8]'
                          : isOver
                          ? 'border-t-4 border-t-[#1D4ED8] bg-[#EFF6FF]'
                          : 'border-[#E5E0D8] shadow-sm'
                      }`}
                    >
                      {/* Left: Drag Handle & Order Badge */}
                      <div className="flex items-center gap-2.5 min-w-0 flex-1">
                        <div
                          className="cursor-grab active:cursor-grabbing p-1 text-[#888888] hover:text-[#1A1A1A] transition-colors"
                          title="拖动调整顺序"
                        >
                          <GripVertical className="w-4 h-4" />
                        </div>

                        <span className="text-xs font-mono font-bold px-2 py-0.5 bg-[#FAF9F6] border border-[#E5E0D8] text-[#1A1A1A] rounded-sm">
                          #{index + 1}
                        </span>

                        <div className="min-w-0 flex-1">
                          <h4 className="text-xs font-serif font-bold text-[#1A1A1A] truncate">
                            {item.book_name}
                          </h4>
                          <p className="text-[10px] text-[#666666] font-mono truncate">
                            {item.message}
                          </p>
                        </div>
                      </div>

                      {/* Right: Auxiliary Up/Down/Top & Remove Buttons */}
                      <div className="flex items-center gap-1 shrink-0">
                        {index > 0 && (
                          <button
                            onClick={(e) => handleMoveItem(item.id, 'top', e)}
                            className="p-1 rounded-sm bg-[#F2EFE9] hover:bg-white text-[#4A4A4A] transition-colors cursor-pointer"
                            title="置顶"
                          >
                            <ArrowUpToLine className="w-3.5 h-3.5" />
                          </button>
                        )}
                        {index > 0 && (
                          <button
                            onClick={(e) => handleMoveItem(item.id, 'up', e)}
                            className="p-1 rounded-sm bg-[#F2EFE9] hover:bg-white text-[#4A4A4A] transition-colors cursor-pointer"
                            title="上移一位"
                          >
                            <ArrowUp className="w-3.5 h-3.5" />
                          </button>
                        )}
                        {index < pendingItems.length - 1 && (
                          <button
                            onClick={(e) => handleMoveItem(item.id, 'down', e)}
                            className="p-1 rounded-sm bg-[#F2EFE9] hover:bg-white text-[#4A4A4A] transition-colors cursor-pointer"
                            title="下移一位"
                          >
                            <ArrowDown className="w-3.5 h-3.5" />
                          </button>
                        )}

                        <button
                          onClick={(e) => handleCancelItem(item.id, e)}
                          className="p-1 rounded-sm bg-[#F2EFE9] hover:bg-rose-50 text-[#666666] hover:text-rose-600 transition-colors cursor-pointer ml-1"
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
              <div className="flex items-center justify-between text-xs font-serif font-bold text-[#666666] px-1">
                <span>🏁 历史完成与异常记录 ({finishedItems.length})</span>
                <button
                  onClick={() => handleClearQueue('all_finished')}
                  className="text-[11px] text-[#888888] hover:text-[#1A1A1A] cursor-pointer underline"
                >
                  全部清空
                </button>
              </div>

              <div className="space-y-2 max-h-[300px] overflow-y-auto pr-1">
                {finishedItems.map((item) => {
                  const isSuccess = item.status === 'completed';
                  const isFailed = item.status === 'failed';

                  return (
                    <div
                      key={item.id}
                      className={`flex items-center justify-between gap-3 rounded-sm p-3 text-xs border ${
                        isSuccess
                          ? 'bg-white border-[#E5E0D8]'
                          : 'bg-rose-50 border-rose-200'
                      }`}
                    >
                      <div className="flex items-center gap-2.5 min-w-0 flex-1">
                        {isSuccess ? (
                          <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
                        ) : (
                          <AlertCircle className="w-4 h-4 text-rose-600 shrink-0" />
                        )}

                        <div className="min-w-0 flex-1">
                          <h4 className="font-serif font-bold text-[#1A1A1A] truncate">
                            {item.book_name}
                          </h4>
                          <p className={`text-[10px] truncate ${isFailed ? 'text-rose-700' : 'text-[#666666]'}`}>
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
                            className="flex items-center gap-1 px-2.5 py-1 rounded-sm bg-rose-600 hover:bg-rose-500 text-white text-[11px] font-medium shadow-sm cursor-pointer"
                            title="重新入队执行重试"
                          >
                            <RotateCcw className="w-3 h-3" />
                            重试
                          </button>
                        )}

                        {isSuccess && (
                          <button
                            onClick={() => onSelectBook(item.book_id, 'reader')}
                            className="flex items-center gap-1 px-2.5 py-1 rounded-sm bg-white border border-[#E5E0D8] hover:bg-[#FAF9F6] text-[#1A1A1A] text-[11px] font-medium cursor-pointer shadow-sm"
                          >
                            <BookOpen className="w-3 h-3" />
                            阅读
                          </button>
                        )}

                        <button
                          onClick={(e) => handleCancelItem(item.id, e)}
                          className="p-1 rounded-sm text-[#888888] hover:text-[#1A1A1A] cursor-pointer"
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
