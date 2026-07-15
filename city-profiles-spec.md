# The Bourdain Brief — City Profiles Architecture Spec

## What changed and why

The original architecture generated categories fresh per search via LLM, ran
research on all of them in parallel, and presented a single undifferentiated
HITL candidate wall. The new architecture treats cities as persistent knowledge
objects — categories are derived once and stored, research results are cached
with a TTL, and the user has two explicit decision points: which categories
they care about, then which specific venues to include. Web search is steered
toward local publications and city-specific journalism rather than aggregator
sites. Venue coordinates are captured during research via Nominatim/geopy so
the itinerary view can render a Leaflet map.

---

## Graph topology

```
START
  → city_profile_node
      Looks up city_slug in city_profiles table.
      If found: loads stored categories, skips LLM generation.
      If not found: LLM derives categories for the destination
      (steered toward authentic, non-touristy character —
      e.g. Austin → food trucks, live music, BBQ, thrifting, outdoor
      activities), stores to city_profiles table, returns categories.

  → HITL 1 (category_select)
      interrupt() — user sees all city categories with rationales,
      selects which ones they want researched.
      Frontend POSTs selected category names → backend resumes with
      Command(resume=selected_category_names).

  → Send fan-out: [Send("research_category", {"category": c})
      for c in selected_categories]
      Each branch:
        → check_category_cache: if valid cache entry exists for
          city_slug + category_name and not expired → return cached
          ScoredRecommendations, skip research entirely
        → if cache miss or expired:
            → pgvector_retrieve
            → crag_grade (needs_fallback bool)
            → conditional: web_fallback_agent if needs_fallback
            → scoring_node (per-category, not batch)
            → guardrail_node (per-category)
            → geocode_candidates: Nominatim lookup per venue,
              populate lat/lng on each ScoredRecommendation
            → write_category_cache: store results with
              expires_at = now() + 90 days

  → reduce_candidates (operator.add merge)

  → supervisor_replan_check (bounded to 1 iteration, same as before)

  → HITL 2 (venue_select)
      interrupt() — user sees ScoredRecommendations grouped by
      category, selects specific venues from each group.
      No minimum per category — empty category selection is valid.
      Frontend POSTs selected venue ids → backend resumes with
      Command(resume=selected_ids).

  → assemble_itinerary (deterministic, same logic as before)

  → END
```

---

## Postgres schema

### New table: `city_profiles`

```sql
CREATE TABLE city_profiles (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    city_slug   TEXT NOT NULL UNIQUE,
    city_name   TEXT NOT NULL,
    categories  JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`city_slug` is a normalized identifier: lowercase, hyphenated, country-suffix
where disambiguation is needed — e.g. `austin-tx`, `oaxaca-mx`, `lisbon-pt`.
Normalization happens in Python before any DB lookup.

### New table: `category_cache`

```sql
CREATE TABLE category_cache (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    city_slug       TEXT NOT NULL,
    category_name   TEXT NOT NULL,
    recommendations JSONB NOT NULL,
    cached_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    UNIQUE (city_slug, category_name)
);
```

TTL is 90 days. `expires_at = cached_at + INTERVAL '90 days'` set at write
time. Cache check: `SELECT * FROM category_cache WHERE city_slug = $1 AND
category_name = $2 AND expires_at > now()`.

On cache hit: deserialize JSONB → list[ScoredRecommendation], skip research
entirely for that category branch.

On cache write: UPSERT on (city_slug, category_name), replacing existing row
and resetting expires_at.

---

## Pydantic schema changes — `models/schemas.py`

### `Candidate` — add coordinates

```python
class Candidate(BaseModel):
    id: str
    name: str
    category: str
    description: str
    source: Literal["vector_store", "web_search", "cache"]  # add "cache"
    source_url: str | None = None
    raw_signal: str
    lat: float | None = None   # populated by geocode_candidates node
    lng: float | None = None   # populated by geocode_candidates node
```

`lat`/`lng` are optional everywhere — geocoding can fail for obscure venues
and that should never block the pipeline. Frontend renders a pin only when
both are present.

### New SSE payload types

```python
class CategoryListPayload(BaseModel):
    categories: list[Category]   # for HITL 1 pause event

class HitlPayload(BaseModel):
    recommendations: list[ScoredRecommendation]  # for HITL 2 pause event

class ItineraryPayload(BaseModel):
    days: list[ItineraryDay]     # for assemble_itinerary complete event

class CacheHitPayload(BaseModel):
    category: str
    recommendations_count: int   # so UI can show "loaded X from cache"
```

Add all four to the `SSEEvent.payload` union.

### `BriefState` changes

```python
class BriefState(TypedDict):
    destination: str
    city_slug: str               # normalized, derived from destination
    trip_length_days: int
    categories: list[Category]   # all city categories (from profile or LLM)
    selected_categories: list[Category] | None   # user's HITL 1 selections
    candidates: Annotated[list[Candidate], operator.add]
    graded_candidates: list[GradedCandidate]
    scored_recommendations: list[ScoredRecommendation]
    user_selections: list[str] | None
    itinerary: list[ItineraryDay] | None
    research_iteration: int
    replan_categories: list[Category]
