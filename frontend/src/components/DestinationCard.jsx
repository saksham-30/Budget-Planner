import { useState } from 'react'
import {
  ImageOffIcon,
  MapIcon,
  ClockFastIcon,
  TagIcon,
  StarIcon,
  HeartIcon,
  HeartFilledIcon,
} from './Icons.jsx'
import { getSeasonalScore, getCurrentMonth } from '../data/seasonalUtils.js'
import { useSavedPlaces } from '../hooks/useSavedPlaces.js'

function formatBudgetRange(range) {
  if (!range) return '—'
  const { min, max, currency } = range
  const sym = { INR: '₹', USD: '$', EUR: '€', GBP: '£' }[currency] || currency
  const fmt = (n) => n >= 1000 ? `${sym}${(n / 1000).toFixed(0)}k` : `${sym}${n}`
  return `${fmt(min)}–${fmt(max)}`
}

function SeasonBadge({ destination, month }) {
  const effectiveMonth = month || getCurrentMonth()
  const score = getSeasonalScore(destination, effectiveMonth)
  if (score >= 90) return <span className="dest-season-badge season-peak">Best season</span>
  if (score >= 60) return <span className="dest-season-badge season-good">Good time</span>
  if (score >= 30) return <span className="dest-season-badge season-ok">Acceptable</span>
  return <span className="dest-season-badge season-off">Off season</span>
}

export function DestinationCardSkeleton() {
  return (
    <div className="destination-card destination-card-skeleton" aria-hidden="true">
      <div className="destination-card-image skeleton-block" />
      <div className="destination-card-body">
        <div className="skeleton-line" style={{ width: '65%', height: '1.1rem' }} />
        <div className="skeleton-line" style={{ width: '40%', height: '0.8rem', marginTop: '0.3rem' }} />
        <div className="skeleton-line" style={{ width: '90%', height: '0.75rem', marginTop: '0.6rem' }} />
        <div className="skeleton-line" style={{ width: '75%', height: '0.75rem', marginTop: '0.3rem' }} />
      </div>
    </div>
  )
}

const CATEGORY_LABELS = {
  adventure: 'Adventure',
  relaxation: 'Relaxation',
  culture: 'Culture',
  budget: 'Budget',
  nature: 'Nature',
  food: 'Food',
}

export default function DestinationCard({ destination, onSelect, onPlanTrip, compact, month }) {
  const [imgError, setImgError] = useState(false)
  const { isSaved, toggleSaved } = useSavedPlaces()
  const saved = isSaved(destination.id)

  const handleImgError = (e) => {
    e.target.style.display = 'none'
    setImgError(true)
  }

  const visibleCategories = (destination.categories || []).slice(0, compact ? 2 : 3)

  return (
    <article className={`destination-card${compact ? ' destination-card-compact' : ''}`}>
      <div className="destination-card-image">
        {!imgError && (
          <img
            src={destination.image}
            alt={destination.name}
            loading="lazy"
            onError={handleImgError}
          />
        )}
        {imgError && (
          <div className="destination-card-image-fallback">
            <ImageOffIcon width={28} height={28} />
            <span>Image unavailable</span>
          </div>
        )}
        <div className="destination-card-image-overlay">
          <SeasonBadge destination={destination} month={month} />
          <button
            type="button"
            className={`dest-card-heart${saved ? ' saved' : ''}`}
            onClick={(e) => { e.stopPropagation(); toggleSaved(destination.id) }}
            aria-label={saved ? `Remove ${destination.name} from saved places` : `Save ${destination.name}`}
            title={saved ? 'Remove from saved' : 'Save destination'}
          >
            {saved
              ? <HeartFilledIcon width={16} height={16} />
              : <HeartIcon width={16} height={16} />}
          </button>
        </div>
      </div>

      <div className="destination-card-body">
        <div className="dest-card-header">
          <div className="dest-card-header-text">
            <h3 className="dest-card-name">{destination.name}</h3>
            <span className="dest-card-country">
              <MapIcon width={12} height={12} />
              {destination.country}
            </span>
          </div>
          <div className="dest-card-score">
            <StarIcon width={12} height={12} />
            <span>{destination.popularityScore}</span>
          </div>
        </div>

        {!compact && (
          <p className="dest-card-tagline">{destination.tagline}</p>
        )}

        <div className="dest-card-meta">
          <span className="dest-meta-item">
            <ClockFastIcon width={12} height={12} />
            {destination.recommendedDays}d
          </span>
          <span className="dest-meta-item">
            <TagIcon width={12} height={12} />
            {formatBudgetRange(destination.budgetRange)}
          </span>
        </div>

        <div className="dest-card-categories">
          {visibleCategories.map(cat => (
            <span key={cat} className={`dest-category-tag cat-${cat}`}>
              {CATEGORY_LABELS[cat] || cat}
            </span>
          ))}
        </div>

        {!compact && destination.recommendationReason && (
          <p className="dest-card-reason">{destination.recommendationReason}</p>
        )}

        <div className="dest-card-actions">
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => onSelect && onSelect(destination)}
          >
            View details
          </button>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => onPlanTrip && onPlanTrip(destination)}
          >
            Plan trip
          </button>
        </div>
      </div>
    </article>
  )
}
