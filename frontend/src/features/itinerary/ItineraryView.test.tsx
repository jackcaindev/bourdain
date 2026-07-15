import { MemoryRouter } from 'react-router'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { useBriefStore } from '../../store'
import { itineraryDay } from '../../test/fixtures'
import { ItineraryView } from './ItineraryView'

describe('ItineraryView', () => {
  beforeEach(() => {
    useBriefStore.getState().reset()
    useBriefStore.getState().setItineraryDays([itineraryDay])
  })

  it('renders meal slots before activities and links available sources', () => {
    const { container } = render(
      <MemoryRouter>
        <ItineraryView />
      </MemoryRouter>,
    )

    const text = container.textContent ?? ''
    expect(text.indexOf('Breakfast')).toBeLessThan(text.indexOf('Activity 1'))
    expect(screen.queryByText('Lunch')).not.toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'VIEW SOURCE ↗' })[0]).toHaveAttribute(
      'href',
      'https://example.com/cafe',
    )
  })
})
