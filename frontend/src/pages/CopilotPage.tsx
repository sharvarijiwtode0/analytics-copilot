import React, { useEffect, useRef, useState } from 'react'
import { BarChart2, Database, Clock, Plus, Trash2, MessageSquare, Code, LayoutDashboard } from 'lucide-react'
import { useChatStore, ChatSession } from '../store/chat'
import { useThemeStore } from '../store/theme'
import { useAuthStore } from '../store/auth'
import { sendQuery, getDatasources, getSchema } from '../api/client'
import { ChatMessageComponent } from '../components/Chat/ChatMessage'
import { ChatInput } from '../components/Chat/ChatInput'
import DisambiguationModal from '../components/DisambiguationModal'
import Sidebar from '../components/Layout/Sidebar'
import TransparencyPanel from '../components/TransparencyPanel'
import AgentSidebar, { type AgentRun } from '../components/AgentSidebar'
import { useStreamingQuery, type TransparencyStep } from '../hooks/useStreamingQuery'

export const CopilotPage: React.FC = () => {
  const {
    sessions, activeSessionId, isLoading, datasourceId,
    addUserMessage, addAssistantMessage, setLoading, startNewSession,
    setConversationId, setUploadedFile, setDatasourceId, loadSession, purgeExpiredSessions,
    deleteSession, deleteMessage, clearForNewUser
  } = useChatStore()

  const { theme } = useThemeStore()
  const { user } = useAuthStore()

  const [datasources, setDatasources] = useState<any[]>([])
  const [schema, setSchema] = useState<any>(null)
  const [schemaLoading, setSchemaLoading] = useState(false)

  // Load registered datasources on mount
  useEffect(() => {
    const fetchDatasources = async () => {
      try {
        const list = await getDatasources()
        setDatasources(list)
        // If the persisted datasource is not in the list, fallback to first available
        if (list.length > 0 && !list.some((ds: { id: string }) => ds.id === datasourceId)) {
          setDatasourceId(list[0].id)
        }
      } catch (err) {
        console.error("Failed to fetch datasources:", err)
      }
    }
    fetchDatasources()
    setUploadedFile(null)
  }, [])

  // Load schema whenever active datasource changes
  useEffect(() => {
    if (!datasourceId) return
    const fetchSchema = async () => {
      setSchemaLoading(true)
      try {
        const data = await getSchema(datasourceId)
        setSchema(data)
      } catch (err) {
        console.error(`Failed to fetch schema for ${datasourceId}:`, err)
        setSchema(null)
      } finally {
        setSchemaLoading(false)
      }
    }
    fetchSchema()
  }, [datasourceId])

  const activeSession = sessions.find(s => s.id === activeSessionId)
  const messages = activeSession?.messages || []
  const conversationId = activeSession?.conversationId || null
  const [sessionToDelete, setSessionToDelete] = useState<ChatSession | null>(null)
  const [chatInputValue, setChatInputValue] = useState('')
  const [messageToDelete, setMessageToDelete] = useState<string | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(true)

  // Transparency panel state
  const [transparencySteps, setTransparencySteps] = useState<TransparencyStep[]>([])
  const [showTransparency, setShowTransparency] = useState(false)

  // Agent sidebar history (persisted across messages)
  const [agentHistory, setAgentHistory] = useState<AgentRun[]>([])
  const currentRunRef = useRef<AgentRun | null>(null)

  // Streaming query hook
  const { streamQuery, isStreaming, abort } = useStreamingQuery()

  // Chat modes
  type ChatMode = 'chat' | 'sql' | 'dashboard'
  const [chatMode, setChatMode] = useState<ChatMode>('chat')

  // Disambiguation state
  const [disambiguation, setDisambiguation] = useState<{
    keyword: string
    options: string[]
    originalQuestion: string
  } | null>(null)

  useEffect(() => {
    purgeExpiredSessions()
    const interval = setInterval(purgeExpiredSessions, 60000)
    return () => clearInterval(interval)
  }, [purgeExpiredSessions])

  // Check user on mount and clear chat if user changed
  useEffect(() => {
    const state = useChatStore.getState()
    if (user && state.userId !== user.id) {
      clearForNewUser(user.id)
    }
  }, [user?.id, clearForNewUser])

  const bottomRef = useRef<HTMLDivElement>(null)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isLoading])

  // Start / stop elapsed timer when loading state changes
  useEffect(() => {
    if (isLoading) {
      setElapsedSeconds(0)
      timerRef.current = setInterval(() => {
        setElapsedSeconds(prev => prev + 1)
      }, 1000)
    } else {
      if (timerRef.current) {
        clearInterval(timerRef.current)
        timerRef.current = null
      }
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [isLoading])

  const handleSend = async (question: string) => {
    if (isLoading || isStreaming) return
    addUserMessage(question)

    // Immediately capture the activeSessionId in case the user switches sessions during loading
    const targetSessionId = useChatStore.getState().activeSessionId || undefined

    setLoading(true)
    setTransparencySteps([])
    setShowTransparency(true)

    // Start a new agent run entry
    const runId = crypto.randomUUID()
    currentRunRef.current = {
      id: runId,
      question,
      startedAt: Date.now(),
      nodes: {},
    }

    // Use streaming query for real-time updates
    streamQuery(question, datasourceId, {
      onData: (step) => {
        setTransparencySteps(prev => [...prev, step])
        // Accumulate into current run
        if (currentRunRef.current) {
          const run = currentRunRef.current
          if (step.data?.tables) run.tables = step.data.tables as string[]
          if (step.data?.row_count !== undefined) run.rowCount = step.data.row_count as number
          if (step.data?.sql) run.sql = step.data.sql as string
          if (step.data?.intent) run.intent = step.data.intent as string
          if (step.data?.insights) run.insights = step.data.insights as string[]
          if (step.data?.key_metrics) run.keyMetrics = step.data.key_metrics as Record<string, string>
        }
      },
      onComplete: (response) => {
        setLoading(false)
        setShowTransparency(false)
        // Finalise run and push to history
        if (currentRunRef.current) {
          const run = currentRunRef.current
          run.completedAt = Date.now()
          if (response.sql) run.sql = response.sql
          if (response.row_count) run.rowCount = response.row_count
          setAgentHistory(prev => [...prev, { ...run }])
          currentRunRef.current = null
        }

        // Check for disambiguation error
        if (response.error?.startsWith('DISAMBIGUATION_NEEDED:')) {
          const errorParts = response.error.split(':')
          const keyword = errorParts[1]
          const options = response.follow_up_questions || []

          // Remove the assistant message with disambiguation request
          const session = useChatStore.getState().sessions.find(s => s.id === targetSessionId)
          if (session?.messages.length) {
            useChatStore.getState().deleteMessage(session.messages[session.messages.length - 1].id)
          }

          setDisambiguation({ keyword, options, originalQuestion: question })
          return
        }

        addAssistantMessage(response, targetSessionId)
        if (response.conversation_id && !conversationId) {
          setConversationId(response.conversation_id, targetSessionId)
        }
      },
      onError: (error) => {
        setLoading(false)
        setShowTransparency(false)
        if (currentRunRef.current) {
          const run = currentRunRef.current
          run.completedAt = Date.now()
          run.error = error
          setAgentHistory(prev => [...prev, { ...run }])
          currentRunRef.current = null
        }
        addAssistantMessage({
          conversation_id: conversationId || '',
          message_id: crypto.randomUUID(),
          text: 'Something went wrong. Please try after some time.',
          chart: null, insights: [], key_metrics: {}, follow_up_questions: [],
          sql: '', sql_explanation: '', row_count: 0, viz_type: null,
          columns: [], rows: [], total_latency_ms: 0, model_used: '',
          error,
        }, targetSessionId)
      }
    })
  }

  const handleDisambiguationSelect = async (selectedMeaning: string) => {
    if (!disambiguation) return

    setDisambiguation(null)
    setLoading(true)
    setShowTransparency(true)

    const targetSessionId = useChatStore.getState().activeSessionId || undefined
    const modifiedQuestion = `${disambiguation.originalQuestion} (Note: ${disambiguation.keyword} means '${selectedMeaning}')`

    streamQuery(modifiedQuestion, datasourceId, {
      onData: (step) => {
        setTransparencySteps(prev => [...prev, step])
      },
      onComplete: (response) => {
        setLoading(false)
        setShowTransparency(false)
        addAssistantMessage(response, targetSessionId)
        if (response.conversation_id && !conversationId) {
          setConversationId(response.conversation_id, targetSessionId)
        }
      },
      onError: (error) => {
        setLoading(false)
        setShowTransparency(false)
        addAssistantMessage({
          conversation_id: conversationId || '',
          message_id: crypto.randomUUID(),
          text: 'Something went wrong. Please try after some time.',
          chart: null, insights: [], key_metrics: {}, follow_up_questions: [],
          sql: '', sql_explanation: '', row_count: 0, viz_type: null,
          columns: [], rows: [], total_latency_ms: 0, model_used: '',
          error,
        }, targetSessionId)
      }
    })
  }


  return (
    <div className={`flex h-screen ${theme === 'dark' ? 'bg-zinc-950' : 'bg-slate-50'}`}>
      {/* Left Sidebar — chat history */}
      <Sidebar 
        isOpen={sidebarOpen} 
        onToggle={() => setSidebarOpen(!sidebarOpen)} 
        onSessionChange={() => {
          if (isStreaming || isLoading) {
            abort()
            setLoading(false)
            setShowTransparency(false)
          }
        }}
      />

      {/* Main Content — full width between sidebars */}
      <div className={`flex flex-col flex-1 min-w-0 transition-all duration-300 ${sidebarOpen ? 'lg:ml-60' : 'lg:ml-14'}`}>
        {/* Header */}
        <header className={`flex items-center justify-between px-5 py-3 border-b z-10 ${
          theme === 'dark' ? 'bg-zinc-900 border-zinc-800' : 'bg-white border-slate-200'
        }`}>
          <div className="flex items-center gap-3">
            <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${theme === 'dark' ? 'bg-zinc-800' : 'bg-zinc-900'}`}>
              <BarChart2 size={16} className="text-white" />
            </div>
            <div>
              <h1 className={`font-semibold text-sm ${theme === 'dark' ? 'text-zinc-100' : 'text-slate-800'}`}>
                Data Visualization Copilot
              </h1>
              <p className={`text-xs ${theme === 'dark' ? 'text-zinc-400' : 'text-slate-400'}`}>
                {datasourceId === 'default' ? 'SQLite Demo' : datasourceId === 'limese' ? 'Limese ClickHouse' : datasourceId}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {/* Mode Switcher */}
            <div className={`flex items-center gap-0.5 rounded-lg p-0.5 text-xs ${
              theme === 'dark' ? 'bg-zinc-800' : 'bg-slate-100'
            }`}>
              {[
                { id: 'chat' as ChatMode, icon: <MessageSquare size={12} />, label: 'Chat' },
                { id: 'sql' as ChatMode, icon: <Code size={12} />, label: 'SQL' },
                { id: 'dashboard' as ChatMode, icon: <LayoutDashboard size={12} />, label: 'Dashboard' },
              ].map(m => (
                <button
                  key={m.id}
                  onClick={() => setChatMode(m.id)}
                  className={`flex items-center gap-1 px-2.5 py-1.5 rounded-md transition ${
                    chatMode === m.id
                      ? theme === 'dark' ? 'bg-zinc-700 text-white shadow-sm' : 'bg-white text-slate-800 shadow-sm'
                      : theme === 'dark' ? 'text-zinc-400 hover:text-zinc-200' : 'text-slate-500 hover:text-slate-700'
                  }`}
                >
                  {m.icon}
                  <span>{m.label}</span>
                </button>
              ))}
            </div>
            {/* Datasource */}
            <div className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs ${
              theme === 'dark' ? 'bg-zinc-800 text-zinc-300' : 'bg-slate-100 text-slate-600'
            }`}>
              <Database size={12} className="text-green-500" />
              {datasources.length === 0 ? (
                <span>Loading...</span>
              ) : (
                <select
                  value={datasourceId}
                  onChange={(e) => { setDatasourceId(e.target.value); startNewSession() }}
                  className="bg-transparent border-none outline-none font-medium cursor-pointer"
                >
                  {datasources.map((ds) => (
                    <option key={ds.id} value={ds.id} className={theme === 'dark' ? 'bg-zinc-950 text-zinc-300' : 'bg-white text-slate-600'}>
                      {ds.id === 'default' ? 'SQLite Demo' : ds.id === 'limese' ? 'Limese ClickHouse' : `${ds.id} (${ds.type})`}
                    </option>
                  ))}
                </select>
              )}
            </div>
          </div>
        </header>

        {/* Body — chat + agent sidebar side by side */}
        <div className="flex flex-1 min-h-0">
          {/* Chat area — fills all remaining space */}
          <div className="flex flex-col flex-1 min-w-0">
            {/* Messages */}
            <div className="flex-1 overflow-y-auto overflow-x-hidden px-4 md:px-8 py-6">
              <div className="w-full max-w-3xl mx-auto">
                {messages.length === 0 ? (
                  <WelcomeScreen
                    onQuestionClick={handleSend}
                    theme={theme}
                    schema={schema}
                    datasourceId={datasourceId}
                    loading={schemaLoading}
                  />
                ) : (
                  <>
                    {messages.map((msg) => (
                      <ChatMessageComponent
                        key={msg.id}
                        message={msg}
                        onFollowUp={handleSend}
                        onEdit={(id, content) => setChatInputValue(content)}
                        onDelete={(id) => setMessageToDelete(id)}
                        theme={theme}
                      />
                    ))}
                    {isLoading && <ThinkingIndicator seconds={elapsedSeconds} theme={theme} />}
                  </>
                )}
                <div ref={bottomRef} />
              </div>
            </div>

            {/* Disambiguation */}
            {disambiguation && (
              <div className="px-8 pb-2 max-w-3xl mx-auto w-full">
                <DisambiguationModal
                  keyword={disambiguation.keyword}
                  options={disambiguation.options}
                  onSelect={handleDisambiguationSelect}
                  onDismiss={() => setDisambiguation(null)}
                />
              </div>
            )}

            {/* Input */}
            <div className={`border-t ${theme === 'dark' ? 'bg-zinc-900 border-zinc-800' : 'bg-white border-slate-200'}`}>
              <div className="max-w-3xl mx-auto">
                <ChatInput
                  value={chatInputValue}
                  onChange={setChatInputValue}
                  onSend={handleSend}
                  onStop={() => { abort(); setLoading(false); setShowTransparency(false) }}
                  isLoading={isLoading}
                  theme={theme}
                />
              </div>
            </div>
          </div>

          {/* Right Agent Sidebar */}
          <AgentSidebar
            steps={transparencySteps}
            isStreaming={isStreaming || isLoading}
            currentQuestion={activeSession?.messages.filter(m => m.role === 'user').slice(-1)[0]?.content || ''}
            history={agentHistory}
          />
        </div>
      </div>

      {/* Delete Confirmation Modal */}
      {sessionToDelete && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-[100] animate-in fade-in duration-200">
          <div className={`${theme === 'dark' ? 'bg-zinc-900 border-zinc-700' : 'bg-white border-slate-200'} border rounded-2xl shadow-xl max-w-sm w-full mx-4 p-5 animate-in zoom-in-95 duration-200`}>
            <h3 className={`font-bold text-base mb-1.5 flex items-center gap-2 ${theme === 'dark' ? 'text-zinc-100' : 'text-slate-800'}`}>
              <span className="p-1.5 bg-red-500/10 rounded-lg text-red-500">
                <Trash2 size={16} />
              </span>
              Delete Conversation?
            </h3>
            <p className={`text-xs mb-5 leading-relaxed ${theme === 'dark' ? 'text-zinc-400' : 'text-slate-500'}`}>
              Are you sure you want to delete <span className={`font-semibold ${theme === 'dark' ? 'text-zinc-300' : 'text-slate-700'}`}>"{sessionToDelete.title}"</span>? This action cannot be undone.
            </p>
            <div className="flex items-center justify-end gap-2">
              <button
                onClick={() => setSessionToDelete(null)}
                className={`px-3 py-1.5 rounded-lg border text-xs font-medium transition ${
                  theme === 'dark'
                    ? 'border-zinc-700 text-zinc-300 hover:bg-zinc-800'
                    : 'border-slate-200 text-slate-600 hover:bg-slate-50'
                }`}
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  deleteSession(sessionToDelete.id);
                  setSessionToDelete(null);
                  if (sessionToDelete.id === activeSessionId && (isLoading || isStreaming)) {
                    abort();
                    setLoading(false);
                    setShowTransparency(false);
                  }
                }}
                className="px-3 py-1.5 rounded-lg bg-red-500 hover:bg-red-600 text-white text-xs font-medium transition shadow-sm"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Message Confirmation Modal */}
      {messageToDelete && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-[100] animate-in fade-in duration-200">
          <div className={`${theme === 'dark' ? 'bg-zinc-900 border-zinc-700' : 'bg-white border-slate-200'} border rounded-2xl shadow-xl max-w-sm w-full mx-4 p-5 animate-in zoom-in-95 duration-200`}>
            <h3 className={`font-bold text-base mb-1.5 flex items-center gap-2 ${theme === 'dark' ? 'text-zinc-100' : 'text-slate-800'}`}>
              <span className="p-1.5 bg-red-500/10 rounded-lg text-red-500">
                <Trash2 size={16} />
              </span>
              Delete Question?
            </h3>
            <p className={`text-xs mb-5 leading-relaxed ${theme === 'dark' ? 'text-zinc-400' : 'text-slate-500'}`}>
              Are you sure you want to delete this question? This will also remove the associated response.
            </p>
            <div className="flex items-center justify-end gap-2">
              <button
                onClick={() => setMessageToDelete(null)}
                className={`px-3 py-1.5 rounded-lg border text-xs font-medium transition ${
                  theme === 'dark'
                    ? 'border-zinc-700 text-zinc-300 hover:bg-zinc-800'
                    : 'border-slate-200 text-slate-600 hover:bg-slate-50'
                }`}
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  deleteMessage(messageToDelete);
                  setMessageToDelete(null);
                  if (isLoading || isStreaming) {
                    abort();
                    setLoading(false);
                    setShowTransparency(false);
                  }
                }}
                className="px-3 py-1.5 rounded-lg bg-red-500 hover:bg-red-600 text-white text-xs font-medium transition shadow-sm"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

