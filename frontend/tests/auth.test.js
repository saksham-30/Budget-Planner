import assert from 'node:assert/strict'
import test from 'node:test'
import { csrfFetch } from '../src/auth.js'

test('unsafe same-origin requests include the CSRF token and credentials', async () => {
  const originalDocument = globalThis.document
  const originalFetch = globalThis.fetch
  globalThis.document = { cookie: 'other=value; travel_planner_csrf=csrf%2Btoken' }
  let captured
  globalThis.fetch = async (_input, options) => {
    captured = options
    return new Response('{}', { status: 200 })
  }
  try {
    await csrfFetch('/api/trips/x/preferences', { method: 'PUT' })
    assert.equal(captured.credentials, 'same-origin')
    assert.equal(captured.headers.get('X-CSRF-Token'), 'csrf+token')
  } finally {
    globalThis.document = originalDocument
    globalThis.fetch = originalFetch
  }
})

test('a 401 dispatches the event that returns the app to sign-in', async () => {
  const originalDocument = globalThis.document
  const originalWindow = globalThis.window
  const originalFetch = globalThis.fetch
  let expired = false
  globalThis.document = { cookie: '' }
  globalThis.window = { dispatchEvent: event => { expired = event.type === 'travel-planner-auth-expired' } }
  globalThis.fetch = async () => new Response('{}', { status: 401 })
  try {
    const response = await csrfFetch('/api/trips')
    assert.equal(response.status, 401)
    assert.equal(expired, true)
  } finally {
    globalThis.document = originalDocument
    globalThis.window = originalWindow
    globalThis.fetch = originalFetch
  }
})
