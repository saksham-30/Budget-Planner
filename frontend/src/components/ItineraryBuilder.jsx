/**
 * ItineraryBuilder — Phase 3
 *
 * Full trip-configuration form + AI generation + edit + modify + save/reopen.
 * Uses POST /api/itinerary/generate  → ItineraryDocument
 *      GET/PUT /api/trips/{trip_id}/itinerary
 *      POST    /api/trips/{trip_id}/itinerary/modify
 */

import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { apiFetch } from '../api.js'
import { DESTINATIONS } from '../data/destinations.js'
import TravelIntelligencePanel from './TravelIntelligencePanel.jsx'
import LogisticsPanel from './LogisticsPanel.jsx'
import CopilotPanel from './CopilotPanel.jsx'
import {
  AlertIcon,
  ArrowRightIcon,
  BotIcon,
  CalendarIcon,
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ClockIcon,
  EditIcon,
  InfoIcon,
  MapIcon,
  MoveDownIcon,
  MoveUpIcon,
  PinIcon,
  PlusIcon,
  RefreshIcon,
  RouteIcon,
  SaveIcon,
  SearchIcon,
  SendIcon,
  SparkIcon,
  StarIcon,
  TagIcon,
  TrashIcon,
  UndoIcon,
  UsersIcon,
  WalletIcon,
  WandIcon,
  WalletCheckIcon,
  XIcon,
} from './Icons.jsx'

const API_BASE = '/api'

// ─── Utilities ────────────────────────────────────────────────────────────────

function todayStr() {
  return new Date().toISOString().slice(0, 10)
}
function addDays(dateStr, n) {
  const d = new Date(`${dateStr}T00:00:00`)
  d.setDate(d.getDate() + n)
  return d.toISOString().slice(0, 10)
}
function dayCount(start, end) {
  const s = new Date(`${start}T00:00:00`)
  const e = new Date(`${end}T00:00:00`)
  return Math.round((e - s) / 86400000) + 1
}
function fmtDate(iso) {
  const d = new Date(`${iso}T00:00:00`)
  return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
}
function formatMoney(amount, currency) {
  if (amount == null) return '—'
  const sym = { INR: '₹', USD: '$', EUR: '€', GBP: '£' }[currency] || currency
  if (typeof amount !== 'number' || isNaN(amount)) return '—'
  return `${sym}${Math.abs(amount).toLocaleString(undefined, { maximumFractionDigits: 0 })}`
}
function uid() {
  return crypto.randomUUID ? crypto.randomUUID() : `id_${Date.now()}_${Math.random().toString(16).slice(2)}`
}
function detectConflicts(activities) {
  const timed = activities
    .filter(a => !a.is_break && a.time)
    .map(a => {
      const [h, m] = a.time.split(':').map(Number)
      const start = h * 60 + m
      return { ...a, startMin: start, endMin: start + (a.duration_minutes || 60) }
    })
    .sort((a, b) => a.startMin - b.startMin)
  const conflicts = new Set()
  for (let i = 0; i < timed.length - 1; i++) {
    if (timed[i].endMin > timed[i + 1].startMin) {
      conflicts.add(timed[i].id)
      conflicts.add(timed[i + 1].id)
    }
  }
  return conflicts
}

// ─── Itinerary state reducer ───────────────────────────────────────────────

function itineraryReducer(state, action) {
  switch (action.type) {
    case 'SET': {
      return { doc: action.doc, history: [], saved: false }
    }
    case 'MARK_SAVED': {
      return { ...state, saved: true }
    }
    case 'EDIT_ACTIVITY': {
      const { dayIdx, actIdx, patch } = action
      const newDoc = deepClone(state.doc)
      const act = newDoc.daily_plans[dayIdx].activities[actIdx]
      Object.assign(act, patch, { is_edited: true })
      recalcBudget(newDoc)
      return { doc: newDoc, history: [...state.history, state.doc].slice(-10), saved: false }
    }
    case 'DELETE_ACTIVITY': {
      const newDoc = deepClone(state.doc)
      newDoc.daily_plans[action.dayIdx].activities.splice(action.actIdx, 1)
      recalcBudget(newDoc)
      return { doc: newDoc, history: [...state.history, state.doc].slice(-10), saved: false }
    }
    case 'ADD_ACTIVITY': {
      const newDoc = deepClone(state.doc)
      newDoc.daily_plans[action.dayIdx].activities.push(action.activity)
      recalcBudget(newDoc)
      return { doc: newDoc, history: [...state.history, state.doc].slice(-10), saved: false }
    }
    case 'MOVE_ACTIVITY': {
      const newDoc = deepClone(state.doc)
      const acts = newDoc.daily_plans[action.dayIdx].activities
      const { actIdx, dir } = action
      const swap = actIdx + dir
      if (swap < 0 || swap >= acts.length) return state
      ;[acts[actIdx], acts[swap]] = [acts[swap], acts[actIdx]]
      return { doc: newDoc, history: [...state.history, state.doc].slice(-10), saved: false }
    }
    case 'APPLY_MODIFICATION': {
      const newDoc = deepClone(action.doc)
      recalcBudget(newDoc)
      return { doc: newDoc, history: [...state.history, state.doc].slice(-10), saved: false }
    }
    case 'UNDO': {
      if (!state.history.length) return state
      const prev = state.history[state.history.length - 1]
      return { doc: prev, history: state.history.slice(0, -1), saved: false }
    }
    default:
      return state
  }
}

