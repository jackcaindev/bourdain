import type { ReactNode } from 'react'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { confirmItineraryDay, getItinerary } from '../../lib/api'
import type { PersistedItineraryResponse } from '../../lib/types'
import { useBriefStore } from '../../store'
import { itineraryDay, persistedRecommendation } from '../../test/fixtures'
import { ItineraryView } from './ItineraryView'

vi.mock('../../lib/api', () => ({
  confirmItineraryDay: vi.fn(),
  getItinerary: vi.fn(),
}))

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

const itinerary: PersistedItineraryResponse = {
  trip_id: 'trip',
  status: 'draft',
  days: [itineraryDay],
}

afterEach(cleanup)

describe('ItineraryView', () => {
  beforeEach(() => {
    vi.mocked(getItinerary).mockReset()
    vi.mocked(confirmItineraryDay).mockReset()
    vi.mocked(getItinerary).mockResolvedValue(itinerary)
    useBriefStore.getState().reset()
    useBriefStore.getState().setTripId('trip')
  })

  it('renders loading state before the itinerary fetch resolves', () => {
    vi.mocked(getItinerary).mockReturnValue(new Promise(() => undefined))

    renderView()

    expect(screen.getByRole('heading', { name: 'Opening your itinerary' }))
      .toBeInTheDocument()
    expect(getItinerary).toHaveBeenCalledWith('trip')
  })

  it('renders an error state when the itinerary fetch fails', async () => {
    vi.mocked(getItinerary).mockRejectedValue(new Error('Unavailable'))

    renderView()

    expect(await screen.findByRole('heading', {
      name: 'This itinerary could not be loaded.',
    })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Start a new brief' }))
      .toHaveAttribute('href', '/')
  })

  it('renders persisted activities and meals without source links', async () => {
    const { container } = renderView()

    await screen.findByRole('heading', { name: 'Old Market' })
    const text = container.textContent ?? ''
    expect(text.indexOf('Morning Activity')).toBeLessThan(text.indexOf('Morning Meal'))
    expect(screen.getAllByText('Food').length).toBeGreaterThan(0)
    expect(screen.queryByRole('link', { name: 'VIEW SOURCE ↗' }))
      .not.toBeInTheDocument()
  })

  it('renders a marker for a persisted recommendation with coordinates', async () => {
    vi.mocked(getItinerary).mockResolvedValue({
      ...itinerary,
      days: [{
        ...itineraryDay,
        slots: [{
          time_block: 'morning',
          activity: null,
          meals: [persistedRecommendation],
        }],
      }],
    })

    renderView()

    expect(await screen.findByTestId('map-container')).toBeInTheDocument()
    expect(screen.getByTestId('map-marker')).toHaveTextContent('Cafe Local')
    expect(screen.getByTestId('map-marker')).toHaveTextContent('Food')
  })

  it('renders no map when no recommendation has coordinates', async () => {
    vi.mocked(getItinerary).mockResolvedValue({
      ...itinerary,
      days: [{
        ...itineraryDay,
        slots: [{
          time_block: 'morning',
          activity: null,
          meals: [{
            ...persistedRecommendation,
            lat: null as unknown as number,
            lng: null as unknown as number,
          }],
        }],
      }],
    })

    renderView()

    await screen.findByText('Cafe Local')
    expect(screen.queryByTestId('map-container')).not.toBeInTheDocument()
  })

  it('confirms only the selected day in local state', async () => {
    const secondDay = {
      ...itineraryDay,
      day_number: 2,
      slots: [],
    }
    vi.mocked(getItinerary).mockResolvedValue({
      ...itinerary,
      days: [itineraryDay, secondDay],
    })
    vi.mocked(confirmItineraryDay).mockResolvedValue({
      ...itineraryDay,
      status: 'confirmed',
    })
    const user = userEvent.setup()

    const { container } = renderView()
    const buttons = await screen.findAllByRole('button', {
      name: 'CONFIRM THIS DAY',
    })
    await user.click(buttons[0])

    await waitFor(() => {
      expect(confirmItineraryDay).toHaveBeenCalledWith('trip', 1)
    })
    const daySections = container.querySelectorAll('.itinerary-day')
    expect(within(daySections[0] as HTMLElement).getByText('CONFIRMED'))
      .toBeInTheDocument()
    expect(within(daySections[1] as HTMLElement).getByRole('button', {
      name: 'CONFIRM THIS DAY',
    })).toBeInTheDocument()
  })
})

function renderView() {
  return render(
    <MemoryRouter>
      <ItineraryView />
    </MemoryRouter>,
  )
}
