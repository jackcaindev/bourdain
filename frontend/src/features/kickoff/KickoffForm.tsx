import { type FormEvent, useState } from 'react'
import { useNavigate } from 'react-router'
import { startBrief } from '../../lib/api'
import { useBriefStore } from '../../store'

export function KickoffForm() {
  const navigate = useNavigate()
  const setSessionId = useBriefStore((state) => state.setSessionId)
  const reset = useBriefStore((state) => state.reset)
  const [destination, setDestination] = useState('')
  const [tripLengthDays, setTripLengthDays] = useState(3)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmedDestination = destination.trim()
    if (!trimmedDestination) {
      setError('Tell us where you want to go.')
      return
    }
    if (tripLengthDays < 1 || tripLengthDays > 14) {
      setError('Trip length must be between 1 and 14 days.')
      return
    }

    setSubmitting(true)
    setError(null)
    const sessionId = crypto.randomUUID()
    try {
      await startBrief(sessionId, trimmedDestination, tripLengthDays)
      reset()
      setSessionId(sessionId)
      navigate(`/brief/${encodeURIComponent(sessionId)}/progress`)
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : 'The brief could not be started.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="kickoff-page">
      <form className="kickoff-form" onSubmit={handleSubmit} noValidate>
        <p className="eyebrow">A FIELD GUIDE, MADE FOR YOU</p>
        <label className="destination-label" htmlFor="destination">
          Where should we go?
        </label>
        <input
          id="destination"
          className="destination-input"
          type="text"
          value={destination}
          onChange={(event) => setDestination(event.target.value)}
          placeholder="Naples, Oaxaca, Hanoi…"
          autoComplete="off"
          autoFocus
        />
        <div className="kickoff-controls">
          <label className="days-control" htmlFor="trip-length">
            <span>TRIP LENGTH</span>
            <span className="days-input-wrap">
              <input
                id="trip-length"
                type="number"
                min="1"
                max="14"
                value={tripLengthDays}
                onChange={(event) => setTripLengthDays(event.target.valueAsNumber)}
              />
              DAYS
            </span>
          </label>
          <button className="primary-button" type="submit" disabled={submitting}>
            {submitting ? 'STARTING…' : 'MAKE MY BRIEF'}
          </button>
        </div>
        {error && <p className="form-error" role="alert">{error}</p>}
      </form>
    </main>
  )
}
