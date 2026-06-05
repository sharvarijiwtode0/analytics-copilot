import React, { useState, useEffect, useRef } from 'react'
import { MessageSquare, X, Plus, RotateCw, Database, Clock, RefreshCw, BarChart2, MessageSquarePlus, History, Trash2, ArrowLeft } from 'lucide-react'
import { useChatStore, ChatSession } from '../../store/chat'
import { useThemeStore } from '../../store/theme'
import { useAuthStore } from '../../store/auth'
import { getDatasources, getSchema } from '../../api/client'
import { ChatMessageComponent } from './ChatMessage'
import { ChatInput } from './ChatInput'
import TransparencyPanel from '../TransparencyPanel'
import DisambiguationModal from '../DisambiguationModal'
import { useStreamingQuery, type TransparencyStep } from '../../hooks/useStreamingQuery'

export const CopilotWidget: React.FC = () => {
  const [isOpen, setIsOpen] = useState(false)
  const [viewMode, setViewMode] = useState<'chat' | 'history'>('chat')
  const {
    sessions, activeSessionId, isLoading, datasourceId,
    addUserMessage, addAssistantMessage, setLoading, startNewSession,
    setConversationId, setUploadedFile, setDatasourceId, loadSession, purgeExpiredSessions,
    deleteSession, deleteMessage
  } = useChatStore()

  const { theme } = useThemeStore()
  const { user } = useAuthStore()

  const [datasources, setDatasources] = useState<any[]>([])
  const [schema, setSchema] = useState<any>(null)
  const [schemaLoading, setSchemaLoading] = useState(false)
  const [chatInputValue, setChatInputValue] = useState('')
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [transparencySteps, setTransparencySteps] = useState<TransparencyStep[]>([])
  const [showTransparency, setShowTransparency] = useState(false)
  const [disambiguation, setDisambiguation] = useState<{
    keyword: string
    options: string[]
    originalQuestion: string
  } | null>(null)

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const { streamQuery, isStreaming, abort } = useStreamingQuery()

  // Load registered datasources
  useEffect(() => {
    const fetchDatasources = async () => {
      try {
        const list = await getDatasources()
        setDatasources(list)
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

  // Load schema on datasource change
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

  // Scroll to bottom when messages or loading state changes
  useEffect(() => {
    if (isOpen && viewMode === 'chat') {
      setTimeout(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
      }, 100)
    }
  }, [isOpen, isLoading, viewMode])

  const activeSession = sessions.find(s => s.id === activeSessionId)
  const messages = activeSession?.messages || []
  const conversationId = activeSession?.conversationId || null

  useEffect(() => {
    if (isOpen && viewMode === 'chat') {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages, viewMode])

  // Handle elapsed timer
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

    const targetSessionId = useChatStore.getState().activeSessionId || undefined
    setLoading(true)
    setTransparencySteps([])
    setShowTransparency(true)

    streamQuery(question, datasourceId, {
      onData: (step) => {
        setTransparencySteps(prev => [...prev, step])
      },
      onComplete: (response) => {
        setLoading(false)
        setShowTransparency(false)

        if (response.error?.startsWith('DISAMBIGUATION_NEEDED:')) {
          const errorParts = response.error.split(':')
          const keyword = errorParts[1]
          const options = response.follow_up_questions || []

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

  const handleClose = () => {
    setIsOpen(false)
    setViewMode('chat')
    if (isLoading || isStreaming) {
      abort()
      setLoading(false)
      setShowTransparency(false)
    }
  }

  // Predefined mock questions for default / empty state
  const mockSuggestions = [
    { q: "Show total sales overview", icon: "💰" },
    { q: "What is average order value?", icon: "📈" },
    { q: "Breakdown of sales by product brand", icon: "💄" },
    { q: "Show monthly sales trend", icon: "📅" }
  ]

  // Get dynamic name of database configured on client website
  const getDynamicDbName = () => {
    const activeDs = datasources.find(ds => ds.id === datasourceId)
    if (!activeDs) return 'Client Database'
    if (activeDs.id === 'default') return 'SQLite Demo'
    if (activeDs.id === 'limese') return 'Limese ClickHouse'
    
    // Capitalize id for other dynamic clients (Solar Energy, Ed Tech, etc.)
    return activeDs.id.charAt(0).toUpperCase() + activeDs.id.slice(1)
  }

  return (
    <>
      {/* Floating launcher bubble button */}
      {!isOpen && (
        <button
          onClick={() => setIsOpen(true)}
          className="fixed bottom-6 right-6 z-40 w-16 h-16 rounded-full flex items-center justify-center bg-gradient-to-tr from-blue-600 to-indigo-600 text-white shadow-xl hover:scale-105 active:scale-95 transition-all duration-300 cursor-pointer shadow-blue-500/30 group"
          title="Open Analytics Copilot"
        >
          <div className="absolute inset-0 rounded-full bg-blue-500 opacity-20 animate-ping group-hover:animate-none group-hover:scale-105 transition-all" />
          <MessageSquare className="w-7 h-7 relative z-10 transition-transform group-hover:rotate-6" />
        </button>
      )}

      {/* Side drawer chatbot panel */}
      {isOpen && (
        <>
          {/* Backdrop overlay */}
          <div
            onClick={handleClose}
            className="fixed inset-0 bg-black/20 dark:bg-black/40 backdrop-blur-xs z-40 transition-opacity animate-in fade-in duration-200"
          />

          {/* Slider Panel Container */}
          <div
            className={`fixed top-0 right-0 h-full w-[440px] max-w-[100vw] flex flex-col bg-white dark:bg-zinc-950 border-l border-slate-200 dark:border-zinc-800 shadow-2xl z-50 transition-transform duration-300 ease-out animate-in slide-in-from-right`}
          >
            {/* Header */}
            <header className="flex items-center justify-between px-4 py-3 border-b border-slate-100 dark:border-zinc-900 bg-white dark:bg-zinc-950 select-none">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-600 to-indigo-600 flex items-center justify-center shadow-md">
                  <BarChart2 className="w-4.5 h-4.5 text-white" />
                </div>
                <div>
                  <h3 className="font-semibold text-xs text-slate-800 dark:text-zinc-100 flex items-center gap-1.5">
                    Data Analytics Copilot
                    <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
                  </h3>
                  <div className="flex items-center gap-1.5 mt-0.5 text-[10px] text-zinc-500 font-medium leading-none">
                    <Database size={10} className="text-zinc-400" />
                    <span>{getDynamicDbName()}</span>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-1">
                {/* View Mode Toggle (History / Chat) */}
                <button
                  onClick={() => setViewMode(prev => prev === 'chat' ? 'history' : 'chat')}
                  className={`p-1.5 rounded-lg transition-colors ${
                    viewMode === 'history'
                      ? 'text-blue-600 dark:text-blue-400 bg-blue-500/10'
                      : 'text-zinc-400 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-slate-100 dark:hover:bg-zinc-900'
                  }`}
                  title={viewMode === 'history' ? "Back to chat" : "View saved history"}
                >
                  <History size={16} />
                </button>
                <button
                  onClick={() => {
                    startNewSession()
                    setViewMode('chat')
                  }}
                  className="p-1.5 text-zinc-400 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-slate-100 dark:hover:bg-zinc-900 rounded-lg transition-colors"
                  title="New conversation"
                >
                  <MessageSquarePlus size={16} />
                </button>
                <button
                  onClick={handleClose}
                  className="p-1.5 text-zinc-400 hover:text-red-500 hover:bg-slate-100 dark:hover:bg-zinc-900 rounded-lg transition-colors"
                  title="Close Copilot"
                >
                  <X size={16} />
                </button>
              </div>
            </header>

            {/* Content Switcher */}
            {viewMode === 'history' ? (
              /* History Panel View */
              <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3 dark:bg-zinc-950/40 select-none animate-in fade-in duration-200">
                <div className="flex items-center justify-between mb-4">
                  <span className="text-xs font-bold text-slate-800 dark:text-zinc-200">Saved Conversations</span>
                  <button
                    onClick={() => {
                      startNewSession()
                      setViewMode('chat')
                    }}
                    className="flex items-center gap-1 text-[11px] font-bold text-blue-600 dark:text-blue-400 hover:underline"
                  >
                    <Plus size={12} />
                    <span>New Chat</span>
                  </button>
                </div>

                {sessions.length === 0 ? (
                  <div className="flex flex-col items-center justify-center py-16 text-center">
                    <div className="w-12 h-12 rounded-xl bg-slate-50 dark:bg-zinc-900 flex items-center justify-center mb-3 text-zinc-400 border border-slate-100 dark:border-zinc-800">
                      <History size={20} />
                    </div>
                    <p className="text-xs text-zinc-400">No saved sessions yet.</p>
                  </div>
                ) : (
                  <div className="space-y-2.5">
                    {sessions.map((session) => (
                      <div
                        key={session.id}
                        className={`flex items-center justify-between p-3 rounded-xl border transition-all ${
                          session.id === activeSessionId
                            ? 'border-blue-500/50 bg-blue-500/5 dark:bg-blue-500/10'
                            : 'border-slate-150 dark:border-zinc-850 bg-white dark:bg-zinc-900/60 hover:border-slate-350 dark:hover:border-zinc-750'
                        }`}
                      >
                        <button
                          onClick={() => {
                            loadSession(session.id)
                            setViewMode('chat')
                          }}
                          className="flex-1 text-left min-w-0 mr-3"
                        >
                          <p className="text-xs font-semibold text-slate-700 dark:text-zinc-200 truncate">
                            {session.title || 'Untitled Session'}
                          </p>
                          <span className="text-[10px] text-zinc-400 font-medium">
                            {new Date(session.updatedAt || session.initiatedAt).toLocaleDateString(undefined, {
                              month: 'short',
                              day: 'numeric',
                              hour: '2-digit',
                              minute: '2-digit'
                            })}
                          </span>
                        </button>
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            if (window.confirm(`Delete conversation "${session.title}"?`)) {
                              deleteSession(session.id)
                            }
                          }}
                          className="p-1.5 text-zinc-400 hover:text-red-500 hover:bg-red-50/10 dark:hover:bg-red-500/10 rounded-lg transition-colors flex-shrink-0"
                          title="Delete conversation"
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              /* Chat panel view */
              <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4 dark:bg-zinc-950/40">
                {messages.length === 0 ? (
                  <div className="flex flex-col items-center justify-center py-8 text-center animate-in fade-in duration-300">
                    <div className="w-12 h-12 rounded-xl bg-blue-500/10 text-blue-500 flex items-center justify-center mb-4">
                      <MessageSquare size={22} className="text-blue-600 dark:text-blue-400" />
                    </div>
                    <h4 className="font-semibold text-sm text-slate-800 dark:text-zinc-200 mb-1">
                      Ask anything about client data
                    </h4>
                    <p className="text-xs text-zinc-400 max-w-[260px] mb-6">
                      Ask questions in plain English, and I will generate SQL, run queries, and build interactive charts.
                    </p>
                    <div className="space-y-2 w-full max-w-[320px]">
                      {mockSuggestions.map((s, idx) => (
                        <button
                          key={idx}
                          onClick={() => handleSend(s.q)}
                          className="w-full flex items-center gap-2.5 px-3 py-2 text-left text-xs font-medium text-slate-700 dark:text-zinc-300 border border-slate-150 dark:border-zinc-800 rounded-xl bg-white dark:bg-zinc-900 hover:border-blue-400 dark:hover:border-blue-500 hover:bg-blue-50/10 transition shadow-xs group"
                        >
                          <span className="text-sm">{s.icon}</span>
                          <span className="truncate group-hover:text-blue-600 dark:group-hover:text-blue-400">{s.q}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                ) : (
                  <>
                    {messages.map((msg) => (
                      <ChatMessageComponent
                        key={msg.id}
                        message={msg}
                        onFollowUp={handleSend}
                        onEdit={(id, content) => setChatInputValue(content)}
                        onDelete={(id) => deleteMessage(id)}
                        theme={theme === 'dark' ? 'dark' : 'light'}
                      />
                    ))}
                    {isLoading && <ThinkingIndicator seconds={elapsedSeconds} theme={theme} />}
                  </>
                )}
                <div ref={messagesEndRef} />
              </div>
            )}

            {/* Inline Transparency Panel (Only visible in chat mode) */}
            {viewMode === 'chat' && showTransparency && transparencySteps.length > 0 && (
              <div className="px-3 pb-2 border-t border-slate-100 dark:border-zinc-900 pt-2 bg-slate-50/30 dark:bg-zinc-950/20">
                <TransparencyPanel
                  steps={transparencySteps}
                  isComplete={!isLoading && !isStreaming}
                  sql={transparencySteps.find(s => s.data?.sql)?.data?.sql as string}
                  tables={((transparencySteps
                    .find(s => s.step === 'discover_schema' || s.step === 'generate_sql')
                    ?.data as { tables?: string[] } | undefined)?.tables) || []}
                  columns={transparencySteps.find(s => s.data?.columns)?.data?.columns as string[]}
                />
              </div>
            )}

            {/* Disambiguation Container (Only visible in chat mode) */}
            {viewMode === 'chat' && disambiguation && (
              <div className="px-4 pb-2 pt-2 border-t border-slate-100 dark:border-zinc-900">
                <DisambiguationModal
                  keyword={disambiguation.keyword}
                  options={disambiguation.options}
                  onSelect={handleDisambiguationSelect}
                  onDismiss={() => setDisambiguation(null)}
                />
              </div>
            )}

            {/* Bottom input area / back button */}
            <div className="p-3 border-t border-slate-100 dark:border-zinc-900 bg-white dark:bg-zinc-950">
              {viewMode === 'history' ? (
                <button
                  onClick={() => setViewMode('chat')}
                  className="w-full py-2.5 bg-slate-100 hover:bg-slate-200 dark:bg-zinc-900 dark:hover:bg-zinc-800 text-slate-700 dark:text-zinc-200 rounded-xl text-xs font-semibold transition"
                >
                  Back to Active Chat
                </button>
              ) : (
                <ChatInput
                  value={chatInputValue}
                  onChange={setChatInputValue}
                  onSend={handleSend}
                  onStop={() => {
                    abort()
                    setLoading(false)
                    setShowTransparency(false)
                  }}
                  isLoading={isLoading}
                  theme={theme === 'dark' ? 'dark' : 'light'}
                />
              )}
            </div>
          </div>
        </>
      )}
    </>
  )
}

// Live Thinking Stage Indicator inside widget
const ThinkingIndicator: React.FC<{ seconds: number; theme: string }> = ({ seconds, theme }) => {
  const stages = [
    { at: 0, label: 'Understanding query...' },
    { at: 3, label: 'Discovering tables...' },
    { at: 6, label: 'Generating SQL...' },
    { at: 10, label: 'Executing on database...' },
    { at: 13, label: 'Analyzing details...' },
    { at: 16, label: 'Creating chart...' },
    { at: 19, label: 'Writing response...' },
  ]

  const reached = stages.filter(s => seconds >= s.at)
  const current = reached.length > 0 ? reached[reached.length - 1] : stages[0]

  return (
    <div className="flex flex-col gap-2 mb-4 animate-in fade-in duration-300">
      <div className="flex items-center gap-2">
        <div className="w-5 h-5 rounded-full bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center">
          <div className="w-1.5 h-1.5 rounded-full bg-white animate-ping" />
        </div>
        <div className={`flex flex-col rounded-xl rounded-tl-sm px-3.5 py-2.5 shadow-sm gap-1.5 ${
          theme === 'dark'
            ? 'bg-zinc-900 border border-zinc-800 text-zinc-300'
            : 'bg-slate-50 border border-slate-200 text-slate-700'
        }`}>
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-medium">{current.label}</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-0.5 text-[10px] font-mono text-blue-500">
              <Clock size={10} />
              <span>{seconds}s</span>
            </div>
            <div className="flex gap-0.5">
              {stages.map((s, i) => (
                <div
                  key={i}
                  className={`w-3 h-0.5 rounded-full transition-all duration-500 ${
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
