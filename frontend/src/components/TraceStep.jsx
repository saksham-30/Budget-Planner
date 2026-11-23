import { useState } from 'react'
import { BoltIcon, BotIcon, ChatIcon, CheckIcon, TargetIcon } from './Icons.jsx'

const STEP_ICONS = {
  user_request: ChatIcon,
  agent_decision: BotIcon,
  tool_call: BoltIcon,
  tool_result: CheckIcon,
  final_answer: TargetIcon,
}

export default function TraceStep({ event, index }) {
  const [expanded, setExpanded] = useState(false)
  const isDecision = event.type === 'agent_decision'
  const hasDetail = Boolean(event.arguments || event.result)
  const Icon = STEP_ICONS[event.type] || BoltIcon
  const hasArguments =
    event.arguments && typeof event.arguments === 'object' && !Array.isArray(event.arguments)
  const isToolError = event.type === 'tool_result' && /^(Error|Validation error):/.test(event.result || '')

  return (
    <div
      className={`trace-step type-${event.type} ${isToolError ? 'trace-step-error' : ''}`}
      id={`trace-step-${event.step}`}
      style={{ animationDelay: `${index * 60}ms` }}
    >
      <div className="trace-step-icon-col">
        <span className="trace-step-icon" title={event.type}>
          <Icon width={15} height={15} />
        </span>
      </div>

      <div className="trace-step-content">
        <div className="trace-step-header">
          <span className="trace-step-number">Step {event.step}</span>
          <span className="trace-step-title">{event.title}</span>
          {isDecision && event.tool && (
            <span className="agent-decision-chip" id={`decision-chip-${event.step}`}>
              <BotIcon width={12} height={12} />
              {event.tool}
            </span>
          )}
        </div>

        <p className="trace-step-desc">{event.description}</p>

        {hasDetail && (
          <button
            type="button"
            className="trace-step-expand-btn"
            onClick={() => setExpanded(!expanded)}
            aria-expanded={expanded}
            id={`expand-btn-${event.step}`}
          >
            {expanded ? 'Hide details' : 'Show details'}
          </button>
        )}

        {expanded && hasDetail && (
          <div className="trace-step-detail">
            {event.tool && (
              <div className="detail-row">
                <span className="detail-key">Tool</span>
                <span className="detail-val">{event.tool}</span>
              </div>
            )}
            {hasArguments && Object.entries(event.arguments).map(([key, value]) => (
              <div className="detail-row" key={key}>
                <span className="detail-key">{key}</span>
                <span className="detail-val">{String(value)}</span>
              </div>
            ))}
            {event.result && (
              <div className="detail-row">
                <span className="detail-key">Result</span>
                <span className="detail-val">{event.result}</span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
