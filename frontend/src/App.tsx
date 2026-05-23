import React, { useState, useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { CopilotPage } from './pages/CopilotPage'
import LoginPage from './pages/LoginPage'
import AdminPage from './pages/AdminPage'
import AnalyticsPage from './pages/AnalyticsPage'
import SettingsPage from './pages/SettingsPage'
import DashboardPage from './pages/DashboardPage'
import ReportsPage from './pages/ReportsPage'
import DummyClientWebsite from './pages/DummyClientWebsite'
import { useAuthStore } from './store/auth'
import { useThemeStore } from './store/theme'
import { login } from './api/auth'

interface ProtectedRouteProps {
  children: React.ReactNode
}

function ProtectedRoute({ children }: ProtectedRouteProps) {
  const user = useAuthStore((s) => s.user)
  const accessToken = useAuthStore((s) => s.accessToken)

  if (!user || !accessToken) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}

// Background Auto-Authentication wrapper to bypass login screen
function AutoAuthWrapper({ children }: { children: React.ReactNode }) {
  const user = useAuthStore((s) => s.user)
  const accessToken = useAuthStore((s) => s.accessToken)
  const setAuth = useAuthStore((s) => s.setAuth)
  const [isAuthenticating, setIsAuthenticating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const performAutoLogin = async () => {
      // If already authenticated, do nothing
      if (user && accessToken) return

      setIsAuthenticating(true)
      try {
        // Authenticate with seeded admin credentials
        const response = await login('admin@demo.com', 'admin123')
        setAuth(response.user, response.access_token)
      } catch (err: any) {
        console.error('Auto login failed:', err)
        setError('Connection to backend services failed. Please ensure the backend server is running.')
      } finally {
        setIsAuthenticating(false)
      }
    }

    performAutoLogin()
  }, [user, accessToken, setAuth])

  if (isAuthenticating) {
    return (
      <div className="flex flex-col items-center justify-center h-screen bg-[#0B0C0E] text-[#E3E4E6] font-sans">
        <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-blue-600 to-indigo-600 flex items-center justify-center mb-6 shadow-lg shadow-blue-500/20 animate-pulse">
          <svg className="animate-spin h-7 w-7 text-white" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
          </svg>
        </div>
        <h2 className="text-base font-bold mb-1 tracking-tight">Initializing Portal</h2>
        <p className="text-xs text-zinc-400">Authenticating guest credentials...</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-screen bg-[#0B0C0E] text-[#E3E4E6] font-sans p-6 text-center">
        <div className="w-14 h-14 rounded-2xl bg-red-500/10 border border-red-500/20 flex items-center justify-center mb-6 text-red-500">
          <svg className="h-7 w-7" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
        </div>
        <h2 className="text-base font-bold text-red-400 mb-1">Backend Connection Error</h2>
        <p className="text-xs text-zinc-400 max-w-sm mb-6 leading-relaxed">{error}</p>
        <button
          onClick={() => window.location.reload()}
          className="px-4 py-2 bg-zinc-900 border border-zinc-800 hover:bg-zinc-850 rounded-xl text-xs font-semibold transition"
        >
          Retry Connection
        </button>
      </div>
    )
  }

  return <>{children}</>
}

export default function App() {
  const { theme } = useThemeStore()

  // Apply theme class to document root for CSS variables / Tailwind dark mode
  useEffect(() => {
    if (theme === 'dark') {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
  }, [theme])

  return (
    <AutoAuthWrapper>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/admin"
            element={
              <ProtectedRoute>
                <AdminPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/analytics"
            element={
              <ProtectedRoute>
                <AnalyticsPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/settings"
            element={
              <ProtectedRoute>
                <SettingsPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/dashboards"
            element={
              <ProtectedRoute>
                <DashboardPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/dashboards/:id"
            element={
              <ProtectedRoute>
                <DashboardPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/reports"
            element={
              <ProtectedRoute>
                <ReportsPage />
              </ProtectedRoute>
            }
          />

          <Route
            path="/"
            element={
              <ProtectedRoute>
                <CopilotPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/demo"
            element={
              <ProtectedRoute>
                <DummyClientWebsite />
              </ProtectedRoute>
            }
          />
          <Route path="/*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AutoAuthWrapper>
  )
}