```

---

## New and changed modules

### `services/city_profiles.py` (new)

Async DB service for city profile reads/writes. Uses the shared asyncpg pool
(same pattern as `vector_store.py` — never create a fresh pool, always
`get_shared_pool()`).

Functions:
- `get_city_profile(city_slug: str) -> list[Category] | None`
- `save_city_profile(city_slug: str, city_name: str, categories: list[Category]) -> None`
- `normalize_city_slug(destination: str) -> str`
  Simple normalization: lowercase, strip punctuation, replace spaces with
  hyphens, append country/state suffix if present in destination string.
  e.g. "Austin, TX" → "austin-tx", "Oaxaca, Mexico" → "oaxaca-mexico".

### `services/category_cache.py` (new)

Async DB service for category cache reads/writes.

Functions:
- `get_cached_recommendations(city_slug: str, category_name: str) -> list[ScoredRecommendation] | None`
  Returns None on cache miss or expired entry.
- `write_category_cache(city_slug: str, category_name: str, recommendations: list[ScoredRecommendation]) -> None`
  UPSERT, sets expires_at = now() + 90 days.

### `services/geocoding.py` (new)

Nominatim geocoder via `geopy`. Single function:

```python
async def geocode_venue(name: str, city_name: str) -> tuple[float, float] | None
```

- Builds query: `f"{name}, {city_name}"`
- Wraps `geopy.geocoders.Nominatim` in `asyncio.to_thread` (blocking call —
  must follow the asyncio.to_thread discipline from the implementation
  conventions)
- User-agent: `"bourdain-brief/1.0"` (Nominatim requires a non-empty
  user-agent)
- Returns `(lat, lng)` tuple or `None` on failure
- Never raises — geocoding failure is non-fatal, lat/lng stay None

### `services/web_search.py` — domain steering

Update Tavily search calls to include domain hints biasing toward local
journalism and city-specific publications. Tavily supports
`include_domains` parameter.

Default include_domains for general searches:
```python
PREFERRED_DOMAINS = [
    "eater.com",
    "thrillist.com",
    "timeout.com",
    "theninfatuation.com",
    "texasmonthly.com",    # Austin-specific but harmless globally
    "austinchronicle.com", # same
]
```

This is a starting list — not exhaustive, and Tavily will still search beyond
these if results are thin. The parameter biases, it doesn't restrict.

### `graph/city_profile_node.py` (new)

Node that runs first in the graph:
1. Normalize destination → city_slug
2. Look up city_slug in city_profiles table
3. If found: return `{"city_slug": slug, "categories": stored_categories}`
4. If not found: LLM call to derive categories (same supervisor prompt as
   before, steered toward authentic local character), store to DB, return

This node replaces the current `supervisor` node's category-generation
responsibility. The supervisor prompt moves here.

### `graph/research.py` — cache check added

Each `research_category` branch now starts with a cache check:
1. `check_category_cache(city_slug, category.name)`
2. Cache hit → emit `CacheHitPayload` SSE event, return cached recommendations
3. Cache miss → run existing pgvector → CRAG → fallback pipeline
4. After research completes: geocode each candidate, write cache

### `graph/build.py` — topology changes

- Add `city_profile_node` as first node after START
- HITL 1 after `city_profile_node`: `interrupt()` with `CategoryListPayload`
- Fan-out from selected_categories (not all categories)
- HITL 2 after `guardrail_node`: `interrupt()` with `HitlPayload`
- Remove standalone `scoring_node` and `guardrail_node` from the main sequence
  — they now run per-category inside the research branch
- `supervisor_replan_check` remains, bounded at 1 iteration
- `assemble_itinerary` unchanged

---

## SSE contract changes — `api/routes.py`

### New events to emit

| Trigger | event_type | node_name | payload |
|---|---|---|---|
| city_profile_node start | node_start | city_profile_node | None |
| city_profile_node end (cache hit) | node_complete | city_profile_node | None |
| city_profile_node end (LLM generated) | node_complete | city_profile_node | None |
| HITL 1 interrupt | hitl_pause | category_select | CategoryListPayload |
| research cache hit | node_complete | research_category | CacheHitPayload |
| HITL 2 interrupt | hitl_pause | venue_select | HitlPayload |
| assemble_itinerary end | node_complete | assemble_itinerary | ItineraryPayload |

### HITL sentinel behavior

Two HITL pauses means the None sentinel must NOT be queued after either pause
— only after terminal completion or unrecoverable error. The stream stays open
across both HITLs and the resume between them.

### `_to_sse_event` copy — Bourdain voice

```python
_START_MESSAGES = {
    "city_profile_node":       "Figuring out what this place actually is…",
    "research_category":       "Asking around…",
    "reduce_graded_candidates": "Cutting the tourist traps…",
    "supervisor_replan_check": "Something's off. Looking again…",
    "guardrail_node":          "Fact-checking the locals…",
    "assemble_itinerary":      "Building your days…",
}

