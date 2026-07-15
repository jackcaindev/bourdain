import type { ScoredRecommendation } from '../../lib/types'

type RecommendationBlockProps = {
  slot: string
  recommendation: ScoredRecommendation
}

export function RecommendationBlock({
  slot,
  recommendation,
}: RecommendationBlockProps) {
  return (
    <article className="recommendation-block">
      <div className="recommendation-meta">
        <span>{slot}</span>
        <span>{recommendation.category}</span>
        <span>{recommendation.bourdain_score}/5</span>
      </div>
      <div className="recommendation-copy">
        <h3>{recommendation.name}</h3>
        <p>{recommendation.scoring_rationale}</p>
        {recommendation.source_url && (
          <a
            className="source-link"
            href={recommendation.source_url}
            target="_blank"
            rel="noreferrer"
          >
            VIEW SOURCE ↗
          </a>
        )}
      </div>
    </article>
  )
}
