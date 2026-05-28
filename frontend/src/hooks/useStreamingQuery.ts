import { useRef, useCallback, useState } from 'react'
import { useAuthStore } from '../store/auth'
import type { QueryResponse } from '../api/client'

export interface TransparencyStep {
  type: 'start' | 'progress' | 'complete' | 'error'
  step?: string
  progress?: number
  message?: string
  data?: {
    status?: string
    intent?: string
    rephrased_question?: string
    sql?: string
    tables?: string[]
    row_count?: number
    columns?: string[]
    insights?: string[]
    key_metrics?: Record<string, string>
    viz_type?: string
  }
  result?: QueryResponse
  error?: string
  timestamp?: number
}

export interface StreamingQueryOptions {
  onData?: (step: TransparencyStep) => void
  onComplete?: (response: QueryResponse) => void
  onError?: (error: string) => void
}

export function useStreamingQuery() {
  const { accessToken } = useAuthStore()
  const abortControllerRef = useRef<AbortController | null>(null)
  const [isStreaming, setIsStreaming] = useState(false)

  const streamQuery = useCallback(async (
    question: string,
    datasourceId: string,
    options?: StreamingQueryOptions
  ) => {
    // Cancel any existing request
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }

    abortControllerRef.current = new AbortController()
    setIsStreaming(true)

    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      }
      if (accessToken) {
        headers['Authorization'] = `Bearer ${accessToken}`
      }

      const response = await fetch('/api/v1/copilot/stream', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          question,
          datasource_id: datasourceId,
        }),
        signal: abortControllerRef.current.signal,
      })

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }

      const reader = response.body?.getReader()
      if (!reader) {
        throw new Error('No response body')
      }

      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()

        if (done) break

        buffer += decoder.decode(value, { stream: true })

        // Process complete SSE messages
        const lines = buffer.split('\n')
        buffer = lines.pop() || '' // Keep incomplete line in buffer

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6).trim()
            if (data) {
              try {
                const parsed = JSON.parse(data) as TransparencyStep

                if (parsed.type === 'start') {
                  options?.onData?.(parsed)
                } else if (parsed.type === 'progress') {
                  options?.onData?.(parsed)
                } else if (parsed.type === 'complete') {
                  const result = parsed.result as QueryResponse
                  options?.onComplete?.(result)
                } else if (parsed.type === 'error') {
                  options?.onError?.(parsed.error || 'Unknown error')
                }
              } catch (e) {
                console.error('Failed to parse SSE data:', data, e)
              }
            }
          }
        }
      }
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') {
        console.log('Request was aborted')
      } else {
        const errorMessage = error instanceof Error ? error.message : 'Unknown error'
        options?.onError?.(errorMessage)
      }
    } finally {
      setIsStreaming(false)
      abortControllerRef.current = null
    }
  }, [accessToken])

  const abort = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      setIsStreaming(false)
    }
  }, [])

  return {
    streamQuery,
    abort,
    isStreaming,
  }
}
