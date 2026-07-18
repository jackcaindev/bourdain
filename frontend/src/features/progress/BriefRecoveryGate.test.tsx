import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchBriefState } from '../../lib/api'
import { useBriefStore } from '../../store'
import { recommendation } from '../../test/fixtures'
import { BriefRecoveryGate } from './BriefRecoveryGate'

vi.mock('../../lib/api', () => ({
  fetchBriefState: vi.fn(),
}))

describe('BriefRecoveryGate', () => {
  beforeEach(() => {
    vi.mocked(fetchBriefState).mockReset()
    useBriefStore.getState().reset()
  })

  afterEach(cleanup)

  it('renders normally without fetching when the store matches the URL', () => {
    useBriefStore.getState().setSessionId('session')

    renderGate()

    expect(screen.getByText('Brief screen')).toBeInTheDocument()
    expect(fetchBriefState).not.toHaveBeenCalled()
  })

  it('fetches and populates an empty store from the URL session', async () => {
    vi.mocked(fetchBriefState).mockResolvedValue({
      session_id: 'session',
      trip_id: 'trip',
      phase: 'venue_select',
      categories: null,
      selected_categories: ['Food'],
      recommendations: [recommendation],
      itinerary_days: null,
    })

    renderGate()

    expect(screen.getByRole('heading', { name: 'Reopening your brief' }))
      .toBeInTheDocument()
    expect(await screen.findByText('Brief screen')).toBeInTheDocument()
    expect(fetchBriefState).toHaveBeenCalledOnce()
    expect(fetchBriefState).toHaveBeenCalledWith('session')
    expect(useBriefStore.getState()).toMatchObject({
      sessionId: 'session',
      tripId: 'trip',
      availableCategories: [],
      selectedCategories: ['Food'],
      recommendations: [recommendation],
      venueSelectionReady: true,
    })
  })

  it('renders a start-over recovery link when fetching fails', async () => {
    vi.mocked(fetchBriefState).mockRejectedValue(new Error('Not found'))

    renderGate()

    await waitFor(() => {
      expect(screen.getByRole('heading', {
        name: 'This brief could not be restored.',
      })).toBeInTheDocument()
    })
    expect(screen.getByRole('link', { name: 'Start a new brief' }))
      .toHaveAttribute('href', '/')
    expect(screen.queryByText('Brief screen')).not.toBeInTheDocument()
  })
})

function renderGate() {
  return render(
    <MemoryRouter initialEntries={['/brief/session/select']}>
      <Routes>
        <Route element={<BriefRecoveryGate />}>
          <Route path="/brief/:sessionId/select" element={<p>Brief screen</p>} />
        </Route>
        <Route path="/" element={<p>Kickoff</p>} />
      </Routes>
    </MemoryRouter>,
  )
}