function deepClone(obj) {
  return JSON.parse(JSON.stringify(obj))
}

function recalcBudget(doc) {
  // Recalculate category totals from activities
  const b = doc.budget_breakdown
  const currency = b.currency || doc.config?.currency || 'INR'
  const travelers = doc.config?.travelers || 1
  const actTotal = doc.daily_plans.reduce((sum, day) =>
    sum + day.activities.reduce((s, a) =>
      s + (a.is_break ? 0 : (a.estimated_cost_per_person || 0) * travelers), 0), 0)
  b.activities_total = round2(actTotal)
  b.grand_total = round2(
    b.accommodation_total + b.food_total + b.transport_total +
    b.activities_total + b.miscellaneous_total
  )
  b.per_person_total = round2(b.grand_total / travelers)
  const budget = doc.config?.budget || 0
  b.budget_remaining = round2(budget - b.grand_total)
  b.is_over_budget = b.grand_total > budget
  b.currency = currency
}

function round2(n) { return Math.round(n * 100) / 100 }

// ─── Default form config ──────────────────────────────────────────────────

const DEFAULT_CONFIG = {
  destination: '',
  origin: '',
  start_date: addDays(todayStr(), 30),
  end_date: addDays(todayStr(), 35),
  travelers: 2,
  budget: 50000,
  currency: 'INR',
  pace: 'balanced',
  budget_style: 'moderate',
  accommodation_preference: 'hotel',
  interests: [],
  special_requirements: '',
  extra_instructions: '',
}

const ALL_INTERESTS = [
  'Food', 'Culture', 'Nature', 'History', 'Shopping',
  'Adventure', 'Relaxation', 'Architecture', 'Art', 'Nightlife',
]

// ─── Sub-components ───────────────────────────────────────────────────────

function FormField({ label, hint, children, error }) {
  return (
    <div className="field">
      {label && <span className="field-label">{label}</span>}
      {children}
      {hint && <span className="field-hint">{hint}</span>}
      {error && <span className="field-error" role="alert">{error}</span>}
    </div>
  )
}

