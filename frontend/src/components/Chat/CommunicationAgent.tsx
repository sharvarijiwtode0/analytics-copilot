import React, { useState, useEffect, useRef } from 'react'
import { Check, Loader, Terminal, Play, AlertCircle, MessageSquare } from 'lucide-react'
import type { TransparencyStep } from '../../hooks/useStreamingQuery'

interface Props {
  steps: TransparencyStep[]
  isLoading: boolean
  theme?: 'light' | 'dark'
  summaryOnly?: boolean
  conversational?: boolean
}

const PIPELINE_STEPS = [
  { id: 'understand_intent', label: 'Intent', short: 'Intent Classification' },
  { id: 'discover_schema', label: 'Schema', short: 'Schema Introspection' },
  { id: 'generate_sql', label: 'SQL Gen', short: 'SQL Query Synthesis' },
  { id: 'execute_sql', label: 'Execution', short: 'Database Query Run' },
  { id: 'analyze_insights', label: 'Insights', short: 'Statistical & Insight Analysis' },
  { id: 'generate_viz_config', label: 'Visualization', short: 'Apache ECharts Configuration' },
  { id: 'compose_response', label: 'Response', short: 'Narrative Composition' },
]

const getConversationalSummary = (steps: TransparencyStep[], isLoading: boolean): string => {
  if (steps.length === 0 && isLoading) {
    return "👋 Hello there! I'm stepping in to guide your analysis. First, I'm pre-warming my memory banks and scanning our recent conversations to see if we've tackled a similar request before. This helps me get a head start and makes sure we retrieve your answers as quickly and efficiently as possible..."
  }

  const parts: string[] = []

  const findStep = (stepName: string) => steps.find(s => s.step === stepName)
  const isStepComplete = (stepName: string) => {
    const idx = steps.findIndex(s => s.step === stepName)
    if (idx === -1) return false
    if (steps[idx].data?.status === 'complete') return true
    return steps.slice(idx + 1).some(s => s.step && s.step !== stepName)
  }

  // 1. Intent Classification
  const intentStep = findStep('understand_intent')
  if (intentStep) {
    const isDone = isStepComplete('understand_intent')
    if (isDone) {
      const intentType = intentStep.data?.intent || 'query'
      parts.push(`🔍 I started by reading your question very closely. I've successfully interpreted your intent as a custom business **${intentType.replace('_', ' ')}** request, meaning you're looking for structured data trends.`)
    } else {
      parts.push("🔍 I am carefully reading your question to decode exactly what you are asking. I'm checking the words to figure out if you're looking for a timeline, a summary total, a visual chart, or a detailed breakdown of your storefront metrics...")
      return parts.join(" ")
    }
  }

  // 2. Schema Selection
  const schemaStep = findStep('discover_schema')
  if (schemaStep) {
    const isDone = isStepComplete('discover_schema')
    if (isDone) {
      const tablesStr = schemaStep.data?.tables?.join(', ') || 'sales'
      parts.push(`📂 Next, I opened our business data registry and scanned the indexes. I found the exact tables containing your metrics (referencing tables: **${tablesStr}**). This makes sure we only pull authentic, high-precision datasets and ignore any cancelled orders.`)
    } else {
      parts.push("📂 I am opening our brand's secure data registry to find exactly where this information lives. I'm scanning our table dictionary to pick the correct fields, ensuring we ignore unrelated details and pull only clean, high-precision records...")
      return parts.join(" ")
    }
  }

  // 3. SQL Gen
  const sqlStep = findStep('generate_sql')
  if (sqlStep) {
    const isDone = isStepComplete('generate_sql')
    if (isDone) {
      parts.push("⚙️ With the correct fields mapped, I drafted a highly optimized data retrieval instructions plan. This plan acts as a blueprint, telling our systems exactly how to query the tables, aggregate dates, and filter out cancelled or returned products.")
    } else {
      parts.push("⚙️ Now, I am crafting a custom data retrieval plan. I am translating our goals into precise instructions, building the exact logic to query the registry, aggregate dates, exclude cancellations, and align with the correct platforms...")
      return parts.join(" ")
    }
  }

  // 4. Execution
  const execStep = findStep('execute_sql')
  if (execStep) {
    const isDone = isStepComplete('execute_sql')
    if (isDone) {
      const count = execStep.data?.row_count || 0
      parts.push(`✅ I then opened a secure connection to our database servers, executed the retrieval, and successfully brought back **${count}** raw data records for analysis.`)
    } else {
      parts.push("🔋 I am connecting to our secure database servers to execute our query plan. I am initiating the query and waiting for the data rows to stream back safely...")
      return parts.join(" ")
    }
  }

  // 5. Analysis
  const analysisStep = findStep('analyze_insights')
  if (analysisStep) {
    const isDone = isStepComplete('analyze_insights')
    if (isDone) {
      const insights = analysisStep.data?.insights || []
      parts.push(`📊 After pulling the numbers, I ran statistical checks to spot performance patterns. I've distilled **${insights.length}** critical takeaways, highlighting positive milestones, potential anomalies, and platform performance.`)
    } else {
      parts.push("📊 I have the raw numbers! Now, I am running deep statistical summaries to highlight key metrics, identify unexpected anomalies, compare performance across platforms, and extract the most valuable takeaways for you...")
      return parts.join(" ")
    }
  }

  // 6. Viz Config
  const vizStep = findStep('generate_viz_config')
  if (vizStep) {
    const isDone = isStepComplete('generate_viz_config')
    if (isDone) {
      const chart = vizStep.data?.viz_type || 'table'
      parts.push(`🎨 To make the findings easy to digest, I customized an interactive **${chart}** layout, choosing the best color palette and structure to present your trends visually.`)
    } else {
      parts.push("🎨 Next, I am designing a beautiful, interactive visual chart. I'm choosing the perfect layout (like a trend line, a bar breakdown, or a structured comparison table) and customizing the color palettes so it looks clean and professional...")
      return parts.join(" ")
    }
  }

  // 7. Responder
  const respStep = findStep('compose_response')
  if (respStep) {
    const isDone = isStepComplete('compose_response') || steps.some(s => s.type === 'complete')
    if (isDone) {
      parts.push("🏁 Lastly, I have organized the metrics, interactive charts, and insights into a cohesive, conversational summary in plain English. Your final analysis is fully rendered and ready below!")
    } else {
      parts.push("💬 We are almost there! I am weaving the insights, visual charts, and key metrics into a clean, easy-to-read narrative and brainstorming some smart follow-up suggestions to help you drill deeper...")
      return parts.join(" ")
    }
  }

  return parts.join(" ")
}

