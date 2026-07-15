import { beforeEach, describe, expect, it } from 'vitest'
import { recommendation, itineraryDay } from './test/fixtures'
import { useBriefStore } from './store'

describe('brief store', () => {
  beforeEach(() => useBriefStore.getState().reset())

  it('sets and resets the brief state', () => {
    const store = useBriefStore.getState()
    store.setSessionId('session')
    store.setRecommendations([recommendation])
    store.setItineraryDays([itineraryDay])

    expect(useBriefStore.getState()).toMatchObject({
      sessionId: 'session',
      recommendations: [recommendation],
      itineraryDays: [itineraryDay],
    })

    useBriefStore.getState().reset()
    expect(useBriefStore.getState()).toMatchObject({
      sessionId: null,
      recommendations: [],
      itineraryDays: [],
    })
  })
})
