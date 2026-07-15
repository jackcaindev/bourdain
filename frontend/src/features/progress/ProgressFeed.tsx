import { Link, useParams } from 'react-router'
import { useBriefStore } from '../../store'
import { ProgressEvent } from './ProgressEvent'

export function ProgressFeed() {
  const { sessionId } = useParams()
  const progressEvents = useBriefStore((state) => state.progressEvents)
  const streamError = useBriefStore((state) => state.streamError)

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

      {streamError && <div className="error-panel" role="alert">{streamError}</div>}

      <ol className="progress-feed" aria-live="polite">
        {progressEvents.map((entry, index) => (
          <ProgressEvent
            key={index}
            event={entry.event}
            receivedAt={new Date(entry.receivedAt)}
          />
        ))}
      </ol>
      {!streamError && <p className="feed-status">CONNECTION OPEN<span aria-hidden="true" /></p>}
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
