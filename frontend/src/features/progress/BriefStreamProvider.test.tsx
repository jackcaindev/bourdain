import { act, cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { BriefEventStreamCallbacks } from '../../lib/sse'
import type { Category } from '../../lib/types'
import { useBriefStore } from '../../store'
import { itineraryDay, recommendation } from '../../test/fixtures'
import { BriefStreamProvider } from './BriefStreamProvider'

const mocks = vi.hoisted(() => ({
  construct: vi.fn(),
  close: vi.fn(),
  callbacks: null as BriefEventStreamCallbacks | null,
}))

vi.mock('../../lib/sse', () => ({
  BriefEventStream: class {
    constructor(sessionId: string, callbacks: BriefEventStreamCallbacks) {
      mocks.construct(sessionId)
      mocks.callbacks = callbacks
    }
    close() {
      mocks.close()
      mocks.callbacks?.onClose()
    }
  },
}))

const category: Category = {
  name: 'Markets',
  rationale: 'Where the city shops and eats.',
}

describe('BriefStreamProvider', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    mocks.callbacks = null
    useBriefStore.getState().reset()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it('opens one stream per session and ignores unrelated rerenders', () => {
    useBriefStore.getState().setSessionId('session-one')
    const view = renderProvider()
    act(() => vi.runAllTimers())

    expect(mocks.construct).toHaveBeenCalledOnce()
    expect(mocks.construct).toHaveBeenLastCalledWith('session-one')

    view.rerender(providerTree())
    useBriefStore.getState().setCitySlug('porto')
    act(() => vi.runAllTimers())
    expect(mocks.construct).toHaveBeenCalledOnce()

    act(() => useBriefStore.getState().setSessionId('session-two'))
    act(() => vi.runAllTimers())
    expect(mocks.construct).toHaveBeenCalledTimes(2)
    expect(mocks.construct).toHaveBeenLastCalledWith('session-two')
  })

  it('stores categories and navigates on a category pause', () => {
    openSession()

    act(() => {
      mocks.callbacks?.onEvent({
        event_type: 'hitl_pause',
        node_name: 'category_select',
        message: 'Choose categories.',
        payload: { categories: [category] },
      })
    })

    expect(useBriefStore.getState().availableCategories).toEqual([category])
    expect(useBriefStore.getState().progressEvents).toHaveLength(1)
    expect(screen.getByText('Categories route')).toBeInTheDocument()
  })

  it('stores recommendations and navigates on a venue pause', () => {
    openSession()

    act(() => {
      mocks.callbacks?.onEvent({
        event_type: 'hitl_pause',
        node_name: 'venue_select',
        message: 'Choose venues.',
        payload: { recommendations: [recommendation] },
      })
    })

    expect(useBriefStore.getState().recommendations).toEqual([recommendation])
    expect(screen.getByText('Selection route')).toBeInTheDocument()
  })

  it('stores the itinerary, navigates, and closes on assembly completion', () => {
    openSession()

    act(() => {
      mocks.callbacks?.onEvent({
        event_type: 'node_complete',
        node_name: 'assemble_itinerary',
        message: 'Ready.',
        payload: { days: [itineraryDay] },
      })
    })

    expect(useBriefStore.getState().itineraryDays).toEqual([itineraryDay])
    expect(screen.getByText('Itinerary route')).toBeInTheDocument()
    expect(mocks.close).toHaveBeenCalledOnce()
  })

  it('stores the message and closes on an error event', () => {
    openSession()

    act(() => {
      mocks.callbacks?.onEvent({
        event_type: 'error',
        node_name: 'research_venues',
        message: 'Research failed.',
        payload: null,
      })
    })

    expect(useBriefStore.getState().streamError).toBe('Research failed.')
    expect(useBriefStore.getState().progressEvents).toHaveLength(1)
    expect(mocks.close).toHaveBeenCalledOnce()
  })
})

function openSession() {
  useBriefStore.getState().setSessionId('session')
  renderProvider()
  act(() => vi.runAllTimers())
}

function renderProvider() {
  return render(providerTree())
}

function providerTree() {
  return (
    <MemoryRouter initialEntries={['/']}>
      <BriefStreamProvider />
      <Routes>
        <Route path="/" element={<div>Home route</div>} />
        <Route path="/brief/:sessionId/categories" element={<div>Categories route</div>} />
        <Route path="/brief/:sessionId/select" element={<div>Selection route</div>} />
        <Route path="/brief/:sessionId/itinerary" element={<div>Itinerary route</div>} />
      </Routes>
    </MemoryRouter>
  )
}
