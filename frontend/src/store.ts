import { create } from 'zustand'
import type {
  Category,
  ItineraryDay,
  ScoredRecommendation,
  SSEEvent,
} from './lib/types'

export type ProgressEntry = {
  event: SSEEvent
  receivedAt: string
}

type BriefStore = {
  sessionId: string | null
  citySlug: string | null
  availableCategories: Category[]
  selectedCategories: string[]
  recommendations: ScoredRecommendation[]
  itineraryDays: ItineraryDay[]
  progressEvents: ProgressEntry[]
  streamError: string | null
  setSessionId: (id: string) => void
  setCitySlug: (citySlug: string) => void
  setAvailableCategories: (categories: Category[]) => void
  setSelectedCategories: (categories: string[]) => void
  setRecommendations: (recommendations: ScoredRecommendation[]) => void
  setItineraryDays: (days: ItineraryDay[]) => void
  addProgressEvent: (event: SSEEvent) => void
  setStreamError: (error: string | null) => void
  reset: () => void
}

const initialState = {
  sessionId: null,
  citySlug: null,
  availableCategories: [],
  selectedCategories: [],
  recommendations: [],
  itineraryDays: [],
  progressEvents: [],
  streamError: null,
}

export const useBriefStore = create<BriefStore>((set) => ({
  ...initialState,
  setSessionId: (sessionId) => set({ sessionId }),
  setCitySlug: (citySlug) => set({ citySlug }),
  setAvailableCategories: (availableCategories) => set({ availableCategories }),
  setSelectedCategories: (selectedCategories) => set({ selectedCategories }),
  setRecommendations: (recommendations) => set({ recommendations }),
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
