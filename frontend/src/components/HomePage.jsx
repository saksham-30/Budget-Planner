import { useState, useMemo } from 'react'
import { DESTINATIONS } from '../data/destinations.js'
import { rankDestinations, getCurrentMonth, getSeasonLabel, MONTH_NAMES } from '../data/seasonalUtils.js'
import DestinationCard from './DestinationCard.jsx'
import DestinationDetail from './DestinationDetail.jsx'
import {
  CompassIcon,
  ArrowRightIcon,
  TrendingIcon,
  SunIcon,
  MountainIcon,
  UtensilsIcon,
  ZapIcon,
  TreeIcon,
  StarIcon,
  ClockFastIcon,
  TagIcon,
  MapIcon,
  ChevronRightIcon,
  SearchIcon,
  CalendarCheckIcon,
  ChevronDownIcon,
} from './Icons.jsx'

const SAMPLE_ITINERARIES = [
  {
    id: 1,
    destination: 'Goa',
    days: 5,
    theme: 'Beach & Culture',
    highlights: ['Calangute Beach', 'Old Goa Churches', 'Dudhsagar Falls'],
    budget: '₹25,000–35,000',
  },
  {
    id: 2,
    destination: 'Paris',
    days: 7,
    theme: 'Art & Gastronomy',
    highlights: ['Eiffel Tower', 'Louvre Museum', 'Montmartre'],
    budget: '€900–1,400',
  },
  {
    id: 3,
    destination: 'Bali',
    days: 8,
    theme: 'Nature & Wellness',
    highlights: ['Ubud Rice Terraces', 'Tanah Lot Temple', 'Mount Batur'],
    budget: '$600–1,000',
  },
]

const TRAVEL_STYLES = [
  { id: 'adventure',  label: 'Adventure',  icon: ZapIcon,       description: 'Trek, climb, and push your limits' },
  { id: 'relaxation', label: 'Relaxation', icon: SunIcon,       description: 'Beaches, spas, and slow living' },
  { id: 'culture',    label: 'Culture',    icon: CompassIcon,   description: 'History, art, and local traditions' },
  { id: 'budget',     label: 'Budget',     icon: TagIcon,       description: 'Maximum experience, minimum spend' },
  { id: 'nature',     label: 'Nature',     icon: TreeIcon,      description: 'Wildlife, forests, and landscapes' },
  { id: 'food',       label: 'Food',       icon: UtensilsIcon,  description: 'Flavours, markets, and chefs' },
]

