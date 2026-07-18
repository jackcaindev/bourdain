import { useRef } from 'react'
import { useNavigate } from 'react-router'
import {
  isCategoryListPayload,
  isHitlPayload,
  isItineraryPayload,
} from '../../lib/types'
import { useBriefStore } from '../../store'
import { useBriefStream } from './useBriefStream'

export function BriefStreamProvider() {
  const navigate = useNavigate()
  const sessionId = useBriefStore((state) => state.sessionId)
  const addProgressEvent = useBriefStore((state) => state.addProgressEvent)
  const setAvailableCategories = useBriefStore(
    (state) => state.setAvailableCategories,
  )
  const setRecommendations = useBriefStore((state) => state.setRecommendations)
  const appendRecommendations = useBriefStore(
    (state) => state.appendRecommendations,
  )
  const setVenueSelectionReady = useBriefStore(
    (state) => state.setVenueSelectionReady,
  )
  const setItineraryDays = useBriefStore((state) => state.setItineraryDays)
  const setStreamError = useBriefStore((state) => state.setStreamError)
  const selectionNavigationSession = useRef<string | null>(null)

  const closeStream = useBriefStream(sessionId ?? undefined, {
    onEvent: (event) => {
      addProgressEvent(event)

      if (
        event.event_type === 'hitl_pause' &&
        event.node_name === 'category_select' &&
        isCategoryListPayload(event.payload)
      ) {
        selectionNavigationSession.current = null
        setVenueSelectionReady(false)
        setAvailableCategories(event.payload.categories)
        if (sessionId) {
          navigate(`/brief/${encodeURIComponent(sessionId)}/categories`)
        }
      }

      if (
        event.event_type === 'node_complete' &&
        event.node_name === 'research_category' &&
        isHitlPayload(event.payload)
      ) {
        appendRecommendations(event.payload.recommendations)
        if (selectionNavigationSession.current !== sessionId && sessionId) {
          selectionNavigationSession.current = sessionId
          navigate(`/brief/${encodeURIComponent(sessionId)}/select`)
        }
      }

      if (
        event.event_type === 'hitl_pause' &&
        event.node_name === 'venue_select' &&
        isHitlPayload(event.payload)
      ) {
        setRecommendations(event.payload.recommendations)
        setVenueSelectionReady(true)
        if (sessionId) {
          navigate(`/brief/${encodeURIComponent(sessionId)}/select`)
        }
      }

      if (
        event.event_type === 'node_complete' &&
        event.node_name === 'assemble_itinerary' &&
        isItineraryPayload(event.payload)
      ) {
        setItineraryDays(event.payload.days)
        if (sessionId) {
          navigate(`/brief/${encodeURIComponent(sessionId)}/itinerary`)
        }
        closeStream()
      }

      if (event.event_type === 'error') {
        setStreamError(event.message)
        closeStream()
      }
    },
    onError: () => setStreamError('The live connection was interrupted.'),
    onClose: () => undefined,
  })

  return null
}
