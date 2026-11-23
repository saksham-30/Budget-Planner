import { AlertIcon, CheckIcon, WalletIcon, XIcon } from './Icons.jsx'

function fmt(amount, currency) {
  if (amount == null) return '—'
  try {
    return new Intl.NumberFormat(currency === 'INR' ? 'en-IN' : 'en-US', {
      style: 'currency',
      currency: currency || 'USD',
      maximumFractionDigits: 2,
    }).format(amount)
  } catch {
    return `${currency || ''} ${Number(amount).toLocaleString()}`
  }
}

export default function ResultCard({ result }) {
  const { answer, feasible, total_cost, budget, currency } = result

  const isFeasible = feasible === true
  const isNotFeasible = feasible === false
  const isUnknown = feasible == null

  return (
    <article className="result-card" id="result-card">
      <header className="result-header">
        <div className="section-heading">
          <span className="section-kicker">Analysis result</span>
          <h2 className="result-title">Verdict</h2>
        </div>
        {isFeasible && (
          <span className="feasibility-badge feasible" id="badge-feasible">
            <CheckIcon width={14} height={14} /> Feasible
          </span>
        )}
        {isNotFeasible && (
          <span className="feasibility-badge not-feasible" id="badge-not-feasible">
            <XIcon width={14} height={14} /> Not feasible
          </span>
        )}
        {isUnknown && (
          <span className="feasibility-badge unknown" id="badge-unknown">
            <AlertIcon width={14} height={14} /> Undetermined
          </span>
        )}
      </header>

      <p className="result-request">
        <span className="field-label">You asked</span>
        {result.query}
      </p>

      <div className="stat-grid">
        <div className="stat-item">
          <span className="stat-label">Trip cost</span>
          <span className="stat-value" id="stat-cost">{fmt(total_cost, currency)}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">Budget</span>
          <span className="stat-value" id="stat-budget">{fmt(budget, currency)}</span>
        </div>
        <div className="stat-item">
          <span className="stat-label">
            {isFeasible ? 'Remaining' : isNotFeasible ? 'Over budget' : 'Status'}
          </span>
          <span
            className={`stat-value ${isFeasible ? 'positive' : isNotFeasible ? 'negative' : ''}`}
            id="stat-remaining"
          >
            {isFeasible ? fmt(result.remaining, currency)
              : isNotFeasible ? fmt(result.overage, currency)
                : '—'}
          </span>
        </div>
      </div>

      <div className="result-answer" id="result-answer">
        <span className="result-answer-icon" aria-hidden="true">
          <WalletIcon width={18} height={18} />
        </span>
        <p>{answer}</p>
      </div>

      {isUnknown && (
        <p className="result-note" role="status">
          The agent replied without completing the budget comparison. Retry the request, or use a
          larger local model for more reliable multi-step tool use.
        </p>
      )}
    </article>
  )
}
