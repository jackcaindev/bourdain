import { MemoryRouter, Route, Routes } from 'react-router'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { useBriefStore } from '../../store'
import { recommendation } from '../../test/fixtures'
import { SelectionScreen } from './SelectionScreen'

describe('SelectionScreen', () => {
  beforeEach(() => {
    useBriefStore.getState().reset()
    useBriefStore.getState().setRecommendations([recommendation])
  })

  it('starts selected and prevents submitting an empty selection', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/brief/session/select']}>
        <Routes>
          <Route path="/brief/:sessionId/select" element={<SelectionScreen />} />
        </Routes>
      </MemoryRouter>,
    )

    const checkbox = screen.getByRole('checkbox', { name: 'Remove Cafe Local' })
    expect(checkbox).toBeChecked()

    await user.click(checkbox)

    expect(screen.getByRole('button', { name: 'BUILD THE ITINERARY' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Select at least one recommendation to continue.',
    )
  })
})