function ActivityCard({
  activity, dayIdx, actIdx, totalActs, currency, conflicts,
  onEdit, onDelete, onMove,
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(activity)
  const hasConflict = conflicts.has(activity.id)

  const save = () => { onEdit(draft); setEditing(false) }
  const cancel = () => { setDraft(activity); setEditing(false) }

  const catColor = {
    sightseeing: 'cat-culture',
    food: 'cat-food',
    transport: 'cat-budget',
    accommodation: 'cat-relaxation',
    leisure: 'cat-relaxation',
    shopping: 'cat-culture',
    nature: 'cat-nature',
    adventure: 'cat-adventure',
  }[activity.category] || ''

  if (editing) {
    return (
      <div className={`activity-card editing${hasConflict ? ' conflict' : ''}`}>
        <div className="activity-edit-grid">
          <label className="field">
            <span className="field-label">Name</span>
            <input value={draft.name} onChange={e => setDraft(d => ({ ...d, name: e.target.value }))} />
          </label>
          <label className="field">
            <span className="field-label">Time (HH:MM)</span>
            <input type="time" value={draft.time} onChange={e => setDraft(d => ({ ...d, time: e.target.value }))} />
          </label>
          <label className="field">
            <span className="field-label">Duration (minutes)</span>
            <input type="number" min="5" max="720" value={draft.duration_minutes}
              onChange={e => setDraft(d => ({ ...d, duration_minutes: parseInt(e.target.value) || 60 }))} />
          </label>
          <label className="field">
            <span className="field-label">Cost per person ({currency})</span>
            <input type="number" min="0" step="0.01" value={draft.estimated_cost_per_person}
              onChange={e => setDraft(d => ({ ...d, estimated_cost_per_person: parseFloat(e.target.value) || 0 }))} />
          </label>
          <label className="field span-2">
            <span className="field-label">Location</span>
            <input value={draft.location} onChange={e => setDraft(d => ({ ...d, location: e.target.value }))} />
          </label>
          <label className="field span-2">
            <span className="field-label">Description</span>
            <textarea rows={2} value={draft.description}
              onChange={e => setDraft(d => ({ ...d, description: e.target.value }))} />
          </label>
          <label className="field span-2">
            <span className="field-label">Notes</span>
            <input value={draft.notes} onChange={e => setDraft(d => ({ ...d, notes: e.target.value }))} />
          </label>
          <label className="check-chip">
            <input type="checkbox" checked={draft.is_optional}
              onChange={e => setDraft(d => ({ ...d, is_optional: e.target.checked }))} />
            <span className="check-box"><CheckIcon width={11} height={11} /></span>
            Optional activity
          </label>
        </div>
        <div className="activity-edit-actions">
          <button className="btn btn-primary btn-sm" type="button" onClick={save}>
            <CheckIcon width={13} height={13} /> Save
          </button>
          <button className="btn btn-ghost btn-sm" type="button" onClick={cancel}>Cancel</button>
        </div>
      </div>
    )
  }

  return (
    <div className={`activity-card${activity.is_break ? ' activity-break' : ''}${hasConflict ? ' activity-conflict' : ''}${activity.is_optional ? ' activity-optional' : ''}`}>
      <div className="activity-time-col">
        <span className="activity-time">{activity.time}</span>
        <span className="activity-duration">{activity.duration_minutes}m</span>
      </div>
      <div className="activity-body">
        <div className="activity-header">
          <span className="activity-name">{activity.name}</span>
          {activity.is_optional && <span className="tag">Optional</span>}
          {hasConflict && <span className="tag tag-amber">⚠ Overlap</span>}
          <span className={`dest-category-tag ${catColor}`}>{activity.category}</span>
        </div>
        {activity.description && <p className="activity-desc">{activity.description}</p>}
        {activity.location && (
          <span className="activity-location">
            <MapIcon width={11} height={11} /> {activity.location}
          </span>
        )}
        {activity.notes && <p className="activity-notes">{activity.notes}</p>}
        {activity.estimated_cost_per_person > 0 && (
          <span className="activity-cost">
            <TagIcon width={11} height={11} />
            {formatMoney(activity.estimated_cost_per_person, currency)} / person
          </span>
        )}
      </div>
      <div className="activity-actions">
        <button className="activity-action-btn" type="button" title="Move up"
          disabled={actIdx === 0} onClick={() => onMove(-1)} aria-label="Move activity up">
          <MoveUpIcon width={13} height={13} />
        </button>
        <button className="activity-action-btn" type="button" title="Move down"
          disabled={actIdx === totalActs - 1} onClick={() => onMove(1)} aria-label="Move activity down">
          <MoveDownIcon width={13} height={13} />
        </button>
        <button className="activity-action-btn" type="button" title="Edit"
          onClick={() => setEditing(true)} aria-label="Edit activity">
          <EditIcon width={13} height={13} />
        </button>
        <button className="activity-action-btn activity-delete" type="button" title="Delete"
          onClick={onDelete} aria-label="Delete activity">
          <TrashIcon width={13} height={13} />
        </button>
      </div>
    </div>
  )
}

function AddActivityForm({ dayIdx, currency, onAdd, onCancel }) {
  const [form, setForm] = useState({
    name: '', time: '10:00', duration_minutes: 60, location: '',
    description: '', category: 'sightseeing', estimated_cost_per_person: 0,
    currency, notes: '', is_break: false, is_optional: false, is_edited: true,
  })
  const [error, setError] = useState('')

  const submit = () => {
    if (!form.name.trim()) { setError('Activity name is required.'); return }
    onAdd({ ...form, id: uid(), name: form.name.trim() })
  }

  return (
    <div className="add-activity-form">
      <h4 className="add-activity-title">Add activity</h4>
      <div className="activity-edit-grid">
        <label className="field span-2">
          <span className="field-label">Name *</span>
          <input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
            placeholder="e.g. Visit Amber Fort" autoFocus />
          {error && <span className="field-error">{error}</span>}
        </label>
        <label className="field">
          <span className="field-label">Time</span>
          <input type="time" value={form.time}
            onChange={e => setForm(f => ({ ...f, time: e.target.value }))} />
        </label>
        <label className="field">
          <span className="field-label">Duration (min)</span>
          <input type="number" min="5" max="720" value={form.duration_minutes}
            onChange={e => setForm(f => ({ ...f, duration_minutes: parseInt(e.target.value) || 60 }))} />
        </label>
        <label className="field">
          <span className="field-label">Cost / person ({currency})</span>
          <input type="number" min="0" step="0.01" value={form.estimated_cost_per_person}
            onChange={e => setForm(f => ({ ...f, estimated_cost_per_person: parseFloat(e.target.value) || 0 }))} />
        </label>
        <label className="field">
          <span className="field-label">Category</span>
          <select value={form.category} onChange={e => setForm(f => ({ ...f, category: e.target.value }))}>
            {['sightseeing', 'food', 'transport', 'leisure', 'shopping', 'nature', 'adventure', 'accommodation'].map(c =>
              <option key={c}>{c}</option>)}
          </select>
        </label>
        <label className="field span-2">
          <span className="field-label">Location</span>
          <input value={form.location} placeholder="Place name"
            onChange={e => setForm(f => ({ ...f, location: e.target.value }))} />
        </label>
      </div>
      <div className="activity-edit-actions">
        <button className="btn btn-primary btn-sm" type="button" onClick={submit}>
          <PlusIcon width={13} height={13} /> Add
        </button>
        <button className="btn btn-ghost btn-sm" type="button" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}

function DayPanel({ day, dayIdx, currency, itineraryDispatch }) {
  const [addingActivity, setAddingActivity] = useState(false)
  const conflicts = detectConflicts(day.activities)
  const dayTotal = day.activities.reduce(
    (s, a) => s + (a.is_break ? 0 : (a.estimated_cost_per_person || 0)),
    0,
  )

  return (
    <div className="day-panel">
      <div className="day-panel-header">
        <div className="day-panel-title">
          <div className="day-marker">
            <strong>{day.day_number}</strong>
            <span>Day</span>
          </div>
          <div>
            <div className="day-title">{day.title}</div>
            <div className="day-date">{fmtDate(day.date)}</div>
          </div>
        </div>
        <span className="day-cost-total">
          <TagIcon width={12} height={12} />
          {formatMoney(dayTotal, currency)} / person
        </span>
      </div>
      {day.description && <p className="day-panel-desc">{day.description}</p>}

      <div className="activity-list">
        {day.activities.map((act, actIdx) => (
          <ActivityCard
            key={act.id}
            activity={act}
            dayIdx={dayIdx}
            actIdx={actIdx}
            totalActs={day.activities.length}
            currency={currency}
            conflicts={conflicts}
            onEdit={patch => itineraryDispatch({ type: 'EDIT_ACTIVITY', dayIdx, actIdx, patch })}
            onDelete={() => itineraryDispatch({ type: 'DELETE_ACTIVITY', dayIdx, actIdx })}
            onMove={dir => itineraryDispatch({ type: 'MOVE_ACTIVITY', dayIdx, actIdx, dir })}
          />
        ))}
      </div>

      {addingActivity ? (
        <AddActivityForm
          dayIdx={dayIdx}
          currency={currency}
          onAdd={activity => {
            itineraryDispatch({ type: 'ADD_ACTIVITY', dayIdx, activity })
            setAddingActivity(false)
          }}
          onCancel={() => setAddingActivity(false)}
        />
      ) : (
        <button className="btn btn-ghost btn-sm add-activity-btn" type="button"
          onClick={() => setAddingActivity(true)}>
          <PlusIcon width={13} height={13} /> Add activity
        </button>
      )}
    </div>
  )
}

function BudgetBreakdown({ budget, config }) {
  if (!budget) return null
  const currency = budget.currency || config.currency
  const categories = [
    { key: 'accommodation_total', label: 'Accommodation' },
    { key: 'food_total', label: 'Food & Drink' },
    { key: 'transport_total', label: 'Transport' },
    { key: 'activities_total', label: 'Activities' },
    { key: 'miscellaneous_total', label: 'Miscellaneous' },
  ]
  const tripTotal = Math.max(Number(budget.grand_total) || 0, 0)
  const tripBudget = Math.max(Number(config.budget) || 0, 0)
  const budgetPercent = tripBudget > 0 ? Math.round((tripTotal / tripBudget) * 100) : null
  return (
    <div className="budget-breakdown-card panel-card">
      <h3 className="card-title">
        <WalletIcon width={15} height={15} /> Budget Breakdown
      </h3>
      <p className="card-sub">{budget.notes}</p>
      {budgetPercent != null && (
        <div className={`itinerary-budget-progress${budget.is_over_budget ? ' over-budget' : ''}`}>
          <div className="itinerary-budget-progress-head">
            <span>{budget.is_over_budget ? 'Budget exceeded' : 'Budget remaining'}</span>
            <strong>{budgetPercent}%</strong>
          </div>
          <div
            className="result-budget-progress-track"
            role="progressbar"
            aria-label="Itinerary cost as a share of budget"
            aria-valuemin="0"
            aria-valuemax="100"
            aria-valuenow={Math.min(budgetPercent, 100)}
          >
            <span style={{ width: `${Math.min(Math.max(budgetPercent, 0), 100)}%` }} />
          </div>
          <div className="itinerary-budget-progress-foot">
            <strong>{formatMoney(budget.grand_total, currency)}</strong>
            <span>{formatMoney(Math.abs(budget.budget_remaining), currency)} {budget.is_over_budget ? 'over' : 'left'}</span>
          </div>
        </div>
      )}
      <div className="budget-category-bars" aria-label="Estimated trip cost by category">
        {categories.map(({ key, label }) => {
          const amount = Math.max(Number(budget[key]) || 0, 0)
          const share = tripTotal > 0 ? (amount / tripTotal) * 100 : 0
          return (
            <div className="budget-category-row" key={key}>
              <div className="budget-category-meta">
                <span>{label}</span>
                <strong>{formatMoney(amount, currency)}</strong>
              </div>
              <div className="budget-category-track" aria-hidden="true">
                <span style={{ width: `${Math.min(share, 100)}%` }} />
              </div>
            </div>
          )
        })}
      </div>
      <dl className="item-list">
        {categories.map(({ key, label }) => (
          <div className="item-row" key={key}>
            <dt>{label}</dt>
            <dd>{formatMoney(budget[key], currency)}</dd>
          </div>
        ))}
        <div className="item-row item-row-total">
          <dt>Total trip</dt>
          <dd>{formatMoney(budget.grand_total, currency)}</dd>
        </div>
      </dl>
      <div className="budget-summary-row">
        <div className="budget-stat">
          <span className="stat-label">Per person</span>
          <span className="stat-value">{formatMoney(budget.per_person_total, currency)}</span>
        </div>
        <div className="budget-stat">
          <span className="stat-label">Daily average</span>
          <span className="stat-value">{formatMoney(budget.daily_average, currency)}</span>
        </div>
        <div className="budget-stat">
          <span className="stat-label">Your budget</span>
          <span className="stat-value">{formatMoney(config.budget, currency)}</span>
        </div>
        <div className={`budget-stat ${budget.is_over_budget ? 'over-budget' : 'under-budget'}`}>
          <span className="stat-label">{budget.is_over_budget ? 'Over budget' : 'Remaining'}</span>
          <span className={`stat-value ${budget.is_over_budget ? 'negative' : 'positive'}`}>
            {formatMoney(Math.abs(budget.budget_remaining), currency)}
          </span>
        </div>
      </div>
      <p className="card-footnote">
        <InfoIcon width={12} height={12} />
        {' '}Estimates are illustrative only. Actual costs depend on booking timing, exchange rates, and personal spending.
        {' '}Accommodation, food and transport totals are as estimated by the AI for the whole group.
        Activity total is recalculated from per-person costs × number of travelers after edits.
      </p>
    </div>
  )
}

function AIModifyPanel({ tripId, itinerary, onPreview, onClose, intelligenceContext, prefilledRequest }) {
  const [request, setRequest] = useState(prefilledRequest || '')
  const [scope, setScope] = useState('all')
  const [targetDay, setTargetDay] = useState(1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    if (!request.trim()) return
    setLoading(true)
    setError('')
    try {
      const body = {
        modification_request: request.trim(),
        target_day: scope === 'day' ? targetDay : null,
        current_itinerary: itinerary,  // pass in-memory doc so modify works before first save
        intelligence_context: intelligenceContext || null,
      }
      const res = await apiFetch(`${API_BASE}/trips/${encodeURIComponent(tripId)}/itinerary/modify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || 'Modification failed.')
      }
      const preview = await res.json()
      onPreview(preview)
    } catch (err) {
      setError(err.message || 'Modification failed.')
    } finally {
      setLoading(false)
    }
  }

  const SUGGESTIONS = [
    'Make Day 2 more relaxed.',
    'Replace expensive activities with free alternatives.',
    'Add more local food experiences.',
    'Make the entire trip cheaper.',
    'Add more historical attractions.',
    'Reduce walking.',
  ]

  return (
    <div className="ai-modify-panel panel-card">
      <div className="ai-modify-header">
        <h3 className="card-title">
          <WandIcon width={15} height={15} /> Modify with AI
        </h3>
        <button className="link-btn" type="button" onClick={onClose}>
          <XIcon width={13} height={13} /> Close
        </button>
      </div>

      <div className="ai-modify-scope">
        <label className="check-chip">
          <input type="radio" name="scope" value="all" checked={scope === 'all'}
            onChange={() => setScope('all')} />
          <span className="check-box"><CheckIcon width={11} height={11} /></span>
          Entire trip
        </label>
        <label className="check-chip">
          <input type="radio" name="scope" value="day" checked={scope === 'day'}
            onChange={() => setScope('day')} />
          <span className="check-box"><CheckIcon width={11} height={11} /></span>
          Specific day
        </label>
        {scope === 'day' && (
          <select value={targetDay} onChange={e => setTargetDay(Number(e.target.value))}
            className="filter-select" style={{ width: '100px' }}>
            {itinerary.daily_plans.map(d => (
              <option key={d.day_number} value={d.day_number}>
                Day {d.day_number}
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="suggestion-row" style={{ marginTop: '0.5rem' }}>
        {SUGGESTIONS.map(s => (
          <button key={s} type="button" className="suggestion-chip"
            onClick={() => setRequest(s)} disabled={loading}>
            {s}
          </button>
        ))}
      </div>

      <div className="follow-up-form" style={{ marginTop: '0.75rem' }}>
        <input
          className="follow-up-input"
          value={request}
          onChange={e => setRequest(e.target.value)}
          placeholder="Describe the change you want…"
          disabled={loading}
          aria-label="Modification request"
        />
        <button className="btn btn-primary btn-sm" type="button"
          disabled={!request.trim() || loading} onClick={submit}>
          {loading ? <span className="spinner" /> : <SendIcon width={14} height={14} />}
          {loading ? 'Modifying…' : 'Preview'}
        </button>
      </div>

      {error && (
        <div className="banner banner-error" role="alert" style={{ marginTop: '0.5rem' }}>
          <AlertIcon className="banner-icon" />
          <div className="banner-copy">
            <div className="banner-title">Modification failed</div>
            <p className="banner-body">{error}</p>
          </div>
        </div>
      )}

      <p className="card-footnote" style={{ marginTop: '0.5rem' }}>
        <InfoIcon width={12} height={12} /> A preview will appear for you to review before applying.
      </p>
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────────────────

export default function ItineraryBuilder({ prefillDestination, initialTripId }) {
  const [config, setConfig] = useState(() => ({
    ...DEFAULT_CONFIG,
    ...(prefillDestination ? { destination: prefillDestination } : {}),
  }))
  const [configErrors, setConfigErrors] = useState({})
  const [phase, setPhase] = useState('form')   // form | generating | view
  const [tripId] = useState(() => {
    if (initialTripId) return initialTripId
    return crypto.randomUUID ? crypto.randomUUID() : `itin-${Date.now()}`
  })
  const [genError, setGenError] = useState('')
  const [saveStatus, setSaveStatus] = useState('idle')  // idle | saving | saved | error
  const [activeDay, setActiveDay] = useState(0)
  const [showModify, setShowModify] = useState(false)
  const [modifyPreview, setModifyPreview] = useState(null)
  const [intelligenceContext, setIntelligenceContext] = useState('')
  const [weatherData, setWeatherData] = useState(null)
  const [feasibilityData, setFeasibilityData] = useState(null)
  const [logisticsData, setLogisticsData] = useState(null)

  const [itineraryState, itineraryDispatch] = useReducer(itineraryReducer, {
    doc: null, history: [], saved: false,
  })
  const { doc, saved } = itineraryState

  // Sync prefill destination
  useEffect(() => {
    if (prefillDestination) {
      setConfig(c => ({ ...c, destination: prefillDestination }))
    }
  }, [prefillDestination])

  // Load existing itinerary if tripId provided
  useEffect(() => {
    if (!initialTripId) return
    const load = async () => {
      try {
        const res = await apiFetch(`${API_BASE}/trips/${encodeURIComponent(initialTripId)}/itinerary`)
        if (res.ok) {
          const existing = await res.json()
          itineraryDispatch({ type: 'SET', doc: existing })
          setConfig(existing.config)
          setPhase('view')
        }
      } catch { /* no existing itinerary — stay on form */ }
    }
    load()
  }, [initialTripId])

  const validateConfig = () => {
    const errors = {}
    if (!config.destination.trim()) errors.destination = 'Destination is required.'
    if (!config.start_date) errors.start_date = 'Start date is required.'
    if (!config.end_date) errors.end_date = 'End date is required.'
    if (config.end_date < config.start_date) errors.end_date = 'End date must be after start date.'
    if (dayCount(config.start_date, config.end_date) > 30) errors.end_date = 'Trip cannot exceed 30 days.'
    if (!config.travelers || config.travelers < 1) errors.travelers = 'At least 1 traveler required.'
    if (!config.budget || config.budget <= 0) errors.budget = 'Budget must be greater than zero.'
    return errors
  }

  const generate = async () => {
    const errors = validateConfig()
    if (Object.keys(errors).length) { setConfigErrors(errors); return }
    setConfigErrors({})
    setGenError('')
    setPhase('generating')

    // First save preferences so the trip is registered
    const prefs = {
      destination: config.destination,
      start_date: config.start_date,
      end_date: config.end_date,
      travelers: config.travelers,
      budget: config.budget,
      currency: config.currency,
      budget_style: config.budget_style,
      preferred_accommodation_type: config.accommodation_preference,
      food_preferences: [],
      interests: config.interests,
      hard_constraints: [],
    }
    try {
      await apiFetch(`${API_BASE}/trips/${encodeURIComponent(tripId)}/preferences`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(prefs),
      })
    } catch { /* non-fatal — itinerary generation can proceed */ }

    try {
      const res = await apiFetch(`${API_BASE}/itinerary/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || 'Generation failed.')
      }
      const newDoc = await res.json()
      itineraryDispatch({ type: 'SET', doc: newDoc })
      setActiveDay(0)
      setPhase('view')
    } catch (err) {
      setGenError(err.message || 'Failed to generate itinerary.')
      setPhase('form')
    }
  }

  const saveItinerary = useCallback(async () => {
    if (!doc) return
    setSaveStatus('saving')
    try {
      const res = await apiFetch(`${API_BASE}/trips/${encodeURIComponent(tripId)}/itinerary`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ itinerary: doc }),
      })
      if (!res.ok) throw new Error('Save failed.')
      itineraryDispatch({ type: 'MARK_SAVED' })
      setSaveStatus('saved')
      setTimeout(() => setSaveStatus('idle'), 3000)
    } catch (err) {
      setSaveStatus('error')
    }
  }, [doc, tripId])

  const applyModification = () => {
    if (!modifyPreview) return
    itineraryDispatch({ type: 'APPLY_MODIFICATION', doc: modifyPreview })
    setModifyPreview(null)
    setShowModify(false)
  }

  const cancelModification = () => {
    setModifyPreview(null)
  }

  // Copilot suggests a modification — populate the AI modify panel with the suggestion
  const handleCopilotModificationRequest = useCallback((suggestionText) => {
    // Extract a concise modification request from the copilot reply
    // The suggestion may be a full paragraph; we extract the actionable part.
    // Simply open the modify panel with the suggestion pre-filled.
    setShowModify(true)
    setModifyPreview(null)
    // The AIModifyPanel will show with this pre-filled text via the state below
    setCopilotPrefilledRequest(suggestionText.slice(0, 500))
  }, [])

  const [copilotPrefilledRequest, setCopilotPrefilledRequest] = useState('')

  const displayDoc = modifyPreview || doc

  // ── Render: config form ────────────────────────────────────────────────
  if (phase === 'form' || phase === 'generating') {
    const days = config.start_date && config.end_date
      ? Math.max(dayCount(config.start_date, config.end_date), 0)
      : 0

    return (
      <div className="itinerary-builder">
        <div className="itinerary-form-header">
          <span className="eyebrow"><SparkIcon width={13} height={13} /> AI Itinerary Builder</span>
          <h1 className="planner-title">Build Your Trip Itinerary</h1>
          <p className="planner-sub">
            Fill in your trip details and our AI will create a day-by-day itinerary with
            estimated costs. You can edit every activity after generation.
          </p>
        </div>

        {genError && (
          <div className="banner banner-error" role="alert">
            <AlertIcon className="banner-icon" />
            <div className="banner-copy">
              <div className="banner-title">Generation failed</div>
              <p className="banner-body">{genError}</p>
            </div>
            <div className="banner-actions">
              <button className="btn btn-ghost btn-sm" type="button"
                onClick={() => { setGenError(''); setPhase('form') }}>Dismiss</button>
            </div>
          </div>
        )}

        <form className="itinerary-form" onSubmit={e => { e.preventDefault(); generate() }} noValidate>
          {/* Row 1 — destination + origin */}
          <div className="itin-form-grid">
            <FormField label="Destination *" error={configErrors.destination}>
              <input
                type="text"
                value={config.destination}
                list="itin-destination-list"
                onChange={e => setConfig(c => ({ ...c, destination: e.target.value }))}
                placeholder="e.g. Goa, Paris, Tokyo"
                autoComplete="off"
              />
              <datalist id="itin-destination-list">
                {DESTINATIONS.map(d => <option key={d.id} value={d.name} />)}
              </datalist>
            </FormField>
            <FormField label="Flying from (optional)">
              <input
                type="text"
                value={config.origin}
                onChange={e => setConfig(c => ({ ...c, origin: e.target.value }))}
                placeholder="e.g. Delhi, Mumbai"
              />
            </FormField>
          </div>

          {/* Row 2 — dates + travelers */}
          <div className="itin-form-grid">
            <FormField label={<><CalendarIcon width={13} height={13} /> Start date *</>}
              error={configErrors.start_date}>
              <input type="date" value={config.start_date}
                min={todayStr()}
                max={config.end_date || undefined}
                onChange={e => setConfig(c => ({ ...c, start_date: e.target.value }))} />
            </FormField>
            <FormField label={<><CalendarIcon width={13} height={13} /> End date *</>}
              error={configErrors.end_date}>
              <input type="date" value={config.end_date}
                min={config.start_date || todayStr()}
                onChange={e => setConfig(c => ({ ...c, end_date: e.target.value }))} />
            </FormField>
            <FormField label="Duration">
              <div className="field-static">{days > 0 ? `${days} day${days !== 1 ? 's' : ''}` : '—'}</div>
            </FormField>
            <FormField label={<><UsersIcon width={13} height={13} /> Travelers *</>}
              error={configErrors.travelers}>
              <input type="number" min="1" max="20" value={config.travelers}
                onChange={e => setConfig(c => ({ ...c, travelers: parseInt(e.target.value) || 1 }))} />
            </FormField>
          </div>

          {/* Row 3 — budget */}
          <div className="itin-form-grid">
            <FormField label={<><WalletIcon width={13} height={13} /> Total budget *</>}
              error={configErrors.budget}>
              <input type="number" min="1" step="0.01" value={config.budget}
                onChange={e => setConfig(c => ({ ...c, budget: parseFloat(e.target.value) || 0 }))} />
            </FormField>
            <FormField label="Currency">
              <select value={config.currency}
                onChange={e => setConfig(c => ({ ...c, currency: e.target.value }))}>
                {['INR', 'EUR', 'USD', 'GBP'].map(cur => <option key={cur}>{cur}</option>)}
              </select>
            </FormField>
            <FormField label="Budget style">
              <select value={config.budget_style}
                onChange={e => setConfig(c => ({ ...c, budget_style: e.target.value }))}>
                {['budget', 'moderate', 'luxury'].map(s => <option key={s}>{s}</option>)}
              </select>
            </FormField>
            <FormField label="Accommodation">
              <select value={config.accommodation_preference}
                onChange={e => setConfig(c => ({ ...c, accommodation_preference: e.target.value }))}>
                {['hotel', 'hostel', 'rental', 'resort', 'guesthouse'].map(s => <option key={s}>{s}</option>)}
              </select>
            </FormField>
          </div>

          {/* Row 4 — pace + interests */}
          <div className="itin-form-grid">
            <FormField label="Travel pace"
              hint="Relaxed: 2–3 activities/day. Balanced: 3–4. Packed: 4–5.">
              <select value={config.pace}
                onChange={e => setConfig(c => ({ ...c, pace: e.target.value }))}>
                {['relaxed', 'balanced', 'packed'].map(s => <option key={s}>{s}</option>)}
              </select>
            </FormField>
            <div className="field span-3">
              <span className="field-label">Interests</span>
              <div className="check-list">
                {ALL_INTERESTS.map(interest => (
                  <label key={interest} className="check-chip">
                    <input type="checkbox"
                      checked={config.interests.includes(interest.toLowerCase())}
                      onChange={() => setConfig(c => {
                        const key = interest.toLowerCase()
                        return {
                          ...c,
                          interests: c.interests.includes(key)
                            ? c.interests.filter(i => i !== key)
                            : [...c.interests, key],
                        }
                      })} />
                    <span className="check-box"><CheckIcon width={11} height={11} /></span>
                    {interest}
                  </label>
                ))}
              </div>
            </div>
          </div>

          {/* Row 5 — optional notes */}
          <div className="itin-form-grid">
            <FormField label="Dietary / accessibility requirements" hint="Optional">
              <input type="text" value={config.special_requirements}
                onChange={e => setConfig(c => ({ ...c, special_requirements: e.target.value }))}
                placeholder="e.g. Vegetarian, wheelchair accessible" />
            </FormField>
            <FormField label="Additional instructions" hint="Optional">
              <input type="text" value={config.extra_instructions}
                onChange={e => setConfig(c => ({ ...c, extra_instructions: e.target.value }))}
                placeholder="e.g. Avoid tourist traps, include local markets" />
            </FormField>
          </div>

          <div className="itin-form-actions">
            <button type="submit" className="btn btn-primary"
              disabled={phase === 'generating'}>
              {phase === 'generating'
                ? <><span className="spinner" /> Generating itinerary…</>
                : <><SparkIcon width={16} height={16} /> Generate Itinerary</>}
            </button>
            {phase === 'generating' && (
              <span className="itin-generating-hint">
                <InfoIcon width={13} height={13} />
                This may take 20–60 seconds depending on your model. Please wait.
              </span>
            )}
          </div>
        </form>
      </div>
    )
  }

  // ── Render: itinerary view ────────────────────────────────────────────
  if (!displayDoc) return null

  const currency = displayDoc.config?.currency || config.currency

  return (
    <div className="itinerary-view">
      {/* Header */}
      <div className="itinerary-view-header">
        <div className="itinerary-view-title">
          <div>
            <span className="eyebrow">
              <RouteIcon width={13} height={13} /> Itinerary
            </span>
            <h1 className="planner-title">{displayDoc.config?.destination}</h1>
            <div className="itinerary-meta-row">
              <span className="dest-meta-item">
                <CalendarIcon width={13} height={13} />
                {fmtDate(displayDoc.config?.start_date)} → {fmtDate(displayDoc.config?.end_date)}
              </span>
              <span className="dest-meta-item">
                <UsersIcon width={13} height={13} />
                {displayDoc.config?.travelers} traveler{displayDoc.config?.travelers !== 1 ? 's' : ''}
              </span>
              <span className="dest-meta-item">
                <TagIcon width={13} height={13} />
                Budget: {formatMoney(displayDoc.config?.budget, currency)}
              </span>
            </div>
          </div>
        </div>

        <div className="itinerary-view-actions">
          {itineraryState.history.length > 0 && (
            <button className="btn btn-ghost btn-sm" type="button"
              onClick={() => itineraryDispatch({ type: 'UNDO' })}>
              <UndoIcon width={14} height={14} /> Undo
            </button>
          )}
          <button className="btn btn-ghost btn-sm" type="button"
            onClick={() => setShowModify(s => !s)}>
            <WandIcon width={14} height={14} /> Modify with AI
          </button>
          <button className="btn btn-ghost btn-sm" type="button"
            onClick={() => setPhase('form')}>
            <RefreshIcon width={14} height={14} /> New itinerary
          </button>
          <button
            className={`btn btn-sm ${saveStatus === 'saved' ? 'btn-ghost' : 'btn-primary'}`}
            type="button"
            disabled={saveStatus === 'saving' || (saved && !modifyPreview)}
            onClick={saveItinerary}
          >
            {saveStatus === 'saving'
              ? <><span className="spinner" /> Saving…</>
              : saveStatus === 'saved'
              ? <><CheckIcon width={14} height={14} /> Saved</>
              : <><SaveIcon width={14} height={14} /> {saved ? 'Saved' : 'Save itinerary'}</>}
          </button>
        </div>
      </div>

      {saveStatus === 'error' && (
        <div className="banner banner-error" role="alert">
          <AlertIcon className="banner-icon" />
          <div className="banner-copy">
            <div className="banner-title">Save failed</div>
            <p className="banner-body">Could not save the itinerary. Please try again.</p>
          </div>
        </div>
      )}

      {displayDoc.summary && (
        <div className="itinerary-summary panel-card">
          <p>{displayDoc.summary}</p>
          {displayDoc.generation_notes && (
            <p className="card-footnote">
              <InfoIcon width={12} height={12} /> {displayDoc.generation_notes}
            </p>
          )}
        </div>
      )}

      {/* Travel Intelligence Panel */}
      <TravelIntelligencePanel
        itinerary={displayDoc}
        onContextUpdate={setIntelligenceContext}
        onWeatherLoad={setWeatherData}
        onFeasibilityLoad={setFeasibilityData}
      />

      {/* Travel Logistics Panel */}
      <LogisticsPanel itinerary={displayDoc} onDataLoad={setLogisticsData} />

      {/* AI Travel Copilot */}
      <CopilotPanel
        tripId={tripId}
        itinerary={displayDoc}
        weatherContext={weatherData}
        feasibilityContext={feasibilityData}
        logisticsContext={logisticsData}
        onModificationRequest={handleCopilotModificationRequest}
      />

      {/* Modification preview */}
      {modifyPreview && (
        <div className="banner banner-warning" role="status">
          <AlertIcon className="banner-icon" />
          <div className="banner-copy">
            <div className="banner-title">AI modification preview</div>
            <p className="banner-body">
              Review the changes below. Apply to update your itinerary, or Cancel to keep the original.
            </p>
          </div>
          <div className="banner-actions">
            <button className="btn btn-primary btn-sm" type="button" onClick={applyModification}>
              Apply changes
            </button>
            <button className="btn btn-ghost btn-sm" type="button" onClick={cancelModification}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* AI Modify panel */}
      {showModify && !modifyPreview && (
        <AIModifyPanel
          tripId={tripId}
          itinerary={doc}
          onPreview={setModifyPreview}
          onClose={() => { setShowModify(false); setCopilotPrefilledRequest('') }}
          intelligenceContext={intelligenceContext}
          prefilledRequest={copilotPrefilledRequest}
        />
      )}

      {/* Day selector tabs */}
      <nav className="itin-day-tabs" role="tablist" aria-label="Day selector">
        {displayDoc.daily_plans.map((day, idx) => (
          <button
            key={day.day_number}
            type="button"
            role="tab"
            aria-selected={activeDay === idx}
            className={`itin-day-tab${activeDay === idx ? ' active' : ''}`}
            onClick={() => setActiveDay(idx)}
          >
            <span className="itin-tab-day">Day {day.day_number}</span>
            <span className="itin-tab-date">{fmtDate(day.date)}</span>
            <span className="itin-tab-count">{day.activities.length} {day.activities.length === 1 ? 'activity' : 'activities'}</span>
          </button>
        ))}
      </nav>

      <div role="tabpanel" className="itin-day-content">
        {displayDoc.daily_plans[activeDay] && (
          <DayPanel
            day={displayDoc.daily_plans[activeDay]}
            dayIdx={activeDay}
            currency={currency}
            itineraryDispatch={itineraryDispatch}
          />
        )}
      </div>

      {/* Budget */}
      <BudgetBreakdown budget={displayDoc.budget_breakdown} config={displayDoc.config} />
    </div>
  )
}
