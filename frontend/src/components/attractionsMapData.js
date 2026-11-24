const isValidCoordinate = (value, min, max) =>
  Number.isFinite(value) && value >= min && value <= max

export function validLatLng(latitude, longitude) {
  return isValidCoordinate(latitude, -90, 90)
    && isValidCoordinate(longitude, -180, 180)
}

export function normalizePlaceName(value = '') {
  return String(value)
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
    .replace(/\s+/g, ' ')
}

function osmIdentity(poi) {
  if (poi.osm_type && poi.osm_id != null && Number(poi.osm_id) > 0) {
    return `${poi.osm_type}:${poi.osm_id}`
  }
  return `${normalizePlaceName(poi.name)}:${poi.latitude}:${poi.longitude}`
}

function distanceMeters(a, b) {
  const radians = degrees => degrees * Math.PI / 180
  const dLat = radians(b.latitude - a.latitude)
  const dLon = radians(b.longitude - a.longitude)
  const lat1 = radians(a.latitude)
  const lat2 = radians(b.latitude)
  const haversine = Math.sin(dLat / 2) ** 2
    + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2
  return 6371000 * 2 * Math.asin(Math.sqrt(Math.min(1, haversine)))
}

function uniqueCoordinates(pois) {
  const points = []
  const identities = new Set()
  for (const poi of pois) {
    if (!validLatLng(poi.latitude, poi.longitude)) continue
    const identity = osmIdentity(poi)
    if (identities.has(identity)) continue
    identities.add(identity)
    const nameKey = normalizePlaceName(poi.name)
    const isSameNamedLocation = points.some(point =>
      normalizePlaceName(point.name) === nameKey && distanceMeters(point, poi) <= 100,
    )
    if (!isSameNamedLocation) {
      points.push(poi)
    }
  }
  return points
}

function isNearDestination(poi, poiResponse) {
  if (!validLatLng(poiResponse?.latitude, poiResponse?.longitude)) return true
  return distanceMeters(
    { latitude: poiResponse.latitude, longitude: poiResponse.longitude },
    poi,
  ) <= 10_000
}

function stableAttractionId(destination, name, index) {
  const destinationId = destination?.id || destination?.name || 'destination'
  return `guide:${normalizePlaceName(destinationId)}:${normalizePlaceName(name) || index}`
}

export function buildAttractionItems(destination, poiResponse) {
  const guideAttractions = (destination?.attractions || []).map(attraction =>
    typeof attraction === 'string'
      ? { name: attraction, description: '' }
      : attraction,
  ).filter(attraction => attraction?.name)

  const livePois = poiResponse?.source === 'OSM'
    ? (poiResponse.pois || []).filter(poi =>
      poi?.source !== 'STATIC_FALLBACK'
      && poi?.name
      && validLatLng(poi.latitude, poi.longitude)
      && isNearDestination(poi, poiResponse),
    )
    : []

  if (!guideAttractions.length) {
    const seen = new Set()
    return uniqueCoordinates(livePois)
      .filter(poi => {
        const key = normalizePlaceName(poi.name)
        if (!key || seen.has(key)) return false
        seen.add(key)
        return true
      })
      .map(poi => ({ ...poi, id: `osm:${osmIdentity(poi)}`, source: 'OSM' }))
      .slice(0, 20)
  }

  const byName = new Map()
  for (const poi of livePois) {
    const key = normalizePlaceName(poi.name)
    if (!key) continue
    if (!byName.has(key)) byName.set(key, [])
    byName.get(key).push(poi)
  }

  const seenGuides = new Set()
  const claimedLocations = new Set()
  return guideAttractions
    .filter(attraction => {
      const key = normalizePlaceName(attraction.name)
      if (!key || seenGuides.has(key)) return false
      seenGuides.add(key)
      return true
    })
    .map((attraction, index) => {
      const candidates = uniqueCoordinates(byName.get(normalizePlaceName(attraction.name)) || [])
      // If the same name resolves to multiple distinct places, keep it
      // unmapped instead of choosing an arbitrary marker location.
      let unambiguous = candidates.length === 1 ? candidates[0] : null
      if (unambiguous) {
        const locationKey = `${unambiguous.latitude.toFixed(5)}:${unambiguous.longitude.toFixed(5)}`
        if (claimedLocations.has(locationKey)) unambiguous = null
        else claimedLocations.add(locationKey)
      }
      return {
        id: stableAttractionId(destination, attraction.name, index),
        name: attraction.name,
        category: unambiguous?.category || attraction.category || 'Attraction',
        description: unambiguous?.description || attraction.description || '',
        latitude: unambiguous?.latitude ?? null,
        longitude: unambiguous?.longitude ?? null,
        source: unambiguous ? 'OSM' : 'STATIC_FALLBACK',
        osm_type: unambiguous?.osm_type,
        osm_id: unambiguous?.osm_id,
        website: unambiguous?.website,
      }
    })
}

export function buildMapViewport(attractions, destinationCoordinates, destinationKey = '') {
  const seenMarkerIds = new Set()
  const markers = (attractions || [])
    .filter(item => item.source === 'OSM' && validLatLng(item.latitude, item.longitude))
    .map(item => ({
      id: item.id,
      latitude: item.latitude,
      longitude: item.longitude,
    }))
    .filter(marker => {
      if (!marker.id || seenMarkerIds.has(marker.id)) return false
      seenMarkerIds.add(marker.id)
      return true
    })
  const center = validLatLng(destinationCoordinates?.latitude, destinationCoordinates?.longitude)
    ? [destinationCoordinates.latitude, destinationCoordinates.longitude]
    : markers.length ? [markers[0].latitude, markers[0].longitude] : null
  const positionsKey = JSON.stringify(markers.map(item => [item.id, item.latitude, item.longitude]))
  return {
    center,
    markers,
    destinationKey,
    positionsKey,
    bounds: markers.length > 1
      ? markers.map(marker => [marker.latitude, marker.longitude])
      : [],
  }
}

export function formatAttractionCategory(category = '') {
  return category.replace(/^(tourism|historic|leisure|amenity):/, '').replaceAll('_', ' ')
}
