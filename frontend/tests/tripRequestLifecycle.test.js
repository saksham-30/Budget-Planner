import assert from 'node:assert/strict'
import test from 'node:test'
import {
  createTripRequestLifecycle,
  readPlanStream,
} from '../src/components/tripRequestLifecycle.js'

function deferred() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

test('starting a replacement request aborts and invalidates the previous request', () => {
  const lifecycle = createTripRequestLifecycle()
  const first = lifecycle.begin()
  const replacement = lifecycle.begin()

  assert.equal(first.signal.aborted, true)
  assert.equal(first.isCurrent(), false)
  assert.equal(replacement.signal.aborted, false)
  assert.equal(replacement.isCurrent(), true)
})

test('a late response from a switched trip cannot update the active trip state', async () => {
  const lifecycle = createTripRequestLifecycle()
  const state = { tripId: 'trip-a', plan: null }
  const firstResult = deferred()
  const firstRequest = lifecycle.begin()
  const oldTripLoad = firstResult.promise.then(plan => {
    if (firstRequest.isCurrent()) state.plan = plan
  })

  state.tripId = 'trip-b'
  lifecycle.begin()
  firstResult.resolve({ tripId: 'trip-a', answer: 'stale result' })
  await oldTripLoad

  assert.equal(state.tripId, 'trip-b')
  assert.equal(state.plan, null)
})

test('invalidating requests on unmount prevents late updates', async () => {
  const lifecycle = createTripRequestLifecycle()
  const result = deferred()
  const request = lifecycle.begin()
  const updates = []
  const pending = result.promise.then(value => {
    if (request.isCurrent()) updates.push(value)
  })

  lifecycle.abort()
  result.resolve('late state')
  await pending

  assert.equal(request.signal.aborted, true)
  assert.deepEqual(updates, [])
})

test('aborting a trip-plan stream cancels its reader and rejects as an abort', async () => {
  const lifecycle = createTripRequestLifecycle()
  const request = lifecycle.begin()
  let cancelled = false
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('data: {"type":"stage"}\n\n'))
    },
    cancel() {
      cancelled = true
    },
  })
  const streamResult = readPlanStream(new Response(body), { signal: request.signal })

  lifecycle.begin()

  await assert.rejects(streamResult, { name: 'AbortError' })
  assert.equal(cancelled, true)
})

test('an ended stream without a complete event is reported as incomplete', async () => {
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('data: {"type":"stage"}\n\n'))
      controller.close()
    },
  })

  await assert.rejects(
    readPlanStream(new Response(body)),
    /Trip planning ended before a plan was produced/,
  )
})

test('malformed SSE data is surfaced instead of silently discarded', async () => {
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('data: {not-json}\n\n'))
      controller.close()
    },
  })

  await assert.rejects(
    readPlanStream(new Response(body)),
    /invalid trip-planning event/,
  )
})
