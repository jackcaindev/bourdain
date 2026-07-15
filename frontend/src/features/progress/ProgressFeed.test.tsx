import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { useBriefStore } from '../../store'
import { ProgressFeed } from './ProgressFeed'

describe('ProgressFeed', () => {
  beforeEach(() => {
    useBriefStore.getState().reset()
  })

  afterEach(cleanup)

  it('renders progress events accumulated in the store', () => {
    useBriefStore.setState({
      progressEvents: [
        {
          event: {
            event_type: 'node_progress',
            node_name: 'research_venues',
            message: 'Found a neighborhood institution.',
            payload: null,
          },
          receivedAt: '2026-07-15T12:00:00.000Z',
        },
      ],
    })

    renderFeed()

    expect(screen.getByText('Found a neighborhood institution.')).toBeInTheDocument()
    expect(screen.getByText('research venues')).toBeInTheDocument()
    expect(screen.getByText('CONNECTION OPEN')).toBeInTheDocument()
  })

  it('renders a stream error from the store', () => {
    useBriefStore.setState({ streamError: 'The live connection was interrupted.' })

    renderFeed()

    expect(screen.getByRole('alert')).toHaveTextContent(
      'The live connection was interrupted.',
    )
    expect(screen.queryByText('CONNECTION OPEN')).not.toBeInTheDocument()
  })
})

function renderFeed() {
  return render(
    <MemoryRouter initialEntries={['/brief/session/progress']}>
      <Routes>
        <Route path="/brief/:sessionId/progress" element={<ProgressFeed />} />
      </Routes>
    </MemoryRouter>,
  )
}
