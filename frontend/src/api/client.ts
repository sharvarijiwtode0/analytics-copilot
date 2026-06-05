import axios from 'axios'
import { useAuthStore } from '../store/auth'

export const api = axios.create({
  baseURL: '/api/v1',
  timeout: 90000,
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true, // For refresh token cookie
})

// Request interceptor: Add access token
api.interceptors.request.use(
  (config) => {
    const { accessToken } = useAuthStore.getState()
    if (accessToken) {
      config.headers.Authorization = `Bearer ${accessToken}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

// Response interceptor: Handle token refresh
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config

    // If 401 and not already retrying
    if (error.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true

      try {
        // Try to refresh token
        const { data } = await axios.post('/api/v1/auth/refresh', {}, { withCredentials: true })

        // Update store
        useAuthStore.getState().setAuth(data.user, data.access_token)

        // Retry original request with new token
        originalRequest.headers.Authorization = `Bearer ${data.access_token}`
        return api(originalRequest)
      } catch (refreshError) {
        // Refresh failed, clear auth and redirect to login
        useAuthStore.getState().clearAuth()
        window.location.href = '/login'
        return Promise.reject(refreshError)
      }
    }

    return Promise.reject(error)
  }
)

export interface QueryResponse {
  conversation_id: string
  message_id: string
  text: string
  chart: object | null
  insights: string[]
  key_metrics: Record<string, string>
  follow_up_questions: string[]
  sql: string
  sql_explanation: string
  row_count: number
  viz_type: string | null
  columns: string[]
  rows: Record<string, unknown>[]
  total_latency_ms: number
  model_used: string
  error: string | null
}

export const sendQuery = async (
  question: string,
  datasource_id: string,
  conversation_id?: string
): Promise<QueryResponse> => {
  const { data } = await api.post('/copilot/query', {
    question,
    datasource_id,
    conversation_id,
  })
  return data
}

export const uploadFile = async (file: File): Promise<{ datasource_id: string; schema: object }> => {
  const form = new FormData()
  form.append('file', file)
  form.append('datasource_name', file.name)
  const { data } = await api.post('/copilot/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}

export const getHistory = async (conversation_id: string) => {
  const { data } = await api.get(`/copilot/history/${conversation_id}`)
  return data
}

export const getSchema = async (datasource_id: string) => {
  const { data } = await api.get(`/copilot/schema/${datasource_id}`)
  return data
}

export const getDatasources = async () => {
  const { data } = await api.get('/copilot/datasources')
  return data
}

export const executeSQL = async (
  sql: string,
  datasource_id: string
): Promise<{ columns: string[]; rows: Record<string, unknown>[]; row_count: number; execution_time_ms: number }> => {
  const { data } = await api.post('/copilot/sql/execute', { sql, datasource_id })
  return data
}

