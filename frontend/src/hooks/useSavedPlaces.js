import { useCallback, useEffect, useState } from 'react'

const STORAGE_KEY = 'travel_planner_saved_places'

function readFromStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function writeToStorage(ids) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(ids))
  } catch {
    // Quota exceeded or private mode — degrade silently
  }
}

export function useSavedPlaces() {
  const [savedIds, setSavedIds] = useState(() => readFromStorage())

  // Sync across tabs
  useEffect(() => {
    const handler = (event) => {
      if (event.key === STORAGE_KEY) {
        setSavedIds(readFromStorage())
      }
    }
    window.addEventListener('storage', handler)
    return () => window.removeEventListener('storage', handler)
  }, [])

  const isSaved = useCallback(
    (destinationId) => savedIds.includes(destinationId),
    [savedIds],
  )

  const toggleSaved = useCallback((destinationId) => {
    setSavedIds((current) => {
      const next = current.includes(destinationId)
        ? current.filter((id) => id !== destinationId)
        : [...current, destinationId]
      writeToStorage(next)
      return next
    })
  }, [])

  const removeSaved = useCallback((destinationId) => {
    setSavedIds((current) => {
      const next = current.filter((id) => id !== destinationId)
      writeToStorage(next)
      return next
    })
  }, [])

  return { savedIds, isSaved, toggleSaved, removeSaved }
}
