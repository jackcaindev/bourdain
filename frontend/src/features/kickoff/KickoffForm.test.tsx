import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { startBrief } from '../../lib/api'
import { useBriefStore } from '../../store'
import { KickoffForm } from './KickoffForm'

vi.mock('../../lib/api', () => ({
  startBrief: vi.fn(),
}))

const candidate = {
  google_place_id: 'place-portland-oregon',
  name: 'Portland',
  formatted_address: 'Portland, OR, USA',
  lat: 45.5152,
  lng: -122.6784,
  google_types: ['locality'],
}

function renderForm() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<KickoffForm />} />
        <Route path="/brief/:sessionId/progress" element={<p>Progress</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('KickoffForm', () => {
  beforeEach(() => {
    vi.mocked(startBrief).mockReset()
    useBriefStore.getState().reset()
  })

  afterEach(cleanup)

  it('requires a destination before starting', async () => {
    const user = userEvent.setup()
    renderForm()

    await user.click(screen.getByRole('button', { name: 'MAKE MY BRIEF' }))

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Tell us where you want to go.',
    )
  })

  it('requires at least one time block before starting', async () => {
    const user = userEvent.setup()
    renderForm()

    await user.type(screen.getByLabelText('Where should we go?'), 'Portland')
    await user.click(screen.getByRole('checkbox', { name: 'Select Nightlife' }))
    await user.click(screen.getByRole('button', { name: 'MAKE MY BRIEF' }))

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Select at least one time block.',
    )
    expect(startBrief).not.toHaveBeenCalled()
  })

  it('requires an activity driver or food selection before starting', async () => {
    const user = userEvent.setup()
    renderForm()

    await user.type(screen.getByLabelText('Where should we go?'), 'Portland')
    await user.click(screen.getByRole('checkbox', { name: 'Select morning' }))
    await user.click(screen.getByRole('button', { name: 'MAKE MY BRIEF' }))

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Select at least one activity driver or food selection.',
    )
    expect(startBrief).not.toHaveBeenCalled()
  })

  it('renders ambiguous city candidates without navigating', async () => {
    const user = userEvent.setup()
    vi.mocked(startBrief).mockResolvedValue({
      status: 'ambiguous',
      data: { status: 'ambiguous', candidates: [candidate] },
    })
    renderForm()

    await fillValidForm(user)
    await user.click(screen.getByRole('button', { name: 'MAKE MY BRIEF' }))

    expect(await screen.findByRole('heading', { name: 'Choose your destination' }))
      .toBeInTheDocument()
    expect(screen.getByText(candidate.name)).toBeInTheDocument()
    expect(screen.getByText(candidate.formatted_address)).toBeInTheDocument()
    expect(screen.queryByText('Progress')).not.toBeInTheDocument()
  })

  it('resubmits a candidate with the original checkbox selections', async () => {
    const user = userEvent.setup()
    vi.mocked(startBrief)
      .mockResolvedValueOnce({
        status: 'ambiguous',
        data: { status: 'ambiguous', candidates: [candidate] },
      })
      .mockResolvedValueOnce({
        status: 'resolved',
        data: { session_id: 'session', trip_id: 'trip' },
      })
    renderForm()

    await fillValidForm(user)
    await user.click(screen.getByRole('button', { name: 'MAKE MY BRIEF' }))
    await user.click(await screen.findByRole('button', {
      name: `${candidate.name}, ${candidate.formatted_address}`,
    }))

    await waitFor(() => expect(startBrief).toHaveBeenCalledTimes(2))
    const firstCall = vi.mocked(startBrief).mock.calls[0]
    expect(startBrief).toHaveBeenNthCalledWith(
      2,
      firstCall[0],
      candidate.formatted_address,
      3,
      ['Nightlife'],
      ['Breakfast'],
      ['morning'],
    )
    expect(await screen.findByText('Progress')).toBeInTheDocument()
    expect(useBriefStore.getState()).toMatchObject({
      sessionId: firstCall[0],
      tripId: 'trip',
      tripLengthDays: 3,
      timeBlocks: ['morning'],
    })
  })
})

async function fillValidForm(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('Where should we go?'), 'Portland')
  await user.click(screen.getByRole('checkbox', { name: 'Select Nightlife' }))
  await user.click(screen.getByRole('checkbox', { name: 'Select Breakfast' }))
  await user.click(screen.getByRole('checkbox', { name: 'Select morning' }))
}
