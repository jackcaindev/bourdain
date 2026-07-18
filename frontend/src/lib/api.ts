import type {
  ActivityDriver,
  BriefRequest,
  BriefStatePayload,
  CityAmbiguityResponse,
  FoodSelection,
  ItinerarySlotRecord,
  PersistedItineraryDay,
  PersistedItineraryResponse,
  ResumeRequest,
  ScoredRecommendation,
  SessionResponse,
  TimeBlock,
} from './types'

const configuredBaseUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000'

export const API_BASE_URL = configuredBaseUrl.replace(/\/$/, '')

async function postJson<TResponse>(path: string, body: unknown): Promise<TResponse> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`
    try {
      const body = (await response.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      // The status-based message is sufficient when the body is not JSON.
    }
    throw new Error(detail)
  }

  return (await response.json()) as TResponse
}

async function getJson<TResponse>(path: string): Promise<TResponse> {
  const response = await fetch(`${API_BASE_URL}${path}`)

  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`
    try {
      const body = (await response.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      // The status-based message is sufficient when the body is not JSON.
    }
    throw new Error(detail)
  }

  return (await response.json()) as TResponse
}

async function patchJson<TResponse>(
  path: string,
  body?: unknown,
): Promise<TResponse> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })

  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`
    try {
      const body = (await response.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      // The status-based message is sufficient when the body is not JSON.
    }
    throw new Error(detail)
  }

  return (await response.json()) as TResponse
}

export type StartBriefResult =
  | { status: 'resolved'; data: SessionResponse }
  | { status: 'ambiguous'; data: CityAmbiguityResponse }

export async function startBrief(
  sessionId: string,
  destination: string,
  tripLengthDays: number,
  activityDrivers: ActivityDriver[],
  foodSelections: FoodSelection[],
  timeBlocks: TimeBlock[],
): Promise<StartBriefResult> {
  const body: BriefRequest = {
    session_id: sessionId,
    destination,
    trip_length_days: tripLengthDays,
    activity_drivers: activityDrivers,
    food_selections: foodSelections,
    time_blocks: timeBlocks,
  }
  const response = await fetch(`${API_BASE_URL}/api/brief`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

  if (response.status === 300) {
    return {
      status: 'ambiguous',
      data: (await response.json()) as CityAmbiguityResponse,
    }
  }

  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`
    try {
      const body = (await response.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      // The status-based message is sufficient when the body is not JSON.
    }
    throw new Error(detail)
  }

  return {
    status: 'resolved',
    data: (await response.json()) as SessionResponse,
  }
}

export function resumeBrief(
  sessionId: string,
  userSelections: string[],
  resumeType: 'categories' | 'venues',
): Promise<SessionResponse> {
  const body: ResumeRequest = {
    user_selections: userSelections,
    resume_type: resumeType,
  }
  return postJson(
    `/api/brief/${encodeURIComponent(sessionId)}/resume`,
    body,
  )
}

export function fetchBriefState(sessionId: string): Promise<BriefStatePayload> {
  return getJson(`/api/brief/${encodeURIComponent(sessionId)}/state`)
}

export function getItinerary(
  tripId: string,
): Promise<PersistedItineraryResponse> {
  return getJson(`/api/trips/${encodeURIComponent(tripId)}/itinerary`)
}

export function confirmItineraryDay(
  tripId: string,
  dayNumber: number,
): Promise<PersistedItineraryDay> {
  return patchJson(
    `/api/trips/${encodeURIComponent(tripId)}/itinerary/days/${dayNumber}/confirm`,
  )
}

export function swapItinerarySlot(
  tripId: string,
  slotId: string,
  recommendationId: string,
): Promise<ItinerarySlotRecord> {
  return patchJson(
    `/api/trips/${encodeURIComponent(tripId)}/itinerary/slots/${encodeURIComponent(slotId)}`,
    { recommendation_id: recommendationId },
  )
}

export function furtherResearch(
  tripId: string,
  categoryId: string,
): Promise<ScoredRecommendation[]> {
  return postJson(
    `/api/trips/${encodeURIComponent(tripId)}/categories/${encodeURIComponent(categoryId)}/further-research`,
    {},
  )
}
