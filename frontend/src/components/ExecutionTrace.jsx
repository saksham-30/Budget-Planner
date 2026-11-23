import TraceStep from './TraceStep.jsx'
import ToolCard from './ToolCard.jsx'
import { SparkIcon } from './Icons.jsx'

function getToolStatus(toolName, trace) {
  const toolEvents = trace.filter(event => event.tool === toolName)
  if (toolEvents.some(event => event.type === 'tool_result')) return 'done'
  if (toolEvents.some(event => event.type === 'tool_call' || event.type === 'agent_decision')) return 'running'
  return 'idle'
}

export default function ExecutionTrace({ trace, loading, submittedQuery }) {
  const currencyStatus = getToolStatus('convert_currency', trace)
  const budgetStatus = getToolStatus('check_budget_feasibility', trace)

  if (loading && trace.length === 0) {
    return (
      <section className="trace-card" aria-busy="true">
        <div className="trace-header">
          <span className="trace-title">
            <SparkIcon width={15} height={15} />
            Agent execution trace
          </span>
          <span className="trace-note">Model is deciding the next step…</span>
        </div>
        <div className="tool-cards">
          <ToolCard toolName="convert_currency" status="idle" />
          <ToolCard toolName="check_budget_feasibility" status="idle" />
        </div>
        <div className="loading-trace">
          {[1, 2, 3].map(index => (
            <div className="skeleton-step" key={index}>
              <div className="skeleton-circle" />
              <div className="skeleton-lines">
                <div className="skeleton-line short" />
                <div className="skeleton-line medium" />
                <div className="skeleton-line long" />
              </div>
            </div>
          ))}
        </div>
      </section>
    )
  }

  if (trace.length === 0) return null

  return (
    <section className="trace-card" aria-live="polite">
      <div className="trace-header">
        <span className="trace-title">
          <SparkIcon width={15} height={15} />
          Agent execution trace
        </span>
        <span className="trace-count">{trace.length} steps</span>
      </div>

      {submittedQuery && (
        <p className="trace-request">
          <span className="field-label">Request</span>
          {submittedQuery}
        </p>
      )}

      <div className="tool-cards">
        <ToolCard toolName="convert_currency" status={currencyStatus} />
        <ToolCard toolName="check_budget_feasibility" status={budgetStatus} />
      </div>

      <div className="trace-timeline" id="trace-timeline" aria-live="polite" aria-relevant="additions">
        {trace.map((event, index) => (
          <TraceStep key={`${event.step}-${event.type}`} event={event} index={index} />
        ))}
      </div>
    </section>
  )
}
