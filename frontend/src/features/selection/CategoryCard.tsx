import * as Checkbox from '@radix-ui/react-checkbox'
import type { Category } from '../../lib/types'

type CategoryCardProps = {
  category: Category
  checked: boolean
  onCheckedChange: (checked: boolean) => void
}

export function CategoryCard({
  category,
  checked,
  onCheckedChange,
}: CategoryCardProps) {
  return (
    <article className={`candidate-card${checked ? ' candidate-card--selected' : ''}`}>
      <div className="candidate-card__topline">
        <span className="category-label">CATEGORY</span>
        <Checkbox.Root
          className="candidate-checkbox"
          checked={checked}
          onCheckedChange={(nextChecked) => onCheckedChange(nextChecked === true)}
          aria-label={`${checked ? 'Remove' : 'Select'} ${category.name}`}
        >
          <Checkbox.Indicator>✓</Checkbox.Indicator>
        </Checkbox.Root>
      </div>
      <h2>{category.name}</h2>
      <p>{category.rationale}</p>
    </article>
  )
}
