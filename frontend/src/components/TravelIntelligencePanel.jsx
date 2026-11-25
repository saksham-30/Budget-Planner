/**
 * TravelIntelligencePanel — Phase 5
 *
 * Collapsible panel shown below the itinerary view header.
 * Loads weather, POI, and feasibility data from the backend.
 *
 * Data sources:
 *   Weather   — GET /api/weather/{destination}?start_date=…&end_date=…
 *   POI       — GET /api/poi/{destination}
 *   Feasibility — POST /api/itinerary/feasibility
 *
 * All sections degrade gracefully on error.
 * Labels clearly show FORECAST vs CLIMATOLOGY vs UNAVAILABLE.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '../api.js'
import {
  AlertIcon,
  CalendarCheckIcon,
  ChevronDownIcon,
  CompassIcon,
  InfoIcon,
  MapIcon,
  RefreshIcon,
  SparkIcon,
  SunIcon,
  TagIcon,
  WalletCheckIcon,
} from './Icons.jsx'

const API_BASE = '/api'

// ─── Utilities ────────────────────────────────────────────────────────────────

function SourceBadge({ source }) {
  const cls = {
    FORECAST:     'source-badge-forecast',
    ARCHIVE:      'source-badge-archive',
    CLIMATOLOGY:  'source-badge-clim',
    UNAVAILABLE:  'source-badge-unavail',
    OSM:          'source-badge-osm',
    STATIC_FALLBACK: 'source-badge-static',
    SOURCED:      'source-badge-sourced',
    ESTIMATED:    'source-badge-static',
  }[source] || 'source-badge-static'
  return <span className={`source-badge ${cls}`}>{source}</span>
}

function SeverityIcon({ severity }) {
  const color = { CRITICAL: '#a33a32', WARNING: '#8a6420', INFO: '#2c5c46' }[severity] || '#5f6357'
  return (
    <span className="feasibility-dot" style={{ background: color }} aria-hidden="true" />
  )
}

// ─── Weather section ──────────────────────────────────────────────────────────

function WeatherDay({ day, compact }) {
  const rain = day.is_rain_likely
  return (
    <div className={`wx-day${rain ? ' wx-rainy' : ''}`}>
      <span className="wx-date">{new Date(`${day.date}T00:00:00`).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })}</span>
      <span className="wx-label">{day.weather_label || '—'}</span>
      <span className="wx-temps">
        {day.temp_min != null ? `${day.temp_min}°` : '—'}
        {' – '}
        {day.temp_max != null ? `${day.temp_max}°C` : '—'}
      </span>
      {day.precipitation_probability != null && (
        <span className={`wx-rain${rain ? ' wx-rain-high' : ''}`}>
          💧 {day.precipitation_probability}%
        </span>
      )}
      <SourceBadge source={day.source} />
    </div>
  )
}

function WeatherSection({ destination, startDate, endDate, onDataLoad }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(true)

  const load = useCallback(async () => {
    if (!destination || !startDate || !endDate) return
    setLoading(true)
    setError('')
    try {
      const res = await apiFetch(
        `${API_BASE}/weather/${encodeURIComponent(destination)}?start_date=${startDate}&end_date=${endDate}`
      )
      if (!res.ok) throw new Error(`Weather unavailable (${res.status})`)
      const result = await res.json()
      setData(result)
      if (onDataLoad) onDataLoad(result)
    } catch (err) {
      setError(err.message || 'Weather data unavailable.')
    } finally {
      setLoading(false)
    }
  }, [destination, startDate, endDate, onDataLoad])

  useEffect(() => { load() }, [load])

  const rainyDays = data?.days?.filter(d => d.is_rain_likely).length || 0

  return (
    <div className="intel-section">
      <button
        type="button"
        className="intel-section-toggle"
        onClick={() => setExpanded(e => !e)}
        aria-expanded={expanded}
      >
        <SunIcon width={15} height={15} />
        <span className="intel-section-title">Weather Outlook</span>
        {data && <SourceBadge source={data.source} />}
        {data && rainyDays > 0 && (
          <span className="intel-badge-warn">⚠ {rainyDays} rainy day{rainyDays !== 1 ? 's' : ''}</span>
        )}
        <ChevronDownIcon width={13} height={13} className={`intel-chevron${expanded ? ' flipped' : ''}`} />
        <button
          type="button"
          className="intel-recheck"
          onClick={e => { e.stopPropagation(); load() }}
          disabled={loading}
          aria-label="Refresh weather"
          title="Refresh"
        >
          <RefreshIcon width={12} height={12} />
        </button>
      </button>

      {expanded && (
        <div className="intel-section-body">
          {loading && !data && <div className="intel-loading"><span className="spinner spinner-accent" /> Loading weather…</div>}
          {error && (
            <div className="intel-error-row">
              <p className="intel-error"><AlertIcon width={13} height={13} /> {error}</p>
              <button className="btn btn-ghost btn-sm" type="button" onClick={load}>Retry</button>
            </div>
          )}
          {data && (
            <>
              {data.notes && <p className="intel-note">{data.notes}</p>}
              <div className="wx-grid">
                {data.days.map(d => <WeatherDay key={d.date} day={d} />)}
              </div>
              {data.attribution && (
                <p className="intel-attribution">{data.attribution}</p>
              )}
              {data.fetched_at && (
                <p className="intel-attribution">Updated: {new Date(data.fetched_at).toLocaleString()}</p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ─── POI section ──────────────────────────────────────────────────────────────

function PoiSection({ destination }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(false)
  const loadedRef = useRef(false)

  const load = useCallback(async () => {
    if (!destination) return
    setLoading(true)
    setError('')
    try {
      const res = await apiFetch(`${API_BASE}/poi/${encodeURIComponent(destination)}`)
      if (!res.ok) throw new Error(`POI data unavailable (${res.status})`)
      setData(await res.json())
      loadedRef.current = true
    } catch (err) {
      setError(err.message || 'Place data unavailable.')
    } finally {
      setLoading(false)
    }
  }, [destination])

  // Load on first expand only (not on mount) to avoid wasted network call
  const handleToggle = () => {
    const next = !expanded
    setExpanded(next)
    if (next && !loadedRef.current && !loading) {
      load()
    }
  }

  return (
    <div className="intel-section">
      <button
        type="button"
        className="intel-section-toggle"
        onClick={handleToggle}
        aria-expanded={expanded}
      >
        <MapIcon width={15} height={15} />
        <span className="intel-section-title">Nearby Places</span>
        {data && <SourceBadge source={data.source} />}
        {data && <span className="intel-count">{data.pois.length} places</span>}
        <ChevronDownIcon width={13} height={13} className={`intel-chevron${expanded ? ' flipped' : ''}`} />
        <button
          type="button"
          className="intel-recheck"
          onClick={e => { e.stopPropagation(); load() }}
          disabled={loading}
          aria-label="Refresh places"
          title="Refresh"
        >
          <RefreshIcon width={12} height={12} />
        </button>
      </button>

      {expanded && (
        <div className="intel-section-body">
          {loading && <div className="intel-loading"><span className="spinner spinner-accent" /> Loading places…</div>}
          {error && (
            <div className="intel-error-row">
              <p className="intel-error"><AlertIcon width={13} height={13} /> {error}</p>
              <button className="btn btn-ghost btn-sm" type="button" onClick={load}>Retry</button>
            </div>
          )}
          {data && (
            <>
              {data.notes && <p className="intel-note">{data.notes}</p>}
              <ul className="poi-list">
                {data.pois.slice(0, 20).map((p, i) => (
                  <li key={`${p.osm_id}-${i}`} className="poi-item">
                    <span className="poi-name">{p.name}</span>
                    <span className="poi-cat">{p.category.replace('tourism:', '').replace('historic:', '').replace('leisure:', '').replace('amenity:', '')}</span>
                    {p.website && (
                      <a href={p.website} target="_blank" rel="noopener noreferrer" className="poi-link">↗</a>
                    )}
                  </li>
                ))}
              </ul>
              {data.attribution && (
                <p className="intel-attribution">{data.attribution}</p>
              )}
            </>
          )}
          {!loading && !error && !data && (
            <p className="intel-note">Expand to load nearby places.</p>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Feasibility section ──────────────────────────────────────────────────────

function FeasibilitySection({ itinerary, onDataLoad }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(true)
  const prevHashRef = useRef(null)

  const analyse = useCallback(async () => {
    if (!itinerary) return
    setLoading(true)
    setError('')
    try {
      const res = await apiFetch(`${API_BASE}/itinerary/feasibility`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ itinerary }),
      })
      if (!res.ok) throw new Error(`Analysis failed (${res.status})`)
      const result = await res.json()
      setData(result)
      if (onDataLoad) onDataLoad(result)
    } catch (err) {
      setError(err.message || 'Feasibility analysis unavailable.')
    } finally {
      setLoading(false)
    }
  }, [itinerary, onDataLoad])

  // Re-analyse when daily_plans content changes (covers edits, reorders, adds, deletes)
  // Use a lightweight content hash to avoid re-running on unrelated prop changes
  useEffect(() => {
    if (!itinerary?.daily_plans) return
    // Stable hash: stringify activity ids, times, durations — enough to detect any structural change
    const hash = itinerary.daily_plans.map(d =>
      d.activities.map(a => `${a.id}:${a.time}:${a.duration_minutes}:${a.estimated_cost_per_person}`).join('|')
    ).join('||')
    if (hash !== prevHashRef.current) {
      prevHashRef.current = hash
      analyse()
    }
  }, [itinerary, analyse])

  const summary = data?.summary || {}
  const hasCritical = (summary.critical || 0) > 0
  const hasWarning  = (summary.warning  || 0) > 0

  const issuesByDay = {}
  const tripIssues = []
  if (data?.issues) {
    for (const issue of data.issues) {
      if (issue.day_number != null) {
        if (!issuesByDay[issue.day_number]) issuesByDay[issue.day_number] = []
        issuesByDay[issue.day_number].push(issue)
      } else {
        tripIssues.push(issue)
      }
    }
  }

  return (
    <div className="intel-section">
      <button
        type="button"
        className="intel-section-toggle"
        onClick={() => setExpanded(e => !e)}
        aria-expanded={expanded}
      >
        <SparkIcon width={15} height={15} />
        <span className="intel-section-title">Schedule Analysis</span>
        {data && hasCritical && <span className="intel-badge-crit">⚠ {summary.critical} conflict{summary.critical !== 1 ? 's' : ''}</span>}
        {data && !hasCritical && hasWarning && <span className="intel-badge-warn">{summary.warning} warning{summary.warning !== 1 ? 's' : ''}</span>}
        {data && !hasCritical && !hasWarning && <span className="intel-badge-ok">✓ No issues</span>}
        <ChevronDownIcon width={13} height={13} className={`intel-chevron${expanded ? ' flipped' : ''}`} />
        <button
          type="button"
          className="intel-recheck"
          onClick={e => { e.stopPropagation(); analyse() }}
          disabled={loading}
          aria-label="Re-analyse itinerary"
          title="Re-analyse"
        >
          <RefreshIcon width={12} height={12} />
        </button>
      </button>

      {expanded && (
        <div className="intel-section-body">
          {loading && <div className="intel-loading"><span className="spinner spinner-accent" /> Analysing…</div>}
          {error && <p className="intel-error"><AlertIcon width={13} height={13} /> {error}</p>}
          {data && data.issues.length === 0 && (
            <p className="intel-ok">✓ No schedule issues detected for this itinerary.</p>
          )}
          {data && tripIssues.length > 0 && (
            <div className="feasibility-group">
              <h4 className="feasibility-group-title">Whole trip</h4>
              {tripIssues.map((issue, i) => (
                <div key={i} className={`feasibility-issue sev-${issue.severity.toLowerCase()}`}>
                  <SeverityIcon severity={issue.severity} />
                  <div>
                    <p className="feasibility-msg">{issue.message}</p>
                    {issue.suggestion && <p className="feasibility-sug">💡 {issue.suggestion}</p>}
                  </div>
                </div>
              ))}
            </div>
          )}
          {data && Object.entries(issuesByDay).map(([dayNum, issues]) => (
            <div key={dayNum} className="feasibility-group">
              <h4 className="feasibility-group-title">Day {dayNum}</h4>
              {issues.map((issue, i) => (
                <div key={i} className={`feasibility-issue sev-${issue.severity.toLowerCase()}`}>
                  <SeverityIcon severity={issue.severity} />
                  <div>
                    <p className="feasibility-msg">{issue.message}</p>
                    {issue.suggestion && <p className="feasibility-sug">💡 {issue.suggestion}</p>}
                  </div>
                </div>
              ))}
            </div>
          ))}
          {data && (
            <p className="intel-attribution">
              Deterministic analysis · {data.analysed_days} day{data.analysed_days !== 1 ? 's' : ''} · {data.summary.total} issue{data.summary.total !== 1 ? 's' : ''} total
            </p>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Main panel ───────────────────────────────────────────────────────────────

export default function TravelIntelligencePanel({ itinerary, onContextUpdate, onWeatherLoad, onFeasibilityLoad }) {
  const [panelOpen, setPanelOpen] = useState(true)
  const [weatherData, setWeatherData] = useState(null)
  const [feasibilityData, setFeasibilityData] = useState(null)

  const handleWeatherData = useCallback((data) => {
    setWeatherData(data)
    if (onWeatherLoad) onWeatherLoad(data)
  }, [onWeatherLoad])

  const handleFeasibilityData = useCallback((data) => {
    setFeasibilityData(data)
    if (onFeasibilityLoad) onFeasibilityLoad(data)
  }, [onFeasibilityLoad])

  // Build a concise intelligence context string whenever weather or feasibility updates
  useEffect(() => {
    if (!onContextUpdate) return
    const parts = []

    if (weatherData?.days?.length) {
      const rainy = weatherData.days.filter(d => d.is_rain_likely)
      const src = weatherData.source
      parts.push(`Weather (${src}):`)
      weatherData.days.forEach(d => {
        const temp = (d.temp_min != null && d.temp_max != null)
          ? ` ${d.temp_min}–${d.temp_max}°C` : ''
        const rain = d.is_rain_likely ? ' — rain likely' : ''
        parts.push(`  ${d.date}: ${d.weather_label}${temp}${rain}`)
      })
      if (rainy.length) {
        parts.push(`  Note: ${rainy.length} day(s) with rain likely — consider indoor alternatives.`)
      }
    }

    if (feasibilityData?.issues?.length) {
      parts.push(`Schedule analysis:`)
      feasibilityData.issues.forEach(i => {
        parts.push(`  [${i.severity}] ${i.message}`)
      })
    } else if (feasibilityData) {
      parts.push(`Schedule analysis: No issues detected.`)
    }

    onContextUpdate(parts.join('\n'))
  }, [weatherData, feasibilityData, onContextUpdate])

  if (!itinerary?.config) return null

  const { destination, start_date, end_date } = itinerary.config

  return (
    <div className="travel-intel-panel panel-card">
      <button
        type="button"
        className="intel-panel-toggle"
        onClick={() => setPanelOpen(o => !o)}
        aria-expanded={panelOpen}
      >
        <CompassIcon width={16} height={16} />
        <span className="intel-panel-title">Travel Intelligence</span>
        <span className="intel-panel-sub">Weather · Places · Schedule</span>
        <ChevronDownIcon width={14} height={14} className={`intel-chevron${panelOpen ? ' flipped' : ''}`} />
      </button>

      {panelOpen && (
        <div className="intel-panel-body">
          <WeatherSection
            destination={destination}
            startDate={start_date}
            endDate={end_date}
            onDataLoad={handleWeatherData}
          />
          <FeasibilitySection
            itinerary={itinerary}
            onDataLoad={handleFeasibilityData}
          />
          <PoiSection destination={destination} />
        </div>
      )}
    </div>
  )
}
