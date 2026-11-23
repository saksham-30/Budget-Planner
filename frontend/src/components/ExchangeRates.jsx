import { useEffect, useState } from 'react'
import { ChevronDownIcon, CurrencyIcon } from './Icons.jsx'

const API_BASE = '/api'
const FETCH_TIMEOUT_MS = 8000

const FALLBACK_PAIRS = [
  { from: 'EUR', to: 'INR', rate: 95.0 },
  { from: 'EUR', to: 'USD', rate: 1.08 },
  { from: 'EUR', to: 'GBP', rate: 0.86 },
  { from: 'USD', to: 'INR', rate: 88.0 },
  { from: 'USD', to: 'EUR', rate: 0.93 },
  { from: 'USD', to: 'GBP', rate: 0.79 },
  { from: 'GBP', to: 'INR', rate: 108.5 },
  { from: 'GBP', to: 'USD', rate: 1.27 },
  { from: 'INR', to: 'USD', rate: 0.011 },
  { from: 'INR', to: 'EUR', rate: 0.0105 },
]

const FALLBACK_NOTE =
  'The rate feed is unreachable, so these bundled demonstration values are shown. ' +
  'They are not market data — do not use them for real financial decisions.'

function formatRate(value) {
  const rate = Number(value)
  if (!Number.isFinite(rate)) return '—'
  return rate.toLocaleString('en-US', {
    maximumFractionDigits: rate < 0.01 ? 6 : 4,
  })
}

export default function ExchangeRates() {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState(null)
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS)

    fetch(`${API_BASE}/rates`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`Rates request failed: ${response.status}`)
        return response.json()
      })
      .then((payload) => {
        if (!active) return
        setData({
          pairs: (payload.pairs || []).map((pair) => ({
            from: pair.from_currency,
            to: pair.to_currency,
            rate: pair.rate,
          })),
          isLive: Boolean(payload.is_live),
          stale: Boolean(payload.stale),
          label: payload.label,
          asOf: payload.as_of,
          note: payload.note,
        })
        setFailed(false)
      })
      .catch((error) => {
        if (!active || error.name === 'AbortError') {
          if (active) setFailed(true)
          return
        }
        setFailed(true)
      })
      .finally(() => window.clearTimeout(timeout))

    return () => {
      active = false
      window.clearTimeout(timeout)
      controller.abort()
    }
  }, [attempt])

  const pairs = data?.pairs?.length ? data.pairs : FALLBACK_PAIRS
  const status = !data && !failed ? 'syncing' : data?.isLive ? (data.stale ? 'cached' : 'live') : 'demo'
  const statusLabel = { syncing: 'Syncing', live: 'Live', cached: 'Cached', demo: 'Demo' }[status]
  const sourceLabel = data?.label || 'Bundled demonstration rates'
  const note = data?.note || FALLBACK_NOTE

  const retry = () => {
    setFailed(false)
    setData(null)
    setAttempt(value => value + 1)
  }

  return (
    <section className={`rates-card ${open ? 'open' : ''}`}>
      <button
        id="exchange-rates-toggle"
        type="button"
        className="rates-toggle"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-controls="rates-panel"
      >
        <span className="rates-toggle-label">
          <CurrencyIcon width={16} height={16} />
          Exchange rates
          <span className={`rate-badge ${status}`}>{statusLabel}</span>
        </span>
        <ChevronDownIcon width={16} height={16} className="chevron" />
      </button>

      <div className="rates-body" id="rates-panel">
        <div className="rates-meta">
          <span className="rates-source">{sourceLabel}</span>
          {data?.asOf && <span>As of {data.asOf}</span>}
          {failed && (
            <span className="rates-failed">
              Feed unreachable
              <button type="button" className="link-btn" onClick={retry}>Retry</button>
            </span>
          )}
        </div>
        <div className="rates-grid">
          {pairs.map((rate) => (
            <span className="rate-chip" key={`${rate.from}-${rate.to}`}>
              <span className="rate-pair">{rate.from} → {rate.to}</span>
              <span className="rate-value">{formatRate(rate.rate)}</span>
            </span>
          ))}
        </div>
        <p className="rates-disclaimer">{note}</p>
      </div>
    </section>
  )
}