_COMPLETE_MESSAGES = {
    "city_profile_node":       "Know what we're dealing with.",
    "research_category":       "Got some leads.",
    "reduce_graded_candidates": "Got a shortlist worth trusting.",
    "supervisor_replan_check": "Good enough. Moving on.",  # no replan
    # supervisor_replan_check with replan: "Took another look. Better."
    "guardrail_node":          "Nothing here we can't stand behind.",
    "assemble_itinerary":      "That's your trip.",
}
```

Apply the same voice — terse, world-weary, specific to food and travel, no
corporate language — to every other piece of user-facing text not listed:
error states, empty states, cache hit messages, geocoding notes.

---

## API contract changes — `api/routes.py`

### Two HITL resumes

Resume endpoint now handles two distinct resume types. Add a `resume_type`
field to distinguish:

```python
class ResumeRequest(BaseModel):
    user_selections: list[str]
    resume_type: Literal["categories", "venues"]
```

`categories` resume: `user_selections` is a list of category names.
`venues` resume: `user_selections` is a list of venue ids.

Same endpoint, same session_id path param — just different payload semantics.
The graph's `Command(resume=user_selections)` passes the list through; the
graph node handles interpretation.

---

## Frontend changes

### New screen: Category Selection (HITL 1)

Route: `/brief/:sessionId/categories`

- Triggered by `hitl_pause` event with `node_name: "category_select"`
- Shows all city categories with name + rationale
- User toggles which categories they want researched
- All selected by default
- Submit calls `resumeBrief(sessionId, selectedCategoryNames, "categories")`
- Design: same grid treatment as venue selection, category name in Cormorant
  Garant headline, rationale in Archivo body, ochre checkbox

### Updated screen: Venue Selection (HITL 2)

Route: `/brief/:sessionId/select`

- Triggered by `hitl_pause` event with `node_name: "venue_select"`
- Candidates grouped by category — one section per category with a rule
  separator
- User selects specific venues within each group
- Empty category selection allowed
- Same CandidateCard treatment as before

### Updated screen: Itinerary View

Route: `/brief/:sessionId/itinerary`

- Add Leaflet map below or alongside the day-by-day itinerary
- Map shows pins for all venues where `lat`/`lng` are present
- Pin popup: venue name + category
- Venues without coordinates get no pin — no placeholder, no error
- Use OpenStreetMap tiles (no API key): `https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png`
- Map attribution: "© OpenStreetMap contributors"

### `lib/api.ts` changes

Update `resumeBrief` signature:
```typescript
resumeBrief(
  sessionId: string,
  userSelections: string[],
  resumeType: "categories" | "venues"
): Promise<SessionResponse>
```

### `lib/sse.ts` changes

Handle two distinct `hitl_pause` events by reading `node_name`:
- `node_name: "category_select"` → navigate to `/brief/{sessionId}/categories`
- `node_name: "venue_select"` → navigate to `/brief/{sessionId}/select`

Do not close the stream on either pause — keep `EventSource` open. Close only
on terminal completion or error.

### Zustand store additions

```typescript
citySlug: string | null
availableCategories: Category[]
selectedCategories: string[]   // category names
setCitySlug(slug: string)
setAvailableCategories(cats: Category[])
setSelectedCategories(names: string[])
```

### React Router — new route

```
/brief/:sessionId/categories   → CategorySelectionScreen
```

---

## Migration / setup steps (in order)

1. Run new SQL migrations for `city_profiles` and `category_cache` tables
2. Delete zero-vector fixture rows from `local_guide_snippets`
3. Add real Austin seed corpus (even 20-30 rows is enough to demonstrate
   pgvector retrieval working)
4. Add `geopy` to backend dependencies (`uv add geopy`)
5. Add `react-leaflet` and `leaflet` to frontend (`pnpm add react-leaflet leaflet`)
6. Add `@types/leaflet` to frontend dev deps (`pnpm add -D @types/leaflet`)

Steps 4-6 already done. Steps 1-3 required before first run.

---

## Implementation conventions — carry forward unchanged

All conventions from the original spec apply:
- Shared Anthropic client singleton (`@lru_cache` on `_create_anthropic_client`)
- Shared pgvector pool (`get_shared_pool()`, never `create_pool()` in app code)
- `asyncio.to_thread` for all blocking calls — including `geopy.geocode()`
- `configure_logging()` at process startup in `main.py`
- Id-based matching for all LLM batch responses
- Streaming internally for all Anthropic calls (`client.messages.stream`)
- `uv run pytest tests/ -v` from inside `backend/`

---

## Open items / known limitations

- Domain steering for Tavily is a bias, not a filter — results from aggregator
  sites are still possible
- Nominatim geocoding will fail for obscure or newly-opened venues — this is
  acceptable, pins are opportunistic
- City slug normalization is simple string manipulation — edge cases exist for
  cities with the same name in different countries (e.g. "Portland" without a
  state suffix). V1 behavior: normalize as-is, let the user be specific in
  their destination input
- Category cache is per city+category — if the supervisor generates a
  differently-named category for the same concept on a cache miss, it won't
  hit the existing cache entry. Acceptable v1 limitation.
- Maps view is itinerary-only — no map on the selection screen
- Multi-city: still v2, explicitly deferred
- Cross-trip preference memory: still v2