export default function HomePage({ onNavigate, onPlanTrip, onBuildItinerary, travelMonth, onMonthChange }) {
  const [heroSearch, setHeroSearch] = useState('')
  const [selectedDestination, setSelectedDestination] = useState(null)

  // Use controlled month from parent if provided, otherwise manage locally
  const [localMonth, setLocalMonth] = useState(getCurrentMonth())
  const currentMonth = travelMonth || localMonth

  const handleMonthChange = (m) => {
    setLocalMonth(m)
    if (onMonthChange) onMonthChange(m)
  }

  const monthName = MONTH_NAMES[currentMonth - 1]
  const seasonLabel = getSeasonLabel(currentMonth)

  const trendingDestinations = useMemo(
    () => rankDestinations(DESTINATIONS, { month: currentMonth, sort: 'popularity' }).slice(0, 6),
    [currentMonth],
  )

  const seasonalPicks = useMemo(
    () => rankDestinations(DESTINATIONS, { month: currentMonth, sort: 'seasonal' }).slice(0, 4),
    [currentMonth],
  )
  const heroDestination = DESTINATIONS.find(destination => destination.id === 'bali') || DESTINATIONS[0]

  const handleHeroSubmit = (e) => {
    e.preventDefault()
    if (heroSearch.trim()) {
      const dest = DESTINATIONS.find(
        d => d.name.toLowerCase() === heroSearch.trim().toLowerCase(),
      )
      if (dest && onPlanTrip) {
        onPlanTrip(dest)
      } else if (onNavigate) {
        onNavigate('explore', { search: heroSearch.trim() })
      }
    } else if (onNavigate) {
      onNavigate('explore', {})
    }
  }

  const handleStyleClick = (styleId) => {
    if (onNavigate) onNavigate('explore', { category: styleId, month: currentMonth })
  }

  const handlePlanTrip = (destination) => {
    setSelectedDestination(null)
    if (onPlanTrip) onPlanTrip(destination)
  }

  const handleSelectDestination = (destination) => {
    setSelectedDestination(destination)
  }

  if (selectedDestination) {
    return (
      <div className="home-page">
        <DestinationDetail
          destination={selectedDestination}
          onClose={() => setSelectedDestination(null)}
          onPlanTrip={handlePlanTrip}
          onBuildItinerary={onBuildItinerary}
          currentMonth={currentMonth}
        />
      </div>
    )
  }

  return (
    <div className="home-page">
      {/* Hero */}
      <section
        className="hero-section"
        style={{ '--hero-image': `url("${heroDestination?.image || ''}")` }}
      >
        <div className="hero-inner">
          <div className="hero-content">
          <span className="hero-eyebrow">
            <CompassIcon width={14} height={14} />
            Thoughtful travel, beautifully planned
          </span>
          <h1 className="hero-title">The world is closer than <em>you think.</em></h1>
          <p className="hero-subtitle">
            Find a place that feels like you. We’ll help shape the days, make sense of the costs,
            and keep the whole trip within reach.
          </p>
          <form className="hero-search" onSubmit={handleHeroSubmit}>
            <div className="hero-search-input-wrap">
              <SearchIcon width={18} height={18} className="hero-search-icon" />
              <input
                type="text"
                className="hero-search-input"
                placeholder="Where do you want to go?"
                value={heroSearch}
                onChange={e => setHeroSearch(e.target.value)}
                list="hero-destination-options"
                autoComplete="off"
              />
              <datalist id="hero-destination-options">
                {DESTINATIONS.map(d => <option key={d.id} value={d.name} />)}
              </datalist>
            </div>
            <button type="submit" className="btn btn-primary hero-search-btn">
              Plan My Trip
              <ArrowRightIcon width={16} height={16} className="btn-trailing-icon" />
            </button>
          </form>
          <div className="hero-quick-links">
            <span className="hero-quick-label">A few places to begin</span>
            {['Goa', 'Paris', 'Tokyo', 'Bali', 'Barcelona'].map(name => (
              <button
                key={name}
                type="button"
                className="hero-quick-btn"
                onClick={() => {
                  const dest = DESTINATIONS.find(d => d.name === name)
                  if (dest) handleSelectDestination(dest)
                }}
              >
                {name}
              </button>
            ))}
          </div>
          </div>
          <aside className="hero-note-card" aria-label="Travel planning highlights">
            <span className="hero-note-kicker">Make room for wonder</span>
            <strong>Good trips start with a plan that feels like yours.</strong>
            <span className="hero-note-rule" aria-hidden="true" />
            <div className="hero-note-stats">
              <span><b>{DESTINATIONS.length}</b> places to explore</span>
              <span><b>1</b> clear trip budget</span>
            </div>
            <button type="button" className="hero-note-link" onClick={() => onNavigate && onNavigate('explore', {})}>
              Find your next place <ArrowRightIcon width={14} height={14} />
            </button>
          </aside>
        </div>
      </section>

      {/* Month selector */}
      <section className="home-section home-month-section">
        <div className="month-selector-row">
          <div className="month-selector-label">
            <CalendarCheckIcon width={16} height={16} />
            <span>Showing recommendations for</span>
          </div>
          <div className="month-selector-wrap">
            <select
              className="month-selector-select"
              value={currentMonth}
              onChange={e => handleMonthChange(Number(e.target.value))}
              aria-label="Select travel month"
            >
              {MONTH_NAMES.map((name, idx) => (
                <option key={name} value={idx + 1}>
                  {name}{idx + 1 === getCurrentMonth() ? ' (this month)' : ''}
                </option>
              ))}
            </select>
            <ChevronDownIcon width={14} height={14} className="month-selector-chevron" />
          </div>
          {currentMonth !== getCurrentMonth() && (
            <button
              type="button"
              className="link-btn"
              onClick={() => handleMonthChange(getCurrentMonth())}
            >
              Reset to now
            </button>
          )}
        </div>
      </section>

      {/* Seasonal Picks */}
      <section className="home-section seasonal-section">
        <div className="home-section-head">
          <div>
            <span className="eyebrow">
              <SunIcon width={13} height={13} /> {seasonLabel}
            </span>
            <h2 className="home-section-title">Best for {monthName}</h2>
            <p className="home-section-sub">
              Destinations at their finest this {currentMonth !== getCurrentMonth() ? 'month' : 'month'}
            </p>
          </div>
          <button
            type="button"
            className="link-btn"
            onClick={() => onNavigate && onNavigate('explore', { month: currentMonth })}
          >
            See all <ChevronRightIcon width={14} height={14} />
          </button>
        </div>
        <div className="seasonal-grid">
          {seasonalPicks.map(dest => (
            <DestinationCard
              key={dest.id}
              destination={dest}
              onSelect={handleSelectDestination}
              onPlanTrip={handlePlanTrip}
              month={currentMonth}
            />
          ))}
        </div>
      </section>

      {/* Trending Destinations */}
      <section className="home-section">
        <div className="home-section-head">
          <div>
            <span className="eyebrow">
              <TrendingIcon width={13} height={13} /> Popular
            </span>
            <h2 className="home-section-title">Trending Destinations</h2>
          </div>
          <button
            type="button"
            className="link-btn"
            onClick={() => onNavigate && onNavigate('explore', { sort: 'popularity', month: currentMonth })}
          >
            View all <ChevronRightIcon width={14} height={14} />
          </button>
        </div>
        <div className="trending-row">
          {trendingDestinations.map(dest => (
            <DestinationCard
              key={dest.id}
              destination={dest}
              onSelect={handleSelectDestination}
              onPlanTrip={handlePlanTrip}
              compact
              month={currentMonth}
            />
          ))}
        </div>
      </section>

      {/* Travel Styles */}
      <section className="home-section">
        <div className="home-section-head">
          <div>
            <span className="eyebrow">
              <MapIcon width={13} height={13} /> Browse by style
            </span>
            <h2 className="home-section-title">How Do You Like to Travel?</h2>
          </div>
        </div>
        <div className="style-grid">
          {TRAVEL_STYLES.map(style => {
            const Icon = style.icon
            return (
              <button
                key={style.id}
                type="button"
                className="style-card"
                onClick={() => handleStyleClick(style.id)}
              >
                <span className="style-card-icon">
                  <Icon width={24} height={24} />
                </span>
                <span className="style-card-label">{style.label}</span>
                <span className="style-card-desc">{style.description}</span>
              </button>
            )
          })}
        </div>
      </section>

      {/* Sample Itineraries */}
      <section className="home-section">
        <div className="home-section-head">
          <div>
            <span className="eyebrow">
              <StarIcon width={13} height={13} /> Inspiration
            </span>
            <h2 className="home-section-title">Popular Itineraries</h2>
            <p className="home-section-sub">Illustrative — not bookings or live prices</p>
          </div>
        </div>
        <div className="itinerary-previews">
          {SAMPLE_ITINERARIES.map(item => {
            const dest = DESTINATIONS.find(d => d.name === item.destination)
            return (
              <div key={item.id} className="itinerary-preview-card">
                {dest && (
                  <div className="itin-preview-image">
                    <img
                      src={dest.image}
                      alt={item.destination}
                      loading="lazy"
                      onError={(e) => { e.target.style.display = 'none' }}
                    />
                  </div>
                )}
                <div className="itin-preview-body">
                  <div className="itin-preview-head">
                    <span className="itin-preview-dest">{item.destination}</span>
                    <span className="itin-preview-theme">{item.theme}</span>
                  </div>
                  <div className="itin-preview-meta">
                    <span className="dest-meta-item">
                      <ClockFastIcon width={12} height={12} /> {item.days} days
                    </span>
                    <span className="dest-meta-item">
                      <TagIcon width={12} height={12} /> {item.budget}
                    </span>
                  </div>
                  <ul className="itin-preview-highlights">
                    {item.highlights.map(h => (
                      <li key={h}>{h}</li>
                    ))}
                  </ul>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm itin-preview-cta"
                    onClick={() => dest && handlePlanTrip(dest)}
                  >
                    Plan this trip
                    <ArrowRightIcon width={14} height={14} className="btn-trailing-icon" />
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      </section>

      {/* CTA Banner */}
      <section className="home-cta-banner">
        <div className="home-cta-content">
          <CompassIcon width={32} height={32} />
          <div>
            <h2>Ready to start planning?</h2>
            <p>Fill in your budget and dates — our AI will do the rest.</p>
          </div>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => onNavigate && onNavigate('planner', {})}
          >
            Open AI Planner
            <ArrowRightIcon width={16} height={16} className="btn-trailing-icon" />
          </button>
        </div>
      </section>
    </div>
  )
}
