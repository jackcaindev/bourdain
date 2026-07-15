import { create } from 'zustand'
import type { ItineraryDay, ScoredRecommendation } from './lib/types'

type BriefStore = {
  sessionId: string | null
  recommendations: ScoredRecommendation[]
  itineraryDays: ItineraryDay[]
  setSessionId: (id: string) => void
  setRecommendations: (recommendations: ScoredRecommendation[]) => void
  setItineraryDays: (days: ItineraryDay[]) => void
  reset: () => void
}

const initialState = {
  sessionId: null,
  recommendations: [],
  itineraryDays: [],
}

export const useBriefStore = create<BriefStore>((set) => ({
  ...initialState,
  setSessionId: (sessionId) => set({ sessionId }),
  setRecommendations: (recommendations) => set({ recommendations }),
  setItineraryDays: (itineraryDays) => set({ itineraryDays }),
  reset: () => set(initialState),
}))
