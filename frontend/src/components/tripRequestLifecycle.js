export function createTripRequestLifecycle() {
  let activeRequest = null

  function abort(request) {
    if (request && activeRequest !== request) return
    if (!activeRequest) return
    const previousRequest = activeRequest
    activeRequest = null
    previousRequest.controller.abort()
  }

  function begin() {
    abort()
    const controller = new AbortController()
    const request = {
      controller,
      signal: controller.signal,
      isCurrent: () => activeRequest === request && !controller.signal.aborted,
      abort: () => abort(request),
    }
    activeRequest = request
    return request
  }

  function finish(request) {
    if (activeRequest === request) activeRequest = null
  }

  return { begin, abort, finish }
}

function throwIfAborted(signal) {
  if (!signal?.aborted) return
  if (signal.reason) throw signal.reason
  const error = new Error('The request was cancelled.')
  error.name = 'AbortError'
  throw error
}

function consumeFrame(frame, onStage) {
  const payload = frame
    .split(/\r?\n/)
    .filter(line => line.startsWith('data:'))
    .map(line => line.slice(5).replace(/^ /, ''))
    .join('\n')
  if (!payload || payload === '[DONE]') return null

  let event
  try {
    event = JSON.parse(payload)
  } catch {
    throw new Error('The server sent an invalid trip-planning event.')
  }

  if (event.type === 'stage') {
    onStage?.(event)
    return null
  }
  if (event.type === 'error') {
    throw new Error(event.message || 'Trip planning failed.')
  }
  if (event.type === 'complete') {
    if (!event.plan || typeof event.plan !== 'object') {
      throw new Error('The server sent an incomplete trip plan.')
    }
    return event.plan
  }
  return null
}

export async function readPlanStream(response, { signal, onStage } = {}) {
  if (!response.body) throw new Error('Streaming is not supported by this browser.')
  throwIfAborted(signal)

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let planBody = null
  let streamEnded = false
  const cancelReader = () => {
    reader.cancel(signal?.reason).catch(() => {})
  }

  signal?.addEventListener('abort', cancelReader, { once: true })
  try {
    while (true) {
      throwIfAborted(signal)
      const { done, value } = await reader.read()
      throwIfAborted(signal)
      if (done) {
        streamEnded = true
        buffer += decoder.decode()
        break
      }

      buffer += decoder.decode(value, { stream: true })
      let boundary = buffer.search(/\r?\n\r?\n/)
      while (boundary !== -1) {
        const delimiter = buffer.slice(boundary).match(/^\r?\n\r?\n/)[0]
        const frame = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + delimiter.length)
        planBody = consumeFrame(frame, onStage) || planBody
        boundary = buffer.search(/\r?\n\r?\n/)
      }
    }

    if (buffer.trim()) planBody = consumeFrame(buffer, onStage) || planBody
  } finally {
    signal?.removeEventListener('abort', cancelReader)
    if (!streamEnded || signal?.aborted) {
      try {
        await reader.cancel(signal?.reason)
      } catch {
        // The stream may already have been cancelled by its abort signal.
      }
    }
    reader.releaseLock()
  }

  if (!planBody) throw new Error('Trip planning ended before a plan was produced.')
  return planBody
}
