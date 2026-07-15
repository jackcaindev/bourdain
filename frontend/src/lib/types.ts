export type Screen = 'kickoff' | 'progress' | 'selection' | 'itinerary'

export type SSEEventType =
  | 'node_start'
  | 'node_progress'
  | 'node_complete'
  | 'hitl_pause'
  | 'error'

export type Category = {
  name: string
  rationale: string
}

export type Candidate = {
  id: string
  name: string
  category: string
  description: string
  lat: number | null
  lng: number | null
  source: 'vector_store' | 'web_search' | 'cache'
  source_url: string | null
  raw_signal: string
}

export type GradedCandidate = Candidate & {
  relevance_score: number
  authenticity_signal: string
  confidence: 'low' | 'medium' | 'high'
  needs_fallback: boolean
}

export type ScoredRecommendation = GradedCandidate & {
  bourdain_score: number
  scoring_rationale: string
  locally_owned_signal: string | null
  passed_guardrail: boolean
  guardrail_note: string | null
}

export type ItineraryDay = {
  day_number: number
  neighborhood_focus: string | null
  breakfast: ScoredRecommendation | null
  lunch: ScoredRecommendation | null
  dinner: ScoredRecommendation | null
  activities: ScoredRecommendation[]
}

export type CandidatePayload = {
  category: string
  candidates_found: number
}

export type ScorePayload = {
  recommendation: ScoredRecommendation
}

export type FallbackPayload = {
  category: string
  reason: string
}

export type ErrorPayload = {
  node_name: string
  detail: string
}

export type CategoryListPayload = {
  categories: Category[]
}

export type HitlPayload = {
  recommendations: ScoredRecommendation[]
}

export type ItineraryPayload = {
  days: ItineraryDay[]
}

export type CacheHitPayload = {
  category: string
  recommendations_count: number
}

export type SSEPayload =
  | CandidatePayload
  | ScorePayload
  | FallbackPayload
  | ErrorPayload
  | CategoryListPayload
  | HitlPayload
  | ItineraryPayload
  | CacheHitPayload

export type SSEEvent = {
  event_type: SSEEventType
  node_name: string
  message: string
  payload: SSEPayload | null
}

export type BriefRequest = {
  session_id: string
  destination: string
  trip_length_days: number
}

export type ResumeRequest = {
  user_selections: string[]
  resume_type: 'categories' | 'venues'
}

export type SessionResponse = {
  session_id: string
}

export type BriefStatePayload = {
  session_id: string
  phase: 'category_select' | 'venue_select' | 'itinerary' | 'in_progress'
  categories: Category[] | null
  selected_categories: string[] | null
  recommendations: ScoredRecommendation[] | null
  itinerary_days: ItineraryDay[] | null
}

export function isHitlPayload(payload: SSEPayload | null): payload is HitlPayload {
  return payload !== null && 'recommendations' in payload
}

export function isCategoryListPayload(
  payload: SSEPayload | null,
): payload is CategoryListPayload {
  return payload !== null && 'categories' in payload
}

export function isItineraryPayload(
  payload: SSEPayload | null,
): payload is ItineraryPayload {
  return payload !== null && 'days' in payload
}

export function isCacheHitPayload(
  payload: SSEPayload | null,
): payload is CacheHitPayload {
  return payload !== null && 'recommendations_count' in payload
}
