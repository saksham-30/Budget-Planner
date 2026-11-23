export function csrfFetch(input, options = {}) {
  const method = String(options.method || 'GET').toUpperCase()
  const headers = new Headers(options.headers || {})
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const token = document.cookie
      .split('; ')
      .find(value => value.startsWith('travel_planner_csrf='))
      ?.split('=').slice(1).join('=')
    if (token) headers.set('X-CSRF-Token', decodeURIComponent(token))
  }
  return fetch(input, { ...options, headers, credentials: 'same-origin' }).then(response => {
    if (response.status === 401) {
      window.dispatchEvent(new Event('travel-planner-auth-expired'))
    }
    return response
  })
}