interface ColumnSchema {
  name: string
  type: string
  nullable: boolean
  null_count: number
  primary_key?: boolean
}

interface TableSchema {
  name: string
  row_count: number
  columns: ColumnSchema[]
  sample_data?: any[]
}

const generateSuggestedQueries = (tables: TableSchema[]): { q: string; icon: string }[] => {
  if (!tables || tables.length === 0) {
    return [
      { q: "Show summary of all tables", icon: "📊" },
      { q: "List all schemas in this database", icon: "🔍" },
      { q: "Show row counts for all tables", icon: "🔢" },
      { q: "Describe the database details", icon: "📋" }
    ]
  }

  const suggestions: { q: string; icon: string }[] = []
  
  // Sort tables by row count descending
  const sortedTables = [...tables].sort((a, b) => b.row_count - a.row_count)
  const primaryTable = sortedTables[0]
  
  // Find a category-like column in primary table (string column that isn't ID)
  const categoryCol = primaryTable.columns.find((c: ColumnSchema) => {
    const n = c.name.toLowerCase()
    const t = c.type.toLowerCase()
    return (t.includes('str') || t.includes('char') || t.includes('text')) && !n.includes('id') && !n.includes('url') && !n.includes('image')
  })
  
  // Find a numeric column in primary table
  const numericCol = primaryTable.columns.find((c: ColumnSchema) => {
    const n = c.name.toLowerCase()
    const t = c.type.toLowerCase()
    return n !== 'id' && (t.includes('int') || t.includes('dec') || t.includes('float') || t.includes('double') || t.includes('num') || t.includes('real'))
  })

  // Find a date column in primary table
  const dateCol = primaryTable.columns.find((c: ColumnSchema) => {
    const n = c.name.toLowerCase()
    const t = c.type.toLowerCase()
    return n.includes('date') || n.includes('time') || n.includes('created') || t.includes('date') || t.includes('time')
  })

  // Question 1: Basic row count or listing
  suggestions.push({
    q: `Show total number of records in ${primaryTable.name}`,
    icon: "🔢"
  })

  // Question 2: Grouping by category
  if (categoryCol) {
    suggestions.push({
      q: `Show breakdown of ${primaryTable.name} by ${categoryCol.name}`,
      icon: "📊"
    })
  }

  // Question 3: Numeric sum/average
  if (numericCol && categoryCol) {
    suggestions.push({
      q: `What is the total ${numericCol.name} grouped by ${categoryCol.name}?`,
      icon: "💰"
    })
  } else if (numericCol) {
    suggestions.push({
      q: `What is the average ${numericCol.name} in ${primaryTable.name}?`,
      icon: "📈"
    })
  }

  // Question 4: Date trend
  if (dateCol && numericCol) {
    suggestions.push({
      q: `What is the monthly trend of ${numericCol.name} over time?`,
      icon: "📈"
    })
  } else if (dateCol) {
    suggestions.push({
      q: `How many ${primaryTable.name} were created by day/month?`,
      icon: "📅"
    })
  }

  // Question 5: Top 10 listing
  if (numericCol) {
    suggestions.push({
      q: `Show the top 10 ${primaryTable.name} based on ${numericCol.name}`,
      icon: "🏆"
    })
  }

  // Question 6: A query on the second table if available
  if (sortedTables.length > 1) {
    const secTable = sortedTables[1]
    const secNumericCol = secTable.columns.find((c: ColumnSchema) => 
      c.name.toLowerCase() !== 'id' && 
      (c.type.toLowerCase().includes('int') || c.type.toLowerCase().includes('dec') || c.type.toLowerCase().includes('float') || c.type.toLowerCase().includes('num'))
    )
    if (secNumericCol) {
      suggestions.push({
        q: `Analyze ${secTable.name} by ${secNumericCol.name}`,
        icon: "⚖️"
      })
    } else {
      suggestions.push({
        q: `Show a preview of data from ${secTable.name}`,
        icon: "🔍"
      })
    }
  }

  // Ensure we have at least 4 suggestions, if not fallback
  while (suggestions.length < 4 && tables.length > 0) {
    const randomTable = tables[Math.floor(Math.random() * tables.length)]
    suggestions.push({
      q: `Describe the schema and columns of ${randomTable.name}`,
      icon: "📋"
    })
  }

  return suggestions.slice(0, 6)
}

