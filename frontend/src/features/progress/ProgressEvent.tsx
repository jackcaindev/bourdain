import type { SSEEvent } from '../../lib/types'

type ProgressEventProps = {
  event: SSEEvent
  receivedAt: Date
}

export function ProgressEvent({ event, receivedAt }: ProgressEventProps) {
  const timestamp = new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(receivedAt)

  return (
    <li className={`progress-event progress-event--${event.event_type}`}>
      <time dateTime={receivedAt.toISOString()}>{timestamp}</time>
      <span className="progress-node">{event.node_name.replaceAll('_', ' ')}</span>
      <p>{event.message}</p>
    </li>
  )
}
