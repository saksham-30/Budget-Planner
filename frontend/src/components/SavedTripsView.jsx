import { useCallback, useEffect, useState } from 'react'
import { csrfFetch } from '../auth.js'
import { MapIcon, ClockFastIcon, TagIcon, ArrowRightIcon, RefreshIcon, AlertIcon } from './Icons.jsx'
import { DESTINATIONS } from '../data/destinations.js'

const API_BASE = '/api'

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

function formatDate(value) {
  if (!value) return ''
  const raw = String(value)
  const parsed = new Date(raw.includes('T') ? raw : `${raw.replace(' ', 'T')}Z`)
  if (Number.isNaN(parsed.getTime())) return ''
  return parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

export default function SavedTripsView({ onOpenTrip }) {
  const [trips, setTrips] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadTrips = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const response = await csrfFetch(`${API_BASE}/trips`)
      if (!response.ok) throw new Error(`Could not load saved trips (${response.status}).`)
      const data = await response.json()
      if (Array.isArray(data)) setTrips(data)
    } catch (err) {
      setError(err.message || 'Failed to load trips.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadTrips() }, [loadTrips])

  return (
    <div className="saved-trips-page">
      <div className="saved-trips-header">
        <div>
          <span className="eyebrow"><span className="eyebrow-dot" aria-hidden="true" /> Saved</span>
          <h1 className="saved-trips-title">My Trips</h1>
          <p className="saved-trips-sub">Your saved trip plans from the AI planner</p>
        </div>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={loadTrips}
          disabled={loading}
          aria-label="Refresh trips"
        >
          <RefreshIcon width={15} height={15} />
          Refresh
        </button>
      </div>

      {loading && (
        <div className="saved-trips-loading">
          <span className="spinner spinner-accent" />
          <span>Loading your trips…</span>
        </div>
      )}

      {!loading && error && (
        <div className="banner banner-error" role="alert">
          <AlertIcon className="banner-icon" />
          <div className="banner-copy">
            <div className="banner-title">Could not load trips</div>
            <p className="banner-body">{error}</p>
          </div>
          <div className="banner-actions">
            <button className="btn btn-ghost btn-sm" type="button" onClick={loadTrips}>
              Try again
            </button>
          </div>
        </div>
      )}

      {!loading && !error && trips.length === 0 && (
        <div className="saved-trips-empty">
          <MapIcon width={40} height={40} />
          <h2>No saved trips yet</h2>
          <p>Plan a trip in the AI Planner and it will appear here automatically.</p>
        </div>
      )}

      {!loading && !error && trips.length > 0 && (
        <ul className="saved-trips-list">
          {trips.map(trip => {
            const destination = DESTINATIONS.find(item => item.name.toLowerCase() === String(trip.destination || '').toLowerCase())
            return (
              <li key={trip.trip_id} className="saved-trip-row">
                <div className="saved-trip-cover">
                  {destination ? (
                    <img src={destination.image} alt={destination.name} loading="lazy" />
                  ) : (
                    <div className="saved-trip-cover-fallback"><MapIcon width={30} height={30} /></div>
                  )}
                  <span className="saved-trip-cover-label">Saved itinerary</span>
                </div>
                <div className="saved-trip-info">
                  <div className="saved-trip-card-heading">
                    <div>
                      <span className="saved-trip-kicker">Your next chapter</span>
                      <h2 className="saved-trip-dest">{trip.destination}</h2>
                    </div>
                    <span className="saved-trip-trip-count">Trip</span>
                  </div>
                  <div className="saved-trip-meta">
                    <span className="dest-meta-item">
                      <MapIcon width={14} height={14} />
                      {trip.travelers} traveler{trip.travelers === 1 ? '' : 's'}
                    </span>
                    <span className="dest-meta-item">
                      <TagIcon width={14} height={14} />
                      {formatMoney(trip.budget, trip.currency)}
                    </span>
                    {trip.updated_at && (
                    <span className="dest-meta-item">
                      <ClockFastIcon width={14} height={14} />
                      {formatDate(trip.updated_at)}
                    </span>
                  )}
                  </div>
                  <div className="saved-trip-card-footer">
                    <span>Plan updated {formatDate(trip.updated_at) || 'recently'}</span>
                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      onClick={() => onOpenTrip && onOpenTrip(trip.trip_id)}
                    >
                      Open trip
                      <ArrowRightIcon width={14} height={14} className="btn-trailing-icon" />
                    </button>
                  </div>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
