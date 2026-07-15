import type { ItineraryDay as ItineraryDayType } from '../../lib/types'
import { RecommendationBlock } from './RecommendationBlock'

export function ItineraryDay({ day }: { day: ItineraryDayType }) {
  const mealSlots = [
    ['Breakfast', day.breakfast],
    ['Lunch', day.lunch],
    ['Dinner', day.dinner],
  ] as const

  return (
    <section className="itinerary-day">
      <header className="itinerary-day__header">
        <p>DAY</p>
        <span>{String(day.day_number).padStart(2, '0')}</span>
        {day.neighborhood_focus && <h2>{day.neighborhood_focus}</h2>}
      </header>
      <div className="itinerary-day__entries">
        {mealSlots.map(([slot, recommendation]) =>
          recommendation ? (
            <RecommendationBlock
              key={slot}
              slot={slot}
              recommendation={recommendation}
            />
          ) : null,
        )}
        {day.activities.map((activity, index) => (
          <RecommendationBlock
            key={activity.id}
            slot={`Activity ${index + 1}`}
            recommendation={activity}
          />
        ))}
      </div>
    </section>
  )
}
