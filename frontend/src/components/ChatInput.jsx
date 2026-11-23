import { ArrowRightIcon, SearchIcon, XIcon } from './Icons.jsx'

export default function ChatInput({ query, setQuery, onSubmit, onCancel, loading }) {
  const handleKeyDown = (event) => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault()
      if (!loading && query.trim()) onSubmit()
    }
  }

  return (
    <div className="input-section">
      <div className="section-heading">
        <span className="section-kicker">Currency feasibility</span>
        <h2 className="input-title">Your travel budget question</h2>
      </div>

      <div className="textarea-wrap">
        <textarea
          id="query-input"
          className="query-textarea"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="e.g. I have a budget of ₹100,000 and my estimated trip cost is €900. Can I afford it?"
          disabled={loading}
          maxLength={4000}
          aria-describedby="query-hint"
          rows={4}
        />
        <span className="textarea-hint" id="query-hint">
          <kbd>Ctrl</kbd> + <kbd>Enter</kbd> to submit
        </span>
      </div>

      <div className="input-actions">
        <button
          id="analyze-btn"
          className="btn btn-primary"
          onClick={onSubmit}
          disabled={loading || !query.trim()}
          aria-busy={loading}
        >
          {loading ? (
            <>
              <span className="spinner" aria-hidden="true" />
              Analyzing…
            </>
          ) : (
            <>
              <SearchIcon width={16} height={16} />
              Analyze trip
              <ArrowRightIcon width={16} height={16} className="btn-trailing-icon" />
            </>
          )}
        </button>
        {loading && (
          <button className="btn btn-ghost" type="button" onClick={onCancel}>
            <XIcon width={15} height={15} />
            Cancel
          </button>
        )}
      </div>
    </div>
  )
}
