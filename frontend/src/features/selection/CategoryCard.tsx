import * as Checkbox from '@radix-ui/react-checkbox'
import type { Category } from '../../lib/types'

type CategoryCardProps = {
  category: Category
  checked: boolean
  disabled: boolean
  disabledReason?: string
  onCheckedChange: (checked: boolean) => void
}

export function CategoryCard({
  category,
  checked,
  disabled,
  disabledReason = "Requires a time block you didn't select",
  onCheckedChange,
}: CategoryCardProps) {
  return (
    <article
      className={`candidate-card${checked ? ' candidate-card--selected' : ''}${
        disabled ? ' candidate-card--disabled' : ''
      }`}
    >
      <div className="candidate-card__topline">
        <span className="category-label">~{category.estimated_duration_minutes} MIN</span>
        <Checkbox.Root
          className="candidate-checkbox"
          checked={checked}
          disabled={disabled}
          onCheckedChange={(nextChecked) => onCheckedChange(nextChecked === true)}
          aria-label={`${checked ? 'Remove' : 'Select'} ${category.name}`}
        >
          <Checkbox.Indicator>✓</Checkbox.Indicator>
        </Checkbox.Root>
      </div>
      <h2>{category.name}</h2>
      <p>{category.rationale}</p>
      {disabled && <p className="category-disabled-reason">{disabledReason}</p>}
    </article>
  )
}
