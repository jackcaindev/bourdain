import { Link } from 'react-router'
import { useBriefStore } from '../../store'
import { ItineraryDay } from './ItineraryDay'

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

  return (
    <main className="content-page itinerary-page">
      <header className="itinerary-title">
        <p className="eyebrow">YOUR BOURDAIN BRIEF</p>
        <h1>The itinerary</h1>
        <p>{days.length} DAYS / {days.reduce((count, day) => (
          count + Number(Boolean(day.breakfast)) + Number(Boolean(day.lunch)) +
          Number(Boolean(day.dinner)) + day.activities.length
        ), 0)} PLACES</p>
      </header>
      {days.map((day) => <ItineraryDay key={day.day_number} day={day} />)}
    </main>
  )
}
