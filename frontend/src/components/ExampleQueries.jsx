const EXAMPLES = [
  {
    id: 'feasible',
    title: 'Within budget',
    tone: 'green',
    text: 'I have a budget of ₹100,000 and my estimated trip cost is €900. Can I afford it?',
  },
  {
    id: 'not-feasible',
    title: 'Over budget',
    tone: 'amber',
    text: 'I have a budget of ₹50,000 and my estimated trip cost is €900. Can I afford it?',
  },
  {
    id: 'estimate-destination',
    title: 'Estimate a destination',
    tone: 'indigo',
    text: 'I have a budget of ₹30,000 and want to visit Manali. Is my trip feasible?',
  },
]

export default function ExampleQueries({ onSelect, disabled = false }) {
  return (
    <div className="examples-section">
      <span className="section-kicker">Try an example</span>
      <div className="example-list">
        {EXAMPLES.map((example) => (
          <button
            key={example.id}
            id={`example-${example.id}`}
            type="button"
            className="example-card"
            onClick={() => onSelect(example.text)}
            title={example.text}
            disabled={disabled}
          >
            <span className={`example-dot tone-${example.tone}`} aria-hidden="true" />
            <span className="example-copy">
              <span className="example-title">{example.title}</span>
              <span className="example-text">{example.text}</span>
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}
