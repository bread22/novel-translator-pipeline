import React, { useState, useEffect } from 'react';
import {
  BookMarked,
  Users,
  Globe,
  Clock,
  Plus,
  Search,
  Tag,
} from 'lucide-react';
import { BookMemoryResponse, BookSummary, GlossaryItem } from '../types/api';
import { api } from '../lib/api';

interface KnowledgeViewProps {
  book: BookSummary | null;
}

export const KnowledgeView: React.FC<KnowledgeViewProps> = ({ book }) => {
  const [activeTab, setActiveTab] = useState<'glossary' | 'characters' | 'world' | 'timeline'>('glossary');
  const [glossaryTerms, setGlossaryTerms] = useState<GlossaryItem[]>([]);
  const [memory, setMemory] = useState<BookMemoryResponse | null>(null);
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
      const [glossaryRes, memoryRes] = await Promise.all([
        api.getGlossary(bookId).catch(() => ({ terms: [] })),
        api.getMemory(bookId).catch(() => null),
      ]);
      setGlossaryTerms(glossaryRes.terms || []);
      setMemory(memoryRes);
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
