import { MemoryRouter, Route, Routes } from 'react-router'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { resumeBrief } from '../../lib/api'
import { useBriefStore } from '../../store'
import { CategorySelectionScreen } from './CategorySelectionScreen'

vi.mock('../../lib/api', () => ({
  resumeBrief: vi.fn(),
}))

const categories = [
  { name: 'Street Food', rationale: 'The city is best understood at the counter.' },
  { name: 'Markets', rationale: 'Daily commerce reveals how the city eats.' },
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
    vi.mocked(resumeBrief).mockResolvedValue({ session_id: 'session' })
    useBriefStore.getState().reset()
    useBriefStore.getState().setAvailableCategories(categories)
  })

  it('renders every category from the store with all categories selected', () => {
    renderScreen()

    expect(screen.getByRole('heading', { name: 'Street Food' })).toBeInTheDocument()
    expect(screen.getByText(categories[0].rationale)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Markets' })).toBeInTheDocument()
    expect(screen.getByText(categories[1].rationale)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Remove Street Food' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Remove Markets' })).toBeChecked()
    expect(screen.getByText('2 OF 2 SELECTED')).toBeInTheDocument()
  })

  it('submits the remaining category names and resumes category selection', async () => {
    const user = userEvent.setup()
    renderScreen()

    await user.click(screen.getByRole('checkbox', { name: 'Remove Street Food' }))
    await user.click(screen.getByRole('button', { name: 'FIND THE PLACES' }))

    await waitFor(() => {
      expect(resumeBrief).toHaveBeenCalledWith('session', ['Markets'], 'categories')
    })
    expect(await screen.findByText('Progress')).toBeInTheDocument()
  })

  it('blocks submitting when no categories are selected', async () => {
    const user = userEvent.setup()
    renderScreen()

    await user.click(screen.getByRole('checkbox', { name: 'Remove Street Food' }))
    await user.click(screen.getByRole('checkbox', { name: 'Remove Markets' }))

    expect(screen.getByRole('button', { name: 'FIND THE PLACES' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Select at least one category to continue.',
    )
    expect(resumeBrief).not.toHaveBeenCalled()
  })
})
