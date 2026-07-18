import { describe, expect, it } from 'vitest'
import { category } from '../test/fixtures'
import {
  isCategoryPlaceable,
  selectedMinutes,
  totalBudgetMinutes,
} from './budget'

describe('budget', () => {
  it('identifies categories with full, partial, or no eligible block overlap', () => {
    expect(isCategoryPlaceable(category, ['afternoon'])).toBe(true)
    expect(
      isCategoryPlaceable(
        { ...category, eligible_blocks: ['morning', 'afternoon'] },
        ['afternoon', 'night'],
      ),
    ).toBe(true)
    expect(isCategoryPlaceable(category, ['morning', 'night'])).toBe(false)
  })

  it('totals a subset of blocks across multiple days', () => {
    expect(totalBudgetMinutes(['morning', 'night'], 3)).toBe(1260)
  })

  it('sums only selected category durations', () => {
    const secondCategory = {
      ...category,
      id: '00000000-0000-0000-0000-000000000011',
      estimated_duration_minutes: 120,
    }

    expect(
      selectedMinutes([category, secondCategory], new Set([category.id, secondCategory.id])),
    ).toBe(210)
    expect(selectedMinutes([category, secondCategory], new Set())).toBe(0)
  })
})
