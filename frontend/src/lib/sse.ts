import { API_BASE_URL } from './api'
import type { SSEEvent, SSEEventType } from './types'

export type BriefEventStreamCallbacks = {
  onEvent: (event: SSEEvent) => void
  onError: () => void
  onClose: () => void
}

const EVENT_TYPES: SSEEventType[] = [
  'node_start',
  'node_progress',
  'node_complete',
  'hitl_pause',
  'error',
]

export class BriefEventStream {
  private readonly eventSource: EventSource
  private readonly callbacks: BriefEventStreamCallbacks
  private closed = false

  constructor(sessionId: string, callbacks: BriefEventStreamCallbacks) {
    this.callbacks = callbacks
    this.eventSource = new EventSource(
      `${API_BASE_URL}/api/brief/${encodeURIComponent(sessionId)}/stream`,
    )

    const handleMessage = (rawEvent: Event) => {
      const messageEvent = rawEvent as MessageEvent<string>
      try {
        const event = JSON.parse(messageEvent.data) as SSEEvent
        this.callbacks.onEvent(event)
        if (event.event_type === 'hitl_pause') this.close()
      } catch {
        this.callbacks.onError()
        this.close()
      }
    }

    for (const eventType of EVENT_TYPES) {
      this.eventSource.addEventListener(eventType, handleMessage)
    }
    this.eventSource.onmessage = handleMessage
    this.eventSource.onerror = () => {
      if (this.closed) return
      this.callbacks.onError()
      this.close()
    }
  }

  close(): void {
    if (this.closed) return
    this.closed = true
    this.eventSource.close()
    this.callbacks.onClose()
  }
}
