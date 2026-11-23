import { ChartIcon, CurrencyIcon, SearchIcon } from './Icons.jsx'

const TOOL_META = {
  convert_currency: {
    Icon: CurrencyIcon,
    name: 'Currency converter',
    id: 'tool-card-currency',
  },
  check_budget_feasibility: {
    Icon: ChartIcon,
    name: 'Budget feasibility',
    id: 'tool-card-budget',
  },
}

const STATUS_COPY = {
  idle: 'Waiting',
  running: 'Running…',
  done: 'Done',
}

export default function ToolCard({ toolName, status }) {
  const meta = TOOL_META[toolName] || {
    Icon: SearchIcon,
    name: toolName,
    id: `tool-card-${toolName}`,
  }
  const { Icon } = meta

  return (
    <div className={`tool-card ${status}`} id={meta.id}>
      <span className="tool-icon">
        <Icon width={16} height={16} />
      </span>
      <span className="tool-meta">
        <span className="tool-name">{meta.name}</span>
        <span className={`tool-status-text tool-status-${status}`}>{STATUS_COPY[status] || 'Waiting'}</span>
      </span>
      <span className="tool-status-dot" aria-hidden="true" />
    </div>
  )
}
