# The Bourdain Brief — Build Spec

## Product
Single-city travel research tool. User enters a destination + trip length.
System researches authentic, non-touristy, locally-flavored recommendations
(not verified ownership — informed judgment from richer signal than star
ratings), user selects which recommendations they want via a HITL step,
system deterministically assembles a day-by-day itinerary from the
selections. Multi-city is out of scope for v1 (future: concatenate
single-city passes).

## Repo layout
Backend is a self-contained project — its own `pyproject.toml`, `uv.lock`,
and `.env` live inside `backend/`, not at the repo root. Frontend is an
equal sibling with its own `package.json`. Root holds no project files of
its own (just `backend/`, `frontend/`, `README.md`, `.gitignore`). Imports
inside backend code use `app.x`, not `backend.app.x`, since `uv run`
executes from within `backend/`.

```
bourdain/
  backend/
    pyproject.toml
    uv.lock
    .env / .env.example
    app/
      ...
  frontend/
    package.json
    ...
```


## Stack

- Backend: Python, FastAPI, LangGraph, Anthropic SDK, PostgreSQL + pgvector
  (vector store — consolidated onto the same Postgres instance already
  used for accounts/trips and the LangGraph checkpointer; avoids adding
  Chroma's native-dependency fragility — hit real Python 3.14 build
  breakage — for no added benefit at this corpus size), OpenAI
  (`text-embedding-3-small` embeddings), Tavily (web
  search fallback), PostgreSQL + asyncpg (accounts/trips + LangGraph
  checkpointer)
- Backend package management: `uv` (not pip/poetry), run from within
  `backend/` — `uv init`, `uv add`, `uv.lock` committed inside `backend/`,
  `uv run` for local execution
- Frontend: React, Vite, TypeScript, Tailwind
- Frontend package management: `pnpm` (not npm/yarn) — `pnpm-lock.yaml`
  committed
- Transport: Server-Sent Events (SSE) for streaming graph progress —
  one-directional, no client→server mid-stream need, native browser
  reconnect via `EventSource`
- Deploy: Docker Compose, even for local dev

## State schema

```python
class BriefState(TypedDict):
    destination: str
    trip_length_days: int
    categories: list[Category]
    candidates: Annotated[list[Candidate], operator.add]  # Send API reducer target
    graded_candidates: list[GradedCandidate]
    scored_recommendations: list[ScoredRecommendation]
    user_selections: list[str] | None   # candidate ids user kept, set on resume
    itinerary: list[ItineraryDay] | None
    research_iteration: int  # starts at 0; hard cap enforced in supervisor_replan_check,
                              # incremented on the one allowed revision pass, never exceeds 1
```

## Pydantic schemas

```python
class Category(BaseModel):
    name: str
    rationale: str  # supervisor's reasoning for choosing this category for this destination

class Candidate(BaseModel):
    id: str  # stable identifier (uuid or slug) — user_selections matches on this, not name
    name: str
    category: str
    description: str
    source: Literal["vector_store", "web_search"]
    source_url: str | None = None
    raw_signal: str  # snippet/context passed to the grader

class GradedCandidate(Candidate):
    relevance_score: float          # 0-1, informational — NOT the fallback trigger
    authenticity_signal: str        # grader's free-text reasoning
    confidence: Literal["low", "medium", "high"]
    needs_fallback: bool            # grader's direct judgment call, not a thresholded score

class ScoredRecommendation(GradedCandidate):
    bourdain_score: int              # 1-5
    scoring_rationale: str
    locally_owned_signal: str | None = None   # opportunistic, never verified/claimed as fact
    passed_guardrail: bool
    guardrail_note: str | None = None          # populated when flagged, still shown to user

class ItineraryDay(BaseModel):
    day_number: int
    neighborhood_focus: str | None  # v1: soft label derived from that day's dominant
                                     # category name, NOT real geographic clustering —
                                     # no location/neighborhood field exists anywhere in
                                     # the pipeline (Candidate, pgvector schema, retrieval),
                                     # so true proximity-based grouping is out of scope for
                                     # v1 and explicitly deferred, same as multi-city
    breakfast: ScoredRecommendation | None
    lunch: ScoredRecommendation | None
    dinner: ScoredRecommendation | None
    activities: list[ScoredRecommendation]     # enforce 1-2 max at assembly time

# SSE event envelope — discriminated union payload, no untyped dicts
class CandidatePayload(BaseModel):
    category: str
    candidates_found: int

class ScorePayload(BaseModel):
    recommendation: ScoredRecommendation

class FallbackPayload(BaseModel):
    category: str
    reason: str

class ErrorPayload(BaseModel):
    node_name: str
    detail: str

class SSEEvent(BaseModel):
    event_type: Literal["node_start", "node_progress", "node_complete", "hitl_pause", "error"]
    node_name: str
    message: str  # human-readable, rendered directly in the UI progress feed
    payload: CandidatePayload | ScorePayload | FallbackPayload | ErrorPayload | None = None
```

