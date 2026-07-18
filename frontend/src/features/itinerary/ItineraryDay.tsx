import { useState } from 'react'
import type { PersistedItineraryDay } from '../../lib/types'
import { RecommendationBlock } from './RecommendationBlock'

type ItineraryDayProps = {
  day: PersistedItineraryDay
  onConfirm: () => Promise<void>
}

export function ItineraryDay({ day, onConfirm }: ItineraryDayProps) {
  const [isConfirming, setIsConfirming] = useState(false)

  async function handleConfirm() {
    setIsConfirming(true)
    try {
      await onConfirm()
    } finally {
      setIsConfirming(false)
    }
  }

  return (
    <section className="itinerary-day">
      <header className="itinerary-day__header">
        <p>DAY</p>
        <span>{String(day.day_number).padStart(2, '0')}</span>
        {day.status === 'draft' ? (
          <button
            className="text-link itinerary-day__confirm"
            type="button"
            disabled={isConfirming}
            onClick={handleConfirm}
          >
            {isConfirming ? 'CONFIRMING…' : 'CONFIRM THIS DAY'}
          </button>
        ) : (
          <p className="itinerary-day__status">CONFIRMED</p>
        )}
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
