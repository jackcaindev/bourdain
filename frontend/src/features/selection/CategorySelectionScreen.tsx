import * as Progress from '@radix-ui/react-progress'
import { type FormEvent, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { resumeBrief } from '../../lib/api'
import {
  isCategoryPlaceable,
  selectedMinutes,
  totalBudgetMinutes,
} from '../../lib/budget'
import type { Category } from '../../lib/types'
import { useBriefStore } from '../../store'
import { CategoryCard } from './CategoryCard'

function initiallySelectedIds(categories: Category[], budgetMinutes: number) {
  const selectedIds = new Set<string>()
  let usedMinutes = 0

  for (const category of categories) {
    if (usedMinutes + category.estimated_duration_minutes <= budgetMinutes) {
      selectedIds.add(category.id)
      usedMinutes += category.estimated_duration_minutes
    }
  }

  return selectedIds
}

export function CategorySelectionScreen() {
  const { sessionId } = useParams()
  const navigate = useNavigate()
  const availableCategories = useBriefStore((state) => state.availableCategories)
  const tripLengthDays = useBriefStore((state) => state.tripLengthDays)
  const timeBlocks = useBriefStore((state) => state.timeBlocks)
  const placeableCategories = availableCategories.filter((category) =>
    isCategoryPlaceable(category, timeBlocks),
  )
  const budgetMinutes = tripLengthDays === null
    ? 0
    : totalBudgetMinutes(timeBlocks, tripLengthDays)
  const [selectedIds, setSelectedIds] = useState(() =>
    initiallySelectedIds(placeableCategories, budgetMinutes),
  )
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const usedMinutes = selectedMinutes(placeableCategories, selectedIds)

  if (
    !sessionId ||
    availableCategories.length === 0 ||
    tripLengthDays === null ||
    timeBlocks.length === 0
  ) {
    return (
      <main className="recovery-state">
        <p className="eyebrow">CATEGORIES UNAVAILABLE</p>
        <h1>This category desk needs an active brief.</h1>
        <Link className="text-link" to="/">Start a new brief</Link>
      </main>
    )
  }

  function setSelected(category: Category, selected: boolean) {
    setSelectedIds((current) => {
      if (
        selected &&
        !current.has(category.id) &&
        selectedMinutes(placeableCategories, current) +
          category.estimated_duration_minutes > budgetMinutes
      ) {
        return current
      }

      const next = new Set(current)
      if (selected) next.add(category.id)
      else next.delete(category.id)
      return next
    })
    setError(null)
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!sessionId || selectedIds.size < 1) {
      setError('Select at least one category to continue.')
      return
    }

    const selectedNames = availableCategories
      .filter((category) => selectedIds.has(category.id))
      .map((category) => category.name)

    setSubmitting(true)
    setError(null)
    try {
      await resumeBrief(sessionId, selectedNames, 'categories')
      navigate(`/brief/${encodeURIComponent(sessionId)}/progress`)
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : 'The brief could not be resumed.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  const groups = [
    { type: 'activity' as const, heading: 'ACTIVITIES' },
    { type: 'food' as const, heading: 'FOOD' },
  ]

  return (
    <main className="content-page selection-page">
      <header className="section-heading selection-heading">
        <div>
          <p className="eyebrow">BUILD THE BRIEF</p>
          <h1>Choose what fits your trip</h1>
        </div>
        <p>
          Fill the time you actually have. Categories that cannot fit your
          selected schedule stay visible for context.
        </p>
      </header>

      <section className="budget-fill" aria-label="Trip budget fill">
        <div className="budget-fill__labels">
          <span>~{usedMinutes} MIN SELECTED</span>
          <span>~{budgetMinutes} MIN AVAILABLE</span>
        </div>
        <Progress.Root
          className="budget-progress"
          value={usedMinutes}
          max={budgetMinutes}
          aria-label={`${usedMinutes} of ${budgetMinutes} trip minutes selected`}
        >
          <Progress.Indicator
            className="budget-progress__indicator"
            style={{ transform: `translateX(-${100 - (usedMinutes / budgetMinutes) * 100}%)` }}
          />
        </Progress.Root>
      </section>

      <form onSubmit={handleSubmit}>
        {groups.map(({ type, heading }) => {
          const categories = availableCategories.filter(
            (category) => category.type === type,
          )
          if (categories.length === 0) return null

          return (
            <section className="recommendation-section" key={type}>
              <h2 className="recommendation-section__heading">{heading}</h2>
              <div className="candidate-grid">
                {categories.map((category) => {
                  const placeable = isCategoryPlaceable(category, timeBlocks)
                  const selected = selectedIds.has(category.id)
                  const exceedsBudget =
                    !selected &&
                    usedMinutes + category.estimated_duration_minutes > budgetMinutes
                  const disabled = !placeable || exceedsBudget

                  return (
                    <CategoryCard
                      key={category.id}
                      category={category}
                      checked={placeable && selected}
                      disabled={disabled}
                      disabledReason={
                        placeable
                          ? 'Does not fit in your remaining trip time'
                          : undefined
                      }
                      onCheckedChange={(checked) => setSelected(category, checked)}
                    />
                  )
                })}
              </div>
            </section>
          )
        })}

        <footer className="selection-actions">
          <p>{selectedIds.size} OF {placeableCategories.length} SELECTED</p>
          <div>
            {selectedIds.size === 0 && (
              <p className="form-error" role="alert">
                Select at least one category to continue.
              </p>
            )}
            {error && selectedIds.size > 0 && (
              <p className="form-error" role="alert">{error}</p>
            )}
            <button
              className="primary-button"
              type="submit"
              disabled={submitting || selectedIds.size === 0}
            >
              {submitting ? 'SEARCHING…' : 'FIND THE PLACES'}
            </button>
          </div>
        </footer>
      </form>
    </main>
  )
}
