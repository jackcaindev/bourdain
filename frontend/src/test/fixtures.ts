import type { ItineraryDay, ScoredRecommendation } from '../lib/types'

export const recommendation: ScoredRecommendation = {
  id: 'rec-1',
  name: 'Cafe Local',
  category: 'Food',
  description: 'A neighborhood cafe.',
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

export const itineraryDay: ItineraryDay = {
  day_number: 1,
  neighborhood_focus: 'Centro',
  breakfast: recommendation,
  lunch: null,
  dinner: null,
  activities: [{ ...recommendation, id: 'rec-2', name: 'Old Market' }],
}
