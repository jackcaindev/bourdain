import { MemoryRouter, Route, Routes } from 'react-router'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { resumeBrief } from '../../lib/api'
import { useBriefStore } from '../../store'
import { marketRecommendation, recommendation } from '../../test/fixtures'
import { SelectionScreen } from './SelectionScreen'

vi.mock('../../lib/api', () => ({
  resumeBrief: vi.fn(),
}))

function renderScreen() {
  render(
    <MemoryRouter initialEntries={['/brief/session/select']}>
      <Routes>
        <Route path="/brief/:sessionId/select" element={<SelectionScreen />} />
        <Route path="/brief/:sessionId/progress" element={<p>Progress</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('SelectionScreen', () => {
  afterEach(cleanup)

  beforeEach(() => {
    vi.mocked(resumeBrief).mockReset()
    vi.mocked(resumeBrief).mockResolvedValue({
      session_id: 'session',
      trip_id: 'trip',
    })
    useBriefStore.getState().reset()
    useBriefStore.getState().setSelectedCategories(['Markets', 'Food'])
    useBriefStore
      .getState()
      .setRecommendations([recommendation, marketRecommendation])
    useBriefStore.getState().setVenueSelectionReady(true)
  })

  it('shows a waiting state while the first recommendations are pending', () => {
    useBriefStore.getState().setRecommendations([])
    useBriefStore.getState().setVenueSelectionReady(false)

    renderScreen()

    expect(screen.getByRole('heading', { name: 'Still gathering results…' }))
      .toBeInTheDocument()
    expect(screen.queryByText('SELECTIONS UNAVAILABLE')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Start a new brief' }))
      .not.toBeInTheDocument()
  })

  it('disables submission until the final venue pause is ready', () => {
    useBriefStore.getState().setVenueSelectionReady(false)
    renderScreen()

    expect(screen.getByRole('button', { name: 'STILL GATHERING…' }))
      .toBeDisabled()

    act(() => useBriefStore.getState().setVenueSelectionReady(true))

    expect(screen.getByRole('button', { name: 'BUILD THE ITINERARY' }))
      .toBeEnabled()
  })

  it('selects recommendations that stream in after the initial render', () => {
    renderScreen()
    const streamedRecommendation = {
      ...recommendation,
      id: 'rec-3',
      name: 'Late Cafe',
    }

    act(() => {
      useBriefStore.getState().appendRecommendations([streamedRecommendation])
    })

    expect(screen.getByRole('checkbox', { name: 'Remove Late Cafe' }))
      .toBeChecked()
    expect(screen.getByText('3 OF 3 SELECTED')).toBeInTheDocument()
  })

  it('preserves explicit removals when more recommendations stream in', async () => {
    const user = userEvent.setup()
    renderScreen()

    await user.click(screen.getByRole('checkbox', { name: 'Remove Cafe Local' }))
    act(() => {
      useBriefStore.getState().appendRecommendations([{
        ...recommendation,
        id: 'rec-3',
        name: 'Late Cafe',
      }])
    })

    expect(screen.getByRole('checkbox', { name: 'Select Cafe Local' }))
      .not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Remove Late Cafe' }))
      .toBeChecked()
    expect(screen.getByText('2 OF 3 SELECTED')).toBeInTheDocument()
  })

  it('groups recommendations under category headings in selection order', () => {
    renderScreen()

    const marketsHeading = screen.getByRole('heading', { name: 'Markets' })
    const foodHeading = screen.getByRole('heading', { name: 'Food' })
    const marketsSection = marketsHeading.closest('section')
    const foodSection = foodHeading.closest('section')

    expect(marketsSection).not.toBeNull()
    expect(foodSection).not.toBeNull()
    expect(within(marketsSection!).getByText('Night Market')).toBeInTheDocument()
    expect(within(marketsSection!).queryByText('Cafe Local')).not.toBeInTheDocument()
    expect(within(foodSection!).getByText('Cafe Local')).toBeInTheDocument()
    expect(within(foodSection!).queryByText('Night Market')).not.toBeInTheDocument()
    expect(
      marketsHeading.compareDocumentPosition(foodHeading) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  it('allows submitting an empty selection', async () => {
    const user = userEvent.setup()
    renderScreen()

    await user.click(screen.getByRole('checkbox', { name: 'Remove Cafe Local' }))
    await user.click(screen.getByRole('checkbox', { name: 'Remove Night Market' }))
    await user.click(screen.getByRole('button', { name: 'BUILD THE ITINERARY' }))

    await waitFor(() => {
      expect(resumeBrief).toHaveBeenCalledWith('session', [], 'venues')
    })
    expect(await screen.findByText('Progress')).toBeInTheDocument()
  })

  it('submits only the remaining recommendation ids', async () => {
    const user = userEvent.setup()
    renderScreen()

    await user.click(screen.getByRole('checkbox', { name: 'Remove Cafe Local' }))
    await user.click(screen.getByRole('button', { name: 'BUILD THE ITINERARY' }))

    await waitFor(() => {
      expect(resumeBrief).toHaveBeenCalledWith(
        'session',
        [marketRecommendation.id],
        'venues',
      )
    })
    expect(await screen.findByText('Progress')).toBeInTheDocument()
  })
})
