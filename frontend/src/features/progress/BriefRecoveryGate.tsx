import { useEffect, useState } from 'react'
import { Link, Outlet, useParams } from 'react-router'
import { fetchBriefState } from '../../lib/api'
import { useBriefStore } from '../../store'

type RecoveryStatus = 'ready' | 'loading' | 'failed'

export function BriefRecoveryGate() {
  const { sessionId: urlSessionId } = useParams()
  const storeSessionId = useBriefStore((state) => state.sessionId)
  const setSessionId = useBriefStore((state) => state.setSessionId)
  const setAvailableCategories = useBriefStore(
    (state) => state.setAvailableCategories,
  )
  const setSelectedCategories = useBriefStore(
    (state) => state.setSelectedCategories,
  )
  const setRecommendations = useBriefStore((state) => state.setRecommendations)
  const setItineraryDays = useBriefStore((state) => state.setItineraryDays)
  const needsRecovery = Boolean(
    urlSessionId && urlSessionId !== storeSessionId,
  )
  const [status, setStatus] = useState<RecoveryStatus>(
    needsRecovery ? 'loading' : 'ready',
  )

  useEffect(() => {
    if (!urlSessionId || urlSessionId === storeSessionId) {
      setStatus('ready')
      return
    }

    let cancelled = false
    setStatus('loading')

    fetchBriefState(urlSessionId)
      .then((state) => {
        if (cancelled) return

        setSessionId(state.session_id)
        setAvailableCategories(state.categories ?? [])
        setSelectedCategories(state.selected_categories ?? [])
        setRecommendations(state.recommendations ?? [])
        setItineraryDays(state.itinerary_days ?? [])
        setStatus('ready')
      })
      .catch(() => {
        if (!cancelled) setStatus('failed')
      })

    return () => {
      cancelled = true
    }
  }, [
    setAvailableCategories,
    setItineraryDays,
    setRecommendations,
    setSelectedCategories,
    setSessionId,
    storeSessionId,
    urlSessionId,
  ])

  if (status === 'failed') {
    return (
      <main className="recovery-state">
        <p className="eyebrow">BRIEF UNAVAILABLE</p>
        <h1>This brief could not be restored.</h1>
        <Link className="text-link" to="/">Start a new brief</Link>
      </main>
    )
  }

  if (status === 'loading' || needsRecovery) {
    return (
      <main className="content-page">
        <header className="section-heading">
          <p className="eyebrow">RESTORING BRIEF</p>
          <h1>Reopening your brief</h1>
        </header>
      </main>
    )
  }

  return <Outlet />
}