## Graph

```
START
  → supervisor
      LLM call: destination + trip_length → list[Category]
      This is the one genuine routing/judgment moment in the graph —
      category choice must differ meaningfully by destination type
      (beach town vs. dense city vs. rural region), not a fixed list.

  → Send fan-out: [Send("research_category", {"category": c}) for c in categories]
      Runs concurrently. Each branch is isolated state that merges back
      via the `candidates: Annotated[list, operator.add]` reducer — no
      manual merge logic, LangGraph handles it.

      research_category(category):
        → pgvector_retrieve (vector search against curated local-guide corpus,
            same Postgres instance as accounts/checkpointer)
        → crag_grade (LLM):
            outputs GradedCandidate fields directly, including
            `needs_fallback: bool` reasoned in-context — NOT a numeric
            threshold applied externally in Python. The judgment stays
            inside the judgment-making step.
        → conditional edge: if needs_fallback → web_fallback_agent → crag_grade again
          else → done

          web_fallback_agent (GENUINELY AGENTIC — the one place the model
          directs its own process, not fixed control flow):
            Built with `create_agent` + a bound Tavily search tool, NOT a
            single fixed query like the earlier draft. The model decides
            its own query, evaluates what came back, decides whether it's
            sufficient evidence or whether to refine the query and search
            again — bounded to a max of 3 search iterations (hard cap,
            enforced in code, not trusted to the model's self-restraint).
            This is the correct use of `create_agent`/middleware in this
            project: an actual loop where the model chooses tool calls and
            when to stop, unlike every other node, which is one bounded
            forced-tool call inside code-defined control flow.

  → reduce (implicit, via operator.add merge into `candidates`)

  → supervisor_replan_check (GENUINELY AGENTIC — bounded to ONE iteration,
      hard-capped in code via `research_iteration: int` in BriefState,
      checked and incremented before this node can run again)
      Distinct judgment from CRAG's fallback: CRAG asks "is this evidence
      good," this node asks "was this category selection itself wrong for
      this destination" (e.g. a category came back thin even after web
      fallback, suggesting the category choice was a bad fit, not just
      under-evidenced) or "did the categories collectively miss the
      destination's character." If the supervisor decides to revise:
      - Only flagged/replaced categories are re-dispatched through Send
        fan-out (surgical, not a full re-run) — categories already settled
        keep their existing candidates untouched, no redundant work.
      - After this one revision pass (or immediately, if no revision is
        needed), proceeds unconditionally to score_node regardless of
        confidence — the cap is absolute, not a soft suggestion.

  → score_node (LLM)
      Applies Bourdain rubric (1-5) per graded candidate. Produces
      ScoredRecommendation. `locally_owned_signal` is populated only when
      found opportunistically — never asserted as verified fact.

  → guardrail_node
      Deterministic + lightweight LLM check: flags recommendations with no
      supporting evidence in raw_signal/source, or scores that don't match
      their rationale. Sets `passed_guardrail` / `guardrail_note`.
      Flagged items are NOT dropped — they flow through to the frontend
      transparently (e.g. "found but couldn't verify") rather than
      vanishing silently.

  → interrupt()  [HITL checkpoint]
      Full state snapshot persists via AsyncPostgresSaver, keyed on
      thread_id = session id. This is a genuine human decision point:
      user reviews ranked/scored candidates and selects which to keep.
      Frontend POSTs selections → backend calls graph with
      Command(resume=selections) → execution continues.

  → assemble_itinerary
      Deterministic, NOT an LLM call. Distributes user's selected
      recommendations evenly across trip_length_days and meal/activity
      slots. This is bucketing/sorting, not a judgment call — spending an
      LLM call here would be reaching for the pattern, not the requirement.

      Meal vs. activity classification: keyword heuristic against
      category.name (contains "food"/"restaurant"/"market"/"bar"/"café"/
      etc. → meal slot; otherwise → activity slot). Documented as a known
      v1 imprecision, not presented as fully solved — e.g. a "night
      market" category is arguably food but could match either bucket.

      Neighborhood grouping: NOT real geographic clustering — no
      location/neighborhood field exists anywhere in the pipeline.
      neighborhood_focus is a soft label (that day's dominant category
      name), explicitly deferred to v2 alongside multi-city and real
      geo data, not silently faked as if it were location-aware.

  → END
```

