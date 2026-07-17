# The Bourdain Brief — v2 Spec

Full rebuild superseding `bourdain-brief-spec.md` and `city-profiles-spec.md`
(archived in `docs/archive/`, not deleted — v1's debugging history is real
interview material). This doc is written incrementally as each piece is
shaped; sections not yet designed are marked **OPEN** below and should not
be assumed settled just because they're absent.

Orchestration layer carried forward unchanged from v1: LangGraph
supervisor-worker with Send API fan-out, per-category CRAG grading with a
bounded (`create_agent` + `ToolCallLimitMiddleware`, 3-iteration cap) web
fallback agent, two-stage guardrails, `AsyncPostgresSaver` HITL
checkpointing, SSE streaming. These map directly to the resume bullets and
are a constraint on every decision below, not a starting point up for
debate.

---

## Product flow (for reference — not a spec section, just orientation)

City input (validated/disambiguated via Places) → activity-driver and food
checkboxes drive category derivation → time-block checkboxes (morning/
afternoon/night) set a duration budget the user must fill via category
selection (soft — underfill shortens the day, no hard minimum) → categories
are time/location blocks, single-day, can span multiple blocks in one day,
never multi-day → research fans out in parallel per category → every
candidate must resolve to a real Google Place before grading → CRAG +
scoring + guardrails run on verified candidates only → active cross-day
avoidance of repeated neighborhood/category type → assembly places
categories into day/block/neighborhood with real geo-clustering, meals
attach by proximity → SSE streams category results in as they complete →
draft itinerary, swappable per slot (same-category candidates first, an
on-demand "further research" trigger if exhausted) → per-day confirm → full
itinerary + map once every day is confirmed.

---

## Domain model

Durable, DB-backed records (versioned migrations), replacing v1's
graph-state-only persistence.

### Trip
- `destination_raw: str` — what the user typed
- `destination_place_id: str` — resolved via Places (also the mechanism for
  city disambiguation, e.g. Portland OR vs ME)
- `destination_formatted: str`
- `trip_length_days: int`
- `activity_drivers: list[str]`
- `food_selections: list[str]`
- `time_blocks: list[str]`
- `status: enum` — gathering_categories / researching / reviewing / confirmed
- `session_id: str` — checkpointer thread link
- `created_at`, `updated_at`

### Category
Absorbs what was ephemeral supervisor output in v1 into a real persisted,
cacheable entity (richer version of the existing `category_cache` concept).
- `trip_id: fk`
- `name: str`
- `type: enum` — food / activity
- `source_drivers: list[str]` — which checkbox(es) produced it
- `estimated_duration_minutes: int` — **soft LLM estimate, not a fact.**
  Used only for the time-budget UI; not grounded until real venues attach.
- `neighborhood_scope: str` — **steering text for the research query, not a
  geometric boundary.** Real geo-boundaries don't exist until Places
  resolves actual venues.
- `status: enum` — candidate / selected / stale_replaced
- `day_number: int | None`, `time_block: str | None` — set once assigned

### Place
Google-verified venue. Trip-agnostic and globally reusable/cached by
`google_place_id` — the same restaurant shouldn't re-resolve through Places
every time a different trip's category wants it.
- `google_place_id: str` (unique)
- `name: str`
- `formatted_address: str`
- `lat: float`, `lng: float`
- `google_types: list[str]`
- `resolved_at: datetime`
- **Unresolved candidates are never persisted here at all** — see
  Places verification gate below.

### Evidence
- `place_id: fk`
- `research_run_id: fk`
- `source_type: enum` — vector_store / web_search / places_api
- `raw_content: text`
- `retrieved_at: datetime`

### ResearchRun
- `trip_id: fk`, `category_id: fk`
- `trigger_reason: enum` — initial / crag_fallback / supervisor_replan /
  on_demand (the last is the manual "further research" button — reuses this
  table rather than a new mechanism)
- `iteration: int`
- `status: enum` — running / completed / failed
- `started_at`, `completed_at`

### Recommendation
Persisted `ScoredRecommendation`.
- `trip_id: fk`, `category_id: fk`, `research_run_id: fk`
- `place_id: fk` — **required, not nullable.** Only Places-verified
  candidates ever reach this table.
- CRAG fields unchanged from v1 `schemas.py`: `relevance_score`,
  `authenticity_signal`, `confidence`, `needs_fallback`
- Scoring fields unchanged: `bourdain_score`, `scoring_rationale`,
  `locally_owned_signal`