interface AgentMessage {
  id: string
  text: string
  status: 'pending' | 'success' | 'error'
}

export const CommunicationAgent: React.FC<Props> = ({ steps, isLoading, theme = 'light', summaryOnly = false, conversational = false }) => {
  const [showSql, setShowSql] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to the bottom of the messenger log as new messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [steps, isLoading])

  // If there are no steps and we are not loading, don't show the agent
  if (steps.length === 0 && !isLoading) return null

  // Find latest step details
  const latestStep = steps[steps.length - 1]
  const currentStepId = latestStep?.step
  const isComplete = latestStep?.type === 'complete' || steps.some(s => s.type === 'complete')
  const hasError = latestStep?.type === 'error' || steps.some(s => s.type === 'error')

  // Find index of the latest step in the pipeline
  const currentStepIndex = PIPELINE_STEPS.findIndex(s => s.id === currentStepId)

  // Extract variables for detailed metrics display
  const tablesUsed = steps.find(s => s.step === 'discover_schema')?.data?.tables
  const sqlQuery = steps.find(s => s.step === 'generate_sql')?.data?.sql
  const rowCount = steps.find(s => s.step === 'execute_sql')?.data?.row_count
  const insightsCount = steps.find(s => s.step === 'analyze_insights')?.data?.insights?.length
  const chartType = steps.find(s => s.step === 'generate_viz_config')?.data?.viz_type

  // Construct conversational messages from steps, strictly without tool names
  const getAgentMessages = (): AgentMessage[] => {
    const list: AgentMessage[] = []

    if (steps.length === 0 && isLoading) {
      list.push({
        id: 'init',
        text: "👋 Hello! I am stepping in to guide your analysis. Spinning up the secure pipeline nodes and pre-warming memory caches...",
        status: 'pending',
      })
      return list
    }

    const findStep = (stepName: string) => steps.find(s => s.step === stepName)
    const isStepComplete = (stepName: string) => {
      const idx = steps.findIndex(s => s.step === stepName)
      if (idx === -1) return false
      if (steps[idx].data?.status === 'complete') return true
      return steps.slice(idx + 1).some(s => s.step && s.step !== stepName)
    }

    // 1. Intent
    const intentStep = findStep('understand_intent')
    if (intentStep) {
      const isDone = isStepComplete('understand_intent')
      list.push({
        id: 'intent_start',
        text: "🔍 First, I am carefully reading your question to understand the core business intent, key metrics, and dates you are interested in...",
        status: isDone ? 'success' : 'pending',
      })
      if (isDone) {
        const intentType = intentStep.data?.intent || 'query'
        list.push({
          id: 'intent_end',
          text: `✨ Question interpreted! I've classified your request as a business ${intentType.replace('_', ' ')} and am preparing the analysis flow.`,
          status: 'success',
        })
      }
    }

    // 2. Schema Selection
    const schemaStep = findStep('discover_schema')
    if (schemaStep) {
      const isDone = isStepComplete('discover_schema')
      list.push({
        id: 'schema_start',
        text: "📂 Now, I am cross-referencing our business registry to identify exactly which tables and fields contain the relevant metrics...",
        status: isDone ? 'success' : 'pending',
      })
      if (isDone) {
        const tablesStr = schemaStep.data?.tables?.join(', ') || 'sales'
        list.push({
          id: 'schema_end',
          text: `🎯 Found matching records! Located the appropriate fields within the system: ${tablesStr}.`,
          status: 'success',
        })
      }
    }

    // 3. SQL Gen
    const sqlStep = findStep('generate_sql')
    if (sqlStep) {
      const isDone = isStepComplete('generate_sql')
      list.push({
        id: 'sql_start',
        text: "⚙️ I am compiling a precise data retrieval plan, applying business exclusions for cancelled/returned orders and timezone rules...",
        status: isDone ? 'success' : 'pending',
      })
      if (isDone) {
        list.push({
          id: 'sql_end',
          text: "⚡ Retrieval plan compiled and optimized for high-speed performance!",
          status: 'success',
        })
      }
    }

    // 4. Execution
    const execStep = findStep('execute_sql')
    if (execStep) {
      const isDone = isStepComplete('execute_sql')
      list.push({
        id: 'exec_start',
        text: "🔋 Contacting the secure data servers to securely fetch your requested records...",
        status: isDone ? 'success' : 'pending',
      })
      if (isDone) {
        const count = execStep.data?.row_count || 0
        list.push({
          id: 'exec_end',
          text: `✅ Data pull completed successfully! Loaded ${count} matching records for analysis.`,
          status: 'success',
        })
      }
    }

    // 5. Analysis
    const analysisStep = findStep('analyze_insights')
    if (analysisStep) {
      const isDone = isStepComplete('analyze_insights')
      list.push({
        id: 'analysis_start',
        text: "📊 Analyzing the numbers! I am running statistical summaries to highlight key metrics, performance trends, and anomalies...",
        status: isDone ? 'success' : 'pending',
      })
      if (isDone) {
        const insights = analysisStep.data?.insights || []
        const insightsStr = insights.length > 0 ? ` Isolated ${insights.length} key takeaways.` : ''
        list.push({
          id: 'analysis_end',
          text: `📈 Statistical analysis finalized!${insightsStr}`,
          status: 'success',
        })
      }
    }

    // 6. Viz Config
    const vizStep = findStep('generate_viz_config')
    if (vizStep) {
      const isDone = isStepComplete('generate_viz_config')
      list.push({
        id: 'viz_start',
        text: "🎨 Designing an interactive, beautiful visual chart to represent your sales performance perfectly...",
        status: isDone ? 'success' : 'pending',
      })
      if (isDone) {
        const chart = vizStep.data?.viz_type || 'table'
        list.push({
          id: 'viz_end',
          text: `🖌️ Visual layout design completed! Rendered a customized ${chart} layout.`,
          status: 'success',
        })
      }
    }

    // 7. Responder
    const respStep = findStep('compose_response')
    if (respStep) {
      const isDone = isStepComplete('compose_response') || isComplete
      list.push({
        id: 'resp_start',
        text: "💬 Lastly, I am weaving the insights, visual charts, and metrics into a cohesive narrative and predicting helpful follow-ups...",
        status: isDone ? 'success' : 'pending',
      })
      if (isDone || isComplete) {
        list.push({
          id: 'resp_end',
          text: "🏁 Success! Your comprehensive analysis is generated and rendered below. Let me know if you would like to drill down further!",
          status: 'success',
        })
      }
    }

    if (hasError) {
      list.push({
        id: 'error_occurred',
        text: "⚠️ An issue occurred during data analysis. Stopping the process to prevent any issues.",
        status: 'error',
      })
    }

    return list
  }

  const agentMessages = getAgentMessages()

  if (conversational) {
    const summaryText = getConversationalSummary(steps, isLoading)
    return (
      <div className={`p-4 rounded-2xl border transition-all duration-300 backdrop-blur-md shadow-sm leading-relaxed text-sm ${
        theme === 'dark'
          ? 'bg-zinc-900/80 border-zinc-800 text-zinc-100 shadow-zinc-950/20'
          : 'bg-white/80 border-slate-200/80 text-slate-700 shadow-slate-100/50'
      }`}>
        <div className="flex items-start gap-2.5">
          <div className="flex-1">
            <span className="font-medium inline-block text-slate-800 dark:text-zinc-200">
              {summaryText}
            </span>
            {isLoading && (
              <span className="inline-flex gap-1 ml-2 items-center">
                <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '0ms' }} />
                <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '150ms' }} />
                <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '300ms' }} />
              </span>
            )}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className={`mb-3 mx-4 md:mx-0 p-3.5 rounded-2xl border transition-all duration-300 backdrop-blur-md shadow-sm ${
      theme === 'dark'
        ? 'bg-zinc-900/80 border-zinc-800 text-zinc-100'
        : 'bg-white/80 border-slate-200/80 text-slate-700'
    }`}>
      {/* Header with pulsing indicator */}
      <div className="flex items-center justify-between mb-3 border-b pb-2 border-zinc-800/30 dark:border-zinc-850">
        <div className="flex items-center gap-2">
          <div className="relative flex h-2 w-2">
            {isLoading && !hasError && !isComplete && (
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
            )}
            <span className={`relative inline-flex rounded-full h-2 w-2 ${
              hasError ? 'bg-red-500' : isComplete ? 'bg-emerald-500' : 'bg-blue-500'
            }`}></span>
          </div>
          <span className={`text-xs font-bold uppercase tracking-wider flex items-center gap-1.5 ${
            theme === 'dark' ? 'text-zinc-400' : 'text-slate-500'
          }`}>
            <MessageSquare size={13} className="text-blue-500" />
            Communication Agent
          </span>
        </div>

        {/* Latency / Status tag */}
        <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
          isComplete 
            ? 'bg-emerald-500/10 text-emerald-500'
            : hasError 
              ? 'bg-red-500/10 text-red-500'
              : 'bg-blue-500/10 text-blue-500 animate-pulse'
        }`}>
          {isComplete ? 'Analysis Ready' : hasError ? 'Interrupted' : 'Thinking...'}
        </span>
      </div>

      {/* Visual Pipeline Dots */}
      <div className="flex items-center justify-between gap-1 mb-3.5 px-1 relative">
        <div className={`absolute top-3 left-6 right-6 h-[2px] -z-10 ${
          theme === 'dark' ? 'bg-zinc-800' : 'bg-slate-100'
        }`} />

        {PIPELINE_STEPS.map((step, idx) => {
          const isFinished = steps.some(s => s.step === step.id && s.data?.status === 'complete') || idx < currentStepIndex || isComplete
          const isActive = currentStepId === step.id && !isComplete
          
          return (
            <div key={step.id} className="flex flex-col items-center group relative flex-1">
              <div className={`w-6.5 h-6.5 rounded-full flex items-center justify-center border transition-all duration-300 ${
                isFinished
                  ? 'bg-emerald-500 border-emerald-500 text-white'
                  : isActive
                    ? 'bg-blue-600 border-blue-600 text-white ring-4 ring-blue-500/20 shadow-md scale-110'
                    : theme === 'dark'
                      ? 'bg-zinc-950 border-zinc-800 text-zinc-600'
                      : 'bg-white border-slate-200 text-slate-400'
              }`}>
                {isFinished ? (
                  <Check size={12} strokeWidth={3} />
                ) : isActive ? (
                  <Loader size={12} className="animate-spin" />
                ) : (
                  <span className="text-[10px] font-bold">{idx + 1}</span>
                )}
              </div>
              <span className={`text-[9px] mt-1.5 font-medium hidden md:block transition-colors duration-200 ${
                isFinished
                  ? theme === 'dark' ? 'text-zinc-300' : 'text-slate-600'
                  : isActive
                    ? 'text-blue-500 font-semibold'
                    : 'text-zinc-400'
              }`}>
                {step.label}
              </span>

              {/* Tooltip on hover */}
              <div className="absolute bottom-8 scale-0 group-hover:scale-100 transition-transform duration-200 bg-zinc-900 text-white text-[10px] py-1 px-2 rounded shadow-lg whitespace-nowrap z-50 pointer-events-none">
                {step.short}
              </div>
            </div>
          )
        })}
      </div>

      {/* Persistent Messenger Conversation Log */}
      {!summaryOnly && (
        <div className={`text-xs p-2.5 rounded-xl border flex flex-col gap-2.5 max-h-48 overflow-y-auto ${
          theme === 'dark'
            ? 'bg-zinc-950/60 border-zinc-800 text-zinc-300'
            : 'bg-slate-50/80 border-slate-100 text-slate-600'
        }`}>
          {agentMessages.map((msg, index) => (
            <div
              key={msg.id}
              className={`flex items-start gap-2.5 animate-in fade-in slide-in-from-bottom-2 duration-300`}
              style={{ animationDelay: `${index * 50}ms` }}
            >
              {/* Status indicator icon for bubble */}
              <div className="mt-0.5 flex-shrink-0">
                {msg.status === 'success' ? (
                  <div className="w-4 h-4 rounded-full bg-emerald-500/10 flex items-center justify-center text-emerald-500">
                    <Check size={10} strokeWidth={3} />
                  </div>
                ) : msg.status === 'error' ? (
                  <div className="w-4 h-4 rounded-full bg-red-500/10 flex items-center justify-center text-red-500">
                    <AlertCircle size={10} />
                  </div>
                ) : (
                  <div className="w-4 h-4 rounded-full bg-blue-500/10 flex items-center justify-center text-blue-500 animate-spin">
                    <Loader size={10} />
                  </div>
                )}
              </div>

              {/* Message Text bubble */}
              <div className="flex-1 text-[11px] leading-relaxed">
                <p className="font-medium text-zinc-800 dark:text-zinc-200">
                  {msg.text}
                </p>
              </div>
            </div>
          ))}
          {/* Anchor for auto-scroll */}
          <div ref={messagesEndRef} />
        </div>
      )}

      {/* Pipeline Artifacts Display (Metadata showing what was discovered/queried) */}
      {(tablesUsed || sqlQuery || rowCount !== undefined || insightsCount !== undefined) && (
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5 text-[10px] text-zinc-400 border-t pt-2 border-zinc-800/20 dark:border-zinc-850">
          {tablesUsed && tablesUsed.length > 0 && (
            <span className={`px-2 py-0.5 rounded border flex items-center gap-1 ${
              theme === 'dark' ? 'border-zinc-800 bg-zinc-950/30' : 'border-slate-100 bg-white'
            }`}>
              📁 Fields Discovered: <span className="font-semibold text-blue-500">{tablesUsed.join(', ')}</span>
            </span>
          )}

          {rowCount !== undefined && (
            <span className={`px-2 py-0.5 rounded border flex items-center gap-1 ${
              theme === 'dark' ? 'border-zinc-800 bg-zinc-950/30' : 'border-slate-100 bg-white'
            }`}>
              📊 Retrieved: <span className="font-semibold text-indigo-500">{rowCount} records</span>
            </span>
          )}

          {insightsCount !== undefined && (
            <span className={`px-2 py-0.5 rounded border flex items-center gap-1 ${
              theme === 'dark' ? 'border-zinc-800 bg-zinc-950/30' : 'border-slate-100 bg-white'
            }`}>
              💡 Findings: <span className="font-semibold text-amber-500">{insightsCount} insights</span>
            </span>
          )}

          {chartType && (
            <span className={`px-2 py-0.5 rounded border flex items-center gap-1 ${
              theme === 'dark' ? 'border-zinc-800 bg-zinc-950/30' : 'border-slate-100 bg-white'
            }`}>
              📈 Visualization: <span className="font-semibold text-purple-500 capitalize">{chartType}</span>
            </span>
          )}

          {sqlQuery && (
            <button
              onClick={() => setShowSql(!showSql)}
              className={`px-2 py-0.5 rounded border flex items-center gap-1 hover:text-zinc-100 hover:border-zinc-700 transition ${
                theme === 'dark' ? 'border-zinc-800 bg-zinc-950/30' : 'border-slate-100 bg-white hover:bg-slate-50'
              }`}
            >
              <Terminal size={10} />
              <span>{showSql ? 'Hide Retrieval Plan' : 'View Retrieval Plan'}</span>
            </button>
          )}

          {showSql && sqlQuery && (
            <pre className={`w-full p-2 mt-2 rounded border font-mono text-[9px] overflow-x-auto text-left leading-relaxed ${
              theme === 'dark'
                ? 'bg-zinc-950 border-zinc-850 text-blue-400'
                : 'bg-slate-100 border-slate-200 text-blue-700'
            }`}>
              {sqlQuery}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}
