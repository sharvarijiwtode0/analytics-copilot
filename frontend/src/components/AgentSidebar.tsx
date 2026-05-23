/**
 * AgentSidebar — persistent right-hand activity log.
 * 280px wide. Shows all agent runs as a clickable list.
 * Click a run to expand its full trace: nodes, SQL, row count, insights.
 */
import { useState } from 'react'
import {
  Activity, Brain, Database, Code2, Play, BarChart3, MessageSquare,
  CheckCircle2, Loader2, ChevronDown, ChevronRight, Clock, Zap,
  Search, AlertCircle, Table2, PanelRightClose, PanelRightOpen,
  Cpu, ArrowRight,
} from 'lucide-react'
import { useThemeStore } from '../store/theme'
import type { TransparencyStep } from '../hooks/useStreamingQuery'

export interface AgentRun {
  id: string
  question: string
  startedAt: number
  completedAt?: number
  nodes: NodeTrace[]
  sql?: string
  tables?: string[]
  rowCount?: number
  intent?: string
  insights?: string[]
  keyMetrics?: Record<string, string>
  error?: string
}

interface NodeTrace {
  id: string
  label: string
  status: 'done' | 'running' | 'skipped' | 'error'
  durationMs?: number
  data?: Record<string, unknown>
}

const NODE_META: Record<string, { label: string; icon: React.ReactNode; color: string }> = {
  check_qa_memory:    { label: 'Cache Check',      icon: <Zap size={11} />,         color: 'text-yellow-400' },
  understand_intent:  { label: 'Intent',           icon: <Brain size={11} />,        color: 'text-purple-400' },
  disambiguate:       { label: 'Disambiguate',     icon: <Search size={11} />,       color: 'text-orange-400' },
  general_llm:        { label: 'General LLM',      icon: <Cpu size={11} />,          color: 'text-indigo-400' },
  discover_schema:    { label: 'Schema',           icon: <Database size={11} />,     color: 'text-blue-400' },
  generate_sql:       { label: 'SQL Gen',          icon: <Code2 size={11} />,        color: 'text-cyan-400' },
  execute_sql:        { label: 'Query Run',        icon: <Play size={11} />,         color: 'text-green-400' },
  analyze_insights:   { label: 'Analysis',         icon: <Activity size={11} />,     color: 'text-amber-400' },
  generate_viz_config:{ label: 'Visualisation',    icon: <BarChart3 size={11} />,    color: 'text-pink-400' },
  compose_response:   { label: 'Response',         icon: <MessageSquare size={11} />,color: 'text-indigo-400' },
  insight_followup:   { label: 'Follow-up',        icon: <ArrowRight size={11} />,   color: 'text-teal-400' },
}

interface AgentSidebarProps {
  /** Steps from the current in-flight streaming query */
  steps: TransparencyStep[]
  isStreaming: boolean
  /** Completed runs from previous queries */
  history: AgentRun[]
}

