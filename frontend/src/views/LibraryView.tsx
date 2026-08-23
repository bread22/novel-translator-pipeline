import React, { useState, useRef } from 'react';
import {
  UploadCloud,
  FileText,
  Play,
  CheckCircle2,
  Clock,
  Download,
  Share2,
  Search,
  Sparkles,
  BookOpen,
  RotateCcw,
  Trash2,
} from 'lucide-react';
import { BookSummary } from '../types/api';
import { api } from '../lib/api';

interface LibraryViewProps {
  books: BookSummary[];
  onRefreshBooks: () => Promise<void>;
  onSelectBook: (bookId: string, targetTab?: string) => void;
}

export const LibraryView: React.FC<LibraryViewProps> = ({
  books,
  onRefreshBooks,
  onSelectBook,
}) => {
  const [search, setSearch] = useState('');
  const [filterStatus, setFilterStatus] = useState<'all' | 'in_progress' | 'completed'>('all');
  const [isUploading, setIsUploading] = useState(false);
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);
  const [exportingBookId, setExportingBookId] = useState<string | null>(null);
  const [resettingBookId, setResettingBookId] = useState<string | null>(null);
  const [deletingBookId, setDeletingBookId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const filteredBooks = books.filter((book) => {
    const matchesSearch = book.name.toLowerCase().includes(search.toLowerCase());
    if (!matchesSearch) return false;
    if (filterStatus === 'completed') return book.status === 'completed';
    if (filterStatus === 'in_progress') return book.status !== 'completed';
    return true;
  });

  const handleFileUpload = async (file: File) => {
    setIsUploading(true);
    setUploadMessage(`正在上传并解析 "${file.name}"...`);
    try {
      const newBook = await api.uploadBook(file);
      setUploadMessage(`"${newBook.name}" 导入成功！`);
      await onRefreshBooks();
      onSelectBook(newBook.id, 'studio');
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

  const handleResetBook = async (bookId: string, bookName: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`确定要重置《${bookName}》的翻译进度吗？\n\n注意：这会清空已翻译的段落文本并重置所有提取的长程记忆，从头开始重新翻译。`)) {
      return;
    }
    setResettingBookId(bookId);
    try {
      await api.resetBook(bookId);
      await onRefreshBooks();
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
      setUploadMessage(`《${bookName}》已彻底删除`);
    } catch (err: any) {
      alert(`删除失败: ${err.message}`);
    } finally {
      setDeletingBookId(null);
      setTimeout(() => setUploadMessage(null), 4000);
    }
  };

  return (
    <div className="space-y-8 max-w-7xl mx-auto pb-16">
      {/* Hero / Upload Banner */}
      <div
        onDragOver={(e) => e.preventDefault()}
        onDrop={handleDrop}
        className="relative overflow-hidden rounded-2xl border border-indigo-900/50 bg-gradient-to-br from-indigo-950/40 via-slate-900/80 to-slate-950 p-8 shadow-2xl backdrop-blur-sm"
      >
        <div className="absolute -right-12 -top-12 w-64 h-64 bg-indigo-500/10 rounded-full blur-3xl pointer-events-none" />
        <div className="relative z-10 flex flex-col md:flex-row items-center justify-between gap-6">
          <div className="space-y-2 text-center md:text-left">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-indigo-900/60 border border-indigo-700/50 text-indigo-300 text-xs font-medium">
              <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
              100% 自动 AI 流水线 · Drop & Read
            </div>
            <h1 className="text-2xl md:text-3xl font-bold tracking-tight text-white">
              拖拽日文 EPUB / TXT 小说，即刻启动翻译
            </h1>
            <p className="text-slate-400 text-sm max-w-xl">
              自动构建工作区、执行两级降级容灾、提取长程实体记忆与章节一致性审阅，一键交付横排中文排版 EPUB。
            </p>
          </div>

          <div>
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
              className="flex items-center gap-2.5 px-6 py-3.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-sm shadow-lg shadow-indigo-600/30 transition-all hover:scale-105 active:scale-95 disabled:opacity-50 cursor-pointer"
            >
              <UploadCloud className="w-5 h-5" />
              {isUploading ? '正在上传解析中...' : '点击上传或拖入文件'}
            </button>
          </div>
        </div>

        {uploadMessage && (
          <div className="mt-4 p-3 rounded-lg bg-indigo-900/50 border border-indigo-700 text-indigo-200 text-xs flex items-center gap-2 animate-fade-in">
            <FileText className="w-4 h-4" />
            {uploadMessage}
          </div>
        )}
      </div>

      {/* Filter & Search Bar */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
        <div className="flex items-center gap-2 bg-slate-900/80 p-1 rounded-xl border border-slate-800">
          <button
            onClick={() => setFilterStatus('all')}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-medium transition-all ${
              filterStatus === 'all'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            全部书籍 ({books.length})
          </button>
          <button
            onClick={() => setFilterStatus('in_progress')}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-medium transition-all ${
              filterStatus === 'in_progress'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            翻译进行中 ({books.filter((b) => b.status !== 'completed').length})
          </button>
          <button
            onClick={() => setFilterStatus('completed')}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-medium transition-all ${
              filterStatus === 'completed'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            已完成 ({books.filter((b) => b.status === 'completed').length})
          </button>
        </div>

        <div className="relative w-full sm:w-72">
          <Search className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            placeholder="搜索书名..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full bg-slate-900/90 border border-slate-800 rounded-xl pl-9 pr-4 py-2 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500"
          />
        </div>
      </div>

      {/* Book Grid */}
      {filteredBooks.length === 0 ? (
        <div className="text-center py-16 border border-dashed border-slate-800 rounded-2xl p-12">
          <BookOpen className="w-12 h-12 text-slate-600 mx-auto mb-3" />
          <h3 className="text-slate-300 font-medium">暂无匹配的书籍</h3>
          <p className="text-slate-500 text-xs mt-1">拖拽 EPUB 文件到上方区域即可开始全自动翻译</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {filteredBooks.map((book) => {
            const isCompleted = book.status === 'completed';
            const progressPercent = Math.round((book.progress_percentage || 0) * 100);

            return (
              <div
                key={book.id}
                onClick={() => onSelectBook(book.id, 'reader')}
                className="group relative flex flex-col justify-between bg-slate-900/70 hover:bg-slate-900 border border-slate-800 hover:border-slate-700 rounded-2xl p-6 transition-all duration-200 hover:shadow-xl hover:shadow-indigo-500/5 cursor-pointer"
              >
                <div>
                  {/* Top Badge & Delete / Reset Actions */}
                  <div className="flex items-center justify-between gap-2 mb-3">
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-300 uppercase">
                        {book.source_type || 'epub'}
                      </span>
                      {isCompleted ? (
                        <span className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-400 bg-emerald-950/60 border border-emerald-800/60 px-2 py-0.5 rounded-full">
                          <CheckCircle2 className="w-3 h-3" />
                          已完结
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-[11px] font-medium text-amber-400 bg-amber-950/60 border border-amber-800/60 px-2 py-0.5 rounded-full">
                          <Clock className="w-3 h-3" />
                          {progressPercent}%
                        </span>
                      )}
                    </div>

                    {/* Top Right: Reset & Delete Buttons */}
                    <div className="flex items-center gap-1 opacity-60 group-hover:opacity-100 transition-opacity">
                      <button
                        type="button"
                        onClick={(e) => handleResetBook(book.id, book.name, e)}
                        disabled={resettingBookId === book.id}
                        className="p-1.5 rounded-lg bg-slate-800/80 hover:bg-amber-950/80 text-slate-400 hover:text-amber-300 border border-slate-700/50 transition-colors"
                        title="重置全书翻译进度与记忆"
                      >
                        <RotateCcw className={`w-3.5 h-3.5 ${resettingBookId === book.id ? 'animate-spin' : ''}`} />
                      </button>

                      <button
                        type="button"
                        onClick={(e) => handleDeleteBook(book.id, book.name, e)}
                        disabled={deletingBookId === book.id}
                        className="p-1.5 rounded-lg bg-slate-800/80 hover:bg-rose-950/80 text-slate-400 hover:text-rose-400 border border-slate-700/50 transition-colors"
                        title="删除该书籍及工作区"
                      >
                        <Trash2 className={`w-3.5 h-3.5 ${deletingBookId === book.id ? 'animate-pulse' : ''}`} />
                      </button>
                    </div>
                  </div>

                  {/* Title */}
                  <h3 className="font-bold text-slate-100 text-base line-clamp-2 group-hover:text-indigo-300 transition-colors">
                    {book.name}
                  </h3>

                  {/* Stats */}
                  <div className="grid grid-cols-2 gap-2 mt-4 pt-4 border-t border-slate-800/80 text-xs">
                    <div>
                      <span className="text-slate-500 text-[11px]">章节进度</span>
                      <p className="font-medium text-slate-300">
                        {book.translated_chapters} / {book.total_chapters} 章
                      </p>
                    </div>
                    <div>
                      <span className="text-slate-500 text-[11px]">段落统计</span>
                      <p className="font-medium text-slate-300">
                        {book.translated_paragraphs} / {book.total_paragraphs} 段
                      </p>
                    </div>
                  </div>

                  {/* Progress Bar */}
                  <div className="w-full bg-slate-800/80 rounded-full h-1.5 mt-3 overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${
                        isCompleted
                          ? 'bg-emerald-500'
                          : 'bg-gradient-to-r from-indigo-500 to-purple-500'
                      }`}
                      style={{ width: `${progressPercent}%` }}
                    />
                  </div>
                </div>

                {/* Actions */}
                <div className="flex items-center justify-between gap-2 mt-5 pt-4 border-t border-slate-800">
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelectBook(book.id, 'studio');
                    }}
                    className="flex-1 flex items-center justify-center gap-1.5 py-2 px-3 rounded-lg bg-indigo-600/20 hover:bg-indigo-600 text-indigo-300 hover:text-white text-xs font-medium transition-all"
                  >
                    <Play className="w-3.5 h-3.5" />
                    作战室
                  </button>

                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelectBook(book.id, 'reader');
                    }}
                    className="flex-1 flex items-center justify-center gap-1.5 py-2 px-3 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium transition-all"
                  >
                    <BookOpen className="w-3.5 h-3.5" />
                    双语阅读
                  </button>

                  {book.has_output_epub ? (
                    <a
                      href={book.epub_download_url || `/api/v1/books/${book.id}/download`}
                      download
                      onClick={(e) => e.stopPropagation()}
                      className="flex items-center justify-center p-2 rounded-lg bg-emerald-950/60 border border-emerald-700/60 hover:bg-emerald-700 text-emerald-300 hover:text-white transition-all"
                      title="下载横排成品 EPUB"
                    >
                      <Download className="w-4 h-4" />
                    </a>
                  ) : (
                    <button
                      onClick={(e) => handleExport(book.id, e)}
                      disabled={exportingBookId === book.id}
                      className="flex items-center justify-center p-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-slate-200 transition-all disabled:opacity-50"
                      title="导出横排 EPUB"
                    >
                      <Share2 className={`w-4 h-4 ${exportingBookId === book.id ? 'animate-spin' : ''}`} />
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
