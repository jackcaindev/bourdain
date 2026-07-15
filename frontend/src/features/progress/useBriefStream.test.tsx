import { StrictMode } from 'react'
import { render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useBriefStream } from './useBriefStream'

const mocks = vi.hoisted(() => ({
  construct: vi.fn(),
  close: vi.fn(),
}))

vi.mock('../../lib/sse', () => ({
  BriefEventStream: class {
    constructor() {
      mocks.construct()
    }
    close() {
      mocks.close()
    }
  },
}))

function Harness() {
  useBriefStream('strict-session', {
    onEvent: vi.fn(),
    onError: vi.fn(),
    onClose: vi.fn(),
  })
  return null
}

describe('useBriefStream', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('opens only one stream under StrictMode and closes it on unmount', () => {
    vi.useFakeTimers()
    const view = render(
      <StrictMode>
        <Harness />
      </StrictMode>,
    )

    vi.runAllTimers()
    expect(mocks.construct).toHaveBeenCalledOnce()

    view.unmount()
    expect(mocks.close).toHaveBeenCalledOnce()
  })
})
