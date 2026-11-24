import { lazy, Suspense, useState } from 'react'
import {
  ImageOffIcon,
  ArrowRightIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ClockFastIcon,
  TagIcon,
  SunIcon,
  MapIcon,
  StarIcon,
  CheckIcon,
  HeartIcon,
  HeartFilledIcon,
  PinIcon,
  BoltIcon,
  InfoIcon,
  CompassIcon,
} from './Icons.jsx'
import { getSeasonalScore, MONTH_NAMES_SHORT } from '../data/seasonalUtils.js'
import { useSavedPlaces } from '../hooks/useSavedPlaces.js'
import { DESTINATIONS } from '../data/destinations.js'

const AttractionsMap = lazy(() => import('./AttractionsMap.jsx'))

const CATEGORY_LABELS = {
  adventure: 'Adventure',
  relaxation: 'Relaxation',
  culture: 'Culture',
  budget: 'Budget',
  nature: 'Nature',
  food: 'Food',
}

function formatBudgetRange(range) {
  if (!range) return '—'
  const { min, max, currency } = range
  const sym = { INR: '₹', USD: '$', EUR: '€', GBP: '£' }[currency] || currency
  return `${sym}${min.toLocaleString()} – ${sym}${max.toLocaleString()}`
}

function suitabilityLabel(score) {
  if (score >= 90) return { label: 'Excellent', cls: 'suit-excellent' }
  if (score >= 60) return { label: 'Good', cls: 'suit-good' }
  if (score >= 30) return { label: 'Fair', cls: 'suit-fair' }
  return { label: 'Poor', cls: 'suit-poor' }
}

