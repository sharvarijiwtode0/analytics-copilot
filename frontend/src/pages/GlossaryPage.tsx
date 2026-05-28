import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  BookOpen, Search, Sparkles, Mail, Plus, Trash2, CheckCircle2,
  XCircle, Clock, ChevronLeft, Tag, HelpCircle, ArrowRight, Check, AlertCircle, AlertTriangle
} from 'lucide-react'
import { useThemeStore } from '../store/theme'
import { useAuthStore } from '../store/auth'
import { api } from '../api/client'

interface GlossaryTerm {
  term: string
  definition: string
  variations: string[]
  category: string
}

interface ProposalItem {
  id: string
  term: string
  definition: string
  variations: string[]
  category: string
  status: 'PENDING' | 'APPROVED' | 'REJECTED'
  requested_by: string
  reviewed_by: string | null
  reviewed_at: string | null
  created_at: string
}

export default function GlossaryPage() {
  const navigate = useNavigate()
  const { theme } = useThemeStore()
  const { user } = useAuthStore()

  // Navigation tab
  const [activeTab, setActiveTab] = useState<'explore' | 'proposals'>('explore')

  // Glossary and proposal states
  const [terms, setTerms] = useState<GlossaryTerm[]>([])
  const [proposals, setProposals] = useState<ProposalItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // Search and Filter states
  const [searchQuery, setSearchQuery] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('All')

  // AI drafting state
  const [aiDraftTerm, setAiDraftTerm] = useState('')
  const [isAiLoading, setIsAiLoading] = useState(false)
  const [aiStep, setAiStep] = useState<'idle' | 'searching' | 'drafted'>('idle')
  const [searchSummary, setSearchSummary] = useState('')
  
  // Edited Draft fields
  const [draftTerm, setDraftTerm] = useState('')
  const [draftDefinition, setDraftDefinition] = useState('')
  const [draftCategory, setDraftCategory] = useState('General')
  const [draftVariations, setDraftVariations] = useState<string[]>([])
  const [newVariation, setNewVariation] = useState('')
  const [supervisorEmail, setSupervisorEmail] = useState(user?.email || 'supervisor@demo.com')
  const [proposalSuccess, setProposalSuccess] = useState(false)
  const [proposedId, setProposedId] = useState('')

  // Fetch glossary data
  const fetchData = async () => {
    setLoading(true)
    setError('')
    try {
      const termsRes = await api.get('/knowledge/glossary/terms')
      setTerms(termsRes.data)
      const proposalsRes = await api.get('/knowledge/glossary/proposals')
      setProposals(proposalsRes.data)
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Failed to fetch glossary data')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
  }, [])

  // Auto-search and AI drafting trigger
  const handleAiAutoDraft = async (termToDraft: string) => {
    if (!termToDraft.trim()) return
    setIsAiLoading(true)
    setAiStep('searching')
    setError('')
    try {
      const res = await api.post('/knowledge/glossary/detect', { term: termToDraft })
      const data = res.data
      setSearchSummary(data.search_summary || '')
      setDraftTerm(data.term || termToDraft)
      setDraftDefinition(data.definition || '')
      setDraftCategory(data.category || 'General')
      setDraftVariations(data.variations || [])
      setAiStep('drafted')
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Failed to auto-draft term using AI')
      setAiStep('idle')
    } finally {
      setIsAiLoading(false)
    }
  }

  // Handle submitting proposal to supervisor
  const handleSubmitProposal = async () => {
    if (!draftTerm.trim() || !draftDefinition.trim()) {
      setError('Term and Definition are required')
      return
    }
    if (!supervisorEmail.trim() || !supervisorEmail.includes('@')) {
      setError('A valid supervisor email address is required')
      return
    }

    setLoading(true)
    setError('')
    try {
      const res = await api.post('/knowledge/glossary/propose', {
        term: draftTerm,
        definition: draftDefinition,
        variations: draftVariations,
        category: draftCategory,
        supervisor_email: supervisorEmail
      })
      
      setProposedId(res.data.approval_id)
      setProposalSuccess(true)
      fetchData() // Refresh lists
      
      // Reset AI panel after short delay
      setTimeout(() => {
        setProposalSuccess(false)
        setAiStep('idle')
        setAiDraftTerm('')
        setDraftTerm('')
        setDraftDefinition('')
        setDraftVariations([])
        setSearchSummary('')
      }, 5000)
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Failed to submit proposal')
    } finally {
      setLoading(false)
    }
  }

  // Helper to add alias variation tags
  const handleAddVariation = () => {
    const v = newVariation.trim()
    if (v && !draftVariations.includes(v)) {
      setDraftVariations([...draftVariations, v])
      setNewVariation('')
    }
  }

  // Remove alias variation tag
  const handleRemoveVariation = (variation: string) => {
    setDraftVariations(draftVariations.filter(x => x !== variation))
  }

  // Direct manual approval from dashboard (optional administrative backup)
  const handleDirectApprove = async (proposalId: string) => {
    if (!confirm('Are you sure you want to approve this glossary term directly?')) return
    try {
      await api.get(`/knowledge/glossary/email-approve/${proposalId}`)
      fetchData()
    } catch (err: any) {
      alert(err?.response?.data?.detail || 'Direct approval failed')
    }
  }

  const handleDirectReject = async (proposalId: string) => {
    if (!confirm('Are you sure you want to reject this proposal directly?')) return
    try {
      await api.get(`/knowledge/glossary/email-reject/${proposalId}`)
      fetchData()
    } catch (err: any) {
      alert(err?.response?.data?.detail || 'Direct rejection failed')
    }
  }

  // Categories list
  const categories = ['All', 'Marketing', 'Sales', 'Finance', 'Product', 'Operations', 'General']

  // Filtered terms
  const filteredTerms = terms.filter(t => {
    const matchesSearch = t.term.toLowerCase().includes(searchQuery.toLowerCase()) || 
                          t.definition.toLowerCase().includes(searchQuery.toLowerCase()) ||
                          t.variations.some(v => v.toLowerCase().includes(searchQuery.toLowerCase()))
    const matchesCategory = categoryFilter === 'All' || t.category === categoryFilter
    return matchesSearch && matchesCategory
  })

  // Check if term already exists in active terms
  const checkTermExists = (name: string) => {
    return terms.some(t => t.term.toLowerCase() === name.toLowerCase())
  }

  return (
    <div className={`flex h-screen ${theme === 'dark' ? 'bg-zinc-950 text-zinc-100' : 'bg-slate-50 text-slate-800'}`}>
      <div className="flex flex-col flex-1 transition-all duration-300 overflow-hidden">
        {/* Header */}
        <header className={`flex items-center gap-4 px-6 py-4 border-b shadow-sm z-10 ${
          theme === 'dark' ? 'bg-zinc-900 border-zinc-800' : 'bg-white border-slate-200'
        }`}>
          <button
            onClick={() => navigate(-1)}
            className={`p-2 rounded-lg transition-colors ${
              theme === 'dark' ? 'hover:bg-zinc-800 text-zinc-300' : 'hover:bg-slate-100 text-slate-600'
            }`}
            title="Back"
          >
            <ChevronLeft size={18} />
          </button>
          
          <div className="flex items-center gap-2">
            <div className="p-1.5 rounded-lg bg-blue-600 text-white">
              <BookOpen size={16} />
            </div>
            <h1 className="font-bold text-sm tracking-tight">Business Glossary Manager</h1>
          </div>

          <div className="ml-auto flex items-center gap-2">
            <button
              onClick={() => setActiveTab('explore')}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                activeTab === 'explore'
                  ? 'bg-blue-600 text-white shadow-md'
                  : theme === 'dark' ? 'hover:bg-zinc-800 text-zinc-400' : 'hover:bg-slate-100 text-slate-500'
              }`}
            >
              Glossary Portal
            </button>
            <button
              onClick={() => setActiveTab('proposals')}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all relative ${
                activeTab === 'proposals'
                  ? 'bg-blue-600 text-white shadow-md'
                  : theme === 'dark' ? 'hover:bg-zinc-800 text-zinc-400' : 'hover:bg-slate-100 text-slate-500'
              }`}
            >
              Proposals Timeline
              {proposals.filter(p => p.status === 'PENDING').length > 0 && (
                <span className="absolute -top-1 -right-1 flex h-3 w-3">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-3 w-3 bg-amber-500"></span>
                </span>
              )}
            </button>
          </div>
        </header>

        {/* Content Container */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {error && (
            <div className={`p-4 rounded-xl border flex items-start gap-3 text-xs ${
              theme === 'dark' ? 'bg-red-500/10 border-red-500/20 text-red-400' : 'bg-red-50 border-red-200 text-red-700'
            }`}>
              <AlertCircle size={16} className="shrink-0 mt-0.5" />
              <div>
                <p className="font-semibold">Error Occurred</p>
                <p className="opacity-90">{error}</p>
              </div>
            </div>
          )}

          {activeTab === 'explore' && (
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
              {/* Left & Middle: Glossary Explorer */}
              <div className="lg:col-span-2 space-y-6">
                {/* Search and Filters Card */}
                <div className={`p-5 rounded-2xl border shadow-sm ${
                  theme === 'dark' ? 'bg-zinc-900 border-zinc-850' : 'bg-white border-slate-200'
                }`}>
                  <div className="flex flex-col sm:flex-row gap-4 items-center">
                    <div className="relative flex-1 w-full">
                      <Search size={16} className="absolute left-3.5 top-3 text-zinc-400" />
                      <input
                        type="text"
                        value={searchQuery}
                        onChange={e => setSearchQuery(e.target.value)}
                        placeholder="Search term name, definition, or variations..."
                        className={`w-full pl-10 pr-4 py-2 text-xs rounded-xl border outline-none transition-all ${
                          theme === 'dark'
                            ? 'bg-zinc-950 border-zinc-800 text-zinc-200 focus:border-blue-500'
                            : 'bg-slate-50 border-slate-200 text-slate-800 focus:border-blue-400'
                        }`}
                      />
                    </div>
                    {/* Category Filter Chips */}
                    <div className="flex flex-wrap gap-1.5 justify-start w-full sm:w-auto">
                      {categories.map(cat => (
                        <button
                          key={cat}
                          onClick={() => setCategoryFilter(cat)}
                          className={`px-2.5 py-1.5 rounded-lg text-[10px] font-semibold transition-all ${
                            categoryFilter === cat
                              ? 'bg-blue-600 text-white'
                              : theme === 'dark'
                                ? 'bg-zinc-800 border border-zinc-700 text-zinc-300 hover:bg-zinc-700'
                                : 'bg-slate-100 border border-slate-200 text-slate-600 hover:bg-slate-200'
                          }`}
                        >
                          {cat}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>

                {/* Term List */}
                <div className="space-y-4">
                  {loading ? (
                    <div className="space-y-3">
                      {[1, 2, 3].map(i => (
                        <div key={i} className={`h-24 rounded-2xl animate-pulse ${theme === 'dark' ? 'bg-zinc-900' : 'bg-white'}`}></div>
                      ))}
                    </div>
                  ) : filteredTerms.length > 0 ? (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      {filteredTerms.map(item => (
                        <div
                          key={item.term}
                          className={`p-5 rounded-2xl border shadow-sm hover:shadow-md transition-all group flex flex-col justify-between ${
                            theme === 'dark' ? 'bg-zinc-900 border-zinc-850 hover:border-zinc-700' : 'bg-white border-slate-200 hover:border-slate-300'
                          }`}
                        >
                          <div>
                            <div className="flex items-center justify-between mb-2">
                              <h3 className="font-extrabold text-base tracking-tight text-blue-500 group-hover:text-blue-400 transition-colors">
                                {item.term}
                              </h3>
                              <span className={`px-2 py-0.5 rounded-md text-[9px] font-bold uppercase tracking-wider ${
                                theme === 'dark' ? 'bg-zinc-850 text-zinc-300 border border-zinc-800' : 'bg-slate-100 text-slate-600 border border-slate-200'
                              }`}>
                                {item.category}
                              </span>
                            </div>
                            
                            <p className={`text-xs leading-relaxed mb-4 font-normal ${theme === 'dark' ? 'text-zinc-300' : 'text-slate-600'}`}>
                              {item.definition}
                            </p>
                          </div>

                          {item.variations && item.variations.length > 0 && (
                            <div className="flex flex-wrap gap-1 mt-auto pt-2 border-t border-dashed border-zinc-800 dark:border-zinc-850">
                              {item.variations.map(v => (
                                <span
                                  key={v}
                                  className={`inline-flex items-center gap-0.5 px-2 py-0.5 rounded text-[9px] font-medium ${
                                    theme === 'dark' ? 'bg-zinc-800 text-zinc-400' : 'bg-slate-100 text-slate-500'
                                  }`}
                                >
                                  <Tag size={8} /> {v}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className={`p-8 rounded-2xl border text-center ${
                      theme === 'dark' ? 'bg-zinc-900 border-zinc-850' : 'bg-white border-slate-200'
                    }`}>
                      <AlertCircle size={24} className="mx-auto text-zinc-500 mb-3" />
                      <h4 className="font-bold text-sm mb-1">No glossary terms found</h4>
                      <p className="text-xs text-zinc-500 mb-4 max-w-sm mx-auto">
                        We couldn't find any terms matching your filters. You can use the AI drafting panel on the right to search the web and add a new term!
                      </p>
                      {searchQuery && !checkTermExists(searchQuery) && (
                        <button
                          onClick={() => {
                            setAiDraftTerm(searchQuery)
                            handleAiAutoDraft(searchQuery)
                          }}
                          className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold rounded-xl transition"
                        >
                          <Sparkles size={14} /> Auto-Draft "{searchQuery}"
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </div>

              {/* Right: AI Drafting Workspace */}
              <div className={`p-6 rounded-2xl border shadow-sm relative ${
                theme === 'dark' ? 'bg-zinc-900 border-zinc-850' : 'bg-white border-slate-200'
              }`}>
                {/* Visual Glow */}
                <div className="absolute top-0 right-0 w-24 h-24 bg-gradient-to-bl from-blue-600/10 to-transparent blur-xl pointer-events-none"></div>

                <div className="flex items-center gap-2 mb-4">
                  <Sparkles size={16} className="text-blue-500" />
                  <h3 className="font-bold text-sm">AI Term Auto-Discovery</h3>
                </div>

                {aiStep === 'idle' && (
                  <div className="space-y-4">
                    <p className="text-xs text-zinc-500 leading-relaxed">
                      Type in any business acronym, retail metric, or ambiguous industry term (e.g. CAC, AOV, GMV, CTR).
                      The system will auto-search the web and draft a structured entry using AI.
                    </p>
                    <div>
                      <label className="block text-[10px] font-bold uppercase text-zinc-500 mb-1.5">Enter Term</label>
                      <div className="flex gap-2">
                        <input
                          type="text"
                          value={aiDraftTerm}
                          onChange={e => setAiDraftTerm(e.target.value)}
                          placeholder="e.g. CRO or ROAS"
                          className={`flex-1 px-3.5 py-2 text-xs rounded-xl border outline-none transition-all ${
                            theme === 'dark'
                              ? 'bg-zinc-950 border-zinc-800 text-zinc-200 focus:border-blue-500'
                              : 'bg-slate-50 border-slate-200 text-slate-800 focus:border-blue-400'
                          }`}
                          onKeyDown={e => e.key === 'Enter' && handleAiAutoDraft(aiDraftTerm)}
                        />
                        <button
                          onClick={() => handleAiAutoDraft(aiDraftTerm)}
                          disabled={!aiDraftTerm.trim() || isAiLoading}
                          className="px-3.5 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-xs font-semibold rounded-xl transition shrink-0"
                        >
                          Auto-Search
                        </button>
                      </div>
                    </div>
                  </div>
                )}

                {aiStep === 'searching' && (
                  <div className="text-center py-8 space-y-4">
                    <div className="flex justify-center">
                      <div className="relative flex items-center justify-center">
                        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500"></div>
                        <Sparkles size={16} className="absolute text-blue-400 animate-pulse" />
                      </div>
                    </div>
                    <div>
                      <h4 className="font-semibold text-xs text-blue-500">Auto-searching the Web</h4>
                      <p className="text-[10px] text-zinc-500 mt-1">Retrieving definitions and synonyms for "{aiDraftTerm}"...</p>
                    </div>
                  </div>
                )}

                {aiStep === 'drafted' && (
                  <div className="space-y-4">
                    {proposalSuccess ? (
                      <div className="text-center py-6 space-y-3">
                        <div className="inline-flex p-3 rounded-full bg-emerald-500/10 text-emerald-500 border border-emerald-500/20">
                          <CheckCircle2 size={32} />
                        </div>
                        <div>
                          <h4 className="font-extrabold text-sm text-emerald-500">Proposal Dispatched!</h4>
                          <p className="text-[10px] text-zinc-500 mt-1 leading-relaxed">
                            An approval request has been sent to the supervisor at <span className="font-bold text-zinc-400">{supervisorEmail}</span>. 
                            The term will be added once approved.
                          </p>
                        </div>
                        {proposedId && (
                          <div className={`mt-2 p-2 rounded-xl text-[10px] ${
                            theme === 'dark' ? 'bg-zinc-950 text-zinc-400' : 'bg-slate-50 text-slate-500'
                          }`}>
                            Proposal ID: <span className="font-mono">{proposedId}</span>
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="space-y-4">
                        <div className={`p-3 rounded-xl text-[10px] border leading-relaxed ${
                          theme === 'dark' ? 'bg-zinc-950 border-zinc-800 text-zinc-400' : 'bg-slate-50 border-slate-200 text-slate-600'
                        }`}>
                          <span className="font-bold text-blue-500">Web Search Results:</span> {searchSummary.slice(0, 160)}...
                        </div>

                        <div className="space-y-3">
                          <div>
                            <label className="block text-[9px] font-bold uppercase text-zinc-500 mb-1">Term Name</label>
                            <input
                              type="text"
                              value={draftTerm}
                              onChange={e => setDraftTerm(e.target.value)}
                              className={`w-full px-3.5 py-1.5 text-xs rounded-lg border outline-none ${
                                theme === 'dark' ? 'bg-zinc-950 border-zinc-800 text-zinc-200' : 'bg-slate-50 border-slate-200 text-slate-800'
                              }`}
                            />
                          </div>

                          <div className="grid grid-cols-2 gap-3">
                            <div>
                              <label className="block text-[9px] font-bold uppercase text-zinc-500 mb-1">Category</label>
                              <select
                                value={draftCategory}
                                onChange={e => setDraftCategory(e.target.value)}
                                className={`w-full px-3.5 py-1.5 text-xs rounded-lg border outline-none ${
                                  theme === 'dark' ? 'bg-zinc-950 border-zinc-800 text-zinc-200' : 'bg-slate-50 border-slate-200'
                                }`}
                              >
                                {categories.filter(c => c !== 'All').map(c => (
                                  <option key={c} value={c}>{c}</option>
                                ))}
                              </select>
                            </div>
                            
                            <div>
                              <label className="block text-[9px] font-bold uppercase text-zinc-500 mb-1">Status check</label>
                              <div className={`px-3.5 py-1.5 text-[10px] rounded-lg border flex items-center gap-1 font-semibold ${
                                checkTermExists(draftTerm)
                                  ? 'bg-red-500/10 text-red-400 border-red-500/20'
                                  : 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                              }`}>
                                {checkTermExists(draftTerm) ? (
                                  <><AlertTriangle size={10} /> Duplicate</>
                                ) : (
                                  <><Check size={10} /> Available</>
                                )}
                              </div>
                            </div>
                          </div>

                          <div>
                            <label className="block text-[9px] font-bold uppercase text-zinc-500 mb-1">Definition</label>
                            <textarea
                              value={draftDefinition}
                              onChange={e => setDraftDefinition(e.target.value)}
                              rows={3}
                              className={`w-full px-3.5 py-1.5 text-xs rounded-lg border outline-none resize-none ${
                                theme === 'dark' ? 'bg-zinc-950 border-zinc-800 text-zinc-200' : 'bg-slate-50 border-slate-200 text-slate-800'
                              }`}
                            />
                          </div>

                          <div>
                            <label className="block text-[9px] font-bold uppercase text-zinc-500 mb-1">Aliases / Variations</label>
                            <div className="flex gap-1.5 mb-1.5">
                              <input
                                type="text"
                                value={newVariation}
                                onChange={e => setNewVariation(e.target.value)}
                                placeholder="Add synonym tag..."
                                onKeyDown={e => e.key === 'Enter' && handleAddVariation()}
                                className={`flex-1 px-3 py-1 text-xs rounded border outline-none ${
                                  theme === 'dark' ? 'bg-zinc-950 border-zinc-800 text-zinc-200' : 'bg-slate-50 border-slate-200'
                                }`}
                              />
                              <button
                                onClick={handleAddVariation}
                                className="px-2 py-1 bg-blue-600 text-white rounded text-[10px] font-semibold"
                              >
                                Add
                              </button>
                            </div>
                            <div className="flex flex-wrap gap-1">
                              {draftVariations.map(tag => (
                                <span
                                  key={tag}
                                  className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[9px] font-semibold ${
                                    theme === 'dark' ? 'bg-zinc-800 text-zinc-300' : 'bg-slate-100 text-slate-655'
                                  }`}
                                >
                                  {tag}
                                  <button onClick={() => handleRemoveVariation(tag)} className="text-zinc-500 hover:text-red-400">×</button>
                                </span>
                              ))}
                            </div>
                          </div>

                          <div className="pt-2 border-t border-zinc-800 dark:border-zinc-850">
                            <label className="block text-[9px] font-bold uppercase text-zinc-500 mb-1">Supervisor's Email</label>
                            <div className="relative">
                              <Mail size={12} className="absolute left-3 top-2.5 text-zinc-400" />
                              <input
                                type="email"
                                value={supervisorEmail}
                                onChange={e => setSupervisorEmail(e.target.value)}
                                placeholder="supervisor@company.com"
                                className={`w-full pl-8 pr-4 py-1.5 text-xs rounded-lg border outline-none ${
                                  theme === 'dark' ? 'bg-zinc-950 border-zinc-800 text-zinc-200' : 'bg-slate-50 border-slate-200 text-slate-800'
                                }`}
                              />
                            </div>
                          </div>
                        </div>

                        <div className="flex gap-2 pt-2">
                          <button
                            onClick={() => setAiStep('idle')}
                            className={`flex-1 py-2 rounded-xl text-xs font-semibold border ${
                              theme === 'dark'
                                ? 'bg-zinc-800 border-zinc-700 text-zinc-300 hover:bg-zinc-700'
                                : 'bg-slate-100 border-slate-200 text-slate-600 hover:bg-slate-200'
                            }`}
                          >
                            Cancel
                          </button>
                          <button
                            onClick={handleSubmitProposal}
                            disabled={loading || checkTermExists(draftTerm)}
                            className="flex-1 py-2 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 disabled:opacity-50 text-white text-xs font-bold rounded-xl transition shadow-lg shadow-blue-500/20 flex items-center justify-center gap-1.5"
                          >
                            <Mail size={12} /> Send Proposal
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}

          {activeTab === 'proposals' && (
            <div className={`p-6 rounded-2xl border shadow-sm ${
              theme === 'dark' ? 'bg-zinc-900 border-zinc-850' : 'bg-white border-slate-200'
            }`}>
              <div className="flex items-center justify-between mb-6">
                <div>
                  <h3 className="font-extrabold text-base">Approval Requests History</h3>
                  <p className="text-xs text-zinc-500">Monitor and approve pending glossary term additions sent to supervisors.</p>
                </div>
                <button
                  onClick={fetchData}
                  className="px-3 py-1.5 bg-zinc-800 border border-zinc-700 rounded-lg text-[10px] font-semibold text-zinc-300 hover:bg-zinc-700"
                >
                  Refresh Queue
                </button>
              </div>

              {loading ? (
                <div className="space-y-3">
                  {[1, 2].map(i => <div key={i} className="h-16 rounded-xl animate-pulse bg-zinc-800"></div>)}
                </div>
              ) : proposals.length > 0 ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs border-collapse">
                    <thead>
                      <tr className="border-b border-zinc-800 dark:border-zinc-850 text-zinc-500">
                        <th className="py-3 px-4">Term</th>
                        <th className="py-3 px-4">Category</th>
                        <th className="py-3 px-4">Definition</th>
                        <th className="py-3 px-4">Created Date</th>
                        <th className="py-3 px-4">Status</th>
                        <th className="py-3 px-4 text-right">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-zinc-800 dark:divide-zinc-850">
                      {proposals.map(item => (
                        <tr key={item.id} className="hover:bg-zinc-950/20 transition-all">
                          <td className="py-3.5 px-4 font-bold text-blue-500">{item.term}</td>
                          <td className="py-3.5 px-4"><span className="px-2 py-0.5 rounded bg-zinc-800 text-[10px] font-medium text-zinc-400">{item.category}</span></td>
                          <td className="py-3.5 px-4 max-w-xs truncate text-zinc-400" title={item.definition}>{item.definition}</td>
                          <td className="py-3.5 px-4 text-zinc-500 font-mono text-[10px]">{new Date(item.created_at).toLocaleDateString()}</td>
                          <td className="py-3.5 px-4">
                            <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-bold ${
                              item.status === 'APPROVED'
                                ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                                : item.status === 'REJECTED'
                                  ? 'bg-red-500/10 text-red-400 border border-red-500/20'
                                  : 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
                            }`}>
                              {item.status === 'APPROVED' && <CheckCircle2 size={10} />}
                              {item.status === 'REJECTED' && <XCircle size={10} />}
                              {item.status === 'PENDING' && <Clock size={10} className="animate-spin" />}
                              {item.status}
                            </span>
                          </td>
                          <td className="py-3.5 px-4 text-right">
                            {item.status === 'PENDING' ? (
                              <div className="flex gap-2 justify-end">
                                <button
                                  onClick={() => handleDirectApprove(item.id)}
                                  className="px-2 py-1 bg-emerald-600 hover:bg-emerald-700 text-white rounded text-[10px] font-bold"
                                >
                                  Approve
                                </button>
                                <button
                                  onClick={() => handleDirectReject(item.id)}
                                  className="px-2 py-1 bg-red-650 hover:bg-red-750 text-white rounded text-[10px] font-bold"
                                >
                                  Reject
                                </button>
                              </div>
                            ) : (
                              <span className="text-[10px] text-zinc-550 font-medium">Reviewed by {item.reviewed_by || 'Supervisor'}</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="text-center py-12 text-zinc-550">
                  <AlertCircle className="mx-auto mb-3" size={24} />
                  <p className="text-xs font-semibold">No Proposals Yet</p>
                  <p className="text-[10px] text-zinc-550 mt-1">When you propose terms using the auto-drafting panel, they will appear here.</p>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
