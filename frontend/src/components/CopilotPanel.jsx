/**
 * CopilotPanel — Phase 7 AI Travel Copilot
 *
 * Conversational assistant scoped to the current trip.
 * Answers questions, suggests alternatives, and can trigger the existing
 * itinerary modification preview → Apply/Cancel workflow.
 *
 * API:
 *   POST   /api/trips/{trip_id}/copilot         → CopilotResponse
 *   GET    /api/trips/{trip_id}/copilot/history  → CopilotTurn[]
 *   DELETE /api/trips/{trip_id}/copilot/history  → 204
 *
 * The copilot NEVER modifies the itinerary automatically.
 * When it suggests a change, the user clicks "Apply this change" which
 * calls the existing modify endpoint with the same preview/Apply/Cancel flow.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '../api.js'
import {
  AlertIcon,
  BotIcon,
  ChevronDownIcon,
  InfoIcon,
  RefreshIcon,
  SendIcon,
  SparkIcon,
  TrashIcon,
  WandIcon,
  XIcon,
} from './Icons.jsx'

const API_BASE = '/api'

const SUGGESTED_QUESTIONS = [
  'What should I know about the weather on my trip?',
  'Are there any schedule conflicts in my itinerary?',
  'How can I reduce costs on this trip?',
  'What indoor activities are available if it rains?',
  'What is the best way to get around locally?',
  'Make Day 2 more relaxed.',
]

function TypingIndicator() {
  return (
    <div className="copilot-typing" aria-label="AI is thinking">
      <span /><span /><span />
    </div>
  )
}

function MessageBubble({ turn }) {
  const isUser = turn.role === 'user'
  return (
    <div className={`copilot-message copilot-message-${isUser ? 'user' : 'assistant'}`}>
      {!isUser && (
        <span className="copilot-avatar" aria-hidden="true">
          <BotIcon width={13} height={13} />
        </span>
      )}
      <div className="copilot-bubble">
        {turn.content.split('\n').map((line, i) => (
          line.trim()
            ? <p key={i}>{line}</p>
            : <br key={i} />
        ))}
      </div>
    </div>
  )
}

export default function CopilotPanel({
  tripId,
  itinerary,
  weatherContext,
  feasibilityContext,
  logisticsContext,
  onModificationRequest,   // (modificationText) => void  — triggers modify flow
}) {
  const [panelOpen, setPanelOpen]       = useState(false)
  const [history, setHistory]           = useState([])
  const [input, setInput]               = useState('')
  const [loading, setLoading]           = useState(false)
  const [error, setError]               = useState('')
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [pendingModification, setPendingModification] = useState(null) // last AI reply that suggests a change
  const historyEndRef = useRef(null)
  const inputRef = useRef(null)

  // Scroll to latest message
  const scrollToBottom = useCallback(() => {
    historyEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  useEffect(() => { scrollToBottom() }, [history, scrollToBottom])

  // Load history when panel first opens
  const loadHistory = useCallback(async () => {
    if (!tripId) return
    setLoadingHistory(true)
    try {
      const res = await apiFetch(`${API_BASE}/trips/${encodeURIComponent(tripId)}/copilot/history`)
      if (res.ok) {
        const data = await res.json()
        setHistory(Array.isArray(data) ? data : [])
      }
    } catch { /* network error — start with empty history */ }
    finally { setLoadingHistory(false) }
  }, [tripId])

  const handleToggle = () => {
    const next = !panelOpen
    setPanelOpen(next)
    if (next && history.length === 0) {
      loadHistory()
    }
  }

  const send = useCallback(async (messageText) => {
    const text = (messageText || input).trim()
    if (!text || loading || !tripId) return
    setInput('')
    setError('')
    setPendingModification(null)

    // Optimistically add user message
    setHistory(h => [...h, { role: 'user', content: text }])
    setLoading(true)

    try {
      const body = {
        message: text,
        weather_context:     weatherContext     || null,
        feasibility_context: feasibilityContext || null,
        logistics_context:   logisticsContext   || null,
      }
      const res = await apiFetch(
        `${API_BASE}/trips/${encodeURIComponent(tripId)}/copilot`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        },
      )
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || `Request failed (${res.status})`)
      }
      const data = await res.json()
      setHistory(data.history || [])
      if (data.suggests_modification) {
        setPendingModification(data.reply)
      }
    } catch (err) {
      setError(err.message || 'The copilot could not respond. Check Ollama is running.')
      // Remove the optimistic user message on failure
      setHistory(h => h.slice(0, -1))
    } finally {
      setLoading(false)
      // Re-focus input after reply
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [input, loading, tripId, weatherContext, feasibilityContext, logisticsContext])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  const handleApplyModification = () => {
    if (pendingModification && onModificationRequest) {
      onModificationRequest(pendingModification)
      setPendingModification(null)
    }
  }

  const clearHistory = async () => {
    if (!tripId) return
    try {
      await apiFetch(
        `${API_BASE}/trips/${encodeURIComponent(tripId)}/copilot/history`,
        { method: 'DELETE' },
      )
      setHistory([])
      setPendingModification(null)
    } catch { /* ignore */ }
  }

  if (!tripId) return null

  return (
    <div className="copilot-panel panel-card">
      {/* Toggle header */}
      <button
        type="button"
        className="copilot-panel-toggle"
        onClick={handleToggle}
        aria-expanded={panelOpen}
      >
        <SparkIcon width={16} height={16} />
        <span className="copilot-panel-title">AI Copilot</span>
        <span className="copilot-panel-sub">Ask anything about this trip</span>
        <ChevronDownIcon
          width={14}
          height={14}
          className={`intel-chevron${panelOpen ? ' flipped' : ''}`}
        />
        {history.length > 0 && (
          <span className="copilot-turn-count">
            {Math.floor(history.length / 2)} turn{history.length !== 2 ? 's' : ''}
          </span>
        )}
      </button>

      {panelOpen && (
        <div className="copilot-panel-body">
          {/* Toolbar */}
          <div className="copilot-toolbar">
            <span className="copilot-toolbar-hint">
              <InfoIcon width={12} height={12} />
              Answers are grounded in your trip data. No bookings are made automatically.
            </span>
            {history.length > 0 && (
              <button
                type="button"
                className="btn btn-ghost btn-sm copilot-clear-btn"
                onClick={clearHistory}
                aria-label="Clear conversation"
                title="Clear conversation"
              >
                <TrashIcon width={13} height={13} /> Clear
              </button>
            )}
          </div>

          {/* Message history */}
          <div
            className="copilot-messages"
            role="log"
            aria-live="polite"
            aria-label="Conversation with AI copilot"
          >
            {loadingHistory && (
              <div className="copilot-loading">
                <span className="spinner spinner-accent" /> Loading conversation…
              </div>
            )}

            {!loadingHistory && history.length === 0 && (
              <div className="copilot-empty">
                <BotIcon width={28} height={28} />
                <p>Ask me anything about your trip to <strong>{itinerary?.config?.destination || 'your destination'}</strong>.</p>
              </div>
            )}

            {history.map((turn, i) => (
              <MessageBubble key={i} turn={turn} />
            ))}

            {loading && (
              <div className="copilot-message copilot-message-assistant">
                <span className="copilot-avatar"><BotIcon width={13} height={13} /></span>
                <TypingIndicator />
              </div>
            )}

            <div ref={historyEndRef} />
          </div>

          {/* Modification suggestion banner */}
          {pendingModification && (
            <div className="copilot-modify-banner">
              <WandIcon width={14} height={14} />
              <span>The copilot suggested a change to your itinerary.</span>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={handleApplyModification}
              >
                Preview change
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setPendingModification(null)}
              >
                Dismiss
              </button>
            </div>
          )}

          {/* Error */}
          {error && (
            <div className="intel-error-row" style={{ padding: '0 1rem 0.5rem' }}>
              <p className="intel-error">
                <AlertIcon width={13} height={13} /> {error}
              </p>
              <button
                className="btn btn-ghost btn-sm"
                type="button"
                onClick={() => setError('')}
              >
                Dismiss
              </button>
            </div>
          )}

          {/* Suggested questions (shown when history is empty) */}
          {history.length === 0 && !loadingHistory && (
            <div className="copilot-suggestions">
              {SUGGESTED_QUESTIONS.map(q => (
                <button
                  key={q}
                  type="button"
                  className="suggestion-chip"
                  disabled={loading}
                  onClick={() => send(q)}
                >
                  {q}
                </button>
              ))}
            </div>
          )}

          {/* Input */}
          <div className="copilot-input-row">
            <textarea
              ref={inputRef}
              className="copilot-input"
              rows={2}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask about weather, costs, activities, Day 2…"
              disabled={loading}
              maxLength={2000}
              aria-label="Message to AI copilot"
            />
            <button
              type="button"
              className="btn btn-primary copilot-send-btn"
              disabled={!input.trim() || loading}
              onClick={() => send()}
              aria-label="Send message"
            >
              {loading
                ? <span className="spinner" />
                : <SendIcon width={15} height={15} />}
            </button>
          </div>
          <p className="copilot-disclaimer">
            <InfoIcon width={11} height={11} />
            AI responses may contain errors. Verify critical information before travelling.
          </p>
        </div>
      )}
    </div>
  )
}
