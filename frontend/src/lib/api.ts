import type { BriefRequest, ResumeRequest, SessionResponse } from './types'

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

export function startBrief(
  sessionId: string,
  destination: string,
  tripLengthDays: number,
): Promise<SessionResponse> {
  const body: BriefRequest = {
    session_id: sessionId,
    destination,
    trip_length_days: tripLengthDays,
  }
  return postJson('/api/brief', body)
}

export function resumeBrief(
  sessionId: string,
  userSelections: string[],
): Promise<SessionResponse> {
  const body: ResumeRequest = { user_selections: userSelections }
  return postJson(
    `/api/brief/${encodeURIComponent(sessionId)}/resume`,
    body,
  )
}
