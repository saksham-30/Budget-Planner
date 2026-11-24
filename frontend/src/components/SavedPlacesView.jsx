import { useState } from 'react'
import { DESTINATIONS } from '../data/destinations.js'
import { useSavedPlaces } from '../hooks/useSavedPlaces.js'
import DestinationCard from './DestinationCard.jsx'
import DestinationDetail from './DestinationDetail.jsx'
import { HeartIcon, CompassIcon, ArrowRightIcon } from './Icons.jsx'

export default function SavedPlacesView({ onNavigate, onPlanTrip, onBuildItinerary }) {
  const { savedIds, removeSaved } = useSavedPlaces()
  const [selectedDestination, setSelectedDestination] = useState(null)

  const savedDestinations = DESTINATIONS.filter(d => savedIds.includes(d.id))

  const handlePlanTrip = (destination) => {
    setSelectedDestination(null)
    if (onPlanTrip) onPlanTrip(destination)
  }

  const handleBuildItinerary = (destination) => {
    setSelectedDestination(null)
    if (onBuildItinerary) onBuildItinerary(destination)
  }

  if (selectedDestination) {
    return (
      <DestinationDetail
        destination={selectedDestination}
        onClose={() => setSelectedDestination(null)}
        onPlanTrip={handlePlanTrip}
        onBuildItinerary={handleBuildItinerary}
      />
    )
  }

  return (
    <div className="saved-places-page">
      <div className="saved-places-header">
        <div>
          <span className="eyebrow">
            <span className="eyebrow-dot" aria-hidden="true" />
            Wishlist
          </span>
          <h1 className="saved-places-title">Saved Places</h1>
          <p className="saved-places-sub">
            Destinations you've saved for later
            {savedDestinations.length > 0 && (
              <> · <strong>{savedDestinations.length}</strong> place{savedDestinations.length !== 1 ? 's' : ''}</>
            )}
          </p>
        </div>
      </div>

      {savedDestinations.length === 0 ? (
        <div className="saved-places-empty">
          <div className="saved-places-empty-icon">
            <HeartIcon width={36} height={36} />
          </div>
          <h2>No saved places yet</h2>
          <p>
            Tap the heart icon on any destination card to save it here for later.
          </p>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => onNavigate && onNavigate('explore', {})}
          >
            <CompassIcon width={16} height={16} />
            Explore Destinations
            <ArrowRightIcon width={16} height={16} className="btn-trailing-icon" />
          </button>
        </div>
      ) : (
        <div className="destinations-grid">
          {savedDestinations.map(dest => (
            <DestinationCard
              key={dest.id}
              destination={dest}
              onSelect={setSelectedDestination}
              onPlanTrip={handlePlanTrip}
            />
          ))}
        </div>
      )}
    </div>
  )
}
