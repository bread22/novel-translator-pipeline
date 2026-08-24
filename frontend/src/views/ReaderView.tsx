import React, { useState, useEffect } from 'react';
import {
  BookOpen,
  ChevronLeft,
  ChevronRight,
  Edit3,
  Check,
  RotateCcw,
  Search,
  ShieldCheck,
  AlertCircle,
  Sparkles,
  CheckCircle2,
  X,
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
  const [chapterReview, setChapterReview] = useState<any | null>(null);
  const [showReviewPanel, setShowReviewPanel] = useState(false);
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
      const [detail, reviewRes] = await Promise.all([
        api.getChapterDetail(bookId, chapterId),
        api.getChapterReview(bookId, chapterId).catch(() => null),
      ]);
      setChapterDetail(detail);
      setChapterReview(reviewRes);
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
      <div className="text-center py-24 bg-white border border-dashed border-[#E5E0D8] rounded-sm p-12 max-w-xl mx-auto shadow-sm">
        <BookOpen className="w-12 h-12 text-[#888888] mx-auto mb-3" />
        <h3 className="text-[#1A1A1A] font-serif font-bold text-base">未选择书籍</h3>
        <p className="text-[#666666] text-xs mt-1">请在顶部下拉列表选择一部小说进行双语阅读与人工校对</p>
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
      
      {/* Sidebar: Table of Contents (TOC) */}
      <div className="lg:col-span-1 bg-white border border-[#E5E0D8] rounded-sm p-4 flex flex-col h-[calc(100vh-140px)] sticky top-20 shadow-sm">
        <div className="flex items-center justify-between gap-2 pb-3 mb-3 border-b border-[#E5E0D8]">
          <div className="flex items-center gap-2">
            <BookOpen className="w-4 h-4 text-[#1D4ED8]" />
            <h3 className="font-serif font-bold text-[#1A1A1A] text-sm">目次 / 章节索引 ({chapters.length})</h3>
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
                className={`w-full text-left p-2.5 rounded-sm text-xs transition-all flex items-center justify-between gap-2 cursor-pointer ${
                  isSelected
                    ? 'bg-[#1D4ED8] text-white font-semibold shadow-sm'
                    : 'text-[#4A4A4A] hover:text-[#1A1A1A] hover:bg-[#FAF9F6]'
                }`}
              >
                <span className="truncate flex-1 font-serif">
                  {ch.index}. {ch.title}
                </span>
                {isFinished && (
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${
                      isSelected ? 'bg-white' : 'bg-emerald-600'
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
        <div className="bg-white border border-[#E5E0D8] p-6 rounded-sm flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 shadow-sm">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-mono px-2 py-0.5 bg-[#F2EFE9] border border-[#E5E0D8] text-[#1A1A1A] rounded-sm font-bold">
                CHAPTER {chapterDetail?.index || 1}
              </span>
              <h2 className="text-xl font-serif font-bold text-[#1A1A1A] tracking-tight">
                {chapterDetail?.title || '加载中...'}
              </h2>
            </div>
            <p className="text-xs text-[#666666] mt-1 font-mono">
              共 {chapterDetail?.total_paragraphs || 0} 个段落 · 已翻译 {chapterDetail?.translated_paragraphs || 0} 段
            </p>
          </div>

          {/* Action buttons & Prev / Next */}
          <div className="flex items-center gap-2 flex-wrap">
            {chapterReview?.status === 'ok' && (
              <button
                onClick={() => setShowReviewPanel(!showReviewPanel)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-sm text-xs font-medium transition-all cursor-pointer ${
                  showReviewPanel
                    ? 'bg-[#1A1A1A] text-white shadow-sm'
                    : 'bg-[#EFF6FF] border border-[#BFDBFE] text-[#1D4ED8] hover:bg-[#DBEAFE]'
                }`}
              >
                <ShieldCheck className="w-4 h-4 text-[#1D4ED8]" />
                本章质检报告
              </button>
            )}
            <button
              onClick={() => prevChapter && handleSelectChapter(prevChapter.id)}
              disabled={!prevChapter}
              className="flex items-center gap-1 px-3 py-1.5 rounded-sm bg-white hover:bg-[#FAF9F6] border border-[#E5E0D8] disabled:opacity-30 text-[#1A1A1A] text-xs font-medium transition-all shadow-sm cursor-pointer"
            >
              <ChevronLeft className="w-4 h-4" />
              上一章
            </button>
            <button
              onClick={() => nextChapter && handleSelectChapter(nextChapter.id)}
              disabled={!nextChapter}
              className="flex items-center gap-1 px-3 py-1.5 rounded-sm bg-white hover:bg-[#FAF9F6] border border-[#E5E0D8] disabled:opacity-30 text-[#1A1A1A] text-xs font-medium transition-all shadow-sm cursor-pointer"
            >
              下一章
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Chapter Review Diagnostics Dropdown Card */}
        {showReviewPanel && chapterReview && (
          <div className="bg-white border border-[#E5E0D8] rounded-sm p-5 shadow-md space-y-4">
            <div className="flex items-center justify-between border-b border-[#E5E0D8] pb-3">
              <div className="flex items-center gap-2">
                <ShieldCheck className="w-4 h-4 text-[#1D4ED8]" />
                <h4 className="text-xs font-serif font-bold text-[#1A1A1A]">本章一致性审阅与质检报告</h4>
                <span className="text-[10px] font-mono px-2 py-0.5 rounded-sm bg-emerald-50 text-emerald-800 border border-emerald-300">
                  100% ID 覆盖
                </span>
              </div>
              <button
                onClick={() => setShowReviewPanel(false)}
                className="text-[#888888] hover:text-[#1A1A1A] p-1 cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Narrative summary */}
            {chapterReview.chapter_state?.summary && (
              <div className="bg-[#FAF9F6] p-3 rounded-sm border border-[#E5E0D8] text-xs space-y-1">
                <span className="text-[11px] font-serif font-bold text-[#4A4A4A] flex items-center gap-1">
                  <Sparkles className="w-3 h-3 text-[#1D4ED8]" />
                  本章摘要与长程记忆 (Narrative Summary):
                </span>
                <p className="text-[#1A1A1A] leading-relaxed font-serif text-xs">
                  {chapterReview.chapter_state.summary}
                </p>
              </div>
            )}

            {/* Fixes Breakdown */}
            {chapterReview.fixes && chapterReview.fixes.length > 0 ? (
              <div className="space-y-2">
                <span className="text-xs font-serif font-bold text-amber-800 flex items-center gap-1">
                  <AlertCircle className="w-3.5 h-3.5 text-amber-600" />
                  已修正客观缺陷 ({chapterReview.fixes.length} 处):
                </span>
                <div className="space-y-2">
                  {chapterReview.fixes.map((fix: any, fIdx: number) => (
                    <div
                      key={fIdx}
                      className="bg-amber-50/50 border border-amber-200 rounded-sm p-3 text-xs space-y-1"
                    >
                      <div className="flex items-center justify-between text-[11px]">
                        <span className="font-mono font-bold text-amber-900">段落 ID: {fix.id}</span>
                        <span className="px-1.5 py-0.5 rounded-sm bg-amber-100 text-amber-900 uppercase font-mono text-[10px]">
                          {fix.category || 'mistranslation'}
                        </span>
                      </div>
                      {fix.reason && (
                        <p className="text-[#4A4A4A] text-[11px]">
                          <strong className="text-amber-900 font-sans">问题:</strong> {fix.reason}
                        </p>
                      )}
                      {fix.invalid_reason && (
                        <div className="text-[10px] text-rose-800 bg-rose-50 p-1.5 rounded-sm border border-rose-200">
                          ⚠️ {fix.invalid_reason}
                        </div>
                      )}
                      {fix.replacement && (
                        <p className="text-emerald-900 text-[11px] font-serif bg-white p-2 rounded-sm border border-emerald-200">
                          <strong className="text-emerald-800 font-sans">修正译文:</strong> {fix.replacement}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div className="text-xs text-[#4A4A4A] flex items-center gap-1.5 bg-[#FAF9F6] p-3 rounded-sm border border-[#E5E0D8]">
                <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
                <span>全章经模型审阅校验，未发现错译、漏译或事实冲突，初译文质量合格。</span>
              </div>
            )}
          </div>
        )}

        {/* Search inside chapter */}
        <div className="relative">
          <Search className="w-3.5 h-3.5 text-[#888888] absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            placeholder="在当前章节中检索双语关键词..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full bg-white border border-[#E5E0D8] rounded-sm pl-9 pr-4 py-2.5 text-xs text-[#1A1A1A] placeholder-[#888888] focus:outline-none focus:border-[#1D4ED8] shadow-sm font-sans"
          />
        </div>

        {/* Paragraphs List */}
        {isLoading ? (
          <div className="text-center py-20 text-[#888888] text-xs font-serif">正在加载章节双语段落...</div>
        ) : !filteredParagraphs || filteredParagraphs.length === 0 ? (
          <div className="text-center py-20 text-[#888888] text-xs font-serif">未找到段落数据</div>
        ) : (
          <div className="space-y-4">
            {filteredParagraphs.map((para) => {
              const isEditing = editingParaId === para.id;
              const isRetranslating = retranslatingParaId === para.id;
              const isFallback = para.status === 'fallback_recovered';

              return (
                <div
                  key={para.id}
                  className="bg-white border border-[#E5E0D8] hover:border-[#D4CEBF] p-5 rounded-sm shadow-sm transition-all space-y-3"
                >
                  {/* Top Bar for Paragraph */}
                  <div className="flex items-center justify-between gap-2 pb-2 border-b border-[#E5E0D8] text-[11px]">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[#888888]">#{para.index + 1}</span>
                      {para.provider && (
                        <span className="px-1.5 py-0.2 rounded-sm bg-[#F2EFE9] border border-[#E5E0D8] text-[#4A4A4A] font-mono text-[10px]">
                          {para.provider}
                        </span>
                      )}
                      {isFallback && (
                        <span className="px-1.5 py-0.2 rounded-sm bg-amber-50 border border-amber-300 text-amber-800 text-[10px]">
                          容灾救回 ({para.fallback_from || '主译'} 拦截)
                        </span>
                      )}
                    </div>

                    {/* Quick Action buttons */}
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => handleRetranslate(para.id)}
                        disabled={isRetranslating}
                        className="flex items-center gap-1 px-2 py-0.5 bg-white hover:bg-[#FAF9F6] border border-[#E5E0D8] text-[#4A4A4A] hover:text-[#1A1A1A] text-[11px] rounded-sm transition-all disabled:opacity-50 cursor-pointer"
                        title="使用当前主译重新翻译这一段"
                      >
                        <RotateCcw className={`w-3 h-3 ${isRetranslating ? 'animate-spin' : ''}`} />
                        重译
                      </button>
                      {!isEditing && (
                        <button
                          onClick={() => handleStartEdit(para.id, para.translated)}
                          className="flex items-center gap-1 px-2 py-0.5 bg-white hover:bg-[#FAF9F6] border border-[#E5E0D8] text-[#4A4A4A] hover:text-[#1A1A1A] text-[11px] rounded-sm transition-all cursor-pointer"
                        >
                          <Edit3 className="w-3 h-3" />
                          编辑
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Japanese Source */}
                  <div className="text-[#666666] text-xs font-serif leading-relaxed select-text">
                    {para.source}
                  </div>

                  {/* Chinese Translated */}
                  {isEditing ? (
                    <div className="space-y-2 pt-1">
                      <textarea
                        value={editContent}
                        onChange={(e) => setEditContent(e.target.value)}
                        className="w-full bg-[#FAF9F6] border border-[#1D4ED8] rounded-sm p-3 text-sm text-[#1A1A1A] font-serif leading-relaxed focus:outline-none"
                        rows={3}
                        autoFocus
                      />
                      <div className="flex justify-end gap-2">
                        <button
                          onClick={() => setEditingParaId(null)}
                          className="px-3 py-1 rounded-sm bg-white border border-[#E5E0D8] text-[#4A4A4A] text-xs hover:text-[#1A1A1A] cursor-pointer"
                        >
                          取消
                        </button>
                        <button
                          onClick={() => handleSaveEdit(para.id)}
                          className="flex items-center gap-1 px-3 py-1 rounded-sm bg-[#1D4ED8] hover:bg-[#1E40AF] text-white text-xs font-medium shadow-sm cursor-pointer"
                        >
                          <Check className="w-3.5 h-3.5" />
                          保存修改
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="text-[#1A1A1A] text-sm font-serif leading-relaxed bg-[#FAF9F6] p-3.5 rounded-sm border border-[#E5E0D8] select-text">
                      {para.translated ? (
                        para.translated
                      ) : (
                        <span className="text-[#888888] italic font-sans">（尚未翻译）</span>
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
