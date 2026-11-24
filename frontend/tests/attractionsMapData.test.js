import test from 'node:test'
import assert from 'node:assert/strict'
import { buildAttractionItems, buildMapViewport, formatAttractionCategory, validLatLng } from '../src/components/attractionsMapData.js'

test('coordinate validation rejects missing and out-of-range values', () => {
  assert.equal(validLatLng(48.8584, 2.2945), true)
  assert.equal(validLatLng(null, 2.2945), false)
  assert.equal(validLatLng(91, 2.2945), false)
  assert.equal(validLatLng(48.8584, 181), false)
})

test('guide fallback attractions remain unlocated instead of receiving invented pins', () => {
  const items = buildAttractionItems({ attractions: [{ name: 'Known place', description: 'Guide text.' }, 'Another place'] }, null)
  assert.equal(items.length, 2)
  assert.equal(items[0].description, 'Guide text.')
  assert.equal(items[0].latitude, null)
  assert.equal(items[1].longitude, null)
})

test('OSM attractions with coordinates are deduplicated and use available descriptions', () => {
  const destination = { id: 'paris', attractions: [
    { name: 'Museum', description: 'Guide description.' },
    { name: 'Missing Place', description: 'Known name, unknown location.' },
  ] }
  const result = buildAttractionItems(destination, {
    source: 'OSM',
    pois: [
      { name: 'Museum', category: 'tourism:museum', latitude: 48.86, longitude: 2.33 },
      { name: 'Museum', category: 'tourism:museum', latitude: 48.86, longitude: 2.33 },
      { name: 'No coordinates', category: 'tourism:attraction', latitude: null, longitude: null },
    ],
    latitude: 48.85,
    longitude: 2.35,
  })
  assert.equal(result.length, 2)
  assert.equal(result[0].description, 'Guide description.')
  assert.equal(result[0].source, 'OSM')
  assert.equal(formatAttractionCategory(result[0].category), 'museum')
  assert.equal(result[1].source, 'STATIC_FALLBACK')
  assert.equal(result[1].latitude, null)
})

test('Overpass description takes precedence over the guide copy', () => {
  const result = buildAttractionItems({ attractions: [{ name: 'Museum', description: 'Guide text.' }] }, {
    source: 'OSM',
    pois: [{ name: 'Museum', description: 'OpenStreetMap text.', category: 'tourism:museum', latitude: 1, longitude: 2 }],
  })
  assert.equal(result[0].description, 'OpenStreetMap text.')
})

test('only exact unique Overpass matches are mapped to guide attractions', () => {
  const result = buildAttractionItems({ id: 'paris', attractions: [
    { name: 'Notre-Dame Cathedral' },
    { name: 'Central Park' },
  ] }, {
    source: 'OSM',
    latitude: 48.85,
    longitude: 2.35,
    pois: [
      { name: 'Notre Dame Cathedral', category: 'historic:cathedral', latitude: 48.853, longitude: 2.349, osm_type: 'way', osm_id: 5 },
      { name: 'Central Park', category: 'leisure:park', latitude: 40.78, longitude: -73.96, osm_type: 'way', osm_id: 6 },
    ],
  })
  assert.equal(result[0].source, 'OSM')
  assert.equal(result[0].latitude, 48.853)
  assert.equal(result[1].source, 'STATIC_FALLBACK')
  assert.equal(result[1].latitude, null)
})

test('same-name Overpass results in different locations remain unmapped', () => {
  const result = buildAttractionItems({ id: 'paris', attractions: [{ name: 'Museum' }] }, {
    source: 'OSM',
    pois: [
      { name: 'Museum', latitude: 48.86, longitude: 2.33, osm_type: 'node', osm_id: 1 },
      { name: 'Museum', latitude: 48.88, longitude: 2.33, osm_type: 'node', osm_id: 2 },
    ],
  })
  assert.equal(result[0].source, 'STATIC_FALLBACK')
  assert.equal(result[0].latitude, null)
})

test('nearby duplicate OSM records collapse to one attraction marker', () => {
  const result = buildAttractionItems({ id: 'paris', attractions: [{ name: 'Eiffel Tower' }] }, {
    source: 'OSM',
    pois: [
      { name: 'Eiffel Tower', latitude: 48.8580, longitude: 2.2940, osm_type: 'node', osm_id: 1 },
      { name: 'Eiffel Tower', latitude: 48.8580, longitude: 2.2950, osm_type: 'node', osm_id: 2 },
    ],
  })
  assert.equal(result.length, 1)
  assert.equal(result[0].source, 'OSM')
})

test('coincident guide attractions do not create duplicate pins', () => {
  const result = buildAttractionItems({ id: 'rome', attractions: [
    { name: 'Place One' },
    { name: 'Place Two' },
  ] }, {
    source: 'OSM',
    pois: [
      { name: 'Place One', latitude: 41.9, longitude: 12.5, osm_type: 'node', osm_id: 1 },
      { name: 'Place Two', latitude: 41.9, longitude: 12.5, osm_type: 'node', osm_id: 2 },
    ],
  })
  assert.equal(result.filter(item => item.source === 'OSM').length, 1)
  assert.equal(result.filter(item => item.source !== 'OSM').length, 1)
})

test('map viewport fits valid attraction markers and updates its identity', () => {
  const attractions = [
    { id: 'guide:a', source: 'OSM', latitude: 48.8, longitude: 2.2 },
    { id: 'guide:b', source: 'OSM', latitude: 48.9, longitude: 2.4 },
    { id: 'guide:c', source: 'STATIC_FALLBACK', latitude: 48.85, longitude: 2.3 },
  ]
  const viewport = buildMapViewport(attractions, { latitude: 48.85, longitude: 2.35 }, 'paris')
  assert.deepEqual(viewport.center, [48.85, 2.35])
  assert.equal(viewport.markers.length, 2)
  assert.deepEqual(viewport.bounds, [[48.8, 2.2], [48.9, 2.4]])
  assert.equal(viewport.destinationKey, 'paris')
  assert.notEqual(viewport.positionsKey, buildMapViewport([attractions[0]], null, 'rome').positionsKey)
})
