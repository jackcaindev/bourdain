import type { Category, TimeBlock } from './types'

export const BLOCK_TARGET_MINUTES: Record<TimeBlock, number> = {
  morning: 240,
  afternoon: 240,
  night: 180,
}

export function isCategoryPlaceable(
  category: Category,
  timeBlocks: TimeBlock[],
): boolean {
  return category.eligible_blocks.some((block) => timeBlocks.includes(block))
}

export function totalBudgetMinutes(
  timeBlocks: TimeBlock[],
  tripLengthDays: number,
): number {
  return timeBlocks.reduce(
    (total, block) => total + BLOCK_TARGET_MINUTES[block] * tripLengthDays,
    0,
  )
}

export function selectedMinutes(
  categories: Category[],
  selectedIds: Set<string>,
): number {
  return categories.reduce(
    (total, category) =>
      total + (selectedIds.has(category.id) ? category.estimated_duration_minutes : 0),
    0,
  )
}
