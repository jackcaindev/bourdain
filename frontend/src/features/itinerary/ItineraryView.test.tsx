import { MemoryRouter } from 'react-router'
import type { ReactNode } from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useBriefStore } from '../../store'
import { itineraryDay, recommendation } from '../../test/fixtures'
import { ItineraryView } from './ItineraryView'

vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }: { children: ReactNode }) => (
    <div data-testid="map-container">{children}</div>
  ),
  Marker: ({ children }: { children: ReactNode }) => (
    <div data-testid="map-marker">{children}</div>
  ),
  Popup: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  TileLayer: () => null,
  useMap: () => ({ fitBounds: vi.fn() }),
}))

afterEach(cleanup)

describe('ItineraryView', () => {
  beforeEach(() => {
    useBriefStore.getState().reset()
    useBriefStore.getState().setItineraryDays([itineraryDay])
  })

  it('renders block activities and meals and links available sources', () => {
    const { container } = render(
      <MemoryRouter>
        <ItineraryView />
      </MemoryRouter>,
    )

    const text = container.textContent ?? ''
    expect(text.indexOf('Morning Activity')).toBeLessThan(text.indexOf('Morning Meal'))
    expect(screen.getAllByRole('link', { name: 'VIEW SOURCE ↗' })[0]).toHaveAttribute(
      'href',
      'https://example.com/cafe',
    )
  })

  it('renders a marker for a recommendation with coordinates', () => {
    useBriefStore.getState().setItineraryDays([{
      ...itineraryDay,
      slots: [{ time_block: 'morning', activity: null, meals: [recommendation] }],
    }])

    render(
      <MemoryRouter>
        <ItineraryView />
      </MemoryRouter>,
    )

    expect(screen.getByTestId('map-container')).toBeInTheDocument()
    expect(screen.getByTestId('map-marker')).toHaveTextContent('Cafe Local')
    expect(screen.getByTestId('map-marker')).toHaveTextContent('Food')
  })

  it('renders no map when no recommendation has coordinates', () => {
    useBriefStore.getState().setItineraryDays([{
      ...itineraryDay,
      slots: [{
        time_block: 'morning',
        activity: null,
        meals: [{ ...recommendation, lat: null, lng: null }],
      }],
    }])

    render(
      <MemoryRouter>
        <ItineraryView />
      </MemoryRouter>,
    )

    expect(screen.queryByTestId('map-container')).not.toBeInTheDocument()
  })
})
