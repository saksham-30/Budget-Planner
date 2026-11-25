import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { MapContainer, Marker, Popup, TileLayer, useMap } from 'react-leaflet'
import L from 'leaflet'
import { apiFetch } from '../api.js'
import { buildAttractionItems, buildMapViewport, formatAttractionCategory, validLatLng } from './attractionsMapData.js'

const API_BASE = '/api'
const DEFAULT_ZOOM = 12

function makeMarkerIcon(selected) {
  return L.divIcon({
    className: `attraction-map-marker${selected ? ' is-selected' : ''}`,
    html: '<span></span>',
    iconSize: [24, 24],
    iconAnchor: [12, 12],
    popupAnchor: [0, -12],
  })
}

function MapViewport({ viewport, selected, markerRefs }) {
  const map = useMap()
  const previousViewKey = useRef('')
  const viewKey = JSON.stringify([viewport.destinationKey, viewport.positionsKey, viewport.center])

  useEffect(() => {
    const viewportChanged = previousViewKey.current !== viewKey
    previousViewKey.current = viewKey

    if (selected && !viewportChanged) {
      const point = [selected.latitude, selected.longitude]
      map.flyTo(point, Math.max(map.getZoom(), 14), { duration: 0.3 })
      markerRefs.current.get(selected.id)?.openPopup()
      return
    }

    if (viewport.bounds.length > 1) {
      map.fitBounds(viewport.bounds, { padding: [32, 32], maxZoom: 13, animate: false })
    } else if (viewport.markers.length === 1) {
      const marker = viewport.markers[0]
      map.setView([marker.latitude, marker.longitude], 14, { animate: false })
    } else if (viewport.center) {
      map.setView(viewport.center, DEFAULT_ZOOM, { animate: false })
    }
    if (selected) markerRefs.current.get(selected.id)?.openPopup()
  }, [map, markerRefs, selected, viewKey, viewport])

  return null
}

function AttractionMarker({ attraction, selected, markerRefs, onSelect }) {
  const icon = useMemo(() => makeMarkerIcon(selected), [selected])
  const position = useMemo(() => [attraction.latitude, attraction.longitude], [attraction.latitude, attraction.longitude])
  const registerMarker = useCallback(marker => {
    if (marker) markerRefs.current.set(attraction.id, marker)
    else markerRefs.current.delete(attraction.id)
  }, [attraction.id, markerRefs])
  const eventHandlers = useMemo(() => ({
    click: () => onSelect(attraction.id),
  }), [attraction.id, onSelect])

  return (
    <Marker
      position={position}
      icon={icon}
      ref={registerMarker}
      eventHandlers={eventHandlers}
    >
      <Popup>
        <div className="attraction-popup">
          <strong>{attraction.name}</strong>
          <span>{formatAttractionCategory(attraction.category)}</span>
          {attraction.description && <p>{attraction.description}</p>}
          <a href={directionsUrl(attraction)} target="_blank" rel="noopener noreferrer">Get directions ↗</a>
        </div>
      </Popup>
    </Marker>
  )
}

const MemoAttractionMarker = memo(AttractionMarker)

function directionsUrl(attraction) {
  if (!validLatLng(attraction?.latitude, attraction?.longitude)) return ''
  return `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(`${attraction.latitude},${attraction.longitude}`)}`
}

