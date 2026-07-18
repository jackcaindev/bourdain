import { beforeEach, describe, expect, it } from 'vitest'
import { category, recommendation } from './test/fixtures'
import type { SSEEvent } from './lib/types'
import { useBriefStore } from './store'

describe('brief store', () => {
  beforeEach(() => useBriefStore.getState().reset())

  it('sets and resets the brief state', () => {
    const progressEvent: SSEEvent = {
      event_type: 'node_start',
      node_name: 'city_profile_node',
      message: 'Starting.',
      payload: null,
    }
    const store = useBriefStore.getState()
    store.setSessionId('session')
    store.setTripId('trip')
    store.setTripLengthDays(3)
    store.setTimeBlocks(['morning', 'night'])
    store.setCitySlug('porto')
    store.setAvailableCategories([category])
    store.setSelectedCategories(['Food markets'])
    store.setRecommendations([recommendation])
    store.setVenueSelectionReady(true)
    store.addProgressEvent(progressEvent)
    store.setStreamError('Stream disconnected')

    expect(useBriefStore.getState()).toMatchObject({
      sessionId: 'session',
      tripId: 'trip',
      tripLengthDays: 3,
      timeBlocks: ['morning', 'night'],
      citySlug: 'porto',
      availableCategories: [category],
      selectedCategories: ['Food markets'],
      recommendations: [recommendation],
      venueSelectionReady: true,
      progressEvents: [
        { event: progressEvent, receivedAt: expect.any(String) },
      ],
      streamError: 'Stream disconnected',
    })

    useBriefStore.getState().reset()
    expect(useBriefStore.getState()).toMatchObject({
      sessionId: null,
      tripId: null,
      tripLengthDays: null,
      timeBlocks: [],
      citySlug: null,
      availableCategories: [],
      selectedCategories: [],
      recommendations: [],
      venueSelectionReady: false,
      progressEvents: [],
      streamError: null,
    })
  })

  it('appends recommendations and replaces duplicates by id', () => {
    useBriefStore.getState().appendRecommendations([recommendation])
    useBriefStore.getState().appendRecommendations([
      { ...recommendation, name: 'Updated Cafe' },
    ])

    expect(useBriefStore.getState().recommendations).toEqual([
      { ...recommendation, name: 'Updated Cafe' },
    ])
  })

  it('appends progress events in arrival order', () => {
    const firstEvent: SSEEvent = {
      event_type: 'node_start',
      node_name: 'research_category',
      message: 'Starting.',
      payload: null,
    }
    const secondEvent: SSEEvent = {
      event_type: 'node_complete',
      node_name: 'research_category',
      message: 'Done.',
      payload: null,
    }

    useBriefStore.getState().addProgressEvent(firstEvent)
    useBriefStore.getState().addProgressEvent(secondEvent)

    expect(useBriefStore.getState().progressEvents).toEqual([
      { event: firstEvent, receivedAt: expect.any(String) },
      { event: secondEvent, receivedAt: expect.any(String) },
    ])
  })
})
