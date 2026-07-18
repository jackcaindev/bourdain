import { type FormEvent, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { resumeBrief } from '../../lib/api'
import { useBriefStore } from '../../store'
import { CandidateCard } from './CandidateCard'

export function SelectionScreen() {
  const { sessionId } = useParams()
  const navigate = useNavigate()
  const recommendations = useBriefStore((state) => state.recommendations)
  const venueSelectionReady = useBriefStore(
    (state) => state.venueSelectionReady,
  )
  const selectedCategories = useBriefStore((state) => state.selectedCategories)
  const [removedIds, setRemovedIds] = useState(
    () => new Set<string>(),
  )
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const selectedIds = new Set(
    recommendations
      .filter((recommendation) => !removedIds.has(recommendation.id))
      .map((recommendation) => recommendation.id),
  )

  const recommendationsByCategory = new Map<string, typeof recommendations>()
  for (const recommendation of recommendations) {
    const categoryRecommendations = recommendationsByCategory.get(
      recommendation.category,
    )
    if (categoryRecommendations) categoryRecommendations.push(recommendation)
    else recommendationsByCategory.set(recommendation.category, [recommendation])
  }

  const selectedCategoryNames = new Set(selectedCategories)
  const orderedCategoryNames = [
    ...new Set(
      selectedCategories.filter((category) => recommendationsByCategory.has(category)),
    ),
    ...Array.from(recommendationsByCategory.keys()).filter(
      (category) => !selectedCategoryNames.has(category),
    ),
  ]

  if (!sessionId) {
    return (
      <main className="recovery-state">
        <p className="eyebrow">SELECTIONS UNAVAILABLE</p>
        <h1>This selection desk needs an active brief.</h1>
        <Link className="text-link" to="/">Start a new brief</Link>
      </main>
    )
  }

  if (recommendations.length === 0 && !venueSelectionReady) {
    return (
      <main className="content-page selection-page">
        <header className="section-heading selection-heading">
          <div>
            <p className="eyebrow">THE SHORTLIST</p>
            <h1>Still gathering results…</h1>
          </div>
        </header>
      </main>
    )
  }

  function setSelected(id: string, selected: boolean) {
    setRemovedIds((current) => {
      const next = new Set(current)
      if (selected) next.delete(id)
      else next.add(id)
      return next
    })
    setError(null)
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!sessionId || !venueSelectionReady) return

    setSubmitting(true)
    setError(null)
    try {
      await resumeBrief(sessionId, Array.from(selectedIds), 'venues')
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

  return (
    <main className="content-page selection-page">
      <header className="section-heading selection-heading">
        <div>
          <p className="eyebrow">THE SHORTLIST</p>
          <h1>Choose what makes the cut</h1>
        </div>
        <p>
          Every place below passed the desk. Keep what belongs; leave behind
          anything that does not belong in your trip.
        </p>
      </header>

      <form onSubmit={handleSubmit}>
        {orderedCategoryNames.map((category) => (
          <section className="recommendation-section" key={category}>
            <h2 className="recommendation-section__heading">{category}</h2>
            <div className="candidate-grid">
              {recommendationsByCategory.get(category)?.map((recommendation) => (
                <CandidateCard
                  key={recommendation.id}
                  recommendation={recommendation}
                  selected={selectedIds.has(recommendation.id)}
                  onSelectedChange={(selected) =>
                    setSelected(recommendation.id, selected)
                  }
                />
              ))}
            </div>
          </section>
        ))}

        <footer className="selection-actions">
          <p>{selectedIds.size} OF {recommendations.length} SELECTED</p>
          <div>
            {error && (
              <p className="form-error" role="alert">{error}</p>
            )}
            <button
              className="primary-button"
              type="submit"
              disabled={submitting || !venueSelectionReady}
            >
              {submitting
                ? 'ASSEMBLING…'
                : venueSelectionReady
                  ? 'BUILD THE ITINERARY'
                  : 'STILL GATHERING…'}
            </button>
          </div>
        </footer>
      </form>
    </main>
  )
}