## Guardrails, memory, eval — explicit, not implicit

- **Guardrail**: `guardrail_node` above — catches ungrounded output
  (hallucinated candidates, unsupported scores), not just malformed JSON.
  Pydantic validation is type safety; this node is the actual guardrail.
- **Memory**: AsyncPostgresSaver checkpointer, scoped to session-resume
  (user closes tab mid-HITL-review, comes back, state is intact). Not
  cross-trip preference memory — that's a real feature but explicit v2 scope.
- **Eval**: separate offline script (`tests/eval/run_eval.py`), NOT the CRAG
  grader repurposed. 5-8 hand-picked destinations (mix of touristy-but-known
  and lesser-known cities), sanity-checks on category relevance and score
  distribution. Distinct from CRAG's per-query retrieval grading — this
  checks system output quality over time.

## Production floor (every node, every service)
- Pydantic validation at every boundary (API in/out, node in/out, LLM
  structured output)
- Real error handling — LLM call failures, Postgres/Tavily timeouts, malformed
  structured output — not happy-path only
- Env-var config via pydantic-settings, no hardcoded secrets/URLs
- Structured logging (not print statements) — one entry per node
  start/complete, errors with context
- Separated module structure per the layout below
- Real tests on core logic: scoring, guardrail, itinerary assembly
  (deterministic logic first — easiest to test meaningfully)
- Docker Compose for local dev: FastAPI app, Postgres (with pgvector
  extension enabled — one container serves app data, checkpointer, and
  vector store)
- Dockerfile installs deps via `uv sync --frozen`, not pip — keeps the
  lockfile as the single source of truth for reproducible builds

## Module structure

```
backend/
  app/
    main.py                  # FastAPI app, lifespan, router mounting
    config.py                 # pydantic-settings
    logging_config.py
    graph/
      state.py
      supervisor.py
      research.py             # retrieve → grade → fallback subgraph
      scoring.py
      guardrails.py
      itinerary.py
      build.py                 # graph wiring, compile, AsyncPostgresSaver
    services/
      vector_store.py          # pgvector wrapper (asyncpg, same Postgres instance)
      embeddings.py              # OpenAI embeddings wrapper
      web_search.py               # Tavily wrapper
      llm.py                        # Anthropic SDK wrapper
    models/
      schemas.py                # all Pydantic types above
    api/
      routes.py                  # /brief (start, SSE stream), /resume
    db/
      models.py                    # accounts, saved trips
      session.py
  tests/
    test_scoring.py
    test_guardrails.py
    test_itinerary.py
    eval/
      run_eval.py
  Dockerfile
  docker-compose.yml
  .env.example

frontend/
  src/
    features/
      kickoff/         # destination + trip length form
      progress/          # SSE-driven live progress feed
      selection/           # HITL candidate review/select screen
      itinerary/             # final day-by-day view
    lib/
      sse.ts             # EventSource wrapper, typed event handling
      api.ts
    App.tsx
```

## Implementation conventions established during the build
(Not in the original spec — learned from real debugging, apply
consistently to any new code.)

- **Package layout**: `src/` layout, not flat — `backend/src/app/`, with
  `backend/pyproject.toml` declaring `packages = ["src/app"]` under
  `[tool.hatch.build.targets.wheel]`. Imports are `from app.x import y`
  (no `backend.` or `src.` prefix). Run `uv sync` from inside `backend/`.
- **All Anthropic calls go through `services/llm.py`'s `call_forced_tool`**,
  which: uses a **cached singleton client** (`@lru_cache` on
  `_create_anthropic_client`, matching `config.py`'s `get_settings()`
  pattern) — never construct a fresh `Anthropic()` client per call; uses
  **streaming internally** (`client.messages.stream(...)` +
  `get_final_message()`, not `.create()`) — non-streaming calls are prone
  to read timeouts on longer structured-output responses per Anthropic's
  own docs; checks `response.stop_reason == "max_tokens"` and raises
  `LLMTruncatedResponseError` explicitly — never let a truncated response
  silently produce a malformed/incomplete dict downstream.
- **All pgvector access goes through `services/vector_store.py`'s
  `get_shared_pool()`** (async-safe double-checked-locking singleton),
  not `create_pool()` directly in application code — a fresh pool per
  call caused a real observed hang under Send API fan-out concurrency.
  `close_shared_pool()` must be called once at graph/process shutdown
  (wired into `build_graph`'s context manager exit).
- **Any blocking/sync call made from inside an `async def` must be
  wrapped in `asyncio.to_thread`** — this bit the codebase three times
  independently (Tavily's `search_web`, `_grade_candidates`,
  `create_embeddings`) before becoming a checked habit. When adding any
  new node, check every call inside it for this.
- **`configure_logging()` (from `app.logging_config`) must be called at
  process startup** — without it, Python's root logger defaults to
  WARNING-only and every `logger.info(...)` call across the whole
  codebase (which is most of the structured logging this project relies
  on) silently never fires. This was missing for the entire build until
  caught late; it MUST be called in whatever wires up the real FastAPI
  app (`main.py`, not yet built), not just in test scripts.
- **Id-based matching, never positional, for any LLM batch response**
  that must map back to input items — established first in CRAG grading,
  then guardrails, applied consistently. Always validate:
  unknown-id-returned, duplicate-id-returned, and missing-id (never
  trust length-match alone, e.g. `zip(..., strict=True)`).
- **`reduce_graded_candidates` deduplicates by candidate `id`**, keeping
  first occurrence — a candidate matched by pgvector under multiple
  categories keeps only its first-seen category label (deliberate v1
  simplification, not a bug).
- **`pytest` is a dev-only dependency** (`uv add --dev pytest`, lands
  under `[dependency-groups] dev`, not `[project.dependencies]`). Run
  with `uv run pytest tests/ -v` from inside `backend/`.

### Still open, not yet resolved
- **Guardrail stage-2 batch-size scaling**: `guardrail_stage_2_incomplete`
  has fired on every full real run so far (batch sizes ~90+ candidates,
  all triggered by the zero-vector fixture data forcing every category
  into web fallback). Dedup may reduce this meaningfully; not yet
  re-verified after the dedup fix landed. If it persists at realistic
  (non-fixture) scale, the guardrail's single-batch design may need
  chunking.
- **Real seed corpus not yet built** — all testing so far has used 5
  hand-seeded fixture rows with zero-vector embeddings (meaningless
  pgvector similarity, forces every category into fallback every time).
  This was deliberate (isolating graph-logic correctness from corpus
  quality), but real ingestion is now the natural next step, and will
  likely reduce both latency (fewer fallback triggers) and guardrail
  batch size at once.

## Open items intentionally deferred (say so if asked, don't hide it)
- Auth: v1 uses an anonymous session id (client-stored), tied to Postgres
  rows for saved trips — no real login/password flow yet
- Multi-city: v1 is single-city only, concatenation is v2
- Cross-trip preference memory: v2, not v1
- Real neighborhood/geographic clustering: no location field exists in
  the pipeline (Candidate, pgvector schema, retrieval never captured
  it); itinerary assembly distributes evenly instead of by proximity,
  neighborhood_focus is a soft category-derived label, not real geo data
