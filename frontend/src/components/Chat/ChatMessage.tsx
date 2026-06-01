import React, { useState } from 'react'
import { ChevronDown, ChevronUp, Code, Lightbulb, Clock, Edit2, Trash2, Sparkles, X, Database } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import { ChartRenderer } from '../Charts/ChartRenderer'
import { indianiseCurrencyText, autoFormatValue } from '../../lib/formatters'
import type { ChatMessage as ChatMessageType } from '../../store/chat'
import { useAuthStore } from '../../store/auth'
import { CommunicationAgent } from './CommunicationAgent'
import type { TransparencyStep } from '../../hooks/useStreamingQuery'

interface Props {
  message: ChatMessageType
  onFollowUp: (question: string) => void
  onEdit?: (messageId: string, content: string) => void
  onDelete?: (messageId: string) => void
  theme?: 'light' | 'dark'
}

export const ChatMessageComponent: React.FC<Props> = ({ message, onFollowUp, onEdit, onDelete, theme = 'light' }) => {
  const [showSQL, setShowSQL] = useState(false)
  const [showExplain, setShowExplain] = useState(false) // controls insights dropdown
  const { user } = useAuthStore()
  const role = user?.role || 'team_member'

  const getTablesForMessage = (msg: any): string[] => {
    if (msg.sql_query) {
      const sql = msg.sql_query.toLowerCase()
      const candidates = ['combined_sales_final', 'product_master', 'product_catlog', 'inventory_sales_overview_new', 'shopify_orders']
      const matched = candidates.filter(t => sql.includes(t))
      if (matched.length > 0) return matched
    }
    return msg.columns && msg.columns.length > 0 ? ['shopify_orders'] : []
  }

  if (message.role === 'user') {
    return (
      <div className="flex justify-end mb-4 group relative">
        <div className="flex items-center gap-2 mr-2 opacity-60 hover:opacity-100 transition-opacity">
          {onEdit && (
            <button
              onClick={() => onEdit(message.id, message.content)}
              className={`p-1.5 hover:text-blue-500 rounded-lg shadow-sm border transition ${
                theme === 'dark'
                  ? 'text-zinc-400 bg-zinc-800 border-zinc-700'
                  : 'text-zinc-400 bg-white border-zinc-200'
              }`}
              title="Edit question"
            >
              <Edit2 size={14} />
            </button>
          )}
          {onDelete && (
            <button
              onClick={() => onDelete(message.id)}
              className={`p-1.5 hover:text-red-500 rounded-lg shadow-sm border transition ${
                theme === 'dark'
                  ? 'text-zinc-400 bg-zinc-800 border-zinc-700'
                  : 'text-zinc-400 bg-white border-zinc-200'
              }`}
              title="Delete question"
            >
              <Trash2 size={14} />
            </button>
          )}
        </div>
        <div className="max-w-2xl bg-blue-600 text-white rounded-2xl rounded-tr-sm px-5 py-3 shadow-sm">
          <p className="text-sm leading-relaxed">{message.content}</p>
        </div>
      </div>
    )
  }

  // Convert any $-denominated text to ₹
  const displayText = message.content ? indianiseCurrencyText(message.content) : ''

  return (
    <div className="flex flex-col gap-3 mb-6">
      {/* Agent badge & Steps toggle */}
      <div className="flex items-center flex-wrap gap-2 text-xs text-zinc-500">
        <div className="w-6 h-6 rounded-full bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white text-xs font-bold">
          AI
        </div>
        <span>Limese Copilot</span>
        {message.latency_ms && (
          <span className="flex items-center gap-1 text-zinc-400">
            <Clock size={11} /> {(message.latency_ms / 1000).toFixed(1)}s
          </span>
        )}
        {message.row_count !== undefined && message.row_count > 0 && (
          <span className="bg-blue-100 text-blue-600 rounded-full px-2 py-0.5 text-xs">
            {message.row_count.toLocaleString('en-IN')} rows
          </span>
        )}

        {/* Inline Steps Taken toggle */}
        {message.sql && (
          <button
            onClick={() => setShowSQL(!showSQL)}
            className={`flex items-center gap-1.5 px-2 py-1 rounded transition-colors ml-1 ${
              showSQL 
                ? theme === 'dark' ? 'bg-zinc-800 text-zinc-300' : 'bg-slate-200 text-slate-700'
                : theme === 'dark' ? 'hover:bg-zinc-800/50 text-zinc-400' : 'hover:bg-slate-100 text-slate-500'
            }`}
          >
            <Database size={12} />
            <span>Steps Taken</span>
            {showSQL ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          </button>
        )}
        {/* Analysis dropdown toggle */}
        {message.insights && message.insights.length > 0 && (
          <button
            onClick={() => setShowExplain(!showExplain)}
            className={`flex items-center gap-1.5 px-2 py-1 rounded transition-colors ml-1 ${
              showExplain 
                ? theme === 'dark' ? 'bg-zinc-800 text-zinc-300' : 'bg-slate-200 text-slate-700'
                : theme === 'dark' ? 'hover:bg-zinc-800/50 text-zinc-400' : 'hover:bg-slate-100 text-slate-500'
            }`}
          >
            <Lightbulb size={12} />
            <span>Analysis</span>
            {showExplain ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          </button>
        )}
      </div>

      {/* Expanded Steps Block */}
      {showSQL && message.sql && (
        <div className="mt-1">
          <CommunicationAgent
            steps={[
              { type: 'progress', step: 'understand_intent', data: { status: 'complete', intent: message.viz_type ? 'chart_request' : 'data_query' } },
              { type: 'progress', step: 'discover_schema', data: { status: 'complete', tables: getTablesForMessage(message) } },
              { type: 'progress', step: 'generate_sql', data: { status: 'complete', sql: message.sql } },
              { type: 'progress', step: 'execute_sql', data: { status: 'complete', row_count: message.row_count } },
              { type: 'progress', step: 'analyze_insights', data: { status: 'complete', insights: message.insights } },
              { type: 'progress', step: 'generate_viz_config', data: { status: 'complete', viz_type: message.viz_type || 'table' } },
              { type: 'complete', step: 'compose_response', data: { status: 'complete' } }
            ]}
            isLoading={false}
            theme={theme}
          />
        </div>
      )}

      {/* Error state — friendly messages for known errors */}
      {message.error && (
        <div className={`rounded-xl px-4 py-3 text-sm border ${
          message.error.toLowerCase().includes('daily ai query limit') ||
          message.error.toLowerCase().includes('rate_limit') ||
          message.error.toLowerCase().includes('ratelimit') ||
          message.error.toLowerCase().includes('rate limit')
            ? 'bg-amber-50 border-amber-200 text-amber-800'
            : 'bg-red-50 border-red-200 text-red-700'
        }`}>
          {message.error.toLowerCase().includes('daily ai query limit') ||
          message.error.toLowerCase().includes('rate_limit') ||
          message.error.toLowerCase().includes('ratelimit') ||
          message.error.toLowerCase().includes('rate limit') ? (
            <div className="space-y-2">
              <p className="font-semibold">⏳ Daily AI limit reached</p>
              <p className="text-xs">Groq free tier: 100K tokens/day used. Options:</p>
              <ul className="text-xs list-disc list-inside space-y-1">
                <li><strong>Free fix:</strong> Add <code className="bg-amber-100 px-1 rounded">GEMINI_API_KEY</code> to .env → restart (aistudio.google.com)</li>
                <li><strong>Wait:</strong> Resets at midnight UTC (~40 min)</li>
                <li><strong>Upgrade:</strong> console.groq.com/settings/billing ($9/mo for 100M tokens)</li>
              </ul>
            </div>
          ) : (
            <span>⚠️ {message.error}</span>
          )}
        </div>
      )}

      {/* Main text — $→₹ converted */}
      {displayText && !message.error && (
        <div className={`rounded-2xl rounded-tl-sm px-5 py-4 shadow-sm ${
          theme === 'dark'
            ? 'bg-zinc-900 border-zinc-800'
            : 'bg-white border border-zinc-200'
        }`}>

          <div className={`text-sm leading-relaxed prose prose-sm max-w-none ${
            theme === 'dark' ? 'text-zinc-300 prose-invert' : 'text-zinc-700'
          }`}>
            <ReactMarkdown>{displayText}</ReactMarkdown>
          </div>
        </div>
      )}

      {/* Key Metrics pills — formatted as ₹ */}
      {message.key_metrics && Object.keys(message.key_metrics).length > 0 && (
        <div className="flex flex-wrap gap-2">
          {Object.entries(message.key_metrics).slice(0, 6).map(([k, v]) => {
            const formatted = autoFormatValue(k, v)
            return (
              <div key={k} className="bg-blue-50 border border-blue-200 rounded-lg px-3 py-1.5 text-xs">
                <span className="text-blue-500 font-medium">{k}:</span>{' '}
                <span className="text-blue-800 font-semibold">{indianiseCurrencyText(String(formatted))}</span>
              </div>
            )
          })}
        </div>
      )}

      {/* Chart */}
      {message.chart && (
        <div className="group relative">
          <ChartRenderer
            vizConfig={message.chart as Record<string, unknown>}
            vizType={message.viz_type || null}
            columns={message.columns}
            rows={message.rows}
            theme={theme}
          />
        </div>
      )}



      {/* Insights — $→₹ converted */}
      {showExplain && message.insights && message.insights.length > 0 && (
        <div className={`border rounded-xl px-4 py-3.5 shadow-sm leading-relaxed transition-all duration-300 ${
          theme === 'dark'
            ? 'bg-amber-500/5 border-amber-500/20 text-amber-100'
            : 'bg-amber-50 border border-amber-200 text-amber-800'
        }`}>
          <div className={`flex items-center gap-2 text-xs font-semibold mb-2.5 ${
            theme === 'dark' ? 'text-amber-400' : 'text-amber-700'
          }`}>
            <Lightbulb size={14} className="text-amber-500 animate-pulse" />
            <span>Key Business Insights & Reasoning</span>
          </div>
          <ul className="space-y-2">
            {message.insights.map((ins, i) => (
              <li key={i} className={`text-xs flex gap-2.5 items-start ${
                theme === 'dark' ? 'text-zinc-300' : 'text-slate-700'
              }`}>
                <span className="text-amber-500 mt-1 select-none">•</span>
                <div className="prose prose-sm dark:prose-invert max-w-none text-xs flex-1">
                  <ReactMarkdown>{indianiseCurrencyText(ins)}</ReactMarkdown>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}



      {/* Follow-up questions */}
      {message.follow_up_questions && message.follow_up_questions.length > 0 && (
        <div>
          <p className={`text-xs mb-2 ${theme === 'dark' ? 'text-zinc-400' : 'text-zinc-400'}`}>Suggested follow-ups:</p>
          <div className="flex flex-wrap gap-2">
            {message.follow_up_questions.map((q, i) => (
              <button
                key={i}
                onClick={() => onFollowUp(q)}
                className={`text-xs border rounded-full px-3 py-1.5 transition ${
                  theme === 'dark'
                    ? 'bg-zinc-800 border-zinc-700 text-blue-400 hover:bg-zinc-700'
                    : 'bg-white border-blue-200 text-blue-600 hover:bg-blue-50'
                }`}
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
