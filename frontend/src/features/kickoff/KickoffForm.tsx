import * as Checkbox from '@radix-ui/react-checkbox'
import { type FormEvent, useState } from 'react'
import { useNavigate } from 'react-router'
import { startBrief } from '../../lib/api'
import {
  ACTIVITY_DRIVERS,
  FOOD_SELECTIONS,
  TIME_BLOCKS,
  type ActivityDriver,
  type FoodSelection,
  type PlaceMatch,
  type TimeBlock,
} from '../../lib/types'
import { useBriefStore } from '../../store'

type AmbiguityState = {
  sessionId: string
  candidates: PlaceMatch[]
}

export function KickoffForm() {
  const navigate = useNavigate()
  const setSessionId = useBriefStore((state) => state.setSessionId)
  const setTripId = useBriefStore((state) => state.setTripId)
  const persistTripLengthDays = useBriefStore((state) => state.setTripLengthDays)
  const persistTimeBlocks = useBriefStore((state) => state.setTimeBlocks)
  const reset = useBriefStore((state) => state.reset)
  const [destination, setDestination] = useState('')
  const [tripLengthDays, setTripLengthDays] = useState(3)
  const [selectedActivityDrivers, setSelectedActivityDrivers] = useState(
    new Set<ActivityDriver>(),
  )
  const [selectedFoodSelections, setSelectedFoodSelections] = useState(
    new Set<FoodSelection>(),
  )
  const [selectedTimeBlocks, setSelectedTimeBlocks] = useState(
    new Set<TimeBlock>(),
  )
  const [ambiguity, setAmbiguity] = useState<AmbiguityState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const activityDrivers = ACTIVITY_DRIVERS.filter((driver) =>
    selectedActivityDrivers.has(driver),
  )
  const foodSelections = FOOD_SELECTIONS.filter((selection) =>
    selectedFoodSelections.has(selection),
  )
  const timeBlocks = TIME_BLOCKS.filter((block) => selectedTimeBlocks.has(block))

  async function submitDestination(sessionId: string, nextDestination: string) {
    setSubmitting(true)
    setError(null)
    try {
      const result = await startBrief(
        sessionId,
        nextDestination,
        tripLengthDays,
        activityDrivers,
        foodSelections,
        timeBlocks,
      )
      if (result.status === 'ambiguous') {
        setAmbiguity({ sessionId, candidates: result.data.candidates })
        return
      }

      reset()
      setSessionId(sessionId)
      setTripId(result.data.trip_id)
      persistTripLengthDays(tripLengthDays)
      persistTimeBlocks(timeBlocks)
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
    if (timeBlocks.length === 0) {
      setError('Select at least one time block.')
      return
    }
    if (activityDrivers.length === 0 && foodSelections.length === 0) {
      setError('Select at least one activity driver or food selection.')
      return
    }

    await submitDestination(crypto.randomUUID(), trimmedDestination)
  }

  function toggleSelection<T>(
    value: T,
    checked: boolean,
    setSelections: React.Dispatch<React.SetStateAction<Set<T>>>,
  ) {
    setSelections((current) => {
      const next = new Set(current)
      if (checked) next.add(value)
      else next.delete(value)
      return next
    })
  }

  if (ambiguity) {
    return (
      <main className="kickoff-page">
        <section className="kickoff-form city-disambiguation">
          <p className="eyebrow">WHICH PLACE DID YOU MEAN?</p>
          <h1>Choose your destination</h1>
          <div className="city-disambiguation__options">
            {ambiguity.candidates.map((candidate) => (
              <button
                key={candidate.google_place_id}
                className="city-option"
                type="button"
                disabled={submitting}
                aria-label={`${candidate.name}, ${candidate.formatted_address}`}
                onClick={() => {
                  void submitDestination(
                    ambiguity.sessionId,
                    candidate.formatted_address,
                  )
                }}
              >
                <strong>{candidate.name}</strong>
                <span>{candidate.formatted_address}</span>
              </button>
            ))}
          </div>
          <button
            className="text-link city-disambiguation__back"
            type="button"
            disabled={submitting}
            onClick={() => {
              setAmbiguity(null)
              setError(null)
            }}
          >
            None of these
          </button>
          {error && <p className="form-error" role="alert">{error}</p>}
        </section>
      </main>
    )
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

        <div className="kickoff-checkbox-groups">
          <CheckboxGroup
            legend="ACTIVITY DRIVERS"
            options={ACTIVITY_DRIVERS}
            selected={selectedActivityDrivers}
            onCheckedChange={(option, checked) =>
              toggleSelection(
                option,
                checked,
                setSelectedActivityDrivers,
              )
            }
          />
          <CheckboxGroup
            legend="FOOD SELECTIONS"
            options={FOOD_SELECTIONS}
            selected={selectedFoodSelections}
            onCheckedChange={(option, checked) =>
              toggleSelection(option, checked, setSelectedFoodSelections)
            }
          />
          <CheckboxGroup
            legend="TIME BLOCKS"
            options={TIME_BLOCKS}
            selected={selectedTimeBlocks}
            onCheckedChange={(option, checked) =>
              toggleSelection(option, checked, setSelectedTimeBlocks)
            }
          />
        </div>

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

type CheckboxGroupProps<T extends string> = {
  legend: string
  options: readonly T[]
  selected: Set<T>
  onCheckedChange: (option: T, checked: boolean) => void
}

function CheckboxGroup<T extends string>({
  legend,
  options,
  selected,
  onCheckedChange,
}: CheckboxGroupProps<T>) {
  return (
    <fieldset className="kickoff-checkbox-group">
      <legend>{legend}</legend>
      <div>
        {options.map((option) => {
          const checked = selected.has(option)
          return (
            <label className="kickoff-checkbox-option" key={option}>
              <Checkbox.Root
                className="candidate-checkbox"
                checked={checked}
                onCheckedChange={(nextChecked) =>
                  onCheckedChange(option, nextChecked === true)
                }
                aria-label={`${checked ? 'Remove' : 'Select'} ${option}`}
              >
                <Checkbox.Indicator>✓</Checkbox.Indicator>
              </Checkbox.Root>
              <span>{option}</span>
            </label>
          )
        })}
      </div>
    </fieldset>
  )
}
