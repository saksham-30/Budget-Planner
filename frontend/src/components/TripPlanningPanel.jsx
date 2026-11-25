import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertIcon,
  ArrowRightIcon,
  BotIcon,
  CalendarIcon,
  ChartIcon,
  CheckIcon,
  ClockIcon,
  CopyIcon,
  DownloadIcon,
  InfoIcon,
  ListIcon,
  PinIcon,
  RefreshIcon,
  RouteIcon,
  SearchIcon,
  SendIcon,
  UsersIcon,
  WalletIcon,
  XIcon,
} from './Icons.jsx'
import { createTripRequestLifecycle, readPlanStream } from './tripRequestLifecycle.js'
import { apiFetch } from '../api.js'

const API_BASE = '/api'
const THREAD_KEY = 'travel-planner-thread-id'
const FALLBACK_DESTINATIONS = ['Goa', 'Paris', 'Tokyo']

const PLAN_STAGES = [
  { id: 'retrieve_context', label: 'Reading the cost guide' },
  { id: 'currency_agent', label: 'Pricing the baseline' },
  { id: 'budget_agent', label: 'Drafting the budget bid' },
  { id: 'itinerary_agent', label: 'Drafting the itinerary bid' },
  { id: 'supervisor', label: 'Ranking bids' },
]

function createThreadId() {
  return globalThis.crypto?.randomUUID?.() || `trip-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function initialDates() {
  const start = new Date()
  start.setDate(start.getDate() + 30)
  const end = new Date(start)
  end.setDate(end.getDate() + 5)
  return {
    start_date: start.toISOString().slice(0, 10),
    end_date: end.toISOString().slice(0, 10),
  }
}

const DEFAULT_PREFERENCES = {
  destination: 'Goa',
  ...initialDates(),
  travelers: 2,
  budget: 30000,
  currency: 'INR',
  budget_style: 'moderate',
  preferred_accommodation_type: 'hotel',
  food_preferences: '',
  interests: '',
  hard_constraints: [],
}

function formatMoney(amount, currency) {
  if (amount == null) return '—'
  try {
    return new Intl.NumberFormat(currency === 'INR' ? 'en-IN' : 'en-US', {
      style: 'currency',
      currency: currency || 'INR',
      maximumFractionDigits: 0,
    }).format(amount)
  } catch {
    return `${currency} ${Number(amount).toLocaleString()}`
  }
}

function toList(value) {
  if (Array.isArray(value)) return value.filter(Boolean)
  return String(value || '').split(',').map(item => item.trim()).filter(Boolean)
}

function toPreferenceForm(saved) {
  return {
    ...saved,
    food_preferences: toList(saved.food_preferences).join(', '),
    interests: toList(saved.interests).join(', '),
    hard_constraints: saved.hard_constraints || [],
  }
}

function buildPlanMarkdown(plan) {
  const prefs = plan.preferences || {}
  const selected = plan.selected_bid
  const lines = [
    `# Trip plan: ${prefs.destination}`,
    '',
    `- Dates: ${prefs.start_date} to ${prefs.end_date}`,
    `- Travelers: ${prefs.travelers}`,
    `- Budget: ${formatMoney(prefs.budget, prefs.currency)}`,
    `- Verdict: ${plan.feasibility || 'unknown'}`,
    '',
    plan.answer || '',
    '',
  ]
  if (selected) {
    lines.push('## Selected estimate', '')
    Object.entries(selected.itemized_costs || {}).forEach(([category, amount]) => {
      lines.push(`- ${category}: ${formatMoney(amount, selected.comparison_currency)}`)
    })
    lines.push(`- **Total: ${formatMoney(selected.proposed_total_cost, selected.comparison_currency)}**`, '')
  }
  if (plan.itinerary?.length) {
    lines.push('## Itinerary', '')
    plan.itinerary.forEach(day => {
      lines.push(`### Day ${day.day}${day.title ? ` — ${day.title}` : ''}`)
      lines.push(`_${day.date}_`)
      lines.push('')
      lines.push(day.plan)
      ;(day.highlights || []).forEach(highlight => lines.push(`- ${highlight}`))
      lines.push(`- Activity budget: ${formatMoney(day.estimated_activity_cost, day.currency)} per traveler`)
      lines.push('')
    })
  }
  if (plan.evidence?.length) {
    lines.push('## Cost evidence', '')
    plan.evidence.forEach(item => {
      lines.push(`- Source: ${item.source_file}${item.is_generic ? ' (generic benchmark)' : ''}`)
    })
    lines.push('')
  }
  lines.push('_Illustrative estimates only — not a booking or live price._')
  return lines.join('\n')
}

