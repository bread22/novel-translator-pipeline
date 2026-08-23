import React, { useState, useEffect } from 'react';
import {
  BookMarked,
  Users,
  Globe,
  Clock,
  Plus,
  Search,
  Tag,
  ShieldCheck,
  FileCheck2,
  AlertCircle,
  Sparkles,
  CheckCircle2,
} from 'lucide-react';
import { BookMemoryResponse, BookSummary, ChapterReviewReport, GlossaryItem } from '../types/api';
import { api } from '../lib/api';

interface KnowledgeViewProps {
  book: BookSummary | null;
}

export const KnowledgeView: React.FC<KnowledgeViewProps> = ({ book }) => {
  const [activeTab, setActiveTab] = useState<'glossary' | 'characters' | 'world' | 'timeline' | 'reports'>('reports');
  const [glossaryTerms, setGlossaryTerms] = useState<GlossaryItem[]>([]);
  const [memory, setMemory] = useState<BookMemoryResponse | null>(null);
  const [reports, setReports] = useState<ChapterReviewReport[]>([]);
  const [search, setSearch] = useState('');
  const [showAddModal, setShowAddModal] = useState(false);

  // New term form state
  const [newSource, setNewSource] = useState('');
  const [newTarget, setNewTarget] = useState('');
  const [newCategory, setNewCategory] = useState('character');
  const [newNotes, setNewNotes] = useState('');

  useEffect(() => {
    if (book) {
      loadData(book.id);
    }
  }, [book]);

  const loadData = async (bookId: string) => {
    try {
      const [glossaryRes, memoryRes, reportsRes] = await Promise.all([
        api.getGlossary(bookId).catch(() => ({ terms: [] })),
        api.getMemory(bookId).catch(() => null),
        api.getReports(bookId).catch(() => []),
      ]);
      setGlossaryTerms(glossaryRes.terms || []);
      setMemory(memoryRes);
      setReports(reportsRes || []);
    } catch (err: any) {
      console.error('Failed to load knowledge:', err);
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
      notes: newNotes.trim(),
    };

    try {
      const updated = await api.updateGlossary(book.id, [...glossaryTerms, newItem]);
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
      <div className="text-center py-24 border border-dashed border-slate-800 rounded-2xl p-12 max-w-xl mx-auto">
        <BookMarked className="w-12 h-12 text-slate-600 mx-auto mb-3" />
        <h3 className="text-slate-300 font-medium">未选择书籍</h3>
        <p className="text-slate-500 text-xs mt-1">请在顶部下拉列表选择一部小说查看其记忆与术语库</p>
      </div>
    );
  }

  const filteredTerms = glossaryTerms.filter(
    (t) =>
      t.source.toLowerCase().includes(search.toLowerCase()) ||
      t.target.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-16">
      {/* Top Banner */}
      <div className="bg-slate-900/90 border border-slate-800 p-6 rounded-2xl flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-purple-950/80 border border-purple-800/50 text-purple-300">
              Knowledge Hub
            </span>
            <h2 className="text-xl font-bold text-white tracking-tight">{book.name} · 知识与长程记忆透视台</h2>
          </div>
          <p className="text-xs text-slate-400 mt-1">
            由审阅模型（Consistency Reviewer）在章节翻译完成后自动提取并动态合并的术语、角色与世界观事实。
          </p>
        </div>

        {/* Sub-tabs */}
        <div className="flex items-center gap-1 bg-slate-950 p-1 rounded-xl border border-slate-800">
          <button
            onClick={() => setActiveTab('glossary')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
              activeTab === 'glossary' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Tag className="w-3.5 h-3.5" />
            术语表 ({glossaryTerms.length})
          </button>
          <button
            onClick={() => setActiveTab('characters')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
              activeTab === 'characters' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Users className="w-3.5 h-3.5" />
            角色档案 ({memory?.characters?.length || 0})
          </button>
          <button
            onClick={() => setActiveTab('world')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
              activeTab === 'world' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Globe className="w-3.5 h-3.5" />
            世界观设定 ({memory?.world_settings?.length || 0})
          </button>
          <button
            onClick={() => setActiveTab('reports')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
              activeTab === 'reports' ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
            章节审阅与质检报告 ({reports.length})
          </button>
          <button
            onClick={() => setActiveTab('timeline')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
              activeTab === 'timeline' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Clock className="w-3.5 h-3.5" />
            章节摘要 ({memory?.chapter_states?.length || 0})
          </button>
        </div>
      </div>

      {/* Tab 1: Glossary */}
      {activeTab === 'glossary' && (
        <div className="space-y-4">
          <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
            <div className="relative w-full sm:w-80">
              <Search className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                placeholder="搜索术语原文或译文..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full bg-slate-900/90 border border-slate-800 rounded-xl pl-9 pr-4 py-2 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>

            <button
              onClick={() => setShowAddModal(true)}
              className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium shadow-md shadow-indigo-600/30 transition-all cursor-pointer"
            >
              <Plus className="w-4 h-4" />
              添加自定义术语
            </button>
          </div>

          {/* Glossary Table */}
          <div className="bg-slate-900/80 border border-slate-800 rounded-2xl overflow-hidden shadow-xl">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-950/70 text-slate-400 border-b border-slate-800 text-[11px] font-mono">
                <tr>
                  <th className="py-3 px-4">日文原文 (Source)</th>
                  <th className="py-3 px-4">中文统一译名 (Target)</th>
                  <th className="py-3 px-4">分类 (Category)</th>
                  <th className="py-3 px-4">置信度</th>
                  <th className="py-3 px-4">备注 / 首次出现</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 font-medium">
                {filteredTerms.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="py-12 text-center text-slate-500">
                      暂无术语记录（流水线在审阅章节时会自动沉淀）
                    </td>
                  </tr>
                ) : (
                  filteredTerms.map((term, idx) => (
                    <tr key={idx} className="hover:bg-slate-800/40 transition-colors">
                      <td className="py-3 px-4 text-slate-200 font-bold font-novel">{term.source}</td>
                      <td className="py-3 px-4 text-indigo-300 font-novel">{term.target}</td>
                      <td className="py-3 px-4">
                        <span className="px-2 py-0.5 rounded-full text-[10px] font-mono bg-slate-800 text-slate-300">
                          {term.category}
                        </span>
                      </td>
                      <td className="py-3 px-4">
                        <span className="text-emerald-400 font-mono">
                          {Math.round(term.confidence * 100)}%
                        </span>
                      </td>
                      <td className="py-3 px-4 text-slate-400 text-[11px]">
                        {term.notes || (term.first_chapter ? `第 ${term.first_chapter} 章` : '-')}
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
            <div className="col-span-3 text-center py-16 text-slate-500 text-xs border border-dashed border-slate-800 rounded-2xl">
              暂未提取到角色档案
            </div>
          ) : (
            memory.characters.map((char, idx) => (
              <div key={idx} className="bg-slate-900/80 border border-slate-800 p-5 rounded-2xl space-y-3">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="font-bold text-slate-100 text-sm font-novel">{char.name}</h3>
                  {char.role && (
                    <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-indigo-950 text-indigo-300 border border-indigo-800/50">
                      {char.role}
                    </span>
                  )}
                </div>

                {char.alias && char.alias.length > 0 && (
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-[10px] text-slate-500">别名:</span>
                    {char.alias.map((a, aIdx) => (
                      <span key={aIdx} className="text-[10px] px-1.5 py-0.2 rounded bg-slate-800 text-slate-300 font-novel">
                        {a}
                      </span>
                    ))}
                  </div>
                )}

                <p className="text-xs text-slate-400 leading-relaxed font-novel">
                  {char.summary || '暂无角色简述'}
                </p>
              </div>
            ))
          )}
        </div>
      )}

      {/* Tab 3: World Settings */}
      {activeTab === 'world' && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {!memory?.world_settings || memory.world_settings.length === 0 ? (
            <div className="col-span-2 text-center py-16 text-slate-500 text-xs border border-dashed border-slate-800 rounded-2xl">
              暂未提取到世界观设定
            </div>
          ) : (
            memory.world_settings.map((ws, idx) => (
              <div key={idx} className="bg-slate-900/80 border border-slate-800 p-5 rounded-2xl space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="font-bold text-indigo-300 text-sm font-novel">{ws.term}</h3>
                  {ws.category && (
                    <span className="text-[10px] px-2 py-0.5 rounded bg-slate-800 text-slate-400 font-mono">
                      {ws.category}
                    </span>
                  )}
                </div>
                <p className="text-xs text-slate-300 leading-relaxed font-novel">{ws.explanation}</p>
              </div>
            ))
          )}
        </div>
      )}

      {/* Tab: Chapter Review Reports */}
      {activeTab === 'reports' && (
        <div className="space-y-6">
          {/* Summary metrics header */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <div className="bg-slate-900/80 border border-slate-800 p-4 rounded-2xl">
              <span className="text-slate-400 text-xs flex items-center gap-1.5">
                <FileCheck2 className="w-4 h-4 text-indigo-400" />
                已审阅章节
              </span>
              <p className="text-xl font-bold text-white font-mono mt-1">
                {reports.length} <span className="text-xs text-slate-500 font-normal">章</span>
              </p>
            </div>
            <div className="bg-slate-900/80 border border-slate-800 p-4 rounded-2xl">
              <span className="text-slate-400 text-xs flex items-center gap-1.5">
                <ShieldCheck className="w-4 h-4 text-emerald-400" />
                质检审查段落
              </span>
              <p className="text-xl font-bold text-emerald-400 font-mono mt-1">
                {reports.reduce((acc, r) => acc + (r.checked_paragraphs || 0), 0)}{' '}
                <span className="text-xs text-slate-500 font-normal">段 (100% 覆盖)</span>
              </p>
            </div>
            <div className="bg-slate-900/80 border border-slate-800 p-4 rounded-2xl">
              <span className="text-slate-400 text-xs flex items-center gap-1.5">
                <AlertCircle className="w-4 h-4 text-amber-400" />
                识别并修复缺陷
              </span>
              <p className="text-xl font-bold text-amber-400 font-mono mt-1">
                {reports.reduce((acc, r) => acc + (r.reported_issues || 0), 0)}{' '}
                <span className="text-xs text-slate-500 font-normal">处客观缺陷</span>
              </p>
            </div>
            <div className="bg-slate-900/80 border border-slate-800 p-4 rounded-2xl">
              <span className="text-slate-400 text-xs flex items-center gap-1.5">
                <Sparkles className="w-4 h-4 text-purple-400" />
                动态抽取术语
              </span>
              <p className="text-xl font-bold text-purple-400 font-mono mt-1">
                {reports.reduce((acc, r) => acc + (r.glossary_delta?.length || 0), 0)}{' '}
                <span className="text-xs text-slate-500 font-normal">条已沉淀</span>
              </p>
            </div>
          </div>

          {/* Per-Chapter Reports List */}
          {reports.length === 0 ? (
            <div className="text-center py-16 text-slate-500 text-xs border border-dashed border-slate-800 rounded-2xl">
              暂无已完成的章节审阅报告（流水线处理完各章节后将自动在此展示审阅质检详情）
            </div>
          ) : (
            <div className="space-y-4">
              {reports.map((report) => (
                <div
                  key={report.chapter_id}
                  className="bg-slate-900/90 border border-slate-800 rounded-2xl p-5 shadow-lg space-y-4"
                >
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-800/80 pb-3">
                    <div className="flex items-center gap-3">
                      <span className="font-mono text-sm font-bold text-indigo-400 bg-indigo-950/80 border border-indigo-800/50 px-2.5 py-1 rounded-lg">
                        {report.chapter_id}
                      </span>
                      <h4 className="text-sm font-bold text-white">
                        {report.chapter_state?.title || `章节 ${report.chapter_id}`}
                      </h4>
                      <span className="text-[11px] font-mono px-2 py-0.5 rounded-full bg-emerald-950/80 border border-emerald-800/50 text-emerald-400 flex items-center gap-1">
                        <CheckCircle2 className="w-3 h-3" />
                        已审查 {report.checked_paragraphs} 段落 (100% 覆盖)
                      </span>
                    </div>

                    <span className="text-[11px] text-slate-400 font-mono">
                      {report.reviewed_at ? new Date(report.reviewed_at).toLocaleString() : ''}
                    </span>
                  </div>

                  {/* Chapter Narrative Summary */}
                  {report.chapter_state?.summary && (
                    <div className="bg-slate-950/70 border border-slate-800/70 p-3.5 rounded-xl space-y-1">
                      <span className="text-[11px] font-semibold text-slate-400 flex items-center gap-1">
                        <Clock className="w-3 h-3 text-indigo-400" />
                        本章剧情与长程记忆摘要 (Narrative Summary):
                      </span>
                      <p className="text-xs text-slate-200 leading-relaxed font-novel">
                        {report.chapter_state.summary}
                      </p>
                      {report.chapter_state.active_entities && report.chapter_state.active_entities.length > 0 && (
                        <div className="flex items-center gap-1.5 flex-wrap pt-1.5 border-t border-slate-800/40 text-[11px]">
                          <span className="text-slate-500">活跃角色/实体:</span>
                          {report.chapter_state.active_entities.map((ent, eIdx) => (
                            <span
                              key={eIdx}
                              className="px-2 py-0.5 bg-slate-800/80 rounded text-slate-300 font-medium"
                            >
                              {ent}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  {/* Discovered & Applied Fixes */}
                  {report.fixes && report.fixes.length > 0 ? (
                    <div className="space-y-2">
                      <span className="text-xs font-semibold text-amber-400 flex items-center gap-1">
                        <AlertCircle className="w-3.5 h-3.5" />
                        客观缺陷与修正记录 ({report.fixes.length} 处):
                      </span>
                      <div className="space-y-2">
                        {report.fixes.map((fix, fIdx) => (
                          <div
                            key={fIdx}
                            className="bg-amber-950/20 border border-amber-800/40 rounded-xl p-3 text-xs space-y-1"
                          >
                            <div className="flex items-center justify-between text-[11px]">
                              <span className="font-mono font-bold text-amber-300">段落 ID: {fix.id}</span>
                              <span className="px-1.5 py-0.5 rounded bg-amber-900/50 text-amber-200 uppercase font-mono text-[10px]">
                                {fix.category || 'mistranslation'} · {fix.severity || 'major'}
                              </span>
                            </div>
                            {fix.reason && (
                              <p className="text-slate-300 text-[11px]">
                                <strong className="text-amber-400">问题原因:</strong> {fix.reason}
                              </p>
                            )}
                            {fix.replacement && (
                              <p className="text-emerald-300 text-[11px] font-novel bg-slate-950/60 p-2 rounded border border-emerald-900/30">
                                <strong className="text-emerald-400">写回修正译文:</strong> {fix.replacement}
                              </p>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div className="text-[11px] text-slate-400 flex items-center gap-1.5 py-1">
                      <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                      <span>未检测到错译、主客颠倒或漏译等客观事实缺陷，初译质量合格。</span>
                    </div>
                  )}

                  {/* Extracted Glossary Delta */}
                  {report.glossary_delta && report.glossary_delta.length > 0 && (
                    <div className="pt-2 border-t border-slate-800/80 space-y-1.5">
                      <span className="text-[11px] font-semibold text-purple-400 flex items-center gap-1">
                        <Sparkles className="w-3 h-3" />
                        本章自动抽取沉淀的专有名词与术语 ({report.glossary_delta.length} 个):
                      </span>
                      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2">
                        {report.glossary_delta.map((term, tIdx) => (
                          <div
                            key={tIdx}
                            className="bg-purple-950/20 border border-purple-800/40 px-2.5 py-1.5 rounded-lg text-xs flex items-center justify-between"
                          >
                            <span className="text-slate-300 font-medium">{term.source}</span>
                            <span className="text-purple-300 font-bold">→ {term.target}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Tab 4: Chapter States Timeline */}
      {activeTab === 'timeline' && (
        <div className="space-y-4">
          {!memory?.chapter_states || memory.chapter_states.length === 0 ? (
            <div className="text-center py-16 text-slate-500 text-xs border border-dashed border-slate-800 rounded-2xl">
              暂无章节状态记录
            </div>
          ) : (
            memory.chapter_states.map((state, idx) => (
              <div key={idx} className="bg-slate-900/80 border border-slate-800 p-5 rounded-2xl space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-xs font-bold text-indigo-400">
                    [{state.chapter_id}] {state.chapter_name || ''}
                  </span>
                </div>
                <p className="text-xs text-slate-300 font-novel leading-relaxed">{state.summary}</p>
              </div>
            ))
          )}
        </div>
      )}

      {/* Add Term Modal */}
      {showAddModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
          <div className="bg-slate-900 border border-slate-700 rounded-2xl p-6 max-w-md w-full shadow-2xl space-y-4">
            <h3 className="text-base font-bold text-white">添加自定义术语</h3>

            <form onSubmit={handleAddTerm} className="space-y-3">
              <div>
                <label className="text-xs text-slate-400 block mb-1">日文原文 (Source)</label>
                <input
                  type="text"
                  required
                  placeholder="例如: ナザリック"
                  value={newSource}
                  onChange={(e) => setNewSource(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl p-2.5 text-xs text-slate-100 focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="text-xs text-slate-400 block mb-1">中文统一译名 (Target)</label>
                <input
                  type="text"
                  required
                  placeholder="例如: 纳萨力克"
                  value={newTarget}
                  onChange={(e) => setNewTarget(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl p-2.5 text-xs text-slate-100 focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="text-xs text-slate-400 block mb-1">分类</label>
                <select
                  value={newCategory}
                  onChange={(e) => setNewCategory(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl p-2.5 text-xs text-slate-100 focus:outline-none focus:border-indigo-500"
                >
                  <option value="character">角色人名 (Character)</option>
                  <option value="location">地理地名 (Location)</option>
                  <option value="item">道具装备 (Item)</option>
                  <option value="skill">技能魔法 (Skill)</option>
                  <option value="organization">阵营组织 (Organization)</option>
                  <option value="general">通用词汇 (General)</option>
                </select>
              </div>

              <div>
                <label className="text-xs text-slate-400 block mb-1">备注说明 (可选)</label>
                <input
                  type="text"
                  placeholder="例如: 大坟墓据点"
                  value={newNotes}
                  onChange={(e) => setNewNotes(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl p-2.5 text-xs text-slate-100 focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div className="flex items-center justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowAddModal(false)}
                  className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-400 text-xs font-medium"
                >
                  取消
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium shadow-md shadow-indigo-600/30"
                >
                  保存术语
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
