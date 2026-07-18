import type { ItineraryDay as ItineraryDayType } from '../../lib/types'
import { RecommendationBlock } from './RecommendationBlock'

export function ItineraryDay({ day }: { day: ItineraryDayType }) {
  return (
    <section className="itinerary-day">
      <header className="itinerary-day__header">
        <p>DAY</p>
        <span>{String(day.day_number).padStart(2, '0')}</span>
        {day.neighborhood_focus && <h2>{day.neighborhood_focus}</h2>}
      </header>
      <div className="itinerary-day__entries">
        {day.slots.flatMap((slot) => {
          const block = slot.time_block[0].toUpperCase() + slot.time_block.slice(1)
          return [
            ...(slot.activity ? [
              <RecommendationBlock
                key={`${slot.time_block}-activity`}
                slot={`${block} Activity`}
                recommendation={slot.activity}
              />,
            ] : []),
            ...slot.meals.map((meal, index) => (
              <RecommendationBlock
                key={`${slot.time_block}-meal-${meal.id}`}
                slot={`${block} Meal${slot.meals.length > 1 ? ` ${index + 1}` : ''}`}
                recommendation={meal}
              />
            )),
          ]
        })}
      </div>
    </section>
  )
}
