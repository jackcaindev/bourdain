import { MemoryRouter } from 'react-router'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { KickoffForm } from './KickoffForm'

describe('KickoffForm', () => {
  it('requires a destination before starting', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <KickoffForm />
      </MemoryRouter>,
    )

    await user.click(screen.getByRole('button', { name: 'MAKE MY BRIEF' }))

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Tell us where you want to go.',
    )
  })
})
