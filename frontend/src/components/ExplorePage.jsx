import { useState, useMemo, useEffect } from 'react'
import { DESTINATIONS } from '../data/destinations.js'
import { rankDestinations, getCurrentMonth, MONTH_NAMES } from '../data/seasonalUtils.js'
import DestinationCard, { DestinationCardSkeleton } from './DestinationCard.jsx'
import DestinationDetail from './DestinationDetail.jsx'
import {
  SearchIcon,
  FilterIcon,
  GridIcon,
  XIcon,
  SortIcon,
  CalendarCheckIcon,
  SliderIcon,
  ChevronDownIcon,
} from './Icons.jsx'

const CATEGORIES = [
  { id: 'all',        label: 'All' },
  { id: 'adventure',  label: 'Adventure' },
  { id: 'relaxation', label: 'Relaxation' },
  { id: 'culture',    label: 'Culture' },
  { id: 'budget',     label: 'Budget' },
  { id: 'nature',     label: 'Nature' },
  { id: 'food',       label: 'Food' },
]

const SORT_OPTIONS = [
  { id: 'seasonal',    label: 'Best seasonal match' },
  { id: 'popularity',  label: 'Most popular' },
  { id: 'budget_asc',  label: 'Lowest budget first' },
]

const DURATION_OPTIONS = [
  { id: '',   label: 'Any duration' },
  { id: '3',  label: 'Up to 3 days' },
  { id: '5',  label: 'Up to 5 days' },
  { id: '7',  label: 'Up to 7 days' },
  { id: '10', label: '8 days or more' },
]

function parseDuration(val) {
  if (!val) return null
  if (val === '10') return 99 // "8+ days" — accept all long trips
  return parseInt(val, 10)
}

