import * as Checkbox from '@radix-ui/react-checkbox'
import type { ScoredRecommendation } from '../../lib/types'

type CandidateCardProps = {
  recommendation: ScoredRecommendation
  selected: boolean
  onSelectedChange: (selected: boolean) => void
}

export function CandidateCard({
  recommendation,
  selected,
  onSelectedChange,
}: CandidateCardProps) {
  return (
    <article className={`candidate-card${selected ? ' candidate-card--selected' : ''}`}>
      <div className="candidate-card__topline">
        <span className="category-label">{recommendation.category}</span>
        <Checkbox.Root
          className="candidate-checkbox"
          checked={selected}
          onCheckedChange={(checked) => onSelectedChange(checked === true)}
          aria-label={`${selected ? 'Remove' : 'Select'} ${recommendation.name}`}
        >
          <Checkbox.Indicator>✓</Checkbox.Indicator>
        </Checkbox.Root>
      </div>
      <h2>{recommendation.name}</h2>
      <div
        className="score-dots"
        role="img"
        aria-label={`${recommendation.bourdain_score} out of 5 Bourdain score`}
      >
        {Array.from({ length: 5 }, (_, index) => (
          <span
            key={index}
            className={index < recommendation.bourdain_score ? 'score-dot--filled' : ''}
          />
        ))}
      </div>
      <p>{recommendation.scoring_rationale}</p>
      {recommendation.guardrail_note && (
        <p className="guardrail-note">
          <span>EDITOR'S NOTE</span> {recommendation.guardrail_note}
        </p>
      )}
    </article>
  )
}
