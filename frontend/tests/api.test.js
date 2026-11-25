import assert from 'node:assert/strict'
import test from 'node:test'
import { apiFetch } from '../src/api.js'

test('API requests carry no cookies or CSRF headers', async () => {
  const originalFetch = globalThis.fetch
  let captured
  globalThis.fetch = async (_input, options) => {
    captured = options
    return new Response('{}', { status: 200 })
  }
  try {
    await apiFetch('/api/trips/x/preferences', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
    })
    assert.equal(captured.credentials, 'omit')
    assert.equal(new Headers(captured.headers).has('X-CSRF-Token'), false)
  } finally {
    globalThis.fetch = originalFetch
  }
})
