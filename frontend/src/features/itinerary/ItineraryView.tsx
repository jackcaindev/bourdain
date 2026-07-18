import { Link } from 'react-router'
import type { ScoredRecommendation } from '../../lib/types'
import { useBriefStore } from '../../store'
import { ItineraryDay } from './ItineraryDay'
import { ItineraryMap } from './ItineraryMap'

export function ItineraryView() {
  const days = useBriefStore((state) => state.itineraryDays)

  if (days.length === 0) {
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
        (recommendation): recommendation is ScoredRecommendation => recommendation !== null,
      ),
    )).map((recommendation) => [recommendation.id, recommendation]),
  ).values())

  return (
    <main className="content-page itinerary-page">
      <header className="itinerary-title">
        <p className="eyebrow">YOUR BOURDAIN BRIEF</p>
        <h1>The itinerary</h1>
        <p>{days.length} DAYS / {recommendations.length} PLACES</p>
      </header>
      {days.map((day) => <ItineraryDay key={day.day_number} day={day} />)}
      <ItineraryMap recommendations={recommendations} />
    </main>
  )
}
