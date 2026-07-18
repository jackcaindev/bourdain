import type { PersistedRecommendationView } from '../../lib/types'

type RecommendationBlockProps = {
  slot: string
  recommendation: PersistedRecommendationView
}

export function RecommendationBlock({
  slot,
  recommendation,
}: RecommendationBlockProps) {
  return (
    <article className="recommendation-block">
      <div className="recommendation-meta">
        <span>{slot}</span>
        <span>{recommendation.category_name}</span>
        <span>{recommendation.bourdain_score}/5</span>
      </div>
      <div className="recommendation-copy">
        <h3>{recommendation.name}</h3>
        <p>{recommendation.scoring_rationale}</p>
      </div>
    </article>
  )
}
