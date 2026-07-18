import type {
  Category,
  PersistedItineraryDay,
  PersistedRecommendationView,
  ScoredRecommendation,
} from '../lib/types'

export const category: Category = {
  id: '00000000-0000-0000-0000-000000000010',
  name: 'Street Food',
  rationale: 'The city is best understood at the counter.',
  type: 'food',
  source_drivers: ['Lunch'],
  estimated_duration_minutes: 90,
  eligible_blocks: ['afternoon'],
  neighborhood_scope: 'Centro',
  status: 'candidate',
}

export const recommendation: ScoredRecommendation = {
  id: 'rec-1',
  name: 'Cafe Local',
  category: 'Food',
  description: 'A neighborhood cafe.',
  lat: 41.1579,
  lng: -8.6291,
  db_recommendation_id: '00000000-0000-0000-0000-000000000001',
  place_id: 'places/cafe-local',
  formatted_address: 'Cafe Local, Centro, Porto',
  source: 'web_search',
  source_url: 'https://example.com/cafe',
  raw_signal: 'Specific local evidence.',
  relevance_score: 0.9,
  authenticity_signal: 'Long-running local spot.',
  confidence: 'high',
  needs_fallback: false,
  bourdain_score: 5,
  scoring_rationale: 'Distinctly rooted in the neighborhood.',
  locally_owned_signal: 'Family operated.',
  passed_guardrail: true,
  guardrail_note: null,
}

export const marketRecommendation: ScoredRecommendation = {
  ...recommendation,
  id: 'rec-2',
  name: 'Night Market',
  category: 'Markets',
  description: 'An evening market of independent food stalls.',
}

export const persistedRecommendation: PersistedRecommendationView = {
  id: 'rec-1',
  slot_id: 'slot-1',
  name: 'Cafe Local',
  description: 'A neighborhood cafe.',
  category_name: 'Food',
  bourdain_score: 5,
  scoring_rationale: 'Distinctly rooted in the neighborhood.',
  formatted_address: 'Cafe Local, Centro, Porto',
  lat: 41.1579,
  lng: -8.6291,
  google_types: ['cafe'],
}

export const itineraryDay: PersistedItineraryDay = {
  day_number: 1,
  status: 'draft',
  slots: [{
    time_block: 'morning',
    activity: {
      ...persistedRecommendation,
      id: 'rec-2',
      slot_id: 'slot-2',
      name: 'Old Market',
    },
    meals: [persistedRecommendation],
  }],
}
