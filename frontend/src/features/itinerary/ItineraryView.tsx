import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { confirmItineraryDay, getItinerary } from '../../lib/api'
import type {
  PersistedItineraryResponse,
  PersistedRecommendationView,
} from '../../lib/types'
import { useBriefStore } from '../../store'
import { ItineraryDay } from './ItineraryDay'
import { ItineraryMap } from './ItineraryMap'

export function ItineraryView() {
  const tripId = useBriefStore((state) => state.tripId)
  const [itinerary, setItinerary] = useState<PersistedItineraryResponse | null>(null)
  const [status, setStatus] = useState<'loading' | 'ready' | 'failed'>(
    tripId ? 'loading' : 'ready',
  )

  useEffect(() => {
    if (!tripId) return

    let cancelled = false
    setStatus('loading')

    getItinerary(tripId)
      .then((response) => {
        if (cancelled) return
        setItinerary(response)
        setStatus('ready')
      })
      .catch(() => {
        if (!cancelled) setStatus('failed')
      })

    return () => {
      cancelled = true
    }
  }, [tripId])

  if (status === 'failed') {
    return (
      <main className="recovery-state">
        <p className="eyebrow">ITINERARY UNAVAILABLE</p>
        <h1>This itinerary could not be loaded.</h1>
        <Link className="text-link" to="/">Start a new brief</Link>
      </main>
    )
  }

  if (status === 'loading') {
    return (
      <main className="content-page">
        <header className="section-heading">
          <p className="eyebrow">LOADING ITINERARY</p>
          <h1>Opening your itinerary</h1>
        </header>
      </main>
    )
  }

  const days = itinerary?.days ?? []

  if (!tripId || days.length === 0) {
    return (
      <main className="recovery-state">
        <p className="eyebrow">ITINERARY UNAVAILABLE</p>
        <h1>This itinerary is no longer in memory.</h1>
        <Link className="text-link" to="/">Start a new brief</Link>
      </main>
    )
  }

  const recommendations = Array.from(new Map(
    days.flatMap((day) => day.slots.flatMap((slot) =>
      [slot.activity, ...slot.meals].filter(
        (recommendation): recommendation is PersistedRecommendationView => recommendation !== null,
      ),
    )).map((recommendation) => [recommendation.id, recommendation]),
  ).values())

  async function handleConfirm(dayNumber: number) {
    if (!tripId) return

    const confirmedDay = await confirmItineraryDay(tripId, dayNumber)
    setItinerary((current) => current && ({
      ...current,
      days: current.days.map((day) => (
        day.day_number === confirmedDay.day_number ? confirmedDay : day
      )),
    }))
  }

  return (
    <main className="content-page itinerary-page">
      <header className="itinerary-title">
        <p className="eyebrow">YOUR BOURDAIN BRIEF</p>
        <h1>The itinerary</h1>
        <p>{days.length} DAYS / {recommendations.length} PLACES</p>
      </header>
      {days.map((day) => (
        <ItineraryDay
          key={day.day_number}
          day={day}
          onConfirm={() => handleConfirm(day.day_number)}
        />
      ))}
      <ItineraryMap recommendations={recommendations} />
    </main>
  )
}
