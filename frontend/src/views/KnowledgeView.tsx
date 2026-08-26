import React, { useState, useEffect, useRef } from 'react';
import {
  BookMarked,
  Users,
  Globe,
  Clock,
  Plus,
  Search,
  Tag,
  ShieldCheck,
  X,
} from 'lucide-react';
import { BookMemoryResponse, BookSummary, ChapterReviewReport, GlossaryItem } from '../types/api';
import { api } from '../lib/api';
import { Modal } from '../components/Modal';

interface KnowledgeViewProps {
  book: BookSummary | null;
}

export const KnowledgeView: React.FC<KnowledgeViewProps> = ({ book }) => {
  const [activeTab, setActiveTab] = useState<'glossary' | 'characters' | 'world' | 'timeline' | 'reports'>('reports');
  const [glossaryTerms, setGlossaryTerms] = useState<GlossaryItem[]>([]);
  const [memory, setMemory] = useState<BookMemoryResponse | null>(null);
  const [reports, setReports] = useState<ChapterReviewReport[]>([]);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('active');
  const [showAddModal, setShowAddModal] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const requestSequence = useRef(0);

  // New term form state
  const [newSource, setNewSource] = useState('');
  const [newTarget, setNewTarget] = useState('');
  const [newCategory, setNewCategory] = useState('character');
  const [newNotes, setNewNotes] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    const sequence = ++requestSequence.current;
    setGlossaryTerms([]);
    setMemory(null);
    setReports([]);
    setLoadError(null);
    if (!book) return () => controller.abort();
    setIsLoading(true);
    void loadData(book.id, controller.signal, sequence);
    return () => controller.abort();
  }, [book]);

  const loadData = async (bookId: string, signal: AbortSignal, sequence: number) => {
    try {
      const [glossaryRes, memoryRes, reportsRes] = await Promise.allSettled([
        api.getGlossary(bookId, { signal }),
        api.getMemory(bookId, { signal }),
        api.getReports(bookId, { signal }),
      ]);
      if (signal.aborted || sequence !== requestSequence.current) return;

      const errors: string[] = [];
      if (glossaryRes.status === 'fulfilled') {
        setGlossaryTerms(glossaryRes.value.terms || []);
      } else {
        errors.push(`术语表：${glossaryRes.reason instanceof Error ? glossaryRes.reason.message : '加载失败'}`);
      }
      if (memoryRes.status === 'fulfilled') {
        setMemory(memoryRes.value);
      } else {
        errors.push(`记忆：${memoryRes.reason instanceof Error ? memoryRes.reason.message : '加载失败'}`);
      }
      if (reportsRes.status === 'fulfilled') {
        setReports(reportsRes.value || []);
      } else {
        errors.push(`质检报告：${reportsRes.reason instanceof Error ? reportsRes.reason.message : '加载失败'}`);
      }
      setLoadError(errors.length > 0 ? errors.join('；') : null);
    } catch (err) {
      if (signal.aborted || sequence !== requestSequence.current) return;
      setLoadError(err instanceof Error ? err.message : '知识库加载失败');
    } finally {
      if (sequence === requestSequence.current) setIsLoading(false);
    }
  };

  const handleAddTerm = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!book || !newSource.trim() || !newTarget.trim()) return;

    const newItem: GlossaryItem = {
      source: newSource.trim(),
      target: newTarget.trim(),
      category: newCategory,
      confidence: 1.0,
      note: newNotes.trim(),
    };

    try {
      const updated = await api.updateGlossary(book.id, [newItem]);
      setGlossaryTerms(updated.terms);
      setShowAddModal(false);
      setNewSource('');
      setNewTarget('');
      setNewNotes('');
    } catch (err: any) {
      alert(`添加术语失败: ${err.message}`);
    }
  };

  if (!book) {
    return (
      <div className="text-center py-24 bg-white border border-dashed border-[#E5E0D8] rounded-sm p-12 max-w-xl mx-auto shadow-sm">
        <BookMarked className="w-12 h-12 text-[#888888] mx-auto mb-3" />
        <h3 className="text-[#1A1A1A] font-serif font-bold text-base">未选择书籍</h3>
        <p className="text-[#666666] text-xs mt-1">请在顶部下拉列表选择一部小说查看其长程记忆与术语库</p>
      </div>
    );
  }

  const filteredTerms = glossaryTerms.filter(
    (t) =>
      (statusFilter === 'all' || (t.status || 'active') === statusFilter) &&
      t.source.toLowerCase().includes(search.toLowerCase()) ||
      (statusFilter === 'all' || (t.status || 'active') === statusFilter) &&
      t.target.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-16">
      {isLoading && <div role="status" className="border border-[#E5E0D8] bg-white p-4 text-xs text-[#666666]">正在加载知识库…</div>}
      {loadError && <div role="alert" className="border border-red-300 bg-red-50 p-4 text-xs text-red-800">知识库加载失败：{loadError}</div>}
      
      {/* Top Banner */}
      <div className="bg-white border border-[#E5E0D8] p-6 rounded-sm flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 shadow-sm">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-mono px-2 py-0.5 bg-[#FAF9F6] border border-[#1A1A1A] text-[#1A1A1A] font-bold rounded-sm">
              EDITORIAL KNOWLEDGE HUB
            </span>
            <h2 className="text-xl font-serif font-bold text-[#1A1A1A] tracking-tight">{book.name} · 记忆与术语库</h2>
          </div>
          <p className="text-xs text-[#666666] mt-1 font-sans">
            由审阅模型在章节完成后自动提取并动态合并的专有名词、人物关系、世界观与全书质检报告。
          </p>
        </div>

        {/* Sub-tabs */}
        <div className="flex items-center gap-1 bg-[#F2EFE9] p-1 rounded-sm border border-[#E5E0D8]">
          <button
            onClick={() => setActiveTab('glossary')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-sm text-xs font-medium transition-all cursor-pointer ${
              activeTab === 'glossary' ? 'bg-[#1D4ED8] text-white font-semibold shadow-sm' : 'text-[#4A4A4A] hover:text-[#1A1A1A]'
            }`}
          >
            <Tag className="w-3.5 h-3.5" />
            术语表 ({glossaryTerms.length})
          </button>
          <button
            onClick={() => setActiveTab('characters')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-sm text-xs font-medium transition-all cursor-pointer ${
              activeTab === 'characters' ? 'bg-[#1D4ED8] text-white font-semibold shadow-sm' : 'text-[#4A4A4A] hover:text-[#1A1A1A]'
            }`}
          >
            <Users className="w-3.5 h-3.5" />
            角色档案 ({memory?.characters?.length || 0})
          </button>
          <button
            onClick={() => setActiveTab('world')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-sm text-xs font-medium transition-all cursor-pointer ${
              activeTab === 'world' ? 'bg-[#1D4ED8] text-white font-semibold shadow-sm' : 'text-[#4A4A4A] hover:text-[#1A1A1A]'
            }`}
          >
            <Globe className="w-3.5 h-3.5" />
            设定 ({memory?.world_settings?.length || 0})
          </button>
          <button
            onClick={() => setActiveTab('reports')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-sm text-xs font-medium transition-all cursor-pointer ${
              activeTab === 'reports' ? 'bg-[#1D4ED8] text-white font-semibold shadow-sm' : 'text-[#4A4A4A] hover:text-[#1A1A1A]'
            }`}
          >
            <ShieldCheck className="w-3.5 h-3.5" />
            质检报告 ({reports.length})
          </button>
          <button
            onClick={() => setActiveTab('timeline')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-sm text-xs font-medium transition-all cursor-pointer ${
              activeTab === 'timeline' ? 'bg-[#1D4ED8] text-white font-semibold shadow-sm' : 'text-[#4A4A4A] hover:text-[#1A1A1A]'
            }`}
          >
            <Clock className="w-3.5 h-3.5" />
            摘要 ({memory?.chapter_states?.length || 0})
          </button>
        </div>
      </div>

      {/* Tab 1: Glossary */}
      {activeTab === 'glossary' && (
        <div className="space-y-4">
          <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
            <div className="relative w-full sm:w-80">
              <Search className="w-3.5 h-3.5 text-[#888888] absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                placeholder="搜索术语原文或译名..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full bg-white border border-[#E5E0D8] rounded-sm pl-9 pr-4 py-2 text-xs text-[#1A1A1A] placeholder-[#888888] focus:outline-none focus:border-[#1D4ED8] shadow-sm font-sans"
              />
            </div>

            <div className="flex flex-wrap gap-1" aria-label="术语状态筛选">
              {(['active', 'candidate', 'disputed', 'retired', 'all'] as const).map((status) => (
                <button
                  key={status}
                  type="button"
                  onClick={() => setStatusFilter(status)}
                  className={`px-2 py-1 text-[10px] font-mono border rounded-sm ${statusFilter === status ? 'bg-[#1D4ED8] text-white border-[#1D4ED8]' : 'bg-white text-[#666666] border-[#E5E0D8]'}`}
                >
                  {status === 'all' ? '全部' : status}
                </button>
              ))}
            </div>

            <button
              onClick={() => setShowAddModal(true)}
              className="flex items-center gap-1.5 px-4 py-2 rounded-sm bg-[#1D4ED8] hover:bg-[#1E40AF] text-white text-xs font-semibold shadow-sm transition-all cursor-pointer"
            >
              <Plus className="w-4 h-4" />
              添加自定义术语
            </button>
          </div>

          {/* Glossary Table */}
          <div className="bg-white border border-[#E5E0D8] rounded-sm overflow-hidden shadow-sm">
            <table className="w-full text-left text-xs">
              <thead className="bg-[#FAF9F6] text-[#4A4A4A] border-b border-[#E5E0D8] text-[11px] font-mono">
                <tr>
                  <th className="py-3 px-4 font-bold">日文原文 (Source)</th>
                  <th className="py-3 px-4 font-bold">中文统一译名 (Target)</th>
                  <th className="py-3 px-4 font-bold">分类 (Category)</th>
                  <th className="py-3 px-4 font-bold">状态</th>
                  <th className="py-3 px-4 font-bold">置信度</th>
                  <th className="py-3 px-4 font-bold">备注 / 首次出现</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#E5E0D8] font-medium font-sans">
                {filteredTerms.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="py-12 text-center text-[#888888] font-serif">
                      暂无术语记录（流水线在审阅章节时会自动沉淀）
                    </td>
                  </tr>
                ) : (
                  filteredTerms.map((term, idx) => (
                    <tr key={idx} className="hover:bg-[#FAF9F6] transition-colors">
                      <td className="py-3 px-4 text-[#1A1A1A] font-bold font-serif">{term.source}</td>
                      <td className="py-3 px-4 text-[#1D4ED8] font-serif font-bold">{term.target}</td>
                      <td className="py-3 px-4">
                        <span className="px-2 py-0.5 rounded-sm text-[10px] font-mono bg-[#FAF9F6] border border-[#E5E0D8] text-[#4A4A4A]">
                          {term.category}
                        </span>
                      </td>
                      <td className="py-3 px-4">
                        <span className={`px-2 py-0.5 rounded-sm text-[10px] font-mono border ${
                          term.status === 'active' || !term.status ? 'bg-emerald-50 border-emerald-200 text-emerald-700' :
                          term.status === 'disputed' ? 'bg-amber-50 border-amber-200 text-amber-800' :
                          term.status === 'retired' ? 'bg-slate-50 border-slate-200 text-slate-600' :
                          'bg-blue-50 border-blue-200 text-blue-700'
                        }`}>
                          {term.status || 'active'}
                        </span>
                      </td>
                      <td className="py-3 px-4">
                        <span className="text-emerald-700 font-mono font-bold">
                          {Math.round(term.confidence * 100)}%
                        </span>
                      </td>
                      <td className="py-3 px-4 text-[#666666] text-[11px]">
                        {term.note || term.retired_reason || (term.first_seen_chunk ? `首次出现：${term.first_seen_chunk}` : '-')}
                        {typeof term.occurrences === 'number' && <span className="block text-[10px] text-[#888888]">证据 {term.occurrences} · 章节 {term.chapter_count || 0}</span>}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Tab 2: Characters */}
      {activeTab === 'characters' && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {!memory?.characters || memory.characters.length === 0 ? (
            <div className="col-span-3 text-center py-16 text-[#888888] text-xs border border-dashed border-[#E5E0D8] rounded-sm bg-white font-serif">
              暂未提取到角色档案
            </div>
          ) : (
            memory.characters.map((char, idx) => (
              <div key={idx} className="bg-white border border-[#E5E0D8] p-5 rounded-sm space-y-3 shadow-sm">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="font-bold text-[#1A1A1A] text-sm font-serif">{char.name}</h3>
                  {char.role && (
                    <span className="px-2 py-0.5 rounded-sm text-[10px] font-mono bg-[#EFF6FF] text-[#1D4ED8] border border-[#BFDBFE]">
                      {char.role}
                    </span>
                  )}
                </div>

                {char.alias && char.alias.length > 0 && (
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-[10px] text-[#888888]">别名:</span>
                    {char.alias.map((a, aIdx) => (
                      <span key={aIdx} className="text-[10px] px-1.5 py-0.2 rounded-sm bg-[#FAF9F6] border border-[#E5E0D8] text-[#4A4A4A] font-serif">
                        {a}
                      </span>
                    ))}
                  </div>
                )}

                {(char.summary || char.traits?.length) && (
                  <p className="text-xs text-[#4A4A4A] leading-relaxed font-serif">
                    {char.summary || char.traits?.join('、')}
                  </p>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {/* Tab 3: World Settings */}
      {activeTab === 'world' && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {!memory?.world_settings || memory.world_settings.length === 0 ? (
            <div className="col-span-2 text-center py-16 text-[#888888] text-xs border border-dashed border-[#E5E0D8] rounded-sm bg-white font-serif">
              暂未提取到世界观设定
            </div>
          ) : (
            memory.world_settings.map((setting, idx) => (
              <div key={idx} className="bg-white border border-[#E5E0D8] p-5 rounded-sm space-y-2 shadow-sm">
                <div className="flex items-center justify-between">
                  <h3 className="font-serif font-bold text-sm text-[#1A1A1A]">{setting.term}</h3>
                  {setting.category && (
                    <span className="text-[10px] font-mono px-2 py-0.5 bg-[#FAF9F6] border border-[#E5E0D8] text-[#4A4A4A] rounded-sm">
                      {setting.category}
                    </span>
                  )}
                </div>
                {setting.explanation && (
                  <p className="text-xs text-[#4A4A4A] font-serif leading-relaxed">
                    {setting.explanation}
                  </p>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {/* Tab 4: Review Reports */}
      {activeTab === 'reports' && (
        <div className="space-y-4">
          {reports.length === 0 ? (
            <div className="text-center py-16 text-[#888888] text-xs border border-dashed border-[#E5E0D8] rounded-sm bg-white font-serif">
              暂无章节审阅报告。启动全自动流水线后，每完成一章将自动生成质检报告。
            </div>
          ) : (
            reports.map((rep, idx) => (
              <div key={rep.chapter_id} className="bg-white border border-[#E5E0D8] p-5 rounded-sm space-y-4 shadow-sm">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-[#E5E0D8] pb-3">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs font-bold px-2 py-0.5 bg-[#F2EFE9] border border-[#E5E0D8] text-[#1A1A1A] rounded-sm">
                      #{idx + 1}
                    </span>
                    <h3 className="font-serif font-bold text-sm text-[#1A1A1A]">
                      {rep.chapter_id}
                    </h3>
                  </div>
                  <div className="flex items-center gap-2 text-xs font-mono">
                    <span className="text-emerald-700 font-bold">
                      修复 {rep.applied_fixes || 0} 处
                    </span>
                    <span>·</span>
                    <span className="text-amber-800">
                      发现 {rep.reported_issues || 0} 处缺陷
                    </span>
                    {rep.reviewed_at && (
                      <span className="hidden md:inline text-[#888888] ml-2">
                        {new Date(rep.reviewed_at).toLocaleString()}
                      </span>
                    )}
                  </div>
                </div>

                <p className="text-xs text-[#4A4A4A] font-serif leading-relaxed bg-[#FAF9F6] p-3 rounded-sm border border-[#E5E0D8]">
                  已核查 {rep.checked_paragraphs} 段落，发现 {rep.reported_issues} 处客观问题，已自动修正 {rep.applied_fixes} 处。
                </p>

                {rep.chapter_state?.summary && (
                  <div className="space-y-1.5">
                    <h4 className="text-[11px] font-mono font-bold text-[#666666]">章节与长程记忆摘要</h4>
                    <p className="text-xs text-[#4A4A4A] font-serif leading-relaxed border-l-2 border-[#BFDBFE] pl-3">
                      {rep.chapter_state.summary}
                    </p>
                  </div>
                )}

                {rep.fixes?.length > 0 && (
                  <div className="space-y-2.5" aria-label={`${rep.chapter_id} 缺陷详情`}>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <h4 className="text-xs font-serif font-bold text-amber-900">
                        客观缺陷与修正记录 ({rep.fixes.length} 处)
                      </h4>
                      {rep.dual_review?.enabled && (
                        <span className="text-[10px] font-mono text-violet-700 bg-violet-50 border border-violet-200 px-2 py-0.5 rounded-sm">
                          双审：主审 {rep.dual_review.primary_fixes_count || 0} · 副审 {rep.dual_review.secondary_fixes_count || 0} · 共识 {rep.dual_review.consensus_fixes_count || 0}
                        </span>
                      )}
                    </div>
                    <div className="space-y-2">
                      {rep.fixes.map((fix, fixIndex) => (
                        <div
                          key={`${fix.id}-${fixIndex}`}
                          className="bg-amber-50/60 border border-amber-200 rounded-sm p-3.5 text-xs space-y-2"
                        >
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <span className="font-mono font-bold text-[#1A1A1A]">#{fix.id}</span>
                              <span className="px-1.5 py-0.5 bg-white border border-amber-200 text-amber-900 uppercase font-mono text-[10px] rounded-sm">
                                {fix.category || 'mistranslation'} · {fix.severity || 'major'}
                              </span>
                              {typeof fix.confidence === 'number' && (
                                <span className="text-[10px] font-mono text-[#666666]">
                                  置信度 {Math.round(fix.confidence * 100)}%
                                </span>
                              )}
                              {fix.consensus && (
                                <span className="text-[10px] font-mono text-violet-700 font-bold">双审共识</span>
                              )}
                            </div>
                            <span className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded-sm border ${
                              fix.applied === true
                                ? 'text-emerald-700 bg-emerald-50 border-emerald-200'
                                : fix.applied === false
                                ? 'text-rose-700 bg-rose-50 border-rose-200'
                                : 'text-amber-800 bg-white border-amber-200'
                            }`}>
                              {fix.applied === true ? '已自动修正' : fix.applied === false ? '未修正' : '写回状态未知'}
                            </span>
                          </div>
                          {fix.reason && (
                            <p className="text-[#4A4A4A] text-[11px] leading-relaxed break-words">
                              <strong className="text-amber-900">问题原因：</strong>{fix.reason}
                            </p>
                          )}
                          {fix.invalid_reason && (
                            <p className="text-[10px] text-rose-700 bg-rose-50 p-2 border border-rose-200 rounded-sm">
                              校验警告：{fix.invalid_reason}
                            </p>
                          )}
                          {fix.applied === false && fix.not_applied_reason && (
                            <p className="text-[11px] text-rose-800 bg-rose-50 p-2.5 border border-rose-200 rounded-sm leading-relaxed">
                              <strong>未修正原因：</strong>{fix.not_applied_reason}
                            </p>
                          )}
                          {(fix.replacement || fix.approved_translation) && (
                            <div className="text-emerald-900 text-[11px] leading-relaxed bg-white p-2.5 border border-emerald-200 rounded-sm break-words">
                              <strong className="text-emerald-700">{fix.applied === true ? '写回修正译文：' : '建议修正译文：'}</strong>{fix.replacement || fix.approved_translation}
                            </div>
                          )}
                          {fix.reporters && fix.reporters.length > 0 && (
                            <p className="text-[10px] font-mono text-[#888888]">
                              报告来源：{fix.reporters.join(' + ')}
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {rep.migration_warning && (
                  <p className="text-[10px] text-rose-700 bg-rose-50 border border-rose-200 p-2 rounded-sm">
                    旧版审阅记录兼容警告：{rep.migration_warning}
                  </p>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {/* Tab 5: Timeline / Chapter States */}
      {activeTab === 'timeline' && (
        <div className="space-y-3">
          {!memory?.chapter_states || memory.chapter_states.length === 0 ? (
            <div className="text-center py-16 text-[#888888] text-xs border border-dashed border-[#E5E0D8] rounded-sm bg-white font-serif">
              暂无章节摘要
            </div>
          ) : (
            memory.chapter_states.map((st, idx) => (
              <div key={idx} className="bg-white border border-[#E5E0D8] p-4 rounded-sm space-y-1.5 shadow-sm">
                <div className="flex items-center justify-between">
                  <h4 className="font-serif font-bold text-xs text-[#1A1A1A]">
                    {st.chapter_name || st.chapter_id}
                  </h4>
                </div>
                {st.summary && (
                  <p className="text-xs text-[#4A4A4A] font-serif leading-relaxed">
                    {st.summary}
                  </p>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {/* Add Term Modal */}
      {showAddModal && (
        <Modal title="添加统一术语 / 人名地名" onClose={() => setShowAddModal(false)} className="p-6 max-w-md w-full space-y-4">
            <div className="flex items-center justify-between border-b border-[#E5E0D8] pb-3">
              <h3 className="font-serif font-bold text-sm text-[#1A1A1A]">添加统一术语 / 人名地名</h3>
              <button
                onClick={() => setShowAddModal(false)}
                aria-label="关闭添加术语对话框"
                className="text-[#888888] hover:text-[#1A1A1A] cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleAddTerm} className="space-y-3 text-xs">
              <div>
                <label htmlFor="glossary-source" className="block text-[#4A4A4A] mb-1 font-serif">日文原文 (Source):</label>
                <input
                  id="glossary-source"
                  type="text"
                  required
                  placeholder="如: 由香利"
                  value={newSource}
                  onChange={(e) => setNewSource(e.target.value)}
                  className="w-full bg-[#FAF9F6] border border-[#E5E0D8] rounded-sm p-2 text-[#1A1A1A] font-serif focus:outline-none focus:border-[#1D4ED8]"
                />
              </div>

              <div>
                <label htmlFor="glossary-target" className="block text-[#4A4A4A] mb-1 font-serif">中文统一译名 (Target):</label>
                <input
                  id="glossary-target"
                  type="text"
                  required
                  placeholder="如: 由香里 / 由香莉"
                  value={newTarget}
                  onChange={(e) => setNewTarget(e.target.value)}
                  className="w-full bg-[#FAF9F6] border border-[#E5E0D8] rounded-sm p-2 text-[#1A1A1A] font-serif focus:outline-none focus:border-[#1D4ED8]"
                />
              </div>

              <div>
                <label htmlFor="glossary-category" className="block text-[#4A4A4A] mb-1">分类:</label>
                <select
                  id="glossary-category"
                  value={newCategory}
                  onChange={(e) => setNewCategory(e.target.value)}
                  className="w-full bg-[#FAF9F6] border border-[#E5E0D8] rounded-sm p-2 text-[#1A1A1A] focus:outline-none"
                >
                  <option value="character">人名 / 称呼</option>
                  <option value="location">地名 / 场景</option>
                  <option value="item">道具 / 专有名词</option>
                  <option value="organization">组织 / 门派</option>
                </select>
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowAddModal(false)}
                  className="px-3 py-1.5 rounded-sm bg-white border border-[#E5E0D8] text-[#4A4A4A] hover:text-[#1A1A1A] cursor-pointer"
                >
                  取消
                </button>
                <button
                  type="submit"
                  className="px-4 py-1.5 rounded-sm bg-[#1D4ED8] hover:bg-[#1E40AF] text-white font-semibold cursor-pointer shadow-sm"
                >
                  保存术语
                </button>
              </div>
            </form>
        </Modal>
      )}

    </div>
  );
};
