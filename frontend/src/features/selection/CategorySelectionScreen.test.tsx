import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { resumeBrief } from '../../lib/api'
import type { Category } from '../../lib/types'
import { useBriefStore } from '../../store'
import { category } from '../../test/fixtures'
import { CategorySelectionScreen } from './CategorySelectionScreen'

vi.mock('../../lib/api', () => ({
  resumeBrief: vi.fn(),
}))

const categories: Category[] = [
  {
    ...category,
    id: 'activity-exact',
    name: 'Long Walk',
    type: 'activity',
    estimated_duration_minutes: 240,
    eligible_blocks: ['morning'],
  },
  {
    ...category,
    id: 'food-exact',
    name: 'Dinner',
    type: 'food',
    estimated_duration_minutes: 180,
    eligible_blocks: ['night'],
  },
  {
    ...category,
    id: 'activity-extra',
    name: 'Quick Museum',
    type: 'activity',
    estimated_duration_minutes: 60,
    eligible_blocks: ['morning'],
  },
  {
    ...category,
    id: 'food-unplaceable',
    name: 'Lunch',
    type: 'food',
    estimated_duration_minutes: 90,
    eligible_blocks: ['afternoon'],
  },
]

function renderScreen() {
  render(
    <MemoryRouter initialEntries={['/brief/session/categories']}>
      <Routes>
        <Route
          path="/brief/:sessionId/categories"
          element={<CategorySelectionScreen />}
        />
        <Route path="/brief/:sessionId/progress" element={<p>Progress</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('CategorySelectionScreen', () => {
  afterEach(cleanup)

  beforeEach(() => {
    vi.mocked(resumeBrief).mockReset()
    vi.mocked(resumeBrief).mockResolvedValue({
      session_id: 'session',
      trip_id: 'trip',
    })
    useBriefStore.getState().reset()
    useBriefStore.getState().setTripLengthDays(1)
    useBriefStore.getState().setTimeBlocks(['morning', 'night'])
    useBriefStore.getState().setAvailableCategories(categories)
  })

  it('renders an unplaceable category disabled and ignores clicks on it', async () => {
    const user = userEvent.setup()
    renderScreen()

    const checkbox = screen.getByRole('checkbox', { name: 'Select Lunch' })
    expect(checkbox).toBeDisabled()
    expect(screen.getByText("Requires a time block you didn't select"))
      .toBeInTheDocument()

    await user.click(checkbox)

    expect(checkbox).not.toBeChecked()
    expect(screen.getByText('2 OF 3 SELECTED')).toBeInTheDocument()
  })

  it('allows an exact fit and disables remaining choices at the cap', async () => {
    const user = userEvent.setup()
    renderScreen()

    await user.click(screen.getByRole('checkbox', { name: 'Remove Long Walk' }))
    await user.click(screen.getByRole('checkbox', { name: 'Remove Dinner' }))
    await user.click(screen.getByRole('checkbox', { name: 'Select Long Walk' }))

    expect(screen.getByRole('checkbox', { name: 'Select Dinner' })).toBeEnabled()
    await user.click(screen.getByRole('checkbox', { name: 'Select Dinner' }))

    expect(screen.getByRole('checkbox', { name: 'Remove Dinner' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Select Quick Museum' })).toBeDisabled()
    expect(screen.getByLabelText('420 of 420 trip minutes selected'))
      .toBeInTheDocument()
  })

  it('allows deselecting a checked category at the cap', async () => {
    const user = userEvent.setup()
    renderScreen()

    const selectedDinner = screen.getByRole('checkbox', { name: 'Remove Dinner' })
    expect(selectedDinner).toBeEnabled()
    await user.click(selectedDinner)

    expect(screen.getByRole('checkbox', { name: 'Select Dinner' })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Select Quick Museum' })).toBeEnabled()
  })

  it('renders activities and food in separate sections', () => {
    renderScreen()

    const activities = screen.getByRole('heading', { name: 'ACTIVITIES' })
      .closest('section')
    const food = screen.getByRole('heading', { name: 'FOOD' }).closest('section')

    expect(activities).not.toBeNull()
    expect(food).not.toBeNull()
    expect(within(activities!).getByRole('heading', { name: 'Long Walk' }))
      .toBeInTheDocument()
    expect(within(activities!).queryByRole('heading', { name: 'Dinner' }))
      .not.toBeInTheDocument()
    expect(within(food!).getByRole('heading', { name: 'Dinner' }))
      .toBeInTheDocument()
    expect(within(food!).queryByRole('heading', { name: 'Long Walk' }))
      .not.toBeInTheDocument()
  })

  it('shows the existing validation error with zero selected', async () => {
    const user = userEvent.setup()
    renderScreen()

    await user.click(screen.getByRole('checkbox', { name: 'Remove Long Walk' }))
    await user.click(screen.getByRole('checkbox', { name: 'Remove Dinner' }))

    expect(screen.getByRole('button', { name: 'FIND THE PLACES' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Select at least one category to continue.',
    )
    expect(resumeBrief).not.toHaveBeenCalled()
  })

  it('submits selected category names while tracking selection by id', async () => {
    const user = userEvent.setup()
    renderScreen()

    await user.click(screen.getByRole('checkbox', { name: 'Remove Long Walk' }))
    await user.click(screen.getByRole('button', { name: 'FIND THE PLACES' }))

    await waitFor(() => {
      expect(resumeBrief).toHaveBeenCalledWith('session', ['Dinner'], 'categories')
    })
    expect(await screen.findByText('Progress')).toBeInTheDocument()
  })

  it('requires recovered trip sizing data', () => {
    useBriefStore.getState().setTimeBlocks([])
    renderScreen()

    expect(screen.getByRole('heading', {
      name: 'This category desk needs an active brief.',
    })).toBeInTheDocument()
  })
})
