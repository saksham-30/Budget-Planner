/**
 * Deterministic destination recommendation engine.
 *
 * Scoring weights:
 *   Seasonal suitability  35 %
 *   Budget compatibility  25 %
 *   Category match        20 %
 *   Duration compatibility 10 %
 *   Popularity            10 %
 *
 * No Math.random() — identical inputs always produce identical output.
 */

// ─── Month helpers ────────────────────────────────────────────────────────────

export const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

export const MONTH_NAMES_SHORT = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]

export function getCurrentMonth() {
  return new Date().getMonth() + 1 // 1–12
}

export function getSeasonLabel(month) {
  if (month >= 3 && month <= 5) return 'Spring'
  if (month >= 6 && month <= 8) return 'Summer'
  if (month >= 9 && month <= 11) return 'Autumn'
  return 'Winter'
}

// ─── Seasonal suitability (0–100) ────────────────────────────────────────────

/**
 * Returns a 0–100 seasonal score for a destination and month.
 *
 * Uses monthSuitability[0..11] (values 0–3) if present.
 * Falls back to bestMonths array with adjacent-month scoring.
 */
export function getSeasonalScore(destination, month) {
  if (!destination || !month) return 20

  // Preferred: use the month-by-month suitability array
  const ms = destination.monthSuitability
  if (Array.isArray(ms) && ms.length === 12) {
    const raw = ms[month - 1] // 0–3
    return Math.round((raw / 3) * 100)
  }

  // Fallback: bestMonths array
  const best = destination.bestMonths || []
  if (best.includes(month)) return 100
  const prev = month === 1 ? 12 : month - 1
  const next = month === 12 ? 1 : month + 1
  if (best.includes(prev) || best.includes(next)) return 60
  return 20
}

// ─── Budget compatibility (0–100) ────────────────────────────────────────────

/** Approximate rough-normalisation to USD for cross-currency comparison. */
function toUsd(amount, currency) {
  if (!currency || currency === 'USD') return amount
  if (currency === 'EUR') return amount * 1.1
  if (currency === 'GBP') return amount * 1.27
  if (currency === 'INR') return amount / 83
  return amount
}

/**
 * Returns 0–100.
 * 100 → budget is comfortably above the destination minimum.
 * 50  → budget matches the minimum exactly.
 * 0   → budget is well below the minimum.
 */
export function getBudgetScore(destination, maxBudget, budgetCurrency) {
  if (maxBudget == null || maxBudget <= 0) return 60 // neutral when no budget given
  const range = destination.budgetRange
  if (!range) return 60

  const userUsd = toUsd(maxBudget, budgetCurrency || 'USD')
  const minUsd = toUsd(range.min, range.currency)
  const maxUsd = toUsd(range.max, range.currency)

  if (userUsd >= maxUsd) return 100              // easily within budget
  if (userUsd >= minUsd) {
    // Linear interpolation between 50 and 100
    const ratio = (userUsd - minUsd) / (maxUsd - minUsd)
    return Math.round(50 + ratio * 50)
  }
  if (userUsd >= minUsd * 0.75) return 25        // slightly below minimum
  return 0                                        // well below minimum
}

// ─── Category match (0–100) ──────────────────────────────────────────────────

/**
 * Returns 100 if category matches, 0 if no match, 60 if no filter given.
 */
export function getCategoryScore(destination, category) {
  if (!category || category === 'all') return 60 // neutral
  const cats = destination.categories || []
  return cats.includes(category) ? 100 : 0
}

// ─── Duration compatibility (0–100) ──────────────────────────────────────────

/**
 * Returns 100 if the user's available days >= destination's recommended days.
 * Scales down proportionally below that; 60 if no duration specified.
 */
export function getDurationScore(destination, availableDays) {
  if (availableDays == null || availableDays <= 0) return 60
  const rec = destination.recommendedDays || 5
  if (availableDays >= rec) return 100
  if (availableDays >= rec * 0.6) return Math.round((availableDays / rec) * 100)
  return 20
}

// ─── Popularity (0–100) ──────────────────────────────────────────────────────

export function getPopularityScore(destination) {
  const pop = destination.popularityScore || 5
  return Math.round((pop / 10) * 100)
}

// ─── Composite ranking ────────────────────────────────────────────────────────

const WEIGHTS = {
  seasonal: 0.35,
  budget:   0.25,
  category: 0.20,
  duration: 0.10,
  popularity: 0.10,
}

/**
 * Build a human-readable explanation string for a recommendation.
 */
