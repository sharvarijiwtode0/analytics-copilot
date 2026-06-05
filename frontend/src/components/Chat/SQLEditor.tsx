import React, { useState, useCallback } from 'react'
import { Play, Clock, AlertCircle, CheckCircle, Table } from 'lucide-react'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'

interface SQLEditorProps {
  datasourceId: string
  theme: string
  initialSQL?: string
  onSQLGenerated?: (sql: string) => void
}

interface QueryResult {
  columns: string[]
  rows: Record<string, unknown>[]
  row_count: number
  execution_time_ms: number
}

const SQLEditor: React.FC<SQLEditorProps> = ({ datasourceId, theme }) => {
  const [sql, setSQL] = useState('')
  const [result, setResult] = useState<QueryResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isRunning, setIsRunning] = useState(false)

  const handleExecute = useCallback(async () => {
    if (!sql.trim() || isRunning) return
    setIsRunning(true)
    setError(null)
    setResult(null)
    try {
      const resp = await fetch('/api/v1/copilot/sql/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sql: sql.trim(), datasource_id: datasourceId }),
      })
      const data = await resp.json()
      if (!resp.ok) {
        setError(data.detail || 'Query execution failed')
      } else {
        setResult(data)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to execute query')
    } finally {
      setIsRunning(false)
    }
  }, [sql, datasourceId, isRunning])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      handleExecute()
    }
  }, [handleExecute])

  return (
    <div className="flex flex-col gap-3">
      <div className={`flex items-center justify-between px-3 py-2 rounded-lg text-xs ${
        theme === 'dark' ? 'bg-zinc-800 text-zinc-400' : 'bg-slate-100 text-slate-500'
      }`}>
        <div className="flex items-center gap-2">
          <Table size={12} />
          <span className="font-medium">SQL Editor</span>
        </div>
        <div className="flex items-center gap-2 text-[10px]">
          <kbd className={`px-1.5 py-0.5 rounded border ${
            theme === 'dark' ? 'border-zinc-600 text-zinc-400' : 'border-slate-300 text-slate-500'
          }`}>Ctrl+Enter</kbd>
          <span>to run</span>
        </div>
      </div>

      <textarea
        value={sql}
        onChange={(e) => setSQL(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="SELECT * FROM sales LIMIT 100&#10;&#10;Write your SQL query here..."
        rows={6}
        spellCheck={false}
        className={`w-full px-4 py-3 rounded-xl border text-sm font-mono resize-y focus:outline-none focus:ring-2 focus:ring-blue-500 transition ${
          theme === 'dark'
            ? 'bg-zinc-900 border-zinc-700 text-zinc-100 placeholder-zinc-600'
            : 'bg-white border-slate-200 text-slate-800 placeholder-slate-400'
        }`}
      />

      <div className="flex items-center justify-between">
        <button
          onClick={handleExecute}
          disabled={isRunning || !sql.trim()}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-medium transition ${
            isRunning || !sql.trim()
              ? 'bg-zinc-700 text-zinc-500 cursor-not-allowed'
              : 'bg-blue-600 hover:bg-blue-700 text-white shadow-sm'
          }`}
        >
          <Play size={12} />
          {isRunning ? 'Running...' : 'Run Query'}
        </button>

        {(result || error) && (
          <div className="flex items-center gap-2 text-xs">
            {error && (
              <span className="flex items-center gap-1 text-red-500">
                <AlertCircle size={12} />
                Error
              </span>
            )}
            {result && (
              <span className="flex items-center gap-1 text-green-500">
                <CheckCircle size={12} />
                {result.row_count} rows
                <Clock size={10} className="ml-1" />
                {result.execution_time_ms}ms
              </span>
            )}
          </div>
        )}
      </div>

      {error && (
        <div className={`rounded-xl border px-4 py-3 text-sm font-mono ${
          theme === 'dark' ? 'bg-red-950/30 border-red-900 text-red-300' : 'bg-red-50 border-red-200 text-red-700'
        }`}>
          {error}
        </div>
      )}

      {result && result.columns.length > 0 && (
        <div className={`rounded-xl border overflow-hidden ${
          theme === 'dark' ? 'border-zinc-700' : 'border-slate-200'
        }`}>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className={theme === 'dark' ? 'bg-zinc-800' : 'bg-slate-50'}>
                  {result.columns.map((col) => (
                    <th
                      key={col}
                      className={`px-3 py-2 text-left font-semibold whitespace-nowrap ${
                        theme === 'dark' ? 'text-zinc-300' : 'text-slate-600'
                      }`}
                    >
                      {col}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.rows.slice(0, 100).map((row, i) => (
                  <tr
                    key={i}
                    className={`border-t ${
                      theme === 'dark' ? 'border-zinc-800 hover:bg-zinc-800/50' : 'border-slate-100 hover:bg-slate-50'
                    }`}
                  >
                    {result.columns.map((col) => (
                      <td
                        key={col}
                        className={`px-3 py-1.5 whitespace-nowrap max-w-[200px] truncate font-mono ${
                          theme === 'dark' ? 'text-zinc-400' : 'text-slate-500'
                        }`}
                      >
                        {row[col] == null ? <span className="text-zinc-600 italic">null</span> : String(row[col])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {result.row_count > 100 && (
            <div className={`px-3 py-2 text-[10px] border-t ${
              theme === 'dark' ? 'border-zinc-700 text-zinc-500' : 'border-slate-200 text-slate-400'
            }`}>
              Showing 100 of {result.row_count} rows
            </div>
          )}
        </div>
      )}

      {result && result.row_count === 0 && !error && (
        <div className={`rounded-xl border px-4 py-6 text-center text-sm ${
          theme === 'dark' ? 'bg-zinc-800 border-zinc-700 text-zinc-400' : 'bg-slate-50 border-slate-200 text-slate-500'
        }`}>
          Query returned 0 rows
        </div>
      )}
    </div>
  )
}

export default SQLEditor
