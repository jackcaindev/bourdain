import { type FormEvent, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { resumeBrief } from '../../lib/api'
import { useBriefStore } from '../../store'
import { CategoryCard } from './CategoryCard'

export function CategorySelectionScreen() {
  const { sessionId } = useParams()
  const navigate = useNavigate()
  const availableCategories = useBriefStore((state) => state.availableCategories)
  const [selectedNames, setSelectedNames] = useState(
    () => new Set(availableCategories.map((category) => category.name)),
  )
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!sessionId || availableCategories.length === 0) {
    return (
      <main className="recovery-state">
        <p className="eyebrow">CATEGORIES UNAVAILABLE</p>
        <h1>This category desk needs an active brief.</h1>
        <Link className="text-link" to="/">Start a new brief</Link>
      </main>
    )
  }

  function setSelected(name: string, selected: boolean) {
    setSelectedNames((current) => {
      const next = new Set(current)
      if (selected) next.add(name)
      else next.delete(name)
      return next
    })
    setError(null)
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!sessionId || selectedNames.size < 1) {
      setError('Select at least one category to continue.')
      return
    }

    setSubmitting(true)
    setError(null)
    try {
      await resumeBrief(sessionId, Array.from(selectedNames), 'categories')
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
          <p className="eyebrow">THE CATEGORIES</p>
          <h1>Choose what belongs in your brief</h1>
        </div>
        <p>
          Keep at least one category. We will use your choices to find the
          places worth putting on the shortlist.
        </p>
      </header>

      <form onSubmit={handleSubmit}>
        <div className="candidate-grid">
          {availableCategories.map((category) => (
            <CategoryCard
              key={category.name}
              category={category}
              checked={selectedNames.has(category.name)}
              onCheckedChange={(selected) => setSelected(category.name, selected)}
            />
          ))}
        </div>

        <footer className="selection-actions">
          <p>{selectedNames.size} OF {availableCategories.length} SELECTED</p>
          <div>
            {selectedNames.size === 0 && (
              <p className="form-error" role="alert">
                Select at least one category to continue.
              </p>
            )}
            {error && selectedNames.size > 0 && (
              <p className="form-error" role="alert">{error}</p>
            )}
            <button
              className="primary-button"
              type="submit"
              disabled={submitting || selectedNames.size === 0}
            >
              {submitting ? 'SEARCHING…' : 'FIND THE PLACES'}
            </button>
          </div>
        </footer>
      </form>
    </main>
  )
}
