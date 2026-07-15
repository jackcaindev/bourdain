import { useEffect, useRef } from 'react'
import {
  BriefEventStream,
  type BriefEventStreamCallbacks,
} from '../../lib/sse'

export function useBriefStream(
  sessionId: string | undefined,
  callbacks: BriefEventStreamCallbacks,
): void {
  const callbacksRef = useRef(callbacks)
  const connectionRef = useRef<BriefEventStream | null>(null)
  const setupTimerRef = useRef<number | null>(null)

  useEffect(() => {
    callbacksRef.current = callbacks
  }, [callbacks])

  useEffect(() => {
    if (!sessionId) return

    let cancelled = false
    setupTimerRef.current = window.setTimeout(() => {
      if (cancelled || connectionRef.current) return
      connectionRef.current = new BriefEventStream(sessionId, {
        onEvent: (event) => callbacksRef.current.onEvent(event),
        onError: () => callbacksRef.current.onError(),
        onClose: () => callbacksRef.current.onClose(),
      })
    }, 0)

    return () => {
      cancelled = true
      if (setupTimerRef.current !== null) {
        window.clearTimeout(setupTimerRef.current)
        setupTimerRef.current = null
      }
      connectionRef.current?.close()
      connectionRef.current = null
    }
  }, [sessionId])
}