export default function AttractionsMap({ destination }) {
  const destinationKey = JSON.stringify([destination.id || '', destination.name.trim().toLowerCase()])
  const [poiState, setPoiState] = useState({ key: '', loading: true, response: null, error: '' })
  const [selectedId, setSelectedId] = useState('')
  const [retryCount, setRetryCount] = useState(0)
  const markerRefs = useRef(new Map())

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    setPoiState({ key: destinationKey, loading: true, response: null, error: '' })

    apiFetch(`${API_BASE}/poi/${encodeURIComponent(destination.name)}`, { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error(`Nearby places unavailable (${response.status})`)
        return response.json()
      })
      .then(response => {
        if (active) setPoiState({ key: destinationKey, loading: false, response, error: '' })
      })
      .catch(error => {
        if (active && error.name !== 'AbortError') {
          setPoiState({ key: destinationKey, loading: false, response: null, error: error.message || 'Nearby places could not be loaded.' })
        }
      })

    return () => { active = false; controller.abort() }
  }, [destination.name, destinationKey, retryCount])

  const isCurrentResponse = poiState.key === destinationKey
  const response = isCurrentResponse ? poiState.response : null
  const loading = !isCurrentResponse || poiState.loading
  const error = isCurrentResponse ? poiState.error : ''
  const attractions = useMemo(() => buildAttractionItems(destination, response), [destination, response])
  const mappable = useMemo(() => attractions.filter(item =>
    item.source === 'OSM' && validLatLng(item.latitude, item.longitude),
  ), [attractions])
  const selected = mappable.find(item => item.id === selectedId) || null
  const viewport = useMemo(() => buildMapViewport(
    mappable,
    response ? { latitude: response.latitude, longitude: response.longitude } : null,
    destinationKey,
  ), [mappable, response, destinationKey])
  const activeDirections = directionsUrl(selected || (viewport.center
    ? { latitude: viewport.center[0], longitude: viewport.center[1] }
    : null))

  useEffect(() => {
    if (selectedId && !attractions.some(item => item.id === selectedId && item.source === 'OSM')) {
      setSelectedId('')
    }
  }, [attractions, selectedId])

  const selectAttraction = useCallback(id => setSelectedId(id), [])

  return (
    <section className="dest-detail-section attractions-map-section" aria-labelledby="attractions-map-title">
      <div className="attractions-map-heading">
        <div>
          <h2 id="attractions-map-title" className="dest-detail-section-title">Attractions map</h2>
          <p className="attractions-map-subtitle">Explore nearby places around {destination.name}</p>
        </div>
        {activeDirections && (
          <a className="attractions-directions-link" href={activeDirections} target="_blank" rel="noopener noreferrer">
            Directions <span aria-hidden="true">↗</span>
          </a>
        )}
      </div>

      {loading && <div className="attractions-map-message" role="status">Loading nearby attractions…</div>}
      {!loading && error && (
        <div className="attractions-map-message attractions-map-notice" role="status">
          <span>{error} Showing the destination guide’s attraction list where available.</span>
          <button type="button" onClick={() => setRetryCount(count => count + 1)}>Retry</button>
        </div>
      )}
      {!loading && !error && response?.notes && (
        <p className="attractions-map-notice" role="status">{response.notes}</p>
      )}

      <div className="attractions-map-layout">
        <div className="attractions-map-canvas">
          {viewport.center ? (
            <MapContainer
              key={destinationKey}
              center={viewport.center}
              zoom={DEFAULT_ZOOM}
              scrollWheelZoom
              className="attractions-leaflet-map"
            >
              <TileLayer
                attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              />
              <MapViewport viewport={viewport} selected={selected} markerRefs={markerRefs} />
              {mappable.map(attraction => (
                <MemoAttractionMarker
                  key={attraction.id}
                  attraction={attraction}
                  selected={selectedId === attraction.id}
                  markerRefs={markerRefs}
                  onSelect={selectAttraction}
                />
              ))}
            </MapContainer>
          ) : (
            <div className="attractions-map-unavailable" role="status">
              <strong>Map coordinates unavailable</strong>
              <span>Attraction names are shown when available, but no reliable locations could be confirmed.</span>
            </div>
          )}
        </div>

        <div className="attractions-map-list-wrap">
          <h3>Nearby attractions <span>{attractions.length}</span></h3>
          {attractions.length ? (
            <ul className="attractions-map-list">
              {attractions.map(item => {
                const canFocus = item.source === 'OSM' && validLatLng(item.latitude, item.longitude)
                return (
                  <li key={item.id}>
                    <button
                      type="button"
                      className={`attractions-map-list-item${selectedId === item.id ? ' is-active' : ''}`}
                      aria-pressed={selectedId === item.id}
                      disabled={!canFocus}
                      onClick={() => selectAttraction(item.id)}
                    >
                      <span className="attractions-map-list-name">{item.name}</span>
                      <span className="attractions-map-list-category">{formatAttractionCategory(item.category)}</span>
                      {!canFocus && <span className="attractions-map-list-unmapped">Location unverified</span>}
                    </button>
                  </li>
                )
              })}
            </ul>
          ) : (
            <p className="attractions-map-empty">No attractions were found for this destination.</p>
          )}
          {response?.attribution && <p className="attractions-map-attribution">{response.attribution}</p>}
          {attractions.some(item => item.source !== 'OSM') && (
            <p className="attractions-map-attribution">Guide attractions without an exact, unique OpenStreetMap location remain unmapped.</p>
          )}
        </div>
      </div>
    </section>
  )
}
