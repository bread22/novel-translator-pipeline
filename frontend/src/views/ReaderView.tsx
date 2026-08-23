import React, { useState, useEffect } from 'react';
import {
  BookOpen,
  ChevronLeft,
  ChevronRight,
  Edit3,
  Check,
  RotateCcw,
  Search,
} from 'lucide-react';
import { BookSummary, ChapterDetail, ChapterSummary } from '../types/api';
import { api } from '../lib/api';

interface ReaderViewProps {
  book: BookSummary | null;
}

export const ReaderView: React.FC<ReaderViewProps> = ({ book }) => {
  const [chapters, setChapters] = useState<ChapterSummary[]>([]);
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(null);
  const [chapterDetail, setChapterDetail] = useState<ChapterDetail | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [editingParaId, setEditingParaId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState('');
  const [retranslatingParaId, setRetranslatingParaId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');

  useEffect(() => {
    if (book) {
      loadChapters(book.id);
    }
  }, [book]);

  const loadChapters = async (bookId: string) => {
    try {
      const data = await api.getChapters(bookId);
      setChapters(data);
      if (data.length > 0) {
        setSelectedChapterId(data[0].id);
        loadChapterDetail(bookId, data[0].id);
      }
    } catch (err: any) {
      console.error('Failed to load chapters:', err);
    }
  };

  const loadChapterDetail = async (bookId: string, chapterId: string) => {
    setIsLoading(true);
    try {
      const detail = await api.getChapterDetail(bookId, chapterId);
      setChapterDetail(detail);
    } catch (err: any) {
      alert(`加载章节失败: ${err.message}`);
    } finally {
      setIsLoading(false);
    }
  };

  const handleSelectChapter = (chId: string) => {
    if (!book) return;
    setSelectedChapterId(chId);
    setEditingParaId(null);
    loadChapterDetail(book.id, chId);
  };

  const handleStartEdit = (paraId: string, currentText: string) => {
    setEditingParaId(paraId);
    setEditContent(currentText);
  };

  const handleSaveEdit = async (paraId: string) => {
    if (!book) return;
    try {
      await api.updateParagraph(book.id, paraId, editContent);
      if (chapterDetail) {
        setChapterDetail({
          ...chapterDetail,
          paragraphs: chapterDetail.paragraphs.map((p) =>
            p.id === paraId ? { ...p, translated: editContent, status: 'manually_edited' } : p
          ),
        });
      }
      setEditingParaId(null);
    } catch (err: any) {
      alert(`保存修改失败: ${err.message}`);
    }
  };

  const handleRetranslate = async (paraId: string) => {
    if (!book || !selectedChapterId) return;
    setRetranslatingParaId(paraId);
    try {
      const res = await api.retranslateParagraph(book.id, selectedChapterId, paraId);
      if (chapterDetail) {
        setChapterDetail({
          ...chapterDetail,
          paragraphs: chapterDetail.paragraphs.map((p) =>
            p.id === paraId ? { ...p, translated: res.translated, status: 'translated' } : p
          ),
        });
      }
    } catch (err: any) {
      alert(`重译失败: ${err.message}`);
    } finally {
      setRetranslatingParaId(null);
    }
  };

  if (!book) {
    return (
      <div className="text-center py-24 border border-dashed border-slate-800 rounded-2xl p-12 max-w-xl mx-auto">
        <BookOpen className="w-12 h-12 text-slate-600 mx-auto mb-3" />
        <h3 className="text-slate-300 font-medium">未选择书籍</h3>
        <p className="text-slate-500 text-xs mt-1">请在顶部下拉列表选择一部小说进行阅读与校对</p>
      </div>
    );
  }

  const currentIndex = chapters.findIndex((c) => c.id === selectedChapterId);
  const prevChapter = currentIndex > 0 ? chapters[currentIndex - 1] : null;
  const nextChapter = currentIndex < chapters.length - 1 ? chapters[currentIndex + 1] : null;

  const filteredParagraphs = chapterDetail?.paragraphs.filter((p) => {
    if (!searchQuery) return true;
    return (
      p.source.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.translated.toLowerCase().includes(searchQuery.toLowerCase())
    );
  });

  return (
    <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 max-w-7xl mx-auto pb-16">
      {/* Sidebar: Chapter List */}
      <div className="lg:col-span-1 bg-slate-900/80 border border-slate-800 rounded-2xl p-4 flex flex-col h-[calc(100vh-140px)] sticky top-20">
        <div className="flex items-center justify-between gap-2 pb-3 mb-3 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <BookOpen className="w-4 h-4 text-indigo-400" />
            <h3 className="font-bold text-slate-200 text-sm">章节目录 ({chapters.length})</h3>
          </div>
        </div>

        <div className="overflow-y-auto flex-1 space-y-1 pr-1">
          {chapters.map((ch) => {
            const isSelected = ch.id === selectedChapterId;
            const isFinished = ch.total_paragraphs > 0 && ch.translated_paragraphs === ch.total_paragraphs;

            return (
              <button
                key={ch.id}
                onClick={() => handleSelectChapter(ch.id)}
                className={`w-full text-left p-2.5 rounded-xl text-xs transition-all flex items-center justify-between gap-2 ${
                  isSelected
                    ? 'bg-indigo-600 text-white font-medium shadow-md shadow-indigo-600/20'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
                }`}
              >
                <span className="truncate flex-1">
                  {ch.index}. {ch.title}
                </span>
                {isFinished && (
                  <span
                    className={`w-2 h-2 rounded-full ${
                      isSelected ? 'bg-white' : 'bg-emerald-400'
                    }`}
                  />
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Main Content Area: Bilingual Reader */}
      <div className="lg:col-span-3 space-y-6">
        {/* Chapter Header Banner & Nav */}
        <div className="bg-slate-900/90 border border-slate-800 p-6 rounded-2xl flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-400">
                Chapter {chapterDetail?.index || 1}
              </span>
              <h2 className="text-xl font-bold text-white tracking-tight">
                {chapterDetail?.title || '加载中...'}
              </h2>
            </div>
            <p className="text-xs text-slate-400 mt-1">
              共 {chapterDetail?.total_paragraphs || 0} 个段落 · 已翻译 {chapterDetail?.translated_paragraphs || 0} 段
            </p>
          </div>

          {/* Prev / Next chapter buttons */}
          <div className="flex items-center gap-2">
            <button
              onClick={() => prevChapter && handleSelectChapter(prevChapter.id)}
              disabled={!prevChapter}
              className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 disabled:opacity-30 text-slate-300 text-xs font-medium transition-all"
            >
              <ChevronLeft className="w-4 h-4" />
              上一章
            </button>
            <button
              onClick={() => nextChapter && handleSelectChapter(nextChapter.id)}
              disabled={!nextChapter}
              className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 disabled:opacity-30 text-slate-300 text-xs font-medium transition-all"
            >
              下一章
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Search inside chapter */}
        <div className="relative">
          <Search className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            placeholder="在当前章节中搜索中日双语关键词..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full bg-slate-900/90 border border-slate-800 rounded-xl pl-9 pr-4 py-2.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
        </div>

        {/* Paragraphs List */}
        {isLoading ? (
          <div className="text-center py-20 text-slate-500 text-xs">正在加载章节双语段落...</div>
        ) : !filteredParagraphs || filteredParagraphs.length === 0 ? (
          <div className="text-center py-20 text-slate-500 text-xs">未找到段落数据</div>
        ) : (
          <div className="space-y-4">
            {filteredParagraphs.map((para) => {
              const isEditing = editingParaId === para.id;
              const isRetranslating = retranslatingParaId === para.id;
              const isFallback = para.status === 'fallback_recovered';

              return (
                <div
                  key={para.id}
                  className={`p-5 rounded-2xl border transition-all duration-150 ${
                    isFallback
                      ? 'bg-slate-900/70 border-amber-800/40 hover:border-amber-700/60'
                      : 'bg-slate-900/70 border-slate-800 hover:border-slate-700'
                  }`}
                >
                  {/* Top Bar for Paragraph: ID, status, provenance badges */}
                  <div className="flex items-center justify-between gap-2 mb-3 pb-2 border-b border-slate-800/60 text-[11px]">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-slate-500">#{para.index + 1}</span>
                      {para.provider && (
                        <span className="px-2 py-0.5 rounded bg-indigo-950/80 border border-indigo-800/50 text-indigo-300 font-mono">
                          {para.provider}
                        </span>
                      )}
                      {isFallback && (
                        <span className="px-2 py-0.5 rounded bg-amber-950/80 border border-amber-800/50 text-amber-300">
                          容灾救回 ({para.fallback_from || '主译'} 拦截)
                        </span>
                      )}
                    </div>

                    {/* Quick Action buttons */}
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => handleRetranslate(para.id)}
                        disabled={isRetranslating}
                        className="flex items-center gap-1 px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-slate-200 text-[11px] transition-all disabled:opacity-50"
                        title="使用当前主译重新翻译这一段"
                      >
                        <RotateCcw className={`w-3 h-3 ${isRetranslating ? 'animate-spin' : ''}`} />
                        重译
                      </button>
                      {!isEditing ? (
                        <button
                          onClick={() => handleStartEdit(para.id, para.translated)}
                          className="flex items-center gap-1 px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-slate-200 text-[11px] transition-all"
                        >
                          <Edit3 className="w-3 h-3" />
                          编辑
                        </button>
                      ) : null}
                    </div>
                  </div>

                  {/* Japanese Source */}
                  <div className="text-slate-400 text-xs font-novel leading-relaxed mb-3 select-text">
                    {para.source}
                  </div>

                  {/* Chinese Translated */}
                  {isEditing ? (
                    <div className="space-y-2 mt-2">
                      <textarea
                        value={editContent}
                        onChange={(e) => setEditContent(e.target.value)}
                        className="w-full bg-slate-950 border border-indigo-500 rounded-xl p-3 text-sm text-slate-100 font-novel leading-relaxed focus:outline-none"
                        rows={3}
                        autoFocus
                      />
                      <div className="flex justify-end gap-2">
                        <button
                          onClick={() => setEditingParaId(null)}
                          className="px-3 py-1 rounded-lg bg-slate-800 text-slate-400 text-xs hover:text-white"
                        >
                          取消
                        </button>
                        <button
                          onClick={() => handleSaveEdit(para.id)}
                          className="flex items-center gap-1 px-3 py-1 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium shadow-md shadow-indigo-600/30"
                        >
                          <Check className="w-3.5 h-3.5" />
                          保存修改
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="text-slate-100 text-sm font-novel leading-relaxed bg-slate-950/40 p-3.5 rounded-xl border border-slate-800/40 select-text">
                      {para.translated ? (
                        para.translated
                      ) : (
                        <span className="text-slate-600 italic">（尚未翻译）</span>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};
