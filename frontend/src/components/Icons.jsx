const base = {
  width: 18,
  height: 18,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
}

export function PlaneIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M17.8 19.2 16 11l3.5-3.5a2.1 2.1 0 0 0-3-3L13 8 4.8 6.2a1 1 0 0 0-.9 1.7l4.6 3-2 3-2.6-.6a1 1 0 0 0-.9 1.6l2.6 2.6 2.6 2.6a1 1 0 0 0 1.6-.9l-.6-2.6 3-2 3 4.6a1 1 0 0 0 1.7-.9Z" />
    </svg>
  )
}

export function SparkIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M12 3v3M12 18v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M3 12h3M18 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" />
      <circle cx="12" cy="12" r="3.2" />
    </svg>
  )
}

export function WalletIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M20 7H5a2 2 0 0 1 0-4h13v4" />
      <path d="M3 5v14a2 2 0 0 0 2 2h15V7H5" />
      <circle cx="16.5" cy="14" r="1.2" />
    </svg>
  )
}

export function CalendarIcon(props) {
  return (
    <svg {...base} {...props}>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M8 3v4M16 3v4M3 10h18" />
    </svg>
  )
}

export function UsersIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3 20a6 6 0 0 1 12 0M16 5.5a3 3 0 0 1 0 5.8M18 20a5.5 5.5 0 0 0-3-4.9" />
    </svg>
  )
}

export function PinIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M12 21s7-5.4 7-11a7 7 0 1 0-14 0c0 5.6 7 11 7 11Z" />
      <circle cx="12" cy="10" r="2.6" />
    </svg>
  )
}

export function BotIcon(props) {
  return (
    <svg {...base} {...props}>
      <rect x="4" y="7" width="16" height="12" rx="3" />
      <path d="M12 3v4M8 13h.01M16 13h.01M9 17h6" />
    </svg>
  )
}

export function CurrencyIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M15 8.5A3.5 3.5 0 0 0 9 10c0 1.7 1.3 2.4 3 3s3 1.3 3 3a3.5 3.5 0 0 1-6 1.5M12 6v2M12 16v2" />
    </svg>
  )
}

export function ChartIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M4 20V4M4 20h16" />
      <path d="M8 16v-5M12 16V7M16 16v-8" />
    </svg>
  )
}

export function ListIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01" />
    </svg>
  )
}

export function RouteIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="6" cy="18" r="2.5" />
      <circle cx="18" cy="6" r="2.5" />
      <path d="M8.5 18H15a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h6.5" />
    </svg>
  )
}

export function ArrowRightIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  )
}

export function CheckIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M20 6 9 17l-5-5" />
    </svg>
  )
}

export function XIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  )
}

export function AlertIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M10.3 3.9 1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
      <path d="M12 9v4M12 17h.01" />
    </svg>
  )
}

export function InfoIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5M12 8h.01" />
    </svg>
  )
}

export function ChatIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M21 12a8 8 0 0 1-8 8H7l-4 3v-7a8 8 0 0 1 16-4Z" />
      <path d="M8 10h8M8 14h5" />
    </svg>
  )
}

export function TargetIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="4.5" />
      <circle cx="12" cy="12" r="1" />
    </svg>
  )
}

export function BoltIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M13 2 4.5 13.5H11L10 22l8.5-11.5H12L13 2Z" />
    </svg>
  )
}

export function ChevronDownIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="m6 9 6 6 6-6" />
    </svg>
  )
}

export function RefreshIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M21 12a9 9 0 1 1-2.6-6.4M21 3v6h-6" />
    </svg>
  )
}

export function SearchIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  )
}

export function DownloadIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M12 3v12M7 10l5 5 5-5M4 21h16" />
    </svg>
  )
}

export function CopyIcon(props) {
  return (
    <svg {...base} {...props}>
      <rect x="9" y="9" width="12" height="12" rx="2" />
      <path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1" />
    </svg>
  )
}

export function ClockIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  )
}

export function SendIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="m3 11 18-8-8 18-2.5-7.5L3 11Z" />
      <path d="M10.5 13.5 21 3" />
    </svg>
  )
}
