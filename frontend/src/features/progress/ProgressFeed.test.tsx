import { act, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BriefEventStreamCallbacks } from '../../lib/sse'
import { useBriefStore } from '../../store'
import { itineraryDay, recommendation } from '../../test/fixtures'
import { ProgressFeed } from './ProgressFeed'

let streamCallbacks: BriefEventStreamCallbacks | null = null

vi.mock('./useBriefStream', () => ({
  useBriefStream: (_sessionId: string, callbacks: BriefEventStreamCallbacks) => {
    streamCallbacks = callbacks
  },
}))

describe('ProgressFeed', () => {
  beforeEach(() => {
    streamCallbacks = null
    useBriefStore.getState().reset()
  })

  it('stores HITL recommendations and routes to selection', () => {
    render(
      <MemoryRouter initialEntries={['/brief/session/progress']}>
        <Routes>
          <Route path="/brief/:sessionId/progress" element={<ProgressFeed />} />
          <Route path="/brief/:sessionId/select" element={<div>Selection route</div>} />
        </Routes>
      </MemoryRouter>,
    )

    expect(streamCallbacks).not.toBeNull()
    act(() => {
      streamCallbacks?.onEvent({
        event_type: 'hitl_pause',
        node_name: 'select_recommendations',
        message: 'Choose.',
        payload: { recommendations: [recommendation] },
      })
    })

    expect(screen.getByText('Selection route')).toBeInTheDocument()
    expect(useBriefStore.getState().recommendations).toEqual([recommendation])
  })

  it('stores assembled days and routes to the itinerary', () => {
    render(
      <MemoryRouter initialEntries={['/brief/session/progress']}>
        <Routes>
          <Route path="/brief/:sessionId/progress" element={<ProgressFeed />} />
          <Route
            path="/brief/:sessionId/itinerary"
            element={<div>Itinerary route</div>}
          />
        </Routes>
      </MemoryRouter>,
    )

    act(() => {
      streamCallbacks?.onEvent({
        event_type: 'node_complete',
        node_name: 'assemble_itinerary',
        message: 'Ready.',
        payload: { days: [itineraryDay] },
      })
    })

    expect(screen.getByText('Itinerary route')).toBeInTheDocument()
    expect(useBriefStore.getState().itineraryDays).toEqual([itineraryDay])
  })
})
