import { afterEach, describe, expect, it, vi } from 'vitest'
import type { SSEEventType } from './types'
import { BriefEventStream } from './sse'

class MockEventSource {
  static instances: MockEventSource[] = []
  readonly listeners = new Map<string, EventListener>()
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
  readonly url: string

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: EventListener) {
    this.listeners.set(type, listener)
  }

  emit(type: SSEEventType, data: string) {
    this.listeners.get(type)?.(new MessageEvent(type, { data }))
  }
}

describe('BriefEventStream', () => {
  afterEach(() => {
    MockEventSource.instances = []
    vi.unstubAllGlobals()
  })

  it('delivers a HITL event and stays open until explicitly closed', () => {
    vi.stubGlobal('EventSource', MockEventSource)
    const calls: string[] = []
    const stream = new BriefEventStream('session one', {
      onEvent: () => calls.push('event'),
      onError: () => calls.push('error'),
      onClose: () => calls.push('close'),
    })
    const source = MockEventSource.instances[0]

    source.emit(
      'hitl_pause',
      JSON.stringify({
        event_type: 'hitl_pause',
        node_name: 'venue_select',
        message: 'Choose.',
        payload: { recommendations: [] },
      }),
    )

    expect(calls).toEqual(['event'])
    expect(source.close).not.toHaveBeenCalled()

    stream.close()

    expect(source.url).toContain('session%20one/stream')
    expect(calls).toEqual(['event', 'close'])
    expect(source.close).toHaveBeenCalledOnce()
  })

  it('treats malformed JSON as a terminal error', () => {
    vi.stubGlobal('EventSource', MockEventSource)
    const onError = vi.fn()
    const onClose = vi.fn()
    new BriefEventStream('session', {
      onEvent: vi.fn(),
      onError,
      onClose,
    })

    MockEventSource.instances[0].emit('node_start', 'not-json')

    expect(onError).toHaveBeenCalledOnce()
    expect(onClose).toHaveBeenCalledOnce()
  })
})
