import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import {
  isHitlPayload,
  isItineraryPayload,
  type SSEEvent,
} from '../../lib/types'
import { useBriefStore } from '../../store'
import { ProgressEvent } from './ProgressEvent'
import { useBriefStream } from './useBriefStream'

type ReceivedEvent = {
  id: number
  event: SSEEvent
  receivedAt: Date
}

export function ProgressFeed() {
  const { sessionId } = useParams()
  const navigate = useNavigate()
  const setSessionId = useBriefStore((state) => state.setSessionId)
  const setRecommendations = useBriefStore((state) => state.setRecommendations)
  const setItineraryDays = useBriefStore((state) => state.setItineraryDays)
  const [events, setEvents] = useState<ReceivedEvent[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (sessionId) setSessionId(sessionId)
  }, [sessionId, setSessionId])

  useBriefStream(sessionId, {
    onEvent: (incomingEvent) => {
      setEvents((current) => [
        ...current,
        {
          id: current.length,
          event: incomingEvent,
          receivedAt: new Date(),
        },
      ])

      if (
        incomingEvent.event_type === 'hitl_pause' &&
        isHitlPayload(incomingEvent.payload) &&
        sessionId
      ) {
        setRecommendations(incomingEvent.payload.recommendations)
        navigate(`/brief/${encodeURIComponent(sessionId)}/select`)
      }

      if (
        incomingEvent.event_type === 'node_complete' &&
        incomingEvent.node_name === 'assemble_itinerary' &&
        isItineraryPayload(incomingEvent.payload) &&
        sessionId
      ) {
        setItineraryDays(incomingEvent.payload.days)
        navigate(`/brief/${encodeURIComponent(sessionId)}/itinerary`)
      }

      if (incomingEvent.event_type === 'error') {
        setError(incomingEvent.message)
      }
    },
    onError: () => setError('The live connection was interrupted.'),
    onClose: () => undefined,
  })

  if (!sessionId) {
    return <RecoveryState message="This brief has no session identifier." />
  }

  return (
    <main className="content-page progress-page">
      <header className="section-heading">
        <p className="eyebrow">RESEARCH DESK / LIVE</p>
        <h1>Building your brief</h1>
        <p>Dispatches arrive as the research desk works.</p>
      </header>

      {error && <div className="error-panel" role="alert">{error}</div>}

      <ol className="progress-feed" aria-live="polite">
        {events.map((receivedEvent) => (
          <ProgressEvent key={receivedEvent.id} {...receivedEvent} />
        ))}
      </ol>
      {!error && <p className="feed-status">CONNECTION OPEN<span aria-hidden="true" /></p>}
    </main>
  )
}

function RecoveryState({ message }: { message: string }) {
  return (
    <main className="recovery-state">
      <p className="eyebrow">BRIEF UNAVAILABLE</p>
      <h1>{message}</h1>
      <Link className="text-link" to="/">Start a new brief</Link>
    </main>
  )
}