export default function ExplorePage({ onPlanTrip, onBuildItinerary, initialCategory, initialMonth }) {
  const [search, setSearch]           = useState('')
  const [category, setCategory]       = useState(initialCategory || 'all')
  const [month, setMonth]             = useState(initialMonth || getCurrentMonth())
  const [sort, setSort]               = useState('seasonal')
  const [durationFilter, setDuration] = useState('')
  const [budgetFilter, setBudget]     = useState('')
  const [selectedDestination, setSelectedDestination] = useState(null)
  const [filtersOpen, setFiltersOpen] = useState(false)

  // Sync initialCategory if parent changes it (e.g. style button click)
  useEffect(() => {
    if (initialCategory && initialCategory !== category) {
      setCategory(initialCategory)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialCategory])

  // Sync initialMonth if parent changes it
  useEffect(() => {
    if (initialMonth && initialMonth !== month) {
      setMonth(initialMonth)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialMonth])

  const maxBudget = budgetFilter ? parseInt(budgetFilter, 10) : null
  const availableDays = parseDuration(durationFilter)

  const results = useMemo(() => {
    return rankDestinations(DESTINATIONS, {
      month,
      category:       category !== 'all' ? category : undefined,
      maxBudget:      maxBudget || undefined,
      budgetCurrency: 'USD',
      availableDays:  availableDays || undefined,
      search:         search.trim() || undefined,
      sort,
    })
  }, [month, category, maxBudget, availableDays, search, sort])

  const hasActiveFilters =
    search.trim() ||
    category !== 'all' ||
    month !== getCurrentMonth() ||
    sort !== 'seasonal' ||
    durationFilter ||
    budgetFilter

  const clearAll = () => {
    setSearch('')
    setCategory('all')
    setMonth(getCurrentMonth())
    setSort('seasonal')
    setDuration('')
    setBudget('')
  }

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
        currentMonth={month}
      />
    )
  }

  return (
    <div className="explore-page">
      {/* Header */}
      <div className="explore-header">
        <div className="explore-title-row">
          <div>
            <span className="eyebrow">
              <span className="eyebrow-dot" aria-hidden="true" /> Destinations
            </span>
            <h1 className="explore-title">Explore the World</h1>
            <p className="explore-sub">
              {DESTINATIONS.length} destinations · ranked for your selected month
            </p>
          </div>
        </div>

        {/* Search */}
        <div className="explore-controls">
          <div className="search-bar-row">
            <SearchIcon width={16} height={16} className="search-bar-icon" />
            <input
              type="search"
              className="search-bar-input"
              placeholder="Search destinations, countries, regions, or attractions…"
              value={search}
              onChange={e => setSearch(e.target.value)}
              aria-label="Search destinations"
            />
            {search && (
              <button
                type="button"
                className="search-bar-clear"
                onClick={() => setSearch('')}
                aria-label="Clear search"
              >
                <XIcon width={14} height={14} />
              </button>
            )}
          </div>

          {/* Category filter chips */}
          <div className="explore-filters" role="group" aria-label="Filter by travel style">
            <FilterIcon width={14} height={14} style={{ color: 'var(--ink-muted)', flexShrink: 0 }} />
            {CATEGORIES.map(cat => (
              <button
                key={cat.id}
                type="button"
                className={`filter-chip${category === cat.id ? ' active' : ''}`}
                onClick={() => setCategory(cat.id)}
                aria-pressed={category === cat.id}
              >
                {cat.label}
              </button>
            ))}
          </div>

          {/* Advanced filters toggle */}
          <button
            type="button"
            className={`filter-advanced-toggle${filtersOpen ? ' open' : ''}`}
            onClick={() => setFiltersOpen(o => !o)}
            aria-expanded={filtersOpen}
          >
            <SliderIcon width={14} height={14} />
            More filters
            <ChevronDownIcon width={13} height={13} className={`filter-chevron${filtersOpen ? ' flipped' : ''}`} />
          </button>

          {filtersOpen && (
            <div className="filter-advanced-panel">
              {/* Travel month */}
              <label className="filter-field">
                <span className="filter-field-label">
                  <CalendarCheckIcon width={13} height={13} /> Travel month
                </span>
                <select
                  value={month}
                  onChange={e => setMonth(Number(e.target.value))}
                  className="filter-select"
                >
                  {MONTH_NAMES.map((name, idx) => (
                    <option key={name} value={idx + 1}>{name}</option>
                  ))}
                </select>
              </label>

              {/* Duration */}
              <label className="filter-field">
                <span className="filter-field-label">
                  <FilterIcon width={13} height={13} /> Duration
                </span>
                <select
                  value={durationFilter}
                  onChange={e => setDuration(e.target.value)}
                  className="filter-select"
                >
                  {DURATION_OPTIONS.map(opt => (
                    <option key={opt.id} value={opt.id}>{opt.label}</option>
                  ))}
                </select>
              </label>

              {/* Budget */}
              <label className="filter-field">
                <span className="filter-field-label">
                  <FilterIcon width={13} height={13} /> Max budget (USD approx.)
                </span>
                <input
                  type="number"
                  min="0"
                  step="100"
                  placeholder="e.g. 1500"
                  value={budgetFilter}
                  onChange={e => setBudget(e.target.value)}
                  className="filter-input"
                  aria-label="Maximum budget in USD"
                />
              </label>

              {/* Sort */}
              <label className="filter-field">
                <span className="filter-field-label">
                  <SortIcon width={13} height={13} /> Sort by
                </span>
                <select
                  value={sort}
                  onChange={e => setSort(e.target.value)}
                  className="filter-select"
                >
                  {SORT_OPTIONS.map(opt => (
                    <option key={opt.id} value={opt.id}>{opt.label}</option>
                  ))}
                </select>
              </label>
            </div>
          )}
        </div>
      </div>

      {/* Results bar */}
      <div className="explore-results-bar">
        <span className="explore-count">
          <GridIcon width={14} height={14} />
          {results.length} destination{results.length !== 1 ? 's' : ''}
          {` · ${MONTH_NAMES[month - 1]}`}
        </span>

        {/* Active filter tags */}
        <div className="explore-active-filters">
          {month !== getCurrentMonth() && (
            <span className="active-filter-tag">
              {MONTH_NAMES[month - 1]}
              <button
                type="button"
                onClick={() => setMonth(getCurrentMonth())}
                aria-label="Reset travel month to current month"
              >
                <XIcon width={10} height={10} />
              </button>
            </span>
          )}
          {category !== 'all' && (
            <span className="active-filter-tag">
              {CATEGORIES.find(c => c.id === category)?.label}
              <button
                type="button"
                onClick={() => setCategory('all')}
                aria-label={`Remove ${category} filter`}
              >
                <XIcon width={10} height={10} />
              </button>
            </span>
          )}
          {durationFilter && (
            <span className="active-filter-tag">
              {DURATION_OPTIONS.find(d => d.id === durationFilter)?.label}
              <button type="button" onClick={() => setDuration('')} aria-label="Remove duration filter">
                <XIcon width={10} height={10} />
              </button>
            </span>
          )}
          {budgetFilter && (
            <span className="active-filter-tag">
              Max ${budgetFilter}
              <button type="button" onClick={() => setBudget('')} aria-label="Remove budget filter">
                <XIcon width={10} height={10} />
              </button>
            </span>
          )}
          {sort !== 'seasonal' && (
            <span className="active-filter-tag">
              {SORT_OPTIONS.find(s => s.id === sort)?.label}
              <button type="button" onClick={() => setSort('seasonal')} aria-label="Reset sort">
                <XIcon width={10} height={10} />
              </button>
            </span>
          )}
        </div>

        {hasActiveFilters && (
          <button type="button" className="link-btn" onClick={clearAll}>
            <XIcon width={12} height={12} /> Clear all
          </button>
        )}
      </div>

      {/* Grid */}
      {results.length === 0 ? (
        <div className="explore-empty">
          <SearchIcon width={36} height={36} />
          <h2>No destinations match your filters</h2>
          <p>Try a different search, category, or relax the budget or duration filters.</p>
          <button type="button" className="btn btn-ghost" onClick={clearAll}>
            Clear all filters
          </button>
        </div>
      ) : (
        <div className="destinations-grid">
          {results.map(dest => (
            <DestinationCard
              key={dest.id}
              destination={dest}
              onSelect={setSelectedDestination}
              onPlanTrip={handlePlanTrip}
              month={month}
            />
          ))}
        </div>
      )}
    </div>
  )
}
