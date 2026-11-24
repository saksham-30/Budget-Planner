import { useCallback, useEffect, useState } from 'react'
import Header from './components/Header.jsx'
import CurrencyAgentView from './components/CurrencyAgentView.jsx'
import TripPlanningPanel from './components/TripPlanningPanel.jsx'
import HomePage from './components/HomePage.jsx'
import ExplorePage from './components/ExplorePage.jsx'
import SavedTripsView from './components/SavedTripsView.jsx'
import SavedPlacesView from './components/SavedPlacesView.jsx'
import ItineraryBuilder from './components/ItineraryBuilder.jsx'
import { csrfFetch } from './auth.js'
import { getCurrentMonth } from './data/seasonalUtils.js'

const API_BASE = '/api'

const VIEWS = [
  { id: 'home',          label: 'Home',             hint: 'Discover destinations' },
  { id: 'explore',       label: 'Explore',          hint: 'Browse all destinations' },
  { id: 'itinerary',     label: 'Itinerary',         hint: 'AI day-by-day planner' },
  { id: 'planner',       label: 'Trip planner',      hint: 'Multi-agent planning' },
  { id: 'currency',      label: 'Budget check',      hint: 'Travel cost feasibility' },
  { id: 'saved-trips',   label: 'My trips',          hint: 'Your saved itineraries' },
  { id: 'saved-places',  label: 'Wishlist',          hint: 'Your favorite places' },
]

const OFFLINE_HEALTH = {
  ollama_reachable: false,
  model_available: false,
  message: 'The backend is not reachable. Start it with uvicorn backend.main:app.',
}

