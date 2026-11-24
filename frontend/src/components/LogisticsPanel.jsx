/**
 * LogisticsPanel — Phase 6
 *
 * Collapsible panel surfacing flight search links, accommodation search
 * links, and static local transport guidance.
 *
 * API: GET /api/logistics/{destination}
 *   ?origin=…&start_date=…&end_date=…&travelers=…&budget_style=…&accommodation_preference=…
 *
 * No bookings are made. All external links open third-party search pages
 * in a new tab. Users control every action.
 *
 * Data labels:
 *   SEARCH_LINK  — parameterised search URL (not a booking or guaranteed fare)
 *   STATIC       — curated transport guidance (not live schedules)
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { csrfFetch } from '../auth.js'
import {
  AlertIcon,
  ArrowRightIcon,
  ChevronDownIcon,
  InfoIcon,
  MapIcon,
  PinIcon,
  RefreshIcon,
  RouteIcon,
  SearchIcon,
  TagIcon,
  WalletIcon,
} from './Icons.jsx'

const API_BASE = '/api'

// ─── Utilities ────────────────────────────────────────────────────────────────

function ExternalLink({ href, label, icon: Icon, variant = 'ghost' }) {
  if (!href) return null
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={`logistics-link btn btn-${variant} btn-sm`}
      aria-label={`${label} (opens in new tab)`}
    >
      {Icon && <Icon width={13} height={13} />}
      {label}
      <ArrowRightIcon width={12} height={12} className="btn-trailing-icon" />
    </a>
  )
}

function SourceTag({ label }) {
  return <span className="logistics-source-tag">{label}</span>
}

// ─── Flights section ──────────────────────────────────────────────────────────

function FlightsSection({ data }) {
  const f = data.flights
  return (
    <div className="logistics-subsection">
      <div className="logistics-subsection-head">
        <RouteIcon width={14} height={14} />
        <span className="logistics-subsection-title">Flights</span>
        <SourceTag label="SEARCH LINKS" />
      </div>
      <div className="logistics-detail">
        <span className="logistics-detail-row">
          <TagIcon width={12} height={12} />
          {f.origin} → {f.destination}
          {f.airport_code && <span className="logistics-iata"> ({f.airport_code})</span>}
        </span>
        <span className="logistics-detail-row">
          <TagIcon width={12} height={12} />
          {f.depart_date} → {f.return_date || 'one way'} · {f.adults} traveler{f.adults !== 1 ? 's' : ''}
        </span>
      </div>
      <div className="logistics-links-row">
        <ExternalLink href={f.google_flights} label="Google Flights" icon={SearchIcon} variant="primary" />
        <ExternalLink href={f.skyscanner}    label="Skyscanner"    icon={SearchIcon} />
        <ExternalLink href={f.rome2rio}      label="Rome2Rio"      icon={RouteIcon} />
      </div>
      <p className="logistics-note">
        <InfoIcon width={11} height={11} />
        {f.notes}
      </p>
    </div>
  )
}

// ─── Accommodation section ────────────────────────────────────────────────────

function AccommodationSection({ data }) {
  const a = data.accommodation
  return (
    <div className="logistics-subsection">
      <div className="logistics-subsection-head">
        <PinIcon width={14} height={14} />
        <span className="logistics-subsection-title">Accommodation</span>
        <SourceTag label="SEARCH LINKS" />
      </div>
      <div className="logistics-detail">
        <span className="logistics-detail-row">
          <TagIcon width={12} height={12} />
          {a.destination} · {a.checkin} → {a.checkout} · {a.adults} guest{a.adults !== 1 ? 's' : ''}
        </span>
        <span className="logistics-detail-row">
          <WalletIcon width={12} height={12} />
          Style: {a.budget_style}
        </span>
      </div>
      <div className="logistics-links-row">
        <ExternalLink href={a.booking_com}  label="Booking.com" icon={SearchIcon} variant="primary" />
        <ExternalLink href={a.airbnb}       label="Airbnb"      icon={SearchIcon} />
        {a.hostelworld && (
          <ExternalLink href={a.hostelworld} label="Hostelworld" icon={SearchIcon} />
        )}
      </div>
      <p className="logistics-note">
        <InfoIcon width={11} height={11} />
        {a.notes}
      </p>
    </div>
  )
}

// ─── Transport section ────────────────────────────────────────────────────────

function TransportSection({ data }) {
  const t = data.transport
  return (
    <div className="logistics-subsection">
      <div className="logistics-subsection-head">
        <MapIcon width={14} height={14} />
        <span className="logistics-subsection-title">Getting Around</span>
        <SourceTag label="STATIC" />
      </div>

      <div className="logistics-transport-grid">
        <div className="logistics-transport-item">
          <span className="logistics-transport-label">Airport transfer</span>
          <p className="logistics-transport-text">{t.airport_transfer}</p>
        </div>
        <div className="logistics-transport-item">
          <span className="logistics-transport-label">Local transport</span>
          <p className="logistics-transport-text">{t.local_transport}</p>
        </div>
        <div className="logistics-transport-item">
          <span className="logistics-transport-label">Public transport</span>
          <p className="logistics-transport-text">{t.public_transport}</p>
        </div>
      </div>

      {t.rome2rio_url && (
        <div className="logistics-links-row" style={{ marginTop: '0.65rem' }}>
          <ExternalLink href={t.rome2rio_url} label="Explore routes on Rome2Rio" icon={RouteIcon} />
        </div>
      )}

      {t.notes && (
        <p className="logistics-note">
          <InfoIcon width={11} height={11} />
          {t.notes}
        </p>
      )}
    </div>
  )
}

// ─── Main panel ───────────────────────────────────────────────────────────────

export default function LogisticsPanel({ itinerary, onDataLoad }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [panelOpen, setPanelOpen] = useState(false)
  const loadedForRef = useRef(null)  // tracks last loaded destination+dates key

  const config = itinerary?.config
  const cacheKey = config
    ? `${config.destination}|${config.start_date}|${config.end_date}|${config.travelers}`
    : null

  const load = useCallback(async () => {
    if (!config?.destination || !config?.start_date || !config?.end_date) return
    setLoading(true)
    setError('')
    try {
      const params = new URLSearchParams({
        origin: config.origin || '',
        start_date: config.start_date,
        end_date: config.end_date,
        travelers: String(config.travelers || 1),
        budget_style: config.budget_style || 'moderate',
        accommodation_preference: config.accommodation_preference || 'hotel',
      })
      const res = await csrfFetch(
        `${API_BASE}/logistics/${encodeURIComponent(config.destination)}?${params}`
      )
      if (!res.ok) throw new Error(`Logistics data unavailable (${res.status})`)
      const result = await res.json()
      setData(result)
      loadedForRef.current = cacheKey
      if (onDataLoad) onDataLoad(result)
    } catch (err) {
      setError(err.message || 'Logistics data unavailable.')
    } finally {
      setLoading(false)
    }
  }, [config, cacheKey])

  // Load on first open, or reload if trip details changed
  const handleToggle = () => {
    const next = !panelOpen
    setPanelOpen(next)
    if (next && (!data || loadedForRef.current !== cacheKey)) {
      load()
    }
  }

  if (!config) return null

  return (
    <div className="logistics-panel panel-card">
      <button
        type="button"
        className="logistics-panel-toggle"
        onClick={handleToggle}
        aria-expanded={panelOpen}
      >
        <RouteIcon width={16} height={16} />
        <span className="logistics-panel-title">Travel Logistics</span>
        <span className="logistics-panel-sub">Flights · Accommodation · Transport</span>
        <ChevronDownIcon width={14} height={14} className={`intel-chevron${panelOpen ? ' flipped' : ''}`} />
        {panelOpen && (
          <button
            type="button"
            className="intel-recheck"
            onClick={e => { e.stopPropagation(); load() }}
            disabled={loading}
            aria-label="Refresh logistics"
            title="Refresh"
          >
            <RefreshIcon width={12} height={12} />
          </button>
        )}
      </button>

      {panelOpen && (
        <div className="logistics-panel-body">
          {loading && (
            <div className="intel-loading">
              <span className="spinner spinner-accent" /> Loading logistics…
            </div>
          )}
          {error && !loading && (
            <div className="intel-error-row">
              <p className="intel-error">
                <AlertIcon width={13} height={13} /> {error}
              </p>
              <button className="btn btn-ghost btn-sm" type="button" onClick={load}>
                Retry
              </button>
            </div>
          )}
          {data && !loading && (
            <>
              <FlightsSection data={data} />
              <AccommodationSection data={data} />
              <TransportSection data={data} />
              {data.disclaimer && (
                <p className="logistics-disclaimer">
                  <InfoIcon width={11} height={11} />
                  {data.disclaimer}
                </p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