export default function AgentSidebar({ steps, isStreaming, history }: AgentSidebarProps) {
  const { theme } = useThemeStore()
  const dark = theme === 'dark'
  const [collapsed, setCollapsed] = useState(false)
  const [openRunId, setOpenRunId] = useState<string | null>(null)

  const bg    = dark ? 'bg-zinc-900'  : 'bg-white'
  const border= dark ? 'border-zinc-800' : 'border-slate-200'
  const sub   = dark ? 'text-zinc-500' : 'text-slate-400'
  const itemBg= dark ? 'bg-zinc-800/50' : 'bg-slate-50'
  const itemHover = dark ? 'hover:bg-zinc-800' : 'hover:bg-slate-100'

  // ── build live node list from streaming steps ──────────────────────────────
  const liveNodes: NodeTrace[] = []
  const seenNodes = new Set<string>()
  let liveSql: string | undefined
  let liveTables: string[] | undefined
  let liveRows: number | undefined
  let liveIntent: string | undefined
  let liveInsights: string[] | undefined
  let liveMetrics: Record<string, string> | undefined
  let liveError: string | undefined
  const nodeStart: Record<string, number> = {}

  for (const s of steps) {
    const nid = s.step
    if (!nid) continue
    if (s.type === 'start') {
      nodeStart[nid] = s.timestamp ?? Date.now()
    } else if (s.type === 'progress') {
      if (!seenNodes.has(nid)) {
        seenNodes.add(nid)
        liveNodes.push({
          id: nid,
          label: NODE_META[nid]?.label ?? nid,
          status: 'running',
          data: s.data as Record<string, unknown> | undefined,
        })
      } else {
        const n = liveNodes.find(n => n.id === nid)
        if (n) n.data = s.data as Record<string, unknown> | undefined
      }
      if (s.data?.sql)        liveSql     = s.data.sql as string
      if (s.data?.tables)     liveTables  = s.data.tables as string[]
      if (s.data?.row_count !== undefined) liveRows = s.data.row_count as number
      if (s.data?.intent)     liveIntent  = s.data.intent as string
      if (s.data?.insights)   liveInsights= s.data.insights as string[]
      if (s.data?.key_metrics)liveMetrics = s.data.key_metrics as Record<string, string>
    } else if (s.type === 'complete') {
      const n = liveNodes.find(n => n.id === nid)
      if (n) {
        n.status = 'done'
        n.durationMs = nodeStart[nid] ? (s.timestamp ?? Date.now()) - nodeStart[nid] : undefined
      }
      const r = s.result as Record<string, unknown> | undefined
      if (r?.sql) liveSql = r.sql as string
    } else if (s.type === 'error') {
      liveError = s.error
    }
  }

  // ── render ─────────────────────────────────────────────────────────────────
  if (collapsed) {
    return (
      <div className={`flex flex-col items-center pt-3 gap-3 border-l w-10 shrink-0 ${bg} ${border}`}>
        <button onClick={() => setCollapsed(false)} title="Expand"
          className={`p-1.5 rounded transition ${dark ? 'hover:bg-zinc-800' : 'hover:bg-slate-100'}`}>
          <PanelRightOpen size={14} className={sub} />
        </button>
        {isStreaming
          ? <Loader2 size={12} className="animate-spin text-blue-400" />
          : history.length > 0 && <Activity size={12} className="text-green-400" />}
      </div>
    )
  }

  return (
    <div className={`flex flex-col border-l shrink-0 overflow-hidden ${bg} ${border}`} style={{ width: 280 }}>
      {/* Header */}
      <div className={`flex items-center justify-between px-3 py-2.5 border-b ${border} shrink-0`}>
        <div className="flex items-center gap-1.5">
          <Activity size={13} className="text-blue-400" />
          <span className={`text-[11px] font-semibold tracking-widest uppercase ${dark ? 'text-zinc-300' : 'text-slate-600'}`}>
            Agent Log
          </span>
        </div>
        <div className="flex items-center gap-2">
          {isStreaming && (
            <span className="flex items-center gap-1 text-[10px] text-blue-400">
              <Loader2 size={9} className="animate-spin" /> live
            </span>
          )}
          <button onClick={() => setCollapsed(true)}
            className={`p-1 rounded transition ${dark ? 'hover:bg-zinc-800' : 'hover:bg-slate-100'}`}>
            <PanelRightClose size={13} className={sub} />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* ── LIVE CURRENT RUN ── */}
        {isStreaming && (
          <div className={`mx-2 my-2 rounded-lg border ${dark ? 'border-blue-500/40 bg-blue-500/5' : 'border-blue-200 bg-blue-50/50'}`}>
            {/* question */}
            {steps.length > 0 && (
              <div className={`px-2.5 py-2 border-b text-[11px] font-medium truncate ${dark ? 'border-blue-500/20 text-zinc-300' : 'border-blue-100 text-slate-700'}`}>
                {liveIntent && (
                  <span className={`inline-block mr-1.5 px-1.5 py-0.5 rounded text-[9px] font-bold uppercase ${
                    dark ? 'bg-purple-900/50 text-purple-300' : 'bg-purple-100 text-purple-700'
                  }`}>{liveIntent}</span>
                )}
              </div>
            )}

            {/* node pipeline */}
            <div className="px-2.5 py-2 space-y-1">
              {liveNodes.map((n, i) => {
                const meta = NODE_META[n.id]
                return (
                  <div key={n.id} className="flex items-center gap-2">
                    <div className="w-4 shrink-0 flex justify-center">
                      {n.status === 'running'
                        ? <Loader2 size={11} className="animate-spin text-blue-400" />
                        : n.status === 'done'
                        ? <CheckCircle2 size={11} className="text-green-400" />
                        : <AlertCircle size={11} className="text-red-400" />}
                    </div>
                    <span className={`${meta?.color ?? 'text-zinc-400'}`}>{meta?.icon}</span>
                    <span className={`text-[11px] flex-1 ${n.status === 'running'
                      ? (dark ? 'text-blue-300 font-medium' : 'text-blue-700 font-medium')
                      : (dark ? 'text-zinc-400' : 'text-slate-500')}`}>
                      {n.label}
                    </span>
                    {n.durationMs !== undefined && (
                      <span className={`text-[10px] font-mono ${sub}`}>{(n.durationMs / 1000).toFixed(1)}s</span>
                    )}
                  </div>
                )
              })}
            </div>

            {/* quick stats */}
            {(liveSql || liveRows !== undefined || (liveTables && liveTables.length > 0)) && (
              <div className={`px-2.5 py-2 border-t space-y-1.5 ${dark ? 'border-blue-500/20' : 'border-blue-100'}`}>
                {liveTables && liveTables.length > 0 && (
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <Table2 size={10} className="text-blue-400 shrink-0" />
                    {liveTables.slice(0, 3).map(t => (
                      <span key={t} className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
                        dark ? 'bg-blue-900/40 text-blue-300' : 'bg-blue-100 text-blue-700'
                      }`}>{t}</span>
                    ))}
                    {liveTables.length > 3 && <span className={`text-[10px] ${sub}`}>+{liveTables.length - 3}</span>}
                  </div>
                )}
                {liveRows !== undefined && (
                  <div className="flex items-center gap-1.5">
                    <Database size={10} className="text-green-400 shrink-0" />
                    <span className={`text-[11px] ${sub}`}>
                      <span className="font-semibold text-green-400">{liveRows.toLocaleString()}</span> rows
                    </span>
                  </div>
                )}
                {liveSql && (
                  <div>
                    <div className={`text-[10px] uppercase font-bold tracking-wider mb-1 ${sub}`}>SQL</div>
                    <pre className={`text-[10px] font-mono p-1.5 rounded overflow-x-auto whitespace-pre-wrap max-h-28 ${
                      dark ? 'bg-zinc-950 text-zinc-400' : 'bg-white text-slate-600 border border-slate-200'
                    }`}>{liveSql.slice(0, 400)}{liveSql.length > 400 ? '…' : ''}</pre>
                  </div>
                )}
              </div>
            )}

            {liveError && (
              <div className={`mx-2.5 mb-2 flex items-start gap-1.5 p-2 rounded text-[10px] ${
                dark ? 'bg-red-900/20 text-red-300' : 'bg-red-50 text-red-700'
              }`}>
                <AlertCircle size={10} className="mt-0.5 shrink-0" />
                {liveError.slice(0, 120)}
              </div>
            )}
          </div>
        )}

        {/* ── HISTORY ── */}
        {history.length === 0 && !isStreaming && (
          <div className={`flex flex-col items-center justify-center h-48 gap-2 ${sub}`}>
            <Activity size={22} className="opacity-20" />
            <p className="text-[11px] text-center opacity-50 px-4">
              Agent activity will appear here
            </p>
          </div>
        )}

        {history.length > 0 && (
          <div className="px-2 py-2 space-y-1.5">
            {[...history].reverse().map((run, idx) => {
              const isOpen = openRunId === run.id
              const elapsed = run.completedAt ? ((run.completedAt - run.startedAt) / 1000).toFixed(1) : null

              return (
                <div key={run.id}
                  className={`rounded-lg border overflow-hidden transition-all ${
                    isOpen
                      ? (dark ? 'border-zinc-600' : 'border-slate-300')
                      : (dark ? 'border-zinc-800' : 'border-slate-200')
                  } ${itemBg}`}>

                  {/* row header — always visible */}
                  <button
                    className={`w-full flex items-start gap-2 px-2.5 py-2 text-left transition ${itemHover}`}
                    onClick={() => setOpenRunId(isOpen ? null : run.id)}>
                    <div className="mt-0.5 shrink-0">
                      {run.error
                        ? <AlertCircle size={12} className="text-red-400" />
                        : <CheckCircle2 size={12} className="text-green-400" />}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className={`text-[11px] font-medium truncate ${dark ? 'text-zinc-300' : 'text-slate-700'}`}>
                        {run.question}
                      </p>
                      <div className="flex items-center gap-2 mt-0.5">
                        {run.intent && (
                          <span className={`text-[9px] px-1 py-0.5 rounded font-bold uppercase ${
                            dark ? 'bg-purple-900/40 text-purple-300' : 'bg-purple-100 text-purple-700'
                          }`}>{run.intent}</span>
                        )}
                        {run.rowCount !== undefined && (
                          <span className={`text-[10px] ${sub}`}>{run.rowCount.toLocaleString()} rows</span>
                        )}
                        {elapsed && (
                          <span className={`text-[10px] font-mono ml-auto ${sub}`}>
                            <Clock size={9} className="inline mr-0.5" />{elapsed}s
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="shrink-0 mt-0.5">
                      {isOpen
                        ? <ChevronDown size={12} className={sub} />
                        : <ChevronRight size={12} className={sub} />}
                    </div>
                  </button>

                  {/* expanded trace */}
                  {isOpen && (
                    <div className={`border-t px-2.5 py-2 space-y-3 ${dark ? 'border-zinc-700' : 'border-slate-200'}`}>

                      {/* nodes */}
                      {run.nodes.length > 0 && (
                        <div>
                          <p className={`text-[10px] uppercase font-bold tracking-wider mb-1.5 ${sub}`}>Pipeline</p>
                          <div className="space-y-1">
                            {run.nodes.map(n => {
                              const meta = NODE_META[n.id]
                              return (
                                <div key={n.id} className="flex items-center gap-2">
                                  <CheckCircle2 size={10} className="text-green-400 shrink-0" />
                                  <span className={`${meta?.color ?? 'text-zinc-400'}`}>{meta?.icon}</span>
                                  <span className={`text-[11px] flex-1 ${dark ? 'text-zinc-400' : 'text-slate-500'}`}>
                                    {n.label}
                                  </span>
                                  {n.durationMs !== undefined && (
                                    <span className={`text-[10px] font-mono ${sub}`}>
                                      {(n.durationMs / 1000).toFixed(1)}s
                                    </span>
                                  )}
                                </div>
                              )
                            })}
                          </div>
                        </div>
                      )}

                      {/* tables */}
                      {run.tables && run.tables.length > 0 && (
                        <div>
                          <p className={`text-[10px] uppercase font-bold tracking-wider mb-1 ${sub}`}>Tables</p>
                          <div className="flex flex-wrap gap-1">
                            {run.tables.map(t => (
                              <span key={t} className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
                                dark ? 'bg-blue-900/40 text-blue-300' : 'bg-blue-100 text-blue-700'
                              }`}>{t}</span>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* SQL */}
                      {run.sql && (
                        <div>
                          <div className="flex items-center justify-between mb-1">
                            <p className={`text-[10px] uppercase font-bold tracking-wider ${sub}`}>SQL</p>
                            <button
                              onClick={() => navigator.clipboard.writeText(run.sql!)}
                              className={`text-[10px] hover:underline ${dark ? 'text-blue-400' : 'text-blue-600'}`}>
                              copy
                            </button>
                          </div>
                          <pre className={`text-[10px] font-mono p-2 rounded overflow-x-auto whitespace-pre-wrap max-h-36 ${
                            dark ? 'bg-zinc-950 text-zinc-400' : 'bg-white text-slate-600 border border-slate-200'
                          }`}>{run.sql}</pre>
                        </div>
                      )}

                      {/* insights */}
                      {run.insights && run.insights.length > 0 && (
                        <div>
                          <p className={`text-[10px] uppercase font-bold tracking-wider mb-1 ${sub}`}>Insights</p>
                          <div className="space-y-1">
                            {run.insights.slice(0, 3).map((ins, i) => (
                              <div key={i} className={`text-[11px] p-1.5 rounded ${
                                dark ? 'bg-amber-900/20 text-amber-300' : 'bg-amber-50 text-amber-800'
                              }`}>{ins}</div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* key metrics */}
                      {run.keyMetrics && Object.keys(run.keyMetrics).length > 0 && (
                        <div>
                          <p className={`text-[10px] uppercase font-bold tracking-wider mb-1 ${sub}`}>Metrics</p>
                          <div className="grid grid-cols-2 gap-1">
                            {Object.entries(run.keyMetrics).slice(0, 4).map(([k, v]) => (
                              <div key={k} className={`p-1.5 rounded border ${dark ? 'border-zinc-700 bg-zinc-800/40' : 'border-slate-200 bg-white'}`}>
                                <div className={`text-[9px] uppercase tracking-wider truncate ${sub}`}>{k}</div>
                                <div className={`text-[11px] font-semibold truncate ${dark ? 'text-zinc-200' : 'text-slate-700'}`}>{v}</div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* error */}
                      {run.error && (
                        <div className={`flex items-start gap-1.5 p-2 rounded text-[10px] ${
                          dark ? 'bg-red-900/20 text-red-300' : 'bg-red-50 text-red-700'
                        }`}>
                          <AlertCircle size={10} className="mt-0.5 shrink-0" />
                          {run.error}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
