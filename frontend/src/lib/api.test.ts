import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchBriefState, resumeBrief, startBrief } from './api'

describe('brief API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('posts the kickoff request', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ session_id: 'session one' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await startBrief('session one', 'Porto', 3)

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/brief',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          session_id: 'session one',
          destination: 'Porto',
          trip_length_days: 3,
        }),
      }),
    )
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
})