- Guardrail fields unchanged: `passed_guardrail`, `guardrail_note`

### CategorySelection (HITL 1)
- `trip_id: fk`, `category_id: fk`

### VenueSelection (HITL 2 + swaps)
- `trip_id: fk`, `recommendation_id: fk`, `day_number: int`, `time_block: str`
- Split from a single polymorphic "Selection" table for simpler queries/tests

### Itinerary / ItineraryDay / ItinerarySlot
- `Itinerary`: `trip_id`, `status` (draft/confirmed)
- `ItineraryDay`: `day_number`, `status` (draft/confirmed) — **per-day
  confirm lives here**
- `ItinerarySlot`: `time_block` (incl. meal slots), `recommendation_id`
  (current assignment — swap just overwrites this field)
- **No swap-history table.** Explicit scope cut, same category as cross-trip
  memory — real feature, not a small addition. Revisit if wanted.

### Cross-day dedup
Not a stored entity — computed on demand as a query over the Trip's
existing `VenueSelection` → `Recommendation` → `Place` at category-selection
time.

---

## Category / food derivation

**Activity-driver checkboxes (confirmed):** Culture & History · Outdoors &
Nature · Arts & Music · Nightlife · Shopping & Markets · Local Life &
Offbeat

**Food checkboxes:** Breakfast · Lunch · Dinner · Coffee · Tea

**Time-block budget targets (confirmed, starting point):** Morning 240 min ·
Afternoon 240 min · Night 180 min

**Derivation shape:** one LLM call per *selected* driver (not batched) —
composes directly with the existing Send fan-out rather than introducing a
second dispatch pattern. Each call returns 2-3 candidate categories, giving
the user headroom to fill the block. Food mirrors this exactly: one call per
*checked meal type*, own HITL selection step, own research pass — proximity
attachment to activities is purely an assembly-time concern, not a
substitute for food going through the same pipeline.

**Known v1-style acceptable limitation, more pronounced now:** category
names are more specific ("South Congress Shopping" vs. "Shopping"), so
`category_cache` hit rate drops further than it already did. Not fixing now.

---

## Places verification gate

**Position in pipeline:** inserted between candidate assembly and CRAG
grading in `research_category` — as a reusable `_verify_candidates` step
called at *both* points candidates currently enter grading (the initial
vector-store batch, and again after `web_fallback_agent` extracts venues).
Applies uniformly to vector-store and web-sourced candidates alike, and
**replaces the v1 Nominatim/geopy geocoding path entirely** — one
verification step gets existence-confirmation and real coordinates in a
single call, for every candidate regardless of origin.

**Call shape:**
- Endpoint: Text Search (New), `places:searchText`, top result only
- Query: `"{proposed_name}, {neighborhood_scope}, {city_name}"`
- `locationBias`: destination's resolved coordinates (same Places
  resolution used for city-input validation), to prevent cross-city/
  cross-neighborhood false matches — the exact mechanism behind v1's
  Tampa-in-Naples cache contamination bug
- Field mask: `places.id, places.displayName, places.formattedAddress,
  places.location, places.types` only (Pro tier). Explicitly excludes
  rating/priceLevel/reviews/openingHours — avoids both a popularity signal
  and the Enterprise pricing tier
- Match validation: name-similarity check between proposed name and
  `displayName` on the top result; below threshold, treat as no-match
  rather than trusting position-zero blindly

**Failure handling — no new graph mechanism required.** v1's existing logic
(`fallback_triggered = not candidates or any(item.needs_fallback for item in
graded)`) already handles it: candidates dropped by verification just
shrink the list, which naturally trips the existing bounded
`web_fallback_agent`. `ResearchRun.trigger_reason` does not get a new value
for this — still `crag_fallback` — only `on_demand` (the manual button) is
new.

**Concurrency:** per-candidate concurrent verification via
`asyncio.gather(..., return_exceptions=True)` — same pattern as the
existing `_score_candidates`.

---

## OPEN — not yet designed

- Itinerary engine (geo-clustering, day/block/neighborhood assignment,
  meal-by-proximity, cross-day dedup enforcement)
- Frontend flow (checkboxes, two HITL screens, per-day confirm, swap-list,
  map, streaming category results into the review screen as they complete)
- Quality floor (versioned migrations, integration tests, e2e, eval corpus)
- Migration/setup steps (deferred until the above are settled — don't want
  to write a migration order against a schema that's still moving)
