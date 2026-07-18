import { create } from 'zustand'
import type {
  Category,
  ItineraryDay,
  ScoredRecommendation,
  SSEEvent,
  TimeBlock,
} from './lib/types'

export type ProgressEntry = {
  event: SSEEvent
  receivedAt: string
}

type BriefStore = {
  sessionId: string | null
  tripId: string | null
  tripLengthDays: number | null
  timeBlocks: TimeBlock[]
  citySlug: string | null
  availableCategories: Category[]
  selectedCategories: string[]
  recommendations: ScoredRecommendation[]
  venueSelectionReady: boolean
  itineraryDays: ItineraryDay[]
  progressEvents: ProgressEntry[]
  streamError: string | null
  setSessionId: (id: string) => void
  setTripId: (id: string) => void
  setTripLengthDays: (days: number) => void
  setTimeBlocks: (timeBlocks: TimeBlock[]) => void
  setCitySlug: (citySlug: string) => void
  setAvailableCategories: (categories: Category[]) => void
  setSelectedCategories: (categories: string[]) => void
  setRecommendations: (recommendations: ScoredRecommendation[]) => void
  appendRecommendations: (recommendations: ScoredRecommendation[]) => void
  setVenueSelectionReady: (ready: boolean) => void
  setItineraryDays: (days: ItineraryDay[]) => void
  addProgressEvent: (event: SSEEvent) => void
  setStreamError: (error: string | null) => void
  reset: () => void
}

const initialState = {
  sessionId: null,
  tripId: null,
  tripLengthDays: null,
  timeBlocks: [],
  citySlug: null,
  availableCategories: [],
  selectedCategories: [],
  recommendations: [],
  venueSelectionReady: false,
  itineraryDays: [],
  progressEvents: [],
  streamError: null,
}

export const useBriefStore = create<BriefStore>((set) => ({
  ...initialState,
  setSessionId: (sessionId) => set({ sessionId }),
  setTripId: (tripId) => set({ tripId }),
  setTripLengthDays: (tripLengthDays) => set({ tripLengthDays }),
  setTimeBlocks: (timeBlocks) => set({ timeBlocks }),
  setCitySlug: (citySlug) => set({ citySlug }),
  setAvailableCategories: (availableCategories) => set({ availableCategories }),
  setSelectedCategories: (selectedCategories) => set({ selectedCategories }),
  setRecommendations: (recommendations) => set({ recommendations }),
  appendRecommendations: (recommendations) =>
    set((state) => ({
      recommendations: Array.from(
        new Map(
          [...state.recommendations, ...recommendations].map((recommendation) => [
            recommendation.id,
            recommendation,
          ]),
        ).values(),
      ),
    })),
  setVenueSelectionReady: (venueSelectionReady) => set({ venueSelectionReady }),
  setItineraryDays: (itineraryDays) => set({ itineraryDays }),
  addProgressEvent: (event) =>
    set((state) => ({
      progressEvents: [
        ...state.progressEvents,
        { event, receivedAt: new Date().toISOString() },
      ],
    })),
  setStreamError: (streamError) => set({ streamError }),
  reset: () => set(initialState),
}))