export default function DestinationDetail({ destination, onClose, onPlanTrip, onBuildItinerary, currentMonth: propMonth }) {
  const [imgError, setImgError] = useState(false)
  const { isSaved, toggleSaved } = useSavedPlaces()
  const [nearbyDest, setNearbyDest] = useState(null)

  const currentMonth = propMonth || (new Date().getMonth() + 1)
  const saved = isSaved(destination.id)

  const handleImgError = (e) => {
    e.target.style.display = 'none'
    setImgError(true)
  }

  // Resolve nearby destinations from IDs
  const nearbyDestinations = (destination.nearbyDestinations || [])
    .map(id => DESTINATIONS.find(d => d.id === id))
    .filter(Boolean)
    .slice(0, 3)

  if (nearbyDest) {
    return (
      <DestinationDetail
        destination={nearbyDest}
        onClose={() => setNearbyDest(null)}
        onPlanTrip={onPlanTrip}
        currentMonth={currentMonth}
      />
    )
  }

  const normaliseAttractions = (attractions) => {
    if (!attractions || attractions.length === 0) return []
    return attractions.map(a =>
      typeof a === 'string' ? { name: a, description: '' } : a,
    )
  }

  return (
    <div className="dest-detail-wrap">
      {/* Hero */}
      <div className="dest-detail-hero">
        {!imgError && (
          <img
            src={destination.image}
            alt={destination.name}
            onError={handleImgError}
          />
        )}
        {imgError && (
          <div className="dest-detail-hero-fallback">
            <ImageOffIcon width={48} height={48} />
          </div>
        )}
        <div className="dest-detail-hero-overlay">
          <div className="dest-detail-hero-actions">
            <button
              type="button"
              className="dest-detail-back"
              onClick={onClose}
              aria-label="Back to destinations"
            >
              <ChevronLeftIcon width={16} height={16} />
              Back
            </button>
            <button
              type="button"
              className={`dest-detail-heart${saved ? ' saved' : ''}`}
              onClick={() => toggleSaved(destination.id)}
              aria-label={saved ? `Remove ${destination.name} from saved places` : `Save ${destination.name}`}
            >
              {saved
                ? <><HeartFilledIcon width={16} height={16} /> Saved</>
                : <><HeartIcon width={16} height={16} /> Save</>}
            </button>
          </div>
          <div className="dest-detail-hero-text">
            <div className="dest-detail-country">
              <MapIcon width={14} height={14} />
              {destination.country}
              {destination.region && destination.region !== destination.country && (
                <> · {destination.region}</>
              )}
            </div>
            <h1 className="dest-detail-name">{destination.name}</h1>
            <p className="dest-detail-tagline">{destination.tagline}</p>
          </div>
        </div>
      </div>

      <div className="dest-detail-body">
        <div className="dest-detail-main">

          {/* Overview */}
          <section className="dest-detail-section">
            <h2 className="dest-detail-section-title">Overview</h2>
            <p className="dest-detail-description">{destination.description}</p>
            <div className="dest-detail-tags">
              {(destination.categories || []).map(cat => (
                <span key={cat} className={`dest-category-tag cat-${cat}`}>
                  {CATEGORY_LABELS[cat] || cat}
                </span>
              ))}
            </div>
          </section>

          {/* Highlights */}
          {destination.highlights && destination.highlights.length > 0 && (
            <section className="dest-detail-section">
              <h2 className="dest-detail-section-title">
                <StarIcon width={16} height={16} />
                Highlights
              </h2>
              <ul className="dest-detail-highlights">
                {destination.highlights.map((h, i) => (
                  <li key={i}>
                    <CheckIcon width={13} height={13} />
                    {h}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* Top Attractions */}
          <section className="dest-detail-section">
            <h2 className="dest-detail-section-title">
              <CompassIcon width={16} height={16} />
              Top Attractions
            </h2>
            <ul className="dest-detail-attractions">
              {normaliseAttractions(destination.attractions).map((attr) => (
                <li key={attr.name} className="dest-attraction-item">
                  <div className="dest-attraction-name">
                    <BoltIcon width={12} height={12} />
                    {attr.name}
                  </div>
                  {attr.description && (
                    <p className="dest-attraction-desc">{attr.description}</p>
                  )}
                </li>
              ))}
            </ul>
          </section>

          <Suspense fallback={<section className="dest-detail-section attractions-map-section"><h2 className="dest-detail-section-title">Attractions map</h2><div className="attractions-map-message" role="status">Preparing map…</div></section>}>
            <AttractionsMap destination={destination} />
          </Suspense>

          {/* Best Time & Seasonal Suitability */}
          <section className="dest-detail-section">
            <h2 className="dest-detail-section-title">
              <SunIcon width={16} height={16} />
              Best Time to Visit
            </h2>
            <p className="dest-detail-weather">{destination.weatherSummary}</p>

            <div className="dest-detail-months" role="list" aria-label="Month-by-month suitability">
              {MONTH_NAMES_SHORT.map((name, idx) => {
                const monthNum = idx + 1
                const score = getSeasonalScore(destination, monthNum)
                const isIdeal = score >= 90
                const isCurrent = monthNum === currentMonth
                const { label, cls } = suitabilityLabel(score)
                return (
                  <span
                    key={name}
                    role="listitem"
                    className={`dest-month-chip ${cls}${isCurrent ? ' current' : ''}`}
                    title={`${name}: ${label}`}
                    aria-label={`${name}: ${label}${isCurrent ? ' (this month)' : ''}`}
                  >
                    {name}
                  </span>
                )
              })}
            </div>
            <div className="dest-month-legend">
              <span className="dest-month-chip suit-excellent" aria-hidden="true" /> Excellent
              <span className="dest-month-chip suit-good" aria-hidden="true" style={{ marginLeft: '0.75rem' }} /> Good
              <span className="dest-month-chip suit-fair" aria-hidden="true" style={{ marginLeft: '0.75rem' }} /> Fair
              <span className="dest-month-chip suit-poor" aria-hidden="true" style={{ marginLeft: '0.75rem' }} /> Poor
              <span className="dest-month-chip current" aria-hidden="true" style={{ marginLeft: '1rem' }} /> This month
            </div>
          </section>

          {/* Accommodation */}
          {destination.accommodationAreas && destination.accommodationAreas.length > 0 && (
            <section className="dest-detail-section">
              <h2 className="dest-detail-section-title">
                <PinIcon width={16} height={16} />
                Where to Stay
              </h2>
              <ul className="dest-detail-list">
                {destination.accommodationAreas.map((area, i) => (
                  <li key={i}>
                    <CheckIcon width={12} height={12} />
                    {area}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* Transport */}
          {destination.transport && (
            <section className="dest-detail-section">
              <h2 className="dest-detail-section-title">
                <CompassIcon width={16} height={16} />
                Getting Around
              </h2>
              <p className="dest-detail-description">{destination.transport}</p>
            </section>
          )}

          {/* Travel Tips */}
          {destination.tips && destination.tips.length > 0 && (
            <section className="dest-detail-section">
              <h2 className="dest-detail-section-title">
                <InfoIcon width={16} height={16} />
                Travel Tips
              </h2>
              <ul className="dest-detail-tips">
                {destination.tips.map((tip, i) => (
                  <li key={i}>{tip}</li>
                ))}
              </ul>
            </section>
          )}

          {/* Nearby Destinations */}
          {nearbyDestinations.length > 0 && (
            <section className="dest-detail-section">
              <h2 className="dest-detail-section-title">
                <MapIcon width={16} height={16} />
                Nearby Destinations
              </h2>
              <div className="dest-nearby-grid">
                {nearbyDestinations.map(near => (
                  <button
                    key={near.id}
                    type="button"
                    className="dest-nearby-card"
                    onClick={() => setNearbyDest(near)}
                  >
                    <div className="dest-nearby-image">
                      <img
                        src={near.image}
                        alt={near.name}
                        loading="lazy"
                        onError={(e) => { e.target.style.display = 'none' }}
                      />
                    </div>
                    <div className="dest-nearby-info">
                      <span className="dest-nearby-name">{near.name}</span>
                      <span className="dest-nearby-country">{near.country}</span>
                    </div>
                    <ChevronRightIcon width={14} height={14} />
                  </button>
                ))}
              </div>
            </section>
          )}

        </div>

        {/* Sidebar */}
        <aside className="dest-detail-sidebar">
          {/* Quick Facts */}
          <div className="dest-detail-sidebar-card">
            <h3 className="dest-sidebar-heading">Quick Facts</h3>
            <dl className="dest-facts-list">
              <div className="dest-fact">
                <dt><StarIcon width={13} height={13} /> Popularity</dt>
                <dd>{destination.popularityScore}/10</dd>
              </div>
              <div className="dest-fact">
                <dt><ClockFastIcon width={13} height={13} /> Recommended stay</dt>
                <dd>{destination.recommendedDays} days</dd>
              </div>
              <div className="dest-fact">
                <dt><TagIcon width={13} height={13} /> Approx. budget</dt>
                <dd>{formatBudgetRange(destination.budgetRange)}</dd>
              </div>
              <div className="dest-fact">
                <dt><SunIcon width={13} height={13} /> This month</dt>
                <dd>
                  {(() => {
                    const sc = getSeasonalScore(destination, currentMonth)
                    return sc >= 90 ? 'Excellent' : sc >= 60 ? 'Good' : sc >= 30 ? 'Fair' : 'Poor'
                  })()}
                </dd>
              </div>
              {destination.region && (
                <div className="dest-fact">
                  <dt><MapIcon width={13} height={13} /> Region</dt>
                  <dd>{destination.region}</dd>
                </div>
              )}
            </dl>
          </div>

          {/* Budget note */}
          <div className="dest-detail-budget-note">
            <InfoIcon width={13} height={13} />
            <p>
              Budget figures are approximate and illustrative only. Actual costs
              depend on travel style, booking timing, and exchange rates.
            </p>
          </div>

          {/* Plan CTA */}
          <div className="dest-detail-cta">
            <h3 className="dest-sidebar-heading">Plan This Trip</h3>
            <p>
              Let our AI agents build a detailed itinerary and cost breakdown
              for {destination.name}.
            </p>
            <button
              type="button"
              className="btn btn-primary"
              style={{ width: '100%', marginTop: '0.75rem' }}
              onClick={() => onBuildItinerary
                ? onBuildItinerary(destination)
                : onPlanTrip && onPlanTrip(destination)}
            >
              Build itinerary for {destination.name}
              <ArrowRightIcon width={16} height={16} className="btn-trailing-icon" />
            </button>
            {onPlanTrip && (
              <button
                type="button"
                className="btn btn-ghost"
                style={{ width: '100%', marginTop: '0.5rem' }}
                onClick={() => onPlanTrip(destination)}
              >
                Open in AI Trip Planner
              </button>
            )}
            <button
              type="button"
              className={`btn btn-ghost dest-save-btn${saved ? ' saved' : ''}`}
              style={{ width: '100%', marginTop: '0.5rem' }}
              onClick={() => toggleSaved(destination.id)}
            >
              {saved
                ? <><HeartFilledIcon width={15} height={15} /> Saved to wishlist</>
                : <><HeartIcon width={15} height={15} /> Save to wishlist</>}
            </button>
          </div>
        </aside>
      </div>
    </div>
  )
}
