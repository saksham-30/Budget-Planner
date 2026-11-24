import { PlaneIcon } from './Icons.jsx'

function statusFor(health) {
  if (!health) return { tone: 'pending', label: 'Checking Ollama…' }
  if (!health.ollama_reachable) return { tone: 'offline', label: 'Ollama offline' }
  if (!health.model_available) return { tone: 'warning', label: `${health.model} not pulled` }
  return { tone: 'online', label: `${health.model} ready` }
}

export default function Header({ health }) {
  const status = statusFor(health)
  const detail = health?.message || 'Checking whether the local model is available.'

  return (
    <header className="app-header">
      <div className="app-header-inner">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">
          <PlaneIcon width={19} height={19} />
        </span>
        <div className="brand-text">
          <span className="brand-title">Travel Budget Planner</span>
          <span className="brand-sub">Plan within a fixed budget</span>
        </div>
      </div>

      <div className="header-meta">
        <span className="header-note">ECB reference rates · illustrative guide estimates</span>
        <span
          className={`status-pill status-${status.tone}`}
          title={detail}
          role="status"
          aria-live="polite"
          aria-label={`Model status: ${status.label}. ${detail}`}
        >
          <span className="status-dot" aria-hidden="true" />
          <span className="status-label">{status.label}</span>
        </span>
      </div>
      </div>
    </header>
  )
}
