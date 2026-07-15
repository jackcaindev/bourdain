import { beforeEach, describe, expect, it } from 'vitest'
import { recommendation, itineraryDay } from './test/fixtures'
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
    store.setCitySlug('porto')
    store.setAvailableCategories([
      { name: 'Food markets', rationale: 'Follow the local appetite.' },
    ])
    store.setSelectedCategories(['Food markets'])
    store.setRecommendations([recommendation])
    store.setItineraryDays([itineraryDay])
    store.addProgressEvent(progressEvent)
    store.setStreamError('Stream disconnected')

    expect(useBriefStore.getState()).toMatchObject({
      sessionId: 'session',
      citySlug: 'porto',
      availableCategories: [
        { name: 'Food markets', rationale: 'Follow the local appetite.' },
      ],
      selectedCategories: ['Food markets'],
      recommendations: [recommendation],
      itineraryDays: [itineraryDay],
      progressEvents: [
        { event: progressEvent, receivedAt: expect.any(String) },
      ],
      streamError: 'Stream disconnected',
    })

    useBriefStore.getState().reset()
    expect(useBriefStore.getState()).toMatchObject({
      sessionId: null,
      citySlug: null,
      availableCategories: [],
      selectedCategories: [],
      recommendations: [],
      itineraryDays: [],
      progressEvents: [],
      streamError: null,
    })
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