function buildReason(scores, month, category, maxBudget) {
  const parts = []

  if (scores.seasonal >= 90) {
    parts.push(`${MONTH_NAMES[month - 1]} is an ideal month to visit`)
  } else if (scores.seasonal >= 60) {
    parts.push(`${MONTH_NAMES[month - 1]} is a good time to visit`)
  } else if (scores.seasonal >= 33) {
    parts.push(`${MONTH_NAMES[month - 1]} is an acceptable visiting period`)
  }

  if (category && category !== 'all' && scores.category === 100) {
    parts.push(`matches your ${category} travel style`)
  }

  if (maxBudget != null && maxBudget > 0) {
    if (scores.budget >= 90) parts.push('well within your budget')
    else if (scores.budget >= 60) parts.push('fits your budget')
    else if (scores.budget >= 40) parts.push('near the lower end of your budget')
  }

  if (scores.popularity >= 90) parts.push('highly popular destination')

  if (parts.length === 0) return ''
  if (parts.length === 1) return `Recommended: ${parts[0]}.`
  const last = parts.pop()
  return `Recommended because ${parts.join(', ')} and ${last}.`
}

/**
 * Returns a sorted array of destinations with added scoring metadata.
 *
 * @param {object[]} destinations
 * @param {object}   options
 * @param {number}   [options.month]          1–12
 * @param {string}   [options.category]       e.g. 'adventure'
 * @param {number}   [options.maxBudget]
 * @param {string}   [options.budgetCurrency]
 * @param {number}   [options.availableDays]
 * @param {string}   [options.country]        filter by country
 * @param {string}   [options.region]         filter by region
 * @param {string}   [options.search]         text search
 * @param {string}   [options.sort]           'seasonal' | 'popularity' | 'budget_asc'
 */
export function rankDestinations(destinations, options = {}) {
  const {
    month,
    category,
    maxBudget,
    budgetCurrency,
    availableDays,
    country,
    region,
    search,
    sort = 'seasonal',
  } = options

  const effectiveMonth = month || getCurrentMonth()

  let list = destinations.slice()

  // ── Filters ────────────────────────────────────────────────────────────────

  if (country) {
    list = list.filter(d => d.country.toLowerCase() === country.toLowerCase())
  }

  if (region) {
    list = list.filter(d =>
      d.region && d.region.toLowerCase().includes(region.toLowerCase()),
    )
  }

  if (category && category !== 'all') {
    list = list.filter(d => (d.categories || []).includes(category))
  }

  if (maxBudget != null && maxBudget > 0) {
    list = list.filter(d => {
      const score = getBudgetScore(d, maxBudget, budgetCurrency)
      return score > 0 // remove destinations clearly out of reach
    })
  }

  if (availableDays != null && availableDays > 0) {
    list = list.filter(d => {
      const rec = d.recommendedDays || 5
      return availableDays >= Math.floor(rec * 0.5) // allow trips at ≥50% recommended length
    })
  }

  if (search && search.trim()) {
    const q = search.trim().toLowerCase()
    list = list.filter(d =>
      d.name.toLowerCase().includes(q) ||
      d.country.toLowerCase().includes(q) ||
      (d.region || '').toLowerCase().includes(q) ||
      d.tagline.toLowerCase().includes(q) ||
      (d.description || '').toLowerCase().includes(q) ||
      (d.categories || []).some(c => c.toLowerCase().includes(q)) ||
      (d.attractions || []).some(a =>
        typeof a === 'string'
          ? a.toLowerCase().includes(q)
          : (a.name || '').toLowerCase().includes(q),
      ),
    )
  }

  // ── Score each destination ─────────────────────────────────────────────────

  const scored = list.map(d => {
    const seasonal   = getSeasonalScore(d, effectiveMonth)
    const budget     = getBudgetScore(d, maxBudget, budgetCurrency)
    const catScore   = getCategoryScore(d, category)
    const duration   = getDurationScore(d, availableDays)
    const popularity = getPopularityScore(d)

    const overall = Math.round(
      seasonal   * WEIGHTS.seasonal   +
      budget     * WEIGHTS.budget     +
      catScore   * WEIGHTS.category   +
      duration   * WEIGHTS.duration   +
      popularity * WEIGHTS.popularity,
    )

    const reason = buildReason(
      { seasonal, budget, category: catScore, popularity },
      effectiveMonth,
      category,
      maxBudget,
    )

    return {
      ...d,
      _scores: { seasonal, budget, category: catScore, duration, popularity, overall },
      seasonalScore: seasonal, // kept for backward compatibility with DestinationCard
      recommendationReason: reason,
    }
  })

  // ── Sort ───────────────────────────────────────────────────────────────────

  if (sort === 'popularity') {
    scored.sort((a, b) => {
      const diff = b._scores.popularity - a._scores.popularity
      return diff !== 0 ? diff : b._scores.overall - a._scores.overall
    })
  } else if (sort === 'budget_asc') {
    scored.sort((a, b) => {
      const aMin = toUsd(a.budgetRange?.min || 0, a.budgetRange?.currency)
      const bMin = toUsd(b.budgetRange?.min || 0, b.budgetRange?.currency)
      return aMin - bMin
    })
  } else {
    // Default: best seasonal match first, tie-break by overall score, then name
    scored.sort((a, b) => {
      const diff = b._scores.overall - a._scores.overall
      if (diff !== 0) return diff
      return a.name.localeCompare(b.name) // deterministic tie-break
    })
  }

  return scored
}
