export function apiFetch(input, options = {}) {
  // API requests are anonymous and carry no session or CSRF cookies.
  return fetch(input, { ...options, credentials: 'omit' })
}