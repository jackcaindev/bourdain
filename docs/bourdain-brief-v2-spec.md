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

## Itinerary engine

**Category → time-block affinity.** Gap caught while speccing this: nothing
upstream tags which block(s) an activity category is eligible for, which
would let the assembly algorithm schedule e.g. Nightlife in a morning slot.
Fixed with a static, deterministic driver → eligible-blocks table — a stable
domain fact, not a judgment call, so no LLM involved:

| Driver | Eligible blocks |
|---|---|
| Nightlife | night |
| Arts & Music | afternoon, night |
| Culture & History | morning, afternoon |
| Outdoors & Nature | morning, afternoon |
| Shopping & Markets | morning, afternoon |
| Local Life & Offbeat | morning, afternoon, night |

Food mirrors this: Breakfast→morning, Coffee→morning/afternoon, Lunch→
afternoon, Tea→afternoon, Dinner→night.

`Category` gains one field vs. the domain-model section above:
`eligible_blocks: list[str]`, set at derivation time from this table based
on `source_drivers` — not LLM output.

**Assembly algorithm (activities):**
1. Representative coordinate per category = centroid of its verified
   `Recommendation`s' `Place` coordinates.
2. Greedy bin-pack into `day × eligible_block` slots against the 240/240/180
   targets, `eligible_blocks` as a hard constraint.
3. Tie-break when multiple valid placements exist: prefer a day that
   *already* uses this category's neighborhood_scope (consolidate onto one
   day) over a fresh day, then prefer geographic proximity to what's
   already placed that day. **Corrected wording** — an earlier draft of
   this doc said "prefer a day whose neighborhood hasn't already been
   used," which is backwards. The actual goal, per the original ask, is to
   avoid re-suggesting the same neighborhood on a *different* day (e.g.
   don't send the user back to South Congress on day 2) — which means
   consolidating same-neighborhood categories onto one day when there's
   room, not spreading them out. **Cross-day dedup lives here, at assembly
   time — not at selection time as originally stated.** Categories aren't
   day-assigned until assembly, so a selection-time dedup query (as
   originally speced under "Cross-day dedup" in the domain model section
   above) isn't actually possible; that earlier note is superseded by
   this.
4. A category spanning two blocks in one day (the "significant" case from
   the original vision) is determined by `estimated_duration_minutes`
   exceeding one block's target, and consumes both blocks' budget that day.

**Selection ceiling:** category-selection UI hard-caps at total available
budget (block target × day count, summed) rather than allowing overfill.
Eliminates a "what gets cut" priority-logic case in assembly by preventing
it at input instead. Symmetric with "underfill is fine."

**Meal attachment (after activities are placed):** each checked meal type
resolves to a block that day via the affinity table; attach the nearest
verified meal-candidate to the closest already-placed activity in that
block, by real coordinate distance. Edge cases: no activity in a meal's
mapped block that day → fall back to the closest activity anywhere that
day; no activities placed that day at all → fall back to the trip
destination's centroid rather than failing.

**Swap-list:** same-category `Recommendation`s not currently occupying a
slot. "Further research" (`on_demand` `ResearchRun`) fires only when that
pool is actually exhausted — a checkable condition, not a UI guess.

---

## Frontend flow

**Graph terminates after itinerary assembly — the big call.** Per-day
confirm and swaps are pure deterministic state mutation against already-
computed candidate pools; no LLM, no orchestration need. Keeping a third
`interrupt()` open for that would mean a checkpointed thread sitting
paused indefinitely while the user reviews — real operational risk (an
abandoned thread never resolves) for zero benefit. Assembly produces a
`draft`-status `Itinerary`, the graph run completes normally. Confirm and
swap become plain REST endpoints against round 1's persisted tables,
decoupled from LangGraph entirely. Only "further research" needs the LLM
again, and that's a small targeted call reusing `research_category`'s
verified-candidate logic for one category — not a graph resume.

**New REST surface (beyond existing SSE `/brief` and `/resume`):**
- `POST /trips/{trip_id}/validate-destination`
- `GET /trips/{trip_id}/itinerary`
- `PATCH /trips/{trip_id}/itinerary/days/{day_number}/confirm`
- `PATCH /trips/{trip_id}/itinerary/slots/{slot_id}` — swap; validates the
  new recommendation belongs to the same category and trip before writing
- `POST /trips/{trip_id}/categories/{category_id}/further-research`

**City validation UX:** submit-then-disambiguate, not live Autocomplete.
Autocomplete is a separate, session-billed Places surface from the Text
Search already used for venue verification — adding it means a second
integration pattern for one input field. User submits a destination,
backend resolves via the same Text Search call already speced. One strong
match → proceed into the graph. Genuinely ambiguous (Portland OR vs. ME) →
return candidates, one-tap disambiguation screen before the trip is
created.

**HITL 1 — one combined screen, not two.** Activities and food both need
selection, but a third graph pause to separate them is real graph
complexity for a UX grouping a single screen with two sections handles
fine. One `interrupt()`, resume payload carries both selected activity and
food category ids. Budget-fill computed client-side per block from data
already in `CategoryListPayload` — which needs `estimated_duration_minutes`
and `eligible_blocks` added to it, a richer existing payload, not a new
round-trip.

**HITL 2 shortlist — progressive render, mostly free.** `ScorePayload`
already fires per-recommendation as scoring completes, before the HITL-2
pause — the streaming data exists today, `SelectionScreen.tsx` just isn't
consuming it yet (it only renders from the store's `recommendations`,
populated at the `HitlPayload` pause). Fix: Zustand accumulates
`ScorePayload` events into a running list as they arrive; screen renders
from that, populating category-by-category. Submission stays gated on the
HITL-2 pause firing (all categories done), not allowing partial submit —
the perceived-latency win was never about early submission, just not
staring at a blank screen. **Map:** itinerary-view only, not on this
screen — see note above.

**Dead code from round 1, to clean up when `research.py` is next touched:**
`category_cache` was dropped in the migration, so `CacheHitPayload` and the
cache-check branch in `research_category` now reference a table that
doesn't exist. Expected — round 1 was persistence-only — but needs to
actually get removed, not just left dangling.

---

## Quality floor

**Test pyramid — four tiers, mocked boundary moves progressively outward:**

1. **Unit** (existing convention, round 1's `db/` tests already this tier)
   — individual functions, real Postgres for db-layer tests, direct
   `AsyncMock` patches elsewhere.
2. **Integration (new)** — wires multiple real modules together, mocks only
   the actual external network boundary (Anthropic, OpenAI embeddings,
   Tavily, Google Places). E.g. a full `research_category` run against
   fixture API responses, asserting a real `Recommendation` lands in
   Postgres correctly linked to a real `Place`. This is the tier that
   proves the Places-gate-before-grading reordering actually works
   end-to-end, not just in isolation.
3. **E2E (new)** — `httpx.AsyncClient` against the running FastAPI app +
   real Postgres, external APIs mocked at the network boundary. Full
   journey: start brief → SSE stream → HITL-1 resume → HITL-2 resume →
   itinerary assembled (graph terminates) → the new REST endpoints
   (confirm, swap, further-research) exercised against the persisted
   result. Validates the graph-terminates-after-assembly decision as a
   full user journey.
4. **Live smoke (optional, not part of default `pytest`)** — real
   Anthropic/OpenAI/Tavily/Places calls for one real city, run manually or
   behind an env flag. Catches real-API drift mocked fixtures can't catch
   by construction. Useful for interview-day confidence, not for every
   test run.

**Eval corpus — richer than v1's, since more is now deterministic and
checkable.** Same 5-8 hand-picked destinations (touristy-known +
lesser-known mix), still separate from CRAG's per-query grading, plus two
new checks v1 had no way to measure at all:
- Places resolution rate per category (proposed vs. verified) — a proxy
  for hallucination rate
- Itinerary structural validity: every checked time-block filled within
  tolerance, no cross-day neighborhood repeats, every checked meal type
  attached

**Explicit scope cut:** no CI/CD. Same call already made on ap-workflow —
optional for a solo portfolio project, cheaper to name as a known gap in
the interview than to build for an audience of one.

**Dependency cleanup falling out of this round:** `geopy` removed (Places
replaces Nominatim entirely). Places API calls go through raw `httpx` (new
dependency), not the `googlemaps` SDK — matches the existing lightweight-
wrapper convention (`vector_store.py`, `web_search.py`) over pulling in a
heavy client library for a straightforward REST + field-mask-header API.

---

## Spec status

All six sections from the original shaping sequence are now settled:
domain model, category/food derivation, Places verification gate,
itinerary engine, frontend flow, quality floor. Round 1 (persistence layer)
is implemented and awaiting verification. Remaining Codex rounds — research
pipeline reorder + Places gate, itinerary engine, REST/frontend layer,
integration/e2e/eval — get written and verified one at a time against this
doc, per-round, not batched.