interface WelcomeScreenProps {
  onQuestionClick: (q: string) => void
  theme: string
  schema: { tables: any[] } | null
  datasourceId: string
  loading: boolean
}

const WelcomeScreen: React.FC<WelcomeScreenProps> = ({ onQuestionClick, theme, schema, datasourceId, loading }) => {
  const tables = schema?.tables || []

  // Generate suggested queries using our helper
  const suggestions = React.useMemo(() => {
    return generateSuggestedQueries(tables)
  }, [tables])

  const dbName = datasourceId === 'default' ? 'SQLite Demo' : datasourceId === 'limese' ? 'Limese ClickHouse' : datasourceId
  const dbType = datasourceId === 'limese' ? 'ClickHouse' : 'SQLite/Local'

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center min-h-96 text-center py-12 animate-in fade-in duration-300">
        <div className="w-12 h-12 rounded-full border-4 border-blue-500 border-t-transparent animate-spin mb-4" />
        <p className={`text-sm ${theme === 'dark' ? 'text-zinc-400' : 'text-slate-500'}`}>
          Scanning schema of {dbName}...
        </p>
      </div>
    )
  }

  const totalRows = tables.reduce((acc, t) => acc + (t.row_count || 0), 0)

  return (
    <div className="flex flex-col items-center justify-center min-h-96 text-center py-12 animate-in fade-in duration-300">
      <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-blue-600 to-purple-600 flex items-center justify-center mb-6 shadow-lg">
        <BarChart2 size={32} className="text-white" />
      </div>
      <h2 className={`text-2xl font-bold mb-2 ${theme === 'dark' ? 'text-zinc-100' : 'text-slate-800'}`}>
        {dbName} Data Copilot
      </h2>
      <p className={`mb-2 max-w-md text-sm ${theme === 'dark' ? 'text-zinc-400' : 'text-slate-500'}`}>
        {tables.length > 0 
          ? `Ask questions about your database tables (${tables.map(t => t.name).slice(0, 3).join(', ')}${tables.length > 3 ? '...' : ''}) in plain English.`
          : 'Ask questions about your database schema in plain English.'
        }
      </p>
      <p className={`text-xs mb-8 ${theme === 'dark' ? 'text-zinc-500' : 'text-slate-400'}`}>
        Connected to {dbType} · {tables.length} tables · {totalRows.toLocaleString()} total rows
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-xl w-full">
        {suggestions.map(({ q, icon }) => (
          <button
            key={q}
            onClick={() => onQuestionClick(q)}
            className={`flex items-start gap-3 text-left p-4 rounded-xl border hover:border-blue-300 hover:bg-blue-50/10 transition shadow-sm group ${
              theme === 'dark'
                ? 'bg-zinc-900 border-zinc-800 text-zinc-300 hover:bg-zinc-800'
                : 'bg-white border-slate-200 text-slate-600 hover:bg-slate-50'
            }`}
          >
            <span className="text-lg">{icon}</span>
            <span className={`text-sm font-medium ${theme === 'dark' ? 'group-hover:text-blue-400' : 'group-hover:text-blue-700'}`}>{q}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

// Live thinking indicator with elapsed timer
const ThinkingIndicator: React.FC<{ seconds: number; theme: string }> = ({ seconds, theme }) => {
  const stages = [
    { at: 0, label: 'Understanding your question...' },
    { at: 3, label: 'Discovering schema & tables...' },
    { at: 6, label: 'Generating SQL query...' },
    { at: 10, label: 'Executing on database...' },
    { at: 13, label: 'Analysing results...' },
    { at: 16, label: 'Building chart & insights...' },
    { at: 19, label: 'Composing response...' },
  ]

  // Pick the latest stage that has been reached
  const reachedStages = stages.filter(s => seconds >= s.at)
  const current = reachedStages.length > 0 ? reachedStages[reachedStages.length - 1] : stages[0]

  return (
    <div className="flex flex-col gap-2 mb-4 animate-in fade-in duration-300">
      <div className={`flex items-center gap-2 ${theme === 'dark' ? 'text-zinc-400' : 'text-slate-400'}`}>
        <div className="w-6 h-6 rounded-full bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center">
          <div className="w-2 h-2 rounded-full bg-white animate-ping" />
        </div>
        <div className={`flex flex-col rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm gap-1.5 ${
          theme === 'dark'
            ? 'bg-zinc-900 border-zinc-800'
            : 'bg-white border border-slate-200'
        }`}>
          {/* Stage label */}
          <div className="flex items-center gap-2">
            <div className="flex gap-1">
              {[0, 1, 2].map((i) => (
                <div
                  key={i}
                  className="w-2 h-2 rounded-full bg-blue-400 animate-bounce"
                  style={{ animationDelay: `${i * 150}ms` }}
                />
              ))}
            </div>
            <span className={`text-xs ${theme === 'dark' ? 'text-zinc-300' : 'text-slate-500'}`}>{current.label}</span>
          </div>
          {/* Elapsed timer + progress bar */}
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1 text-xs font-mono text-blue-500">
              <Clock size={11} />
              <span>{seconds}s</span>
            </div>
            {/* 7-step progress bar — one pip per stage */}
            <div className="flex gap-1">
              {stages.map((s, i) => (
                <div
                  key={i}
                  className={`w-5 h-1 rounded-full transition-all duration-500 ${
                    seconds >= s.at
                      ? 'bg-blue-500'
                      : theme === 'dark' ? 'bg-zinc-700' : 'bg-slate-200'
                  }`}
                />
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
