export type Screen = 'kickoff' | 'progress' | 'selection' | 'itinerary'

export const ACTIVITY_DRIVERS = [
  'Nightlife',
  'Arts & Music',
  'Culture & History',
  'Outdoors & Nature',
  'Shopping & Markets',
  'Local Life & Offbeat',
] as const
export type ActivityDriver = (typeof ACTIVITY_DRIVERS)[number]

export const FOOD_SELECTIONS = [
  'Breakfast',
  'Coffee',
  'Lunch',
  'Tea',
  'Dinner',
] as const
export type FoodSelection = (typeof FOOD_SELECTIONS)[number]

export type SSEEventType =
  | 'node_start'
  | 'node_progress'
  | 'node_complete'
  | 'hitl_pause'
  | 'error'

export type Category = {
  id: string
  name: string
  rationale: string
  type: 'food' | 'activity'
  source_drivers: string[]
  estimated_duration_minutes: number
  eligible_blocks: TimeBlock[]
  neighborhood_scope: string
  status: 'candidate' | 'selected' | 'stale_replaced'
}

export type Candidate = {
  id: string
  name: string
  category: string
  description: string
  lat: number | null
  lng: number | null
  db_recommendation_id: string | null
  place_id: string | null
  formatted_address: string | null
  source: 'vector_store' | 'web_search'
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

export type TimeBlock = 'morning' | 'afternoon' | 'night'

export const TIME_BLOCKS: TimeBlock[] = ['morning', 'afternoon', 'night']

export type ItinerarySlot = {
  time_block: TimeBlock
  activity: ScoredRecommendation | null
  meals: ScoredRecommendation[]
}

export type ItineraryDay = {
  day_number: number
  neighborhood_focus: string | null
  slots: ItinerarySlot[]
}

export type PersistedRecommendationView = {
  id: string
  slot_id: string
  name: string
  description: string
  category_name: string
  bourdain_score: number
  scoring_rationale: string
  formatted_address: string
  lat: number
  lng: number
  google_types: string[]
}

export type PersistedItinerarySlot = {
  time_block: TimeBlock
  activity: PersistedRecommendationView | null
  meals: PersistedRecommendationView[]
}

export type PersistedItineraryDay = {
  day_number: number
  status: 'draft' | 'confirmed'
  slots: PersistedItinerarySlot[]
}

export type PersistedItineraryResponse = {
  trip_id: string
  status: 'draft' | 'confirmed'
  days: PersistedItineraryDay[]
}

export type ItinerarySlotRecord = {
  id: string
  itinerary_day_id: string
  time_block: string
  slot_role: 'activity' | 'meal'
  recommendation_id: string | null
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

export type SSEPayload =
  | CandidatePayload
  | ScorePayload
  | FallbackPayload
  | ErrorPayload
  | CategoryListPayload
  | HitlPayload
  | ItineraryPayload

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
  activity_drivers: ActivityDriver[]
  food_selections: FoodSelection[]
  time_blocks: TimeBlock[]
}

export type PlaceMatch = {
  google_place_id: string
  name: string
  formatted_address: string
  lat: number
  lng: number
  google_types: string[]
}

export type CityAmbiguityResponse = {
  status: 'ambiguous'
  candidates: PlaceMatch[]
}

export type ResumeRequest = {
  user_selections: string[]
  resume_type: 'categories' | 'venues'
}

export type SessionResponse = {
  session_id: string
  trip_id: string
}

export type BriefStatePayload = {
  session_id: string
  trip_id: string | null
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
