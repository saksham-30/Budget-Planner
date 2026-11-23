import { useCallback, useEffect, useRef, useState } from 'react'
import ChatInput from './ChatInput.jsx'
import ExampleQueries from './ExampleQueries.jsx'
import ExchangeRates from './ExchangeRates.jsx'
import ResultCard from './ResultCard.jsx'
import ExecutionTrace from './ExecutionTrace.jsx'
import { AlertIcon, ChartIcon, CurrencyIcon, TargetIcon, WalletIcon, XIcon } from './Icons.jsx'

const API_BASE = '/api'

function HealthBanner({ health }) {
  if (!health) return null
  if (!health.ollama_reachable) {
    return (
      <div className="banner banner-error" id="health-banner-ollama" role="alert">
        <AlertIcon className="banner-icon" />
        <div>
          <div className="banner-title">Ollama is not reachable</div>
          <p className="banner-body">{health.message}</p>
          <p className="banner-body">
            Start it with <code>ollama serve</code>. This page rechecks the connection every 30 seconds.
          </p>
        </div>
      </div>
    )
  }
  if (!health.model_available) {
    return (
      <div className="banner banner-warning" id="health-banner-model" role="alert">
        <AlertIcon className="banner-icon" />
        <div>
          <div className="banner-title">Model not pulled</div>
          <p className="banner-body">{health.message}</p>
          <p className="banner-body">
            Run <code>ollama pull {health.model || 'qwen2.5:3b'}</code> to enable the agent.
          </p>
        </div>
      </div>
    )
  }
  return null
}

function EmptyState() {
  return (
    <div className="empty-state">
      <span className="section-kicker">Start here</span>
      <h2 className="empty-title">Ask a budget question</h2>
      <p className="empty-desc">
        Describe the trip cost and your budget in plain language. The agent chooses the tools —
        you never pick a tool or an order.
      </p>

      <ol className="stepper">
        <li>
          <span className="step-icon"><TargetIcon /></span>
          <div>
            <strong>Estimate</strong>
            <span>Look up a guide-backed cost when no trip cost is given</span>
          </div>
        </li>
        <li>
          <span className="step-icon"><CurrencyIcon /></span>
          <div>
            <strong>Convert</strong>
            <span>Normalise the trip cost into your budget currency</span>
          </div>
        </li>
        <li>
          <span className="step-icon"><WalletIcon /></span>
          <div>
            <strong>Compare</strong>
            <span>Check the converted cost against the budget</span>
          </div>
        </li>
        <li>
          <span className="step-icon"><ChartIcon /></span>
          <div>
            <strong>Explain</strong>
            <span>Return a verdict with every number in the trace</span>
          </div>
        </li>
      </ol>

      <p className="empty-hint">Or select a worked example on the left.</p>
    </div>
  )
}

export default function CurrencyAgentView({ health }) {
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [submittedQuery, setSubmittedQuery] = useState('')
  const [trace, setTrace] = useState([])
  const [error, setError] = useState(null)
  const activeRequest = useRef(null)

  useEffect(() => () => activeRequest.current?.abort(), [])

  const updateQuery = useCallback((nextQuery) => {
    if (loading) return
    setQuery(nextQuery)
    setResult(null)
    setSubmittedQuery('')
    setTrace([])
    setError(null)
  }, [loading])

  const handleSubmit = useCallback(async () => {
    if (!query.trim() || loading) return

    const requestQuery = query.trim()
    const controller = new AbortController()
    activeRequest.current = controller
    setLoading(true)
    setResult(null)
    setSubmittedQuery(requestQuery)
    setTrace([])
    setError(null)

    try {
      const response = await fetch(`${API_BASE}/analyze/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: requestQuery }),
        signal: controller.signal,
      })

      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: response.statusText }))
        throw new Error(err.detail || 'The request failed.')
      }
      if (!response.body) {
        throw new Error('The server did not provide a response stream.')
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let receivedComplete = false
      let failed = false

      while (!failed) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop()

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const raw = line.slice(6).trim()
          if (raw === '[DONE]') continue

          let evt
          try {
            evt = JSON.parse(raw)
          } catch {
            throw new Error('The server sent an invalid response event.')
          }

          if (evt.type === 'error') {
            failed = true
            reader.cancel().catch(() => {})
            throw new Error(evt.message || 'The agent failed while processing the request.')
          }

          if (evt.type === 'complete') {
            receivedComplete = true
            setResult({
              query: requestQuery,
              answer: evt.answer,
              feasible: evt.feasible,
              total_cost: evt.total_cost,
              budget: evt.budget,
              remaining: evt.remaining,
              overage: evt.overage,
              currency: evt.currency,
            })
            setTrace(evt.trace || [])
          }

          if (evt.type === 'tool_start') {
            setTrace(prev => [
              ...prev,
              {
                step: prev.length + 1,
                type: 'agent_decision',
                title: 'Agent selected next tool',
                description: `Invoking tool: ${evt.tool}`,
                tool: evt.tool,
                arguments: evt.arguments || {},
                result: null,
                timestamp: new Date().toISOString(),
                status: 'complete',
              },
            ])
          }

          if (evt.type === 'tool_end') {
            setTrace(prev => [
              ...prev,
              {
                step: prev.length + 1,
                type: 'tool_result',
                title: `Result: ${evt.tool}`,
                description: evt.result,
                tool: evt.tool,
                arguments: null,
                result: evt.result,
                timestamp: new Date().toISOString(),
                status: 'complete',
              },
            ])
          }
        }
      }

      if (!receivedComplete && !failed) {
        throw new Error('The response stream ended before the analysis was complete.')
      }
    } catch (err) {
      if (err.name === 'AbortError') return
      setError(err.message || 'An unexpected error occurred.')
    } finally {
      if (!controller.signal.aborted) setLoading(false)
      if (activeRequest.current === controller) activeRequest.current = null
    }
  }, [query, loading])

  const cancelRequest = useCallback(() => {
    activeRequest.current?.abort()
    setLoading(false)
  }, [])

  return (
    <main className="currency-layout">
      <section className="workspace-panel" aria-label="Ask the currency agent">
        <HealthBanner health={health} />
        <div className="panel-card input-card">
          <ChatInput
            query={query}
            setQuery={updateQuery}
            onSubmit={handleSubmit}
            onCancel={cancelRequest}
            loading={loading}
          />
        </div>
        <ExampleQueries onSelect={updateQuery} disabled={loading} />
        <ExchangeRates />
      </section>

      <section className="results-panel" aria-busy={loading}>
        {error && (
          <div className="banner banner-error" id="error-banner" role="alert">
            <AlertIcon className="banner-icon" />
            <div>
              <div className="banner-title">Something went wrong</div>
              <p className="banner-body">{error}</p>
              <div className="banner-actions">
                <button className="btn btn-ghost btn-sm" type="button" onClick={handleSubmit}>
                  Try again
                </button>
                <button
                  className="link-btn"
                  type="button"
                  onClick={() => setError(null)}
                  aria-label="Dismiss error"
                >
                  <XIcon width={13} height={13} /> Dismiss
                </button>
              </div>
            </div>
          </div>
        )}

        {!result && !loading && !error && <EmptyState />}

        <div aria-live="polite">
          {result && <ResultCard result={result} />}
        </div>

        {(trace.length > 0 || loading) && (
          <ExecutionTrace
            trace={trace}
            loading={loading}
            submittedQuery={submittedQuery}
          />
        )}
      </section>
    </main>
  )
}