export default function App() {
  const [activeView, setActiveView]           = useState('home')
  const [health, setHealth]                   = useState(null)
  const [auth, setAuth]                       = useState('checking')
  const [authError, setAuthError]             = useState('')
  const [password, setPassword]               = useState('')
  const [prefillDestination, setPrefillDestination] = useState('')
  const [itineraryDestination, setItineraryDestination] = useState('')
  const [exploreCategory, setExploreCategory] = useState('all')
  const [exploreMonth, setExploreMonth]       = useState(getCurrentMonth())
  const [travelMonth, setTravelMonth]         = useState(getCurrentMonth())

  const refreshAuth = useCallback(async () => {
    try {
      const response = await csrfFetch(`${API_BASE}/auth/me`)
      setAuth(
        response.ok ? 'authenticated'
        : response.status === 503 ? 'uninitialized'
        : 'signed_out',
      )
    } catch {
      setAuth('offline')
    }
  }, [])

  useEffect(() => { refreshAuth() }, [refreshAuth])

  useEffect(() => {
    const handleExpiredSession = () => setAuth('signed_out')
    window.addEventListener('travel-planner-auth-expired', handleExpiredSession)
    return () => window.removeEventListener('travel-planner-auth-expired', handleExpiredSession)
  }, [])

  async function signIn(event) {
    event.preventDefault()
    setAuthError('')
    try {
      const response = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ password }),
      })
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        throw new Error(body.detail || 'Sign in failed.')
      }
      setPassword('')
      setAuth('authenticated')
    } catch (error) {
      setAuthError(error.message || 'The backend could not be reached.')
    }
  }

  async function signOut() {
    await csrfFetch(`${API_BASE}/auth/logout`, { method: 'POST' })
    setAuth('signed_out')
  }

  useEffect(() => {
    const controller = new AbortController()
    async function loadHealth() {
      try {
        const response = await fetch(`${API_BASE}/health`, { signal: controller.signal })
        if (!response.ok) throw new Error(`Health check failed (${response.status})`)
        setHealth(await response.json())
      } catch (error) {
        if (error.name === 'AbortError') return
        setHealth(OFFLINE_HEALTH)
      }
    }
    loadHealth()
    const timer = window.setInterval(loadHealth, 30000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [])

  // Navigate to a view, optionally passing params
  const handleNavigate = useCallback((viewId, params = {}) => {
    if (params.category) setExploreCategory(params.category)
    if (params.month)    setExploreMonth(params.month)
    setActiveView(viewId)
  }, [])

  // Plan trip: navigate to planner with destination prefilled
  const handlePlanTrip = useCallback((destination) => {
    setPrefillDestination(destination.name)
    setActiveView('planner')
  }, [])

  // Build itinerary: navigate to itinerary builder with destination prefilled
  const handleBuildItinerary = useCallback((destination) => {
    setItineraryDestination(destination.name)
    setActiveView('itinerary')
  }, [])

  // Open a saved AI trip in the planner
  const handleOpenTrip = useCallback((_tripId) => {
    setActiveView('planner')
  }, [])

  // Month change shared between home and explore
  const handleMonthChange = useCallback((m) => {
    setTravelMonth(m)
    setExploreMonth(m)
  }, [])

  const handleTabKeys = (event) => {
    const index = VIEWS.findIndex(view => view.id === activeView)
    let nextIndex = null
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % VIEWS.length
    if (event.key === 'ArrowLeft')  nextIndex = (index - 1 + VIEWS.length) % VIEWS.length
    if (event.key === 'Home')       nextIndex = 0
    if (event.key === 'End')        nextIndex = VIEWS.length - 1
    if (nextIndex == null) return
    event.preventDefault()
    const nextView = VIEWS[nextIndex]
    setActiveView(nextView.id)
    document.getElementById(`view-tab-${nextView.id}`)?.focus()
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>

      <Header health={health} />

      {auth === 'authenticated' && (
        <div className="auth-toolbar">
          <span>Local planner session</span>
          <button type="button" onClick={signOut}>Sign out</button>
        </div>
      )}

      {auth !== 'authenticated' ? (
        <main className="auth-gate">
          {auth === 'checking' ? <p>Checking local session…</p> : (
            <section className="panel-card auth-card">
              <h1>
                {auth === 'uninitialized'
                  ? 'Set up local authentication'
                  : 'Sign in to your planner'}
              </h1>
              {auth === 'uninitialized' ? (
                <p>
                  Initialize the local owner once from the project folder with{' '}
                  <code>python -m backend.manage_auth</code>, then reload this page.
                </p>
              ) : auth === 'offline' ? (
                <p>
                  The backend is unavailable. Start it with{' '}
                  <code>uvicorn backend.main:app</code>.
                </p>
              ) : (
                <form onSubmit={signIn}>
                  <label htmlFor="local-password">Local password</label>
                  <input
                    id="local-password"
                    type="password"
                    autoComplete="current-password"
                    value={password}
                    onChange={event => setPassword(event.target.value)}
                    required
                  />
                  <button type="submit">Sign in</button>
                </form>
              )}
              {authError && <p role="alert">{authError}</p>}
            </section>
          )}
        </main>
      ) : (
        <>
          <nav
            className="view-switch"
            role="tablist"
            aria-label="Application views"
            onKeyDown={handleTabKeys}
          >
            {VIEWS.map(({ id, label, hint }) => (
              <button
                key={id}
                id={`view-tab-${id}`}
                type="button"
                role="tab"
                aria-selected={activeView === id}
                aria-controls={`view-panel-${id}`}
                tabIndex={activeView === id ? 0 : -1}
                className={`view-switch-btn ${activeView === id ? 'active' : ''}`}
                onClick={() => setActiveView(id)}
              >
                <span className="view-switch-label">{label}</span>
                <span className="view-switch-hint">{hint}</span>
              </button>
            ))}
          </nav>

          <div
            className={`app-body${activeView === 'home' ? ' app-body-home' : ''}`}
            id="main-content"
            tabIndex={-1}
          >
            <div
              className="view-panel"
              role="tabpanel"
              id={`view-panel-${activeView}`}
              aria-labelledby={`view-tab-${activeView}`}
            >
              {activeView === 'home' && (
                <HomePage
                  onNavigate={handleNavigate}
                  onPlanTrip={handlePlanTrip}
                  onBuildItinerary={handleBuildItinerary}
                  travelMonth={travelMonth}
                  onMonthChange={handleMonthChange}
                />
              )}
              {activeView === 'explore' && (
                <ExplorePage
                  onPlanTrip={handlePlanTrip}
                  onBuildItinerary={handleBuildItinerary}
                  initialCategory={exploreCategory}
                  initialMonth={exploreMonth}
                />
              )}
              {activeView === 'itinerary' && (
                <ItineraryBuilder
                  prefillDestination={itineraryDestination}
                />
              )}
              {activeView === 'planner' && (
                <TripPlanningPanel
                  prefillDestination={prefillDestination}
                />
              )}
              {activeView === 'currency' && (
                <CurrencyAgentView health={health} />
              )}
              {activeView === 'saved-trips' && (
                <SavedTripsView onOpenTrip={handleOpenTrip} />
              )}
              {activeView === 'saved-places' && (
                <SavedPlacesView
                  onNavigate={handleNavigate}
                  onPlanTrip={handlePlanTrip}
                  onBuildItinerary={handleBuildItinerary}
                />
              )}
            </div>
          </div>

          <footer className="app-footer">
            <div className="footer-brand">
              <span className="footer-brand-name">Travel Budget Planner</span>
              <span className="footer-tagline">Thoughtful plans for the places you want to go.</span>
            </div>
            <div className="footer-meta">
              <span className="footer-note">Illustrative guide costs</span>
              <span className="footer-sep" aria-hidden="true" />
              <span className="footer-note">ECB reference rates</span>
              <span className="footer-sep" aria-hidden="true" />
              <span className="footer-note">No bookings</span>
            </div>
          </footer>
        </>
      )}
    </div>
  )
}
