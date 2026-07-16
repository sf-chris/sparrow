import { useState, useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { getOnboardingStatus } from './api/client'
import Onboarding from './pages/Onboarding'
import Layout from './components/Layout'
import Search from './pages/Search'
import Downloads from './pages/Downloads'
import Library from './pages/Library'
import Settings from './pages/Settings'
import Logs from './pages/Logs'
import Home from './pages/Home'
import Show from './pages/Show'
import Activity from './pages/Activity'

export default function App() {
  const [onboarded, setOnboarded] = useState<boolean | null>(null)

  useEffect(() => {
    getOnboardingStatus()
      .then((s) => setOnboarded(s.complete))
      .catch(() => setOnboarded(false))
  }, [])

  if (onboarded === null) {
    return (
      <div className="min-h-screen bg-bg flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <BrowserRouter>
      <Routes>
        {!onboarded ? (
          <>
            <Route path="/onboarding" element={<Onboarding onComplete={() => setOnboarded(true)} />} />
            <Route path="*" element={<Navigate to="/onboarding" replace />} />
          </>
        ) : (
          <Route element={<Layout />}>
            <Route path="/" element={<Home />} />
            <Route path="/show/:tmdbId" element={<Show />} />
            <Route path="/activity" element={<Activity />} />
            <Route path="/library" element={<Library />} />
            <Route path="/settings" element={<Settings />} />
            {/* Power-user surfaces, reachable from Settings → Advanced */}
            <Route path="/search" element={<Search />} />
            <Route path="/downloads" element={<Downloads />} />
            <Route path="/logs" element={<Logs />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        )}
      </Routes>
    </BrowserRouter>
  )
}