function downloadPlan(plan) {
  const prefs = plan.preferences || {}
  const slug = String(prefs.destination || 'trip').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
  const blob = new Blob([buildPlanMarkdown(plan)], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `trip-${slug || 'plan'}-${prefs.start_date || 'draft'}.md`
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

function formatUpdatedAt(value) {
  if (!value) return ''
  const raw = String(value)
  const parsed = new Date(raw.includes('T') ? raw : `${raw.replace(' ', 'T')}Z`)
  if (Number.isNaN(parsed.getTime())) return ''
  const now = new Date()
  return parsed.toDateString() === now.toDateString()
    ? parsed.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
    : parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function nightCount(startDate, endDate) {
  const start = new Date(`${startDate}T00:00:00`)
  const end = new Date(`${endDate}T00:00:00`)
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return null
  return Math.round((end - start) / 86_400_000)
}

const RESULT_TABS = [
  { id: 'overview', label: 'Overview', Icon: ChartIcon },
  { id: 'bids', label: 'Bids', Icon: ListIcon },
  { id: 'evidence', label: 'Evidence', Icon: SearchIcon },
  { id: 'itinerary', label: 'Itinerary', Icon: RouteIcon },
  { id: 'trace', label: 'Trace', Icon: BotIcon },
]

const FOLLOW_UP_SUGGESTIONS = [
  'Increase my budget to 45000',
  'Switch to budget style',
  'Two more people will travel',
]

function Verdict({ plan }) {
  const tone = plan.feasibility === 'feasible' ? 'feasible' : plan.feasibility === 'infeasible' ? 'infeasible' : 'unavailable'
  const [copyState, setCopyState] = useState('idle')
  const copyTimer = useRef(null)

  useEffect(() => () => clearTimeout(copyTimer.current), [])

  const copySummary = async () => {
    clearTimeout(copyTimer.current)
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable')
      await navigator.clipboard.writeText(buildPlanMarkdown(plan))
      setCopyState('copied')
    } catch {
      setCopyState('failed')
    }
    copyTimer.current = setTimeout(() => setCopyState('idle'), 2400)
  }

  return (
    <section className={`verdict verdict-${tone}`}>
      <div className="verdict-main">
        <span className="verdict-badge">
          {tone === 'feasible' ? <CheckIcon width={14} height={14} /> : <AlertIcon width={14} height={14} />}
          {tone === 'feasible' ? 'Feasible' : tone === 'infeasible' ? 'Over budget' : 'Unavailable'}
        </span>
        <h2 className="verdict-title">
          {tone === 'feasible' ? 'A proposal fits your budget' : tone === 'infeasible' ? 'No proposal fits this budget' : 'No plan could be produced'}
        </h2>
        <p className="verdict-answer">{plan.answer}</p>
        <p className="verdict-rationale">{plan.rationale}</p>
        <div className="verdict-actions">
          <button className="btn btn-ghost btn-sm" type="button" onClick={() => downloadPlan(plan)}>
            <DownloadIcon width={14} height={14} /> Export .md
          </button>
          <button className="btn btn-ghost btn-sm" type="button" onClick={copySummary}>
            {copyState === 'copied' ? <CheckIcon width={14} height={14} /> : <CopyIcon width={14} height={14} />}
            {copyState === 'copied' ? 'Copied' : copyState === 'failed' ? 'Copy failed — select & download' : 'Copy summary'}
          </button>
          <span className="sr-only" role="status" aria-live="polite">
            {copyState === 'copied' ? 'Plan summary copied to the clipboard.' : copyState === 'failed' ? 'Copying failed. Use Export .md instead.' : ''}
          </span>
        </div>
      </div>

      <div className="verdict-stats">
        <div className="verdict-stat">
          <span className="stat-label">Budget</span>
          <span className="stat-value">{formatMoney(plan.preferences.budget, plan.preferences.currency)}</span>
        </div>
        <div className="verdict-stat">
          <span className="stat-label">Selected estimate</span>
          <span className="stat-value">
            {formatMoney(plan.selected_bid?.proposed_total_cost, plan.selected_bid?.comparison_currency || plan.preferences.currency)}
          </span>
        </div>
        <div className="verdict-stat">
          <span className="stat-label">Savings vs baseline</span>
          <span className={`stat-value ${(plan.selected_bid?.savings_amount || 0) > 0 ? 'positive' : ''}`}>
            {formatMoney(plan.selected_bid?.savings_amount, plan.selected_bid?.comparison_currency || plan.preferences.currency)}
          </span>
        </div>
      </div>
    </section>
  )
}

function OverviewTab({ plan }) {
  const selected = plan.selected_bid
  const conversation = plan.conversation || []
  const priorDecisions = plan.prior_decisions || []

  return (
    <div className="tab-stack">
      <div className="card-grid two">
        <section className="panel-card">
          <h3 className="card-title">Selected itemized estimate</h3>
          {selected ? (
            <>
              <p className="card-sub">
                {selected.agent_name} · {selected.comparison_currency} ·{' '}
                {selected.proposed_changes.length ? selected.proposed_changes.join(' ') : 'original trip estimate'}
              </p>
              <dl className="item-list">
                {Object.entries(selected.itemized_costs || {}).map(([category, amount]) => (
                  <div className="item-row" key={category}>
                    <dt>{category}</dt>
                    <dd>{formatMoney(amount, selected.comparison_currency)}</dd>
                  </div>
                ))}
                <div className="item-row item-row-total">
                  <dt>Total</dt>
                  <dd>{formatMoney(selected.proposed_total_cost, selected.comparison_currency)}</dd>
                </div>
              </dl>
            </>
          ) : (
            <p className="card-sub">No bid met the hard constraints for this trip.</p>
          )}
        </section>

        <section className="panel-card">
          <h3 className="card-title">Planning conversation</h3>
          {conversation.length === 0 ? (
            <p className="card-sub">No saved turns yet.</p>
          ) : (
            <ol className="chat-log">
              {conversation.map((message, index) => (
                <li key={`${index}-${message.role}`} className={`chat-line chat-${message.role}`}>
                  <span className="chat-role">{message.role === 'user' ? 'You' : 'Planner'}</span>
                  <p>{message.content}</p>
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>

      {priorDecisions.length > 0 && (
        <section className="panel-card">
          <h3 className="card-title">Earlier decisions in this thread</h3>
          <ol className="decision-list">
            {priorDecisions.map((decision, index) => (
              <li key={index}>
                <strong>{decision.selected_bid?.agent_name || 'No feasible bid'}</strong>
                <span>{decision.rationale}</span>
                {decision.selected_bid && (
                  <span className="decision-cost">
                    {formatMoney(decision.selected_bid.proposed_total_cost, decision.currency)}
                  </span>
                )}
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  )
}

function BidsTab({ plan }) {
  const comparison = plan.comparison || {}
  return (
    <div className="tab-stack">
      <section className="panel-card">
        <h3 className="card-title">Single-agent baseline vs supervisor</h3>
        <p className="card-sub">
          The baseline reuses the same deterministic estimate; it is not an authentic rerun of the
          prior tool-calling agent.
        </p>
        <div className="comparison-table">
          <div className="comparison-head">
            <span>Workflow</span>
            <span>Estimated total</span>
            <span>Feasible</span>
            <span>Proposals</span>
          </div>
          <div className="comparison-row">
            <strong>Single-agent baseline</strong>
            <span>{formatMoney(comparison.baseline_total_cost, plan.preferences.currency)}</span>
            <span>{comparison.baseline_feasible ? 'Yes' : 'No'}</span>
            <span>{comparison.baseline_proposal_count ?? '—'}</span>
          </div>
          <div className="comparison-row highlight">
            <strong>Supervisor</strong>
            <span>{formatMoney(comparison.selected_total_cost, plan.preferences.currency)}</span>
            <span>{comparison.selected_feasible ? 'Yes' : 'No'}</span>
            <span>{comparison.multi_agent_proposal_count ?? '—'}</span>
          </div>
        </div>
      </section>

      <section className="panel-card">
        <h3 className="card-title">Auditable bid comparison</h3>
        <div className="table-scroll">
          <table className="data-table">
            <caption className="sr-only">Every bid considered by the supervisor, with its cost, feasibility and decision.</caption>
            <thead>
              <tr>
                <th scope="col">Agent</th>
                <th scope="col">Changes</th>
                <th scope="col">Estimate</th>
                <th scope="col">Savings</th>
                <th scope="col">Budget</th>
                <th scope="col">Feasible</th>
                <th scope="col">Violations</th>
                <th scope="col">Decision</th>
              </tr>
            </thead>
            <tbody>
              {(plan.bids || []).map(bid => {
                const isSelected = plan.selected_bid?.bid_id === bid.bid_id
                return (
                  <tr key={bid.bid_id} className={isSelected ? 'selected-row' : ''}>
                    <td>
                      <span className="agent-cell">
                        {bid.agent_name}
                        {isSelected && <span className="tag tag-green">Selected</span>}
                      </span>
                    </td>
                    <td>{bid.proposed_changes.length ? bid.proposed_changes.join(' ') : 'Original estimate'}</td>
                    <td>{formatMoney(bid.proposed_total_cost, bid.comparison_currency)}</td>
                    <td>{formatMoney(bid.savings_amount, bid.comparison_currency)}</td>
                    <td>{formatMoney(bid.budget_limit, bid.comparison_currency)}</td>
                    <td>
                      <span className={`tag ${bid.feasible ? 'tag-green' : 'tag-amber'}`}>
                        {bid.feasible ? 'Yes' : 'No'}
                      </span>
                    </td>
                    <td>{bid.constraint_violations.length ? bid.constraint_violations.join(', ') : 'None'}</td>
                    <td>{bid.decision_reason}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <p className="card-footnote">
          Estimates use the app's current exchange rate (live ECB reference rates or the
          cached fallback) and illustrative cost-guide values.
        </p>
      </section>
    </div>
  )
}

function EvidenceTab({ plan }) {
  const evidence = plan.evidence || []
  return (
    <div className="tab-stack">
      {evidence.map(item => (
        <section className="panel-card" key={`${item.destination}-${item.section}`}>
          <div className="evidence-head">
            <h3 className="card-title">{item.destination}</h3>
            <span className="tag">{item.currency} · {item.section.split('|')[0].trim()}</span>
          </div>
          <p className="card-sub">Source: {item.source_file} · illustrative estimates only.</p>
          {item.is_generic && (
            <p className="card-sub evidence-generic" role="note">
              No destination-specific guide entry exists for {item.destination}; the plan uses generic benchmark costs.
            </p>
          )}
          <p className="card-sub">
            Hotel prices are per traveler per night; food, transport, and activities are per traveler per day.
          </p>
          <div className="evidence-grid">
            {Object.entries(item.estimates || {}).map(([category, levels]) => (
              <div className="evidence-cell" key={category}>
                <strong>{category}</strong>
                {Object.entries(levels).map(([style, amount]) => (
                  <span key={style}>
                    <span className="muted">{style}</span>
                    {formatMoney(amount, item.currency)}
                  </span>
                ))}
              </div>
            ))}
          </div>
          <ul className="assumption-list">
            {(item.assumptions || []).map(assumption => <li key={assumption}>{assumption}</li>)}
          </ul>
        </section>
      ))}
    </div>
  )
}

function formatDayDate(value) {
  const parsed = new Date(`${value}T00:00:00`)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
}

function formatMonthDay(value) {
  const parsed = new Date(`${value}T00:00:00`)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function ItineraryTab({ plan }) {
  const itinerary = plan.itinerary || []
  const nights = Math.max(itinerary.length - 1, 0)
  return (
    <div className="tab-stack">
      <section className="panel-card">
        <div className="itinerary-head">
          <div>
            <h3 className="card-title">Suggested itinerary</h3>
            <p className="card-sub">
              {itinerary.length} {itinerary.length === 1 ? 'day' : 'days'} · {nights}{' '}
              {nights === 1 ? 'night' : 'nights'} · illustrative estimates, not bookings.
            </p>
          </div>
          {itinerary.length > 0 && (
            <span className="tag">
              {formatMonthDay(itinerary[0].date)} → {formatMonthDay(itinerary[itinerary.length - 1].date)}
            </span>
          )}
        </div>
        <ol className="day-list">
          {itinerary.map(day => (
            <li key={day.day}>
              <div className="day-marker">
                <strong>{day.day}</strong>
                <span>Day</span>
              </div>
              <div className="day-body">
                <div className="day-head">
                  <h4 className="day-title">{day.title || `Day ${day.day}`}</h4>
                  <span className="day-date">{formatDayDate(day.date)}</span>
                </div>
                <p className="day-plan">{day.plan}</p>
                {day.highlights?.length > 0 && (
                  <ul className="day-highlights">
                    {day.highlights.map(highlight => (
                      <li key={highlight}>{highlight}</li>
                    ))}
                  </ul>
                )}
                <div className="day-cost">
                  <span>Activity budget</span>
                  <strong>
                    {formatMoney(day.estimated_activity_cost, day.currency)}
                    <small> per traveler</small>
                  </strong>
                </div>
              </div>
            </li>
          ))}
        </ol>
      </section>
    </div>
  )
}

function TraceTab({ plan }) {
  const trace = plan.trace || []
  return (
    <div className="tab-stack">
      <section className="panel-card">
        <h3 className="card-title">Supervisor execution trace</h3>
        <ol className="supervisor-trace">
          {trace.map((item, index) => (
            <li key={`${item.step}-${index}`}>
              <span className="trace-agent">{item.agent}</span>
              <span>{item.event}</span>
            </li>
          ))}
        </ol>
      </section>
    </div>
  )
}

export default function TripPlanningPanel({ prefillDestination }) {
  const threadRef = useRef(null)
  const requestLifecycleRef = useRef(null)
  if (threadRef.current === null) {
    const stored = typeof localStorage !== 'undefined' ? localStorage.getItem(THREAD_KEY) : null
    threadRef.current = { id: stored || createThreadId(), restore: Boolean(stored) }
  }
  if (requestLifecycleRef.current === null) {
    requestLifecycleRef.current = createTripRequestLifecycle()
  }

  const [tripId, setTripId] = useState(() => threadRef.current.id)
  const [preferences, setPreferences] = useState(DEFAULT_PREFERENCES)
  const [destinations, setDestinations] = useState(FALLBACK_DESTINATIONS)
  const [query, setQuery] = useState('Plan my trip and compare lower-cost options.')
  const [plan, setPlan] = useState(null)
  const [dirty, setDirty] = useState(false)
  const [loading, setLoading] = useState(false)
  const [restoring, setRestoring] = useState(threadRef.current.restore)
  const [error, setError] = useState('')
  const [activeTab, setActiveTab] = useState('overview')
  const [recentTrips, setRecentTrips] = useState([])
  const [recentOpen, setRecentOpen] = useState(false)
  const [openingTripId, setOpeningTripId] = useState(null)
  const [followUp, setFollowUp] = useState('')
  const [followingUp, setFollowingUp] = useState(false)
  const [stages, setStages] = useState([])
  const recentRef = useRef(null)
  const recentTriggerRef = useRef(null)
  const resultsRef = useRef(null)
  const requestLifecycle = requestLifecycleRef.current

  useEffect(() => () => requestLifecycle.abort(), [requestLifecycle])

  useEffect(() => {
    if (prefillDestination) {
      setPreferences(prev => ({ ...prev, destination: prefillDestination }))
    }
  }, [prefillDestination])

  const refreshRecentTrips = useCallback(async () => {
    try {
      const response = await apiFetch(`${API_BASE}/trips`)
      if (!response.ok) return
      const values = await response.json()
      if (Array.isArray(values)) setRecentTrips(values)
    } catch {
      // Keep the last known list if the endpoint is temporarily unavailable.
    }
  }, [])

  useEffect(() => { refreshRecentTrips() }, [refreshRecentTrips])

  useEffect(() => {
    if (!recentOpen) return undefined

    const handlePointerDown = (event) => {
      if (!recentRef.current?.contains(event.target)) setRecentOpen(false)
    }
    const handleKeyDown = (event) => {
      if (event.key !== 'Escape') return
      setRecentOpen(false)
      recentTriggerRef.current?.focus()
    }

    document.addEventListener('mousedown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [recentOpen])

  useEffect(() => {
    let cancelled = false
    apiFetch(`${API_BASE}/destinations`)
      .then(async response => {
        if (!response.ok) throw new Error('unavailable')
        return response.json()
      })
      .then(values => {
        if (!cancelled && Array.isArray(values) && values.length > 0) setDestinations(values)
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!threadRef.current.restore) return undefined

    const request = requestLifecycle.begin()

    async function restoreThread() {
      try {
        const response = await apiFetch(
          `${API_BASE}/trips/${encodeURIComponent(tripId)}/preferences`,
          { signal: request.signal },
        )
        if (!request.isCurrent()) return
        if (response.status === 404) {
          localStorage.removeItem(THREAD_KEY)
          threadRef.current.restore = false
          return
        }
        if (!response.ok) throw new Error('Could not restore saved trip preferences.')
        const saved = await response.json()
        if (!request.isCurrent()) return
        setPreferences(toPreferenceForm(saved))

        const planResponse = await apiFetch(
          `${API_BASE}/trips/${encodeURIComponent(tripId)}/plan`,
          { signal: request.signal },
        )
        if (!request.isCurrent()) return
        if (planResponse.status === 404) return
        if (!planResponse.ok) throw new Error('Could not restore the previous trip plan.')
        const restored = await planResponse.json()
        if (request.isCurrent()) {
          setPlan(restored)
          setDirty(false)
        }
      } catch (restoreError) {
        if (request.isCurrent() && restoreError.name !== 'AbortError') {
          setError(restoreError.message)
        }
      } finally {
        if (request.isCurrent()) {
          setRestoring(false)
          requestLifecycle.finish(request)
        }
      }
    }

    restoreThread()
    return () => request.abort()
  }, [requestLifecycle, tripId])

  const openTrip = useCallback(async (id) => {
    setRecentOpen(false)
    if (id === tripId && plan) return
    const request = requestLifecycle.begin()
    setOpeningTripId(id)
    setRestoring(true)
    setLoading(false)
    setFollowingUp(false)
    setError('')
    setPlan(null)
    setDirty(false)
    setStages([])
    threadRef.current.restore = false
    try {
      const preferencesResponse = await apiFetch(
        `${API_BASE}/trips/${encodeURIComponent(id)}/preferences`,
        { signal: request.signal },
      )
      if (!request.isCurrent()) return
      if (!preferencesResponse.ok) throw new Error('That saved trip could not be loaded.')
      const savedPreferences = await preferencesResponse.json()
      if (!request.isCurrent()) return
      setPreferences(toPreferenceForm(savedPreferences))
      setTripId(id)
      localStorage.setItem(THREAD_KEY, id)
      const planResponse = await apiFetch(
        `${API_BASE}/trips/${encodeURIComponent(id)}/plan`,
        { signal: request.signal },
      )
      if (!request.isCurrent()) return
      if (planResponse.ok) {
        const savedPlan = await planResponse.json()
        if (!request.isCurrent()) return
        setPlan(savedPlan)
        setActiveTab('overview')
      }
      setQuery('')
    } catch (loadError) {
      if (request.isCurrent() && loadError.name !== 'AbortError') {
        setError(loadError.message || 'That saved trip could not be loaded.')
      }
    } finally {
      if (request.isCurrent()) {
        setRestoring(false)
        setOpeningTripId(null)
        requestLifecycle.finish(request)
      }
    }
  }, [plan, requestLifecycle, tripId])

  const submitFollowUp = useCallback(async (event) => {
    event.preventDefault()
    const message = followUp.trim()
    if (!message) return

    const request = requestLifecycle.begin()
    setFollowingUp(true)
    setLoading(false)
    setError('')
    setStages([])
    try {
      const response = await apiFetch(`${API_BASE}/trips/${encodeURIComponent(tripId)}/plan/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: message }),
        signal: request.signal,
      })
      if (!request.isCurrent()) return
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        throw new Error(body.detail || 'The follow-up could not be planned.')
      }
      const body = await readPlanStream(response, {
        signal: request.signal,
        onStage: stage => {
          if (request.isCurrent()) {
            setStages(current => request.isCurrent() ? [...current, stage] : current)
          }
        },
      })
      if (!request.isCurrent()) return
      setPlan(body)
      setPreferences(toPreferenceForm(body.preferences))
      setFollowUp('')
      setDirty(false)
      setActiveTab('overview')
      refreshRecentTrips()
    } catch (followUpError) {
      if (request.isCurrent() && followUpError.name !== 'AbortError') {
        setError(followUpError.message || 'The follow-up could not be planned.')
      }
    } finally {
      if (request.isCurrent()) {
        setFollowingUp(false)
        requestLifecycle.finish(request)
      }
    }
  }, [followUp, refreshRecentTrips, requestLifecycle, tripId])

  const updatePreference = (event) => {
    const { name, value } = event.target
    setDirty(true)
    setError('')
    setPreferences(current => ({ ...current, [name]: value }))
  }

  const toggleConstraint = (value) => {
    setDirty(true)
    setPreferences(current => ({
      ...current,
      hard_constraints: current.hard_constraints.includes(value)
        ? current.hard_constraints.filter(item => item !== value)
        : [...current.hard_constraints, value],
    }))
  }

  const startNewThread = () => {
    requestLifecycle.abort()
    const id = createThreadId()
    threadRef.current.restore = false
    setTripId(id)
    setPreferences(DEFAULT_PREFERENCES)
    setPlan(null)
    setDirty(false)
    setError('')
    setActiveTab('overview')
    setRestoring(false)
    setLoading(false)
    setFollowingUp(false)
    setOpeningTripId(null)
    setStages([])
    setQuery('Plan my trip and compare lower-cost options.')
    setFollowUp('')
    setRecentOpen(false)
  }

  const nights = nightCount(preferences.start_date, preferences.end_date)

  const scrollToResults = useCallback(() => {
    const node = resultsRef.current
    if (!node) return
    const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    requestAnimationFrame(() => {
      node.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' })
    })
  }, [])

  const planTrip = useCallback(async () => {
    if (restoring) return

    if (!preferences.destination.trim() || !preferences.start_date || !preferences.end_date) {
      setError('Fill in the destination and both travel dates before planning.')
      return
    }
    const destination = destinations.find(
      item => item.toLowerCase() === preferences.destination.trim().toLowerCase(),
    ) || preferences.destination.trim()
    if (nights != null && nights < 0) {
      setError('The end date must be on or after the start date.')
      return
    }
    if (!Number.isFinite(Number(preferences.budget)) || Number(preferences.budget) <= 0) {
      setError('Enter a total budget greater than zero.')
      return
    }
    if (!Number.isFinite(Number(preferences.travelers)) || Number(preferences.travelers) < 1) {
      setError('There must be at least one traveler.')
      return
    }
    if (!query.trim()) {
      setError('Add a message describing the trip you want planned.')
      return
    }

    const request = requestLifecycle.begin()
    setLoading(true)
    setFollowingUp(false)
    setError('')
    setStages([])

    try {
      const payload = {
        ...preferences,
        destination,
        travelers: Number(preferences.travelers),
        budget: Number(preferences.budget),
        food_preferences: toList(preferences.food_preferences),
        interests: toList(preferences.interests),
      }

      const savedResponse = await apiFetch(`${API_BASE}/trips/${encodeURIComponent(tripId)}/preferences`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: request.signal,
      })
      if (!request.isCurrent()) return
      if (!savedResponse.ok) {
        const detail = await savedResponse.json().catch(() => ({}))
        throw new Error(detail.detail || 'Trip preferences could not be saved.')
      }

      const response = await apiFetch(`${API_BASE}/trips/${encodeURIComponent(tripId)}/plan/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: query.trim() }),
        signal: request.signal,
      })
      if (!request.isCurrent()) return
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        throw new Error(body.detail || 'Trip planning failed.')
      }
      const body = await readPlanStream(response, {
        signal: request.signal,
        onStage: stage => {
          if (request.isCurrent()) {
            setStages(current => request.isCurrent() ? [...current, stage] : current)
          }
        },
      })
      if (!request.isCurrent()) return

      localStorage.setItem(THREAD_KEY, tripId)
      setPlan(body)
      setActiveTab('overview')
      setPreferences(toPreferenceForm(body.preferences))
      setQuery('')
      setFollowUp('')
      setDirty(false)
      refreshRecentTrips()
      scrollToResults()
    } catch (submitError) {
      if (request.isCurrent() && submitError.name !== 'AbortError') {
        setError(submitError.message || 'Trip planning failed.')
      }
    } finally {
      if (request.isCurrent()) {
        setLoading(false)
        requestLifecycle.finish(request)
      }
    }
  }, [destinations, nights, preferences, query, refreshRecentTrips, requestLifecycle, restoring, scrollToResults, tripId])

  const submitPlan = (event) => {
    event.preventDefault()
    planTrip()
  }

  const handleTabKeys = (event) => {
    const index = RESULT_TABS.findIndex(tab => tab.id === activeTab)
    if (index === -1) return
    let nextIndex = null
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % RESULT_TABS.length
    else if (event.key === 'ArrowLeft') nextIndex = (index - 1 + RESULT_TABS.length) % RESULT_TABS.length
    else if (event.key === 'Home') nextIndex = 0
    else if (event.key === 'End') nextIndex = RESULT_TABS.length - 1
    if (nextIndex === null) return
    event.preventDefault()
    const nextId = RESULT_TABS[nextIndex].id
    setActiveTab(nextId)
    document.getElementById(`result-tab-${nextId}`)?.focus()
  }

  const selectedBidCount = plan?.bids?.length || 0
  const completedStageIds = new Set(stages.map(stage => stage.stage))
  const activeStage = PLAN_STAGES.find(stage => !completedStageIds.has(stage.id))
  const lastStage = stages[stages.length - 1]

  return (
    <main className="planner">
      <section className="planner-intro">
        <div className="planner-intro-copy">
          <span className="eyebrow">
            <span className="eyebrow-dot" aria-hidden="true" /> Persistent multi-agent planner
          </span>
          <h1 className="planner-title">Plan a trip with grounded estimates</h1>
          <p className="planner-sub">
            Save preferences once, then let specialist agents bid against a local cost guide while a
            supervisor ranks the cheapest feasible proposal.
          </p>
        </div>
        <div className="planner-intro-side">
          <div className="recent-trips" ref={recentRef}>
            <button
              ref={recentTriggerRef}
              className="btn btn-ghost"
              type="button"
              onClick={() => setRecentOpen(open => !open)}
              aria-expanded={recentOpen}
              aria-haspopup="menu"
              aria-controls={recentOpen ? 'recent-trips-menu' : undefined}
            >
              <ClockIcon width={15} height={15} />
              Recent trips
              {recentTrips.length > 0 && <span className="tab-count">{recentTrips.length}</span>}
            </button>
            {recentOpen && (
              <div className="recent-menu" role="menu" id="recent-trips-menu" aria-label="Saved trips">
                {recentTrips.length === 0 ? (
                  <p className="recent-empty">No saved trips yet — plan one to see it here.</p>
                ) : (
                  recentTrips.map(trip => {
                    const isCurrent = trip.trip_id === tripId
                    const isOpening = trip.trip_id === openingTripId
                    return (
                      <button
                        key={trip.trip_id}
                        type="button"
                        role="menuitem"
                        className={`recent-item ${isCurrent ? 'active' : ''}`}
                        onClick={() => openTrip(trip.trip_id)}
                        disabled={openingTripId !== null}
                        aria-current={isCurrent ? 'true' : undefined}
                      >
                        <span className="recent-item-main">
                          <strong>{trip.destination}</strong>
                          <span>
                            {trip.travelers} traveler{trip.travelers === 1 ? '' : 's'} · {formatMoney(trip.budget, trip.currency)}
                            {trip.updated_at && ` · ${formatUpdatedAt(trip.updated_at)}`}
                          </span>
                        </span>
                        {isOpening ? (
                          <span className="tag">Opening…</span>
                        ) : isCurrent ? (
                          <span className="tag tag-amber">Current</span>
                        ) : null}
                      </button>
                    )
                  })
                )}
              </div>
            )}
          </div>
          <button className="btn btn-ghost" type="button" onClick={startNewThread}>
            <RefreshIcon width={15} height={15} />
            New trip
          </button>
          <div className="thread-chip" title={tripId}>
            <span>Thread</span>
            <code>{tripId.slice(0, 8)}…</code>
          </div>
        </div>
      </section>

      {error && (
        <div className="banner banner-error" role="alert">
          <AlertIcon className="banner-icon" />
          <div className="banner-copy">
            <div className="banner-title">Could not complete that request</div>
            <p className="banner-body">{error}</p>
          </div>
          <div className="banner-actions">
            <button className="btn btn-ghost btn-sm" type="button" onClick={planTrip} disabled={loading || restoring}>
              Try again
            </button>
            <button className="link-btn" type="button" onClick={() => setError('')} aria-label="Dismiss error">
              <XIcon width={13} height={13} /> Dismiss
            </button>
          </div>
        </div>
      )}

      <form className="planner-form" onSubmit={submitPlan} noValidate>
        <ol className="planner-flow" aria-label="Trip planning steps">
          <li><span>01</span><strong>Trip basics</strong><small>Destination &amp; dates</small></li>
          <li><span>02</span><strong>Set your budget</strong><small>Spend &amp; travel style</small></li>
          <li><span>03</span><strong>Make it yours</strong><small>Preferences &amp; constraints</small></li>
        </ol>
        <div className="prefs-grid">
          <section className="form-card">
            <header className="form-card-head">
              <span className="form-icon"><PinIcon width={16} height={16} /></span>
              <div>
                <h2 className="form-title">Destination &amp; dates</h2>
                <p className="form-sub">Where and when you are travelling</p>
              </div>
            </header>

            <div className="field-grid">
              <label className="field span-2">
                <span className="field-label">Destination</span>
                <input
                  type="text"
                  name="destination"
                  list="destination-options"
                  value={preferences.destination}
                  onChange={updatePreference}
                  placeholder="Type a destination"
                  autoComplete="off"
                  required
                />
                <datalist id="destination-options">
                  {destinations.map(destination => <option key={destination} value={destination} />)}
                </datalist>
                <span className="field-hint">
                  {destinations.join(', ')} use destination-specific guide estimates; other destinations use generic benchmarks.
                </span>
              </label>
              <label className="field">
                <span className="field-label"><CalendarIcon width={13} height={13} /> Start date</span>
                <input
                  type="date"
                  name="start_date"
                  value={preferences.start_date}
                  max={preferences.end_date || undefined}
                  onChange={updatePreference}
                  required
                />
              </label>
              <label className="field">
                <span className="field-label"><CalendarIcon width={13} height={13} /> End date</span>
                <input
                  type="date"
                  name="end_date"
                  value={preferences.end_date}
                  min={preferences.start_date || undefined}
                  onChange={updatePreference}
                  required
                />
              </label>
              <label className="field">
                <span className="field-label"><UsersIcon width={13} height={13} /> Travelers</span>
                <input type="number" min="1" max="20" name="travelers" value={preferences.travelers} onChange={updatePreference} required />
              </label>
              <div className="field field-readonly">
                <span className="field-label">Duration</span>
                <span className="field-static" aria-live="polite">
                  {nights == null ? '—' : nights < 0 ? 'End date is before the start date' : `${nights} night${nights === 1 ? '' : 's'}`}
                </span>
              </div>
            </div>
          </section>

          <section className="form-card">
            <header className="form-card-head">
              <span className="form-icon"><WalletIcon width={16} height={16} /></span>
              <div>
                <h2 className="form-title">Budget &amp; style</h2>
                <p className="form-sub">How much and how you like to travel</p>
              </div>
            </header>

            <div className="field-grid">
              <label className="field">
                <span className="field-label">Total budget</span>
                <input type="number" min="1" step="0.01" name="budget" value={preferences.budget} onChange={updatePreference} required />
              </label>
              <label className="field">
                <span className="field-label">Currency</span>
                <select name="currency" value={preferences.currency} onChange={updatePreference}>
                  {['INR', 'EUR', 'USD', 'GBP'].map(currency => <option key={currency}>{currency}</option>)}
                </select>
              </label>
              <label className="field">
                <span className="field-label">Budget style</span>
                <select name="budget_style" value={preferences.budget_style} onChange={updatePreference}>
                  {['budget', 'moderate', 'luxury'].map(style => <option key={style}>{style}</option>)}
                </select>
              </label>
              <label className="field">
                <span className="field-label">Accommodation</span>
                <select name="preferred_accommodation_type" value={preferences.preferred_accommodation_type} onChange={updatePreference}>
                  {['hotel', 'hostel', 'rental'].map(value => <option key={value}>{value}</option>)}
                </select>
              </label>
            </div>
          </section>

          <section className="form-card">
            <header className="form-card-head">
              <span className="form-icon"><ListIcon width={16} height={16} /></span>
              <div>
                <h2 className="form-title">Preferences</h2>
                <p className="form-sub">Optional notes and hard constraints</p>
              </div>
            </header>

            <div className="field-grid">
              <label className="field span-2">
                <span className="field-label">Food preferences</span>
                <input name="food_preferences" value={preferences.food_preferences} onChange={updatePreference} placeholder="Vegetarian, allergies…" />
              </label>
              <label className="field span-2">
                <span className="field-label">Interests</span>
                <input name="interests" value={preferences.interests} onChange={updatePreference} placeholder="Beaches, museums…" />
              </label>
              <fieldset className="constraint-field span-2">
                <legend className="field-label">Hard constraints</legend>
                <div className="check-list">
                  <label className="check-chip">
                    <input
                      type="checkbox"
                      checked={preferences.hard_constraints.includes('keep_accommodation')}
                      onChange={() => toggleConstraint('keep_accommodation')}
                    />
                    <span className="check-box"><CheckIcon width={11} height={11} /></span>
                    Keep accommodation tier
                  </label>
                  <label className="check-chip">
                    <input
                      type="checkbox"
                      checked={preferences.hard_constraints.includes('no_activity_reduction')}
                      onChange={() => toggleConstraint('no_activity_reduction')}
                    />
                    <span className="check-box"><CheckIcon width={11} height={11} /></span>
                    Do not remove activity days
                  </label>
                </div>
              </fieldset>
            </div>
          </section>
        </div>

        <div className="composer">
          <div className="composer-intro">
            <span className="composer-step">Your trip at a glance</span>
            <strong>Ready to build your plan?</strong>
            <span>Check the summary, add a note if you like, then let the planner compare options.</span>
          </div>
          <div className="composer-summary">
            <span className="composer-fact">
              <PinIcon width={13} height={13} /> {preferences.destination}
            </span>
            <span className="composer-fact">
              <UsersIcon width={13} height={13} /> {preferences.travelers} traveler{Number(preferences.travelers) === 1 ? '' : 's'}
            </span>
            <span className="composer-fact">
              <WalletIcon width={13} height={13} /> {formatMoney(Number(preferences.budget), preferences.currency)}
            </span>
          </div>

          <label className="field composer-query">
            <span className="field-label">Conversation message</span>
            <textarea
              value={query}
              onChange={(event) => { setDirty(true); setError(''); setQuery(event.target.value) }}
              rows={2}
              maxLength={4000}
              placeholder="Plan this trip, reduce my budget, or ask what if two more people join?"
            />
          </label>

          <button className="btn btn-primary" type="submit" disabled={loading || restoring}>
            {loading ? <><span className="spinner" /> Planning…</> : <>Save &amp; plan <ArrowRightIcon width={16} height={16} className="btn-trailing-icon" /></>}
          </button>
        </div>
      </form>

      <section className="results-area" ref={resultsRef} aria-busy={loading}>        {restoring && (
          <div className="restoring">
            <span className="spinner spinner-accent" />
            Restoring your saved trip…
          </div>
        )}

        {!plan && !loading && !restoring && (
          <div className="how-card">
            <div className="how-head">
              <h2 className="how-title">How the planner works</h2>
              <span className="tag">{selectedBidCount ? `${selectedBidCount} bids` : 'Local cost guide'}</span>
            </div>
            <ol className="how-steps">
              <li>
                <span className="how-step">1</span>
                <div><strong>Save preferences</strong><span>Destination, dates, budget and constraints are stored locally.</span></div>
              </li>
              <li>
                <span className="how-step">2</span>
                <div><strong>Agents bid in parallel</strong><span>Budget and itinerary specialists propose costed options.</span></div>
              </li>
              <li>
                <span className="how-step">3</span>
                <div><strong>Supervisor decides</strong><span>The cheapest bid that meets every hard constraint wins.</span></div>
              </li>
            </ol>
            <p className="how-note">
              <InfoIcon width={14} height={14} /> Estimates come from a bundled cost guide and
              current exchange rates — no booking or supplier price is live.
            </p>
          </div>
        )}

        {loading && (
          <div className="plan-loading" role="status">
            <div className="plan-loading-head">
              <span className="spinner spinner-accent" />
              <div>
                <strong>{activeStage ? `${activeStage.label}…` : 'Wrapping up…'}</strong>
                <span>
                  {lastStage?.detail || 'Saving preferences, then running the specialist agents.'}
                </span>
              </div>
            </div>
            <ol className="stage-list">
              {PLAN_STAGES.map((stage, index) => {
                const done = completedStageIds.has(stage.id)
                const active = !done && stage.id === activeStage?.id
                return (
                  <li
                    key={stage.id}
                    className={`${done ? 'done' : ''} ${active ? 'active' : ''}`.trim()}
                  >
                    <span className="stage-dot">
                      {done ? <CheckIcon width={11} height={11} /> : index + 1}
                    </span>
                    {stage.label}
                  </li>
                )
              })}
            </ol>
          </div>
        )}

        {plan && !loading && (
          <div className="plan-results">
            {dirty && (
              <div className="dirty-note" role="status">
                <InfoIcon width={14} height={14} />
                <span>Preferences changed since this plan was produced. Replan to apply them.</span>
                <button className="btn btn-ghost btn-sm" type="button" onClick={planTrip} disabled={loading || restoring}>
                  Replan
                </button>
              </div>
            )}

            <Verdict plan={plan} />

            <section className="follow-up-card" aria-label="Refine this plan">
              <div className="follow-up-copy">
                <h3 className="card-title">Refine this plan</h3>
                <p className="card-sub">
                  Ask for a new budget, style, or traveler count — the same thread updates in place.
                </p>
              </div>
              <form className="follow-up-form" onSubmit={submitFollowUp}>
                <input
                  className="follow-up-input"
                  value={followUp}
                  onChange={(event) => setFollowUp(event.target.value)}
                  placeholder="e.g. Increase my budget to 45000 INR"
                  aria-label="Follow-up request"
                  disabled={followingUp}
                />
                <button className="btn btn-primary btn-sm" type="submit" disabled={!followUp.trim() || followingUp}>
                  {followingUp ? <span className="spinner" /> : <SendIcon width={15} height={15} />}
                  {followingUp ? 'Updating…' : 'Ask'}
                </button>
              </form>
              {followingUp && (
                <div className="follow-up-progress" role="status">
                  <span className="spinner spinner-accent" />
                  <span>{lastStage ? `${lastStage.title}…` : 'Applying your changes…'}</span>
                </div>
              )}
              <div className="suggestion-row">
                {FOLLOW_UP_SUGGESTIONS.map(text => (
                  <button
                    key={text}
                    type="button"
                    className="suggestion-chip"
                    onClick={() => setFollowUp(text)}
                    disabled={followingUp}
                  >
                    {text}
                  </button>
                ))}
              </div>
            </section>

            <nav className="result-tabs" role="tablist" aria-label="Plan details" onKeyDown={handleTabKeys}>
              {RESULT_TABS.map(({ id, label, Icon }) => (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  id={`result-tab-${id}`}
                  aria-selected={activeTab === id}
                  aria-controls={`result-panel-${id}`}
                  tabIndex={activeTab === id ? 0 : -1}
                  className={`result-tab ${activeTab === id ? 'active' : ''}`}
                  onClick={() => setActiveTab(id)}
                >
                  <Icon width={15} height={15} />
                  {label}
                  {id === 'bids' && <span className="tab-count">{plan.bids?.length || 0}</span>}
                  {id === 'evidence' && <span className="tab-count">{plan.evidence?.length || 0}</span>}
                  {id === 'itinerary' && <span className="tab-count">{plan.itinerary?.length || 0}</span>}
                </button>
              ))}
            </nav>

            <div
              className="tab-panel"
              role="tabpanel"
              id={`result-panel-${activeTab}`}
              aria-labelledby={`result-tab-${activeTab}`}
              tabIndex={0}
            >
              {activeTab === 'overview' && <OverviewTab plan={plan} />}
              {activeTab === 'bids' && <BidsTab plan={plan} />}
              {activeTab === 'evidence' && <EvidenceTab plan={plan} />}
              {activeTab === 'itinerary' && <ItineraryTab plan={plan} />}
              {activeTab === 'trace' && <TraceTab plan={plan} />}
            </div>
          </div>
        )}
      </section>
    </main>
  )
}
