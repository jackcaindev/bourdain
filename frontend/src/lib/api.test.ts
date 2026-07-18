import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  confirmItineraryDay,
  fetchBriefState,
  furtherResearch,
  getItinerary,
  resumeBrief,
  startBrief,
  swapItinerarySlot,
} from './api'

describe('brief API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('posts the kickoff request', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ session_id: 'session one', trip_id: 'trip' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(startBrief(
      'session one',
      'Porto',
      3,
      ['Nightlife'],
      ['Dinner'],
      ['night'],
    )).resolves.toEqual({
      status: 'resolved',
      data: { session_id: 'session one', trip_id: 'trip' },
    })

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/brief',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          session_id: 'session one',
          destination: 'Porto',
          trip_length_days: 3,
          activity_drivers: ['Nightlife'],
          food_selections: ['Dinner'],
          time_blocks: ['night'],
        }),
      }),
    )
  })

  it('returns an ambiguous city response without throwing', async () => {
    const payload = {
      status: 'ambiguous',
      candidates: [{
        google_place_id: 'place-1',
        name: 'Portland',
        formatted_address: 'Portland, OR, USA',
        lat: 45.5152,
        lng: -122.6784,
        google_types: ['locality'],
      }],
    } as const
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), {
        status: 300,
        headers: { 'Content-Type': 'application/json' },
      }),
    ))

    await expect(startBrief(
      'session',
      'Portland',
      3,
      ['Culture & History'],
      [],
      ['morning'],
    )).resolves.toEqual({ status: 'ambiguous', data: payload })
  })

  it('encodes resume session ids and surfaces API details', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Session expired' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(resumeBrief('session one', ['rec-1'], 'venues')).rejects.toThrow(
      'Session expired',
    )
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/brief/session%20one/resume',
      expect.objectContaining({
        body: JSON.stringify({
          user_selections: ['rec-1'],
          resume_type: 'venues',
        }),
      }),
    )
  })

  it('gets the current brief state with an encoded session id', async () => {
    const payload = {
      session_id: 'session one',
      trip_id: null,
      phase: 'in_progress',
      categories: null,
      selected_categories: null,
      recommendations: null,
      itinerary_days: null,
    } as const
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchBriefState('session one')).resolves.toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/brief/session%20one/state',
    )
  })

  it('surfaces errors while fetching brief state', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Session not found' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchBriefState('missing')).rejects.toThrow('Session not found')
  })

  it('calls persisted itinerary endpoints with encoded ids', async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(
      new Response(JSON.stringify({}), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ))
    vi.stubGlobal('fetch', fetchMock)

    await getItinerary('trip one')
    await confirmItineraryDay('trip one', 2)
    await swapItinerarySlot('trip one', 'slot one', 'rec one')
    await furtherResearch('trip one', 'category one')

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://localhost:8000/api/trips/trip%20one/itinerary',
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://localhost:8000/api/trips/trip%20one/itinerary/days/2/confirm',
      expect.objectContaining({ method: 'PATCH' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      'http://localhost:8000/api/trips/trip%20one/itinerary/slots/slot%20one',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ recommendation_id: 'rec one' }),
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      'http://localhost:8000/api/trips/trip%20one/categories/category%20one/further-research',
      expect.objectContaining({ method: 'POST' }),
    )
  })
})
