"""Create the v2 domain schema.

Revision ID: 96239a912e74
Revises:
Create Date: 2026-07-17
"""

from collections.abc import Sequence

from alembic import op


revision: str = "96239a912e74"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "DROP TABLE IF EXISTS city_profiles, category_cache, "
        "local_guide_snippets CASCADE;"
    )
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute(
        """
        CREATE TABLE trips (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            destination_raw TEXT NOT NULL,
            destination_place_id TEXT NOT NULL,
            destination_formatted TEXT NOT NULL,
            trip_length_days INTEGER NOT NULL CHECK (trip_length_days > 0),
            activity_drivers TEXT[] NOT NULL DEFAULT '{}',
            food_selections TEXT[] NOT NULL DEFAULT '{}',
            time_blocks TEXT[] NOT NULL DEFAULT '{}',
            status TEXT NOT NULL CHECK (status IN (
                'gathering_categories', 'researching', 'reviewing', 'confirmed'
            )),
            session_id TEXT NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE TABLE places (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            google_place_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            formatted_address TEXT NOT NULL,
            lat DOUBLE PRECISION NOT NULL,
            lng DOUBLE PRECISION NOT NULL,
            google_types TEXT[] NOT NULL DEFAULT '{}',
            resolved_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE TABLE categories (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            trip_id UUID NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            type TEXT NOT NULL CHECK (type IN ('food', 'activity')),
            source_drivers TEXT[] NOT NULL DEFAULT '{}',
            estimated_duration_minutes INTEGER NOT NULL
                CHECK (estimated_duration_minutes > 0),
            neighborhood_scope TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('candidate', 'selected', 'stale_replaced')
            ),
            day_number INTEGER,
            time_block TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE TABLE research_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            trip_id UUID NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
            category_id UUID NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
            trigger_reason TEXT NOT NULL CHECK (trigger_reason IN (
                'initial', 'crag_fallback', 'supervisor_replan', 'on_demand'
            )),
            iteration INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ
        );
        """
    )
    op.execute(
        """
        CREATE TABLE evidence (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            place_id UUID NOT NULL REFERENCES places(id) ON DELETE CASCADE,
            research_run_id UUID NOT NULL
                REFERENCES research_runs(id) ON DELETE CASCADE,
            source_type TEXT NOT NULL CHECK (
                source_type IN ('vector_store', 'web_search', 'places_api')
            ),
            raw_content TEXT NOT NULL,
            retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE TABLE recommendations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            trip_id UUID NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
            category_id UUID NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
            research_run_id UUID NOT NULL
                REFERENCES research_runs(id) ON DELETE CASCADE,
            place_id UUID NOT NULL REFERENCES places(id) ON DELETE RESTRICT,
            relevance_score DOUBLE PRECISION NOT NULL,
            authenticity_signal TEXT NOT NULL,
            confidence TEXT NOT NULL CHECK (confidence IN ('low', 'medium', 'high')),
            needs_fallback BOOLEAN NOT NULL,
            bourdain_score INTEGER NOT NULL CHECK (bourdain_score BETWEEN 1 AND 5),
            scoring_rationale TEXT NOT NULL,
            locally_owned_signal TEXT,
            passed_guardrail BOOLEAN NOT NULL,
            guardrail_note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE TABLE category_selections (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            trip_id UUID NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
            category_id UUID NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
            UNIQUE (trip_id, category_id)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE venue_selections (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            trip_id UUID NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
            recommendation_id UUID NOT NULL
                REFERENCES recommendations(id) ON DELETE CASCADE,
            day_number INTEGER NOT NULL,
            time_block TEXT NOT NULL
        );
        """
    )
    op.execute(
        """
        CREATE TABLE itineraries (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            trip_id UUID NOT NULL REFERENCES trips(id) ON DELETE CASCADE UNIQUE,
            status TEXT NOT NULL CHECK (status IN ('draft', 'confirmed'))
        );
        """
    )
    op.execute(
        """
        CREATE TABLE itinerary_days (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            itinerary_id UUID NOT NULL REFERENCES itineraries(id) ON DELETE CASCADE,
            day_number INTEGER NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('draft', 'confirmed')),
            UNIQUE (itinerary_id, day_number)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE itinerary_slots (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            itinerary_day_id UUID NOT NULL
                REFERENCES itinerary_days(id) ON DELETE CASCADE,
            time_block TEXT NOT NULL,
            recommendation_id UUID REFERENCES recommendations(id) ON DELETE SET NULL,
            UNIQUE (itinerary_day_id, time_block)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE local_guide_snippets (
            id UUID PRIMARY KEY,
            name TEXT NOT NULL,
            content TEXT NOT NULL,
            category TEXT NOT NULL,
            city_slug TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            embedding vector(1536) NOT NULL
        );
        """
    )
    op.execute(
        "CREATE INDEX ix_local_guide_snippets_city_slug "
        "ON local_guide_snippets (city_slug);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS local_guide_snippets;")
    op.execute("DROP TABLE IF EXISTS itinerary_slots;")
    op.execute("DROP TABLE IF EXISTS itinerary_days;")
    op.execute("DROP TABLE IF EXISTS itineraries;")
    op.execute("DROP TABLE IF EXISTS venue_selections;")
    op.execute("DROP TABLE IF EXISTS category_selections;")
    op.execute("DROP TABLE IF EXISTS recommendations;")
    op.execute("DROP TABLE IF EXISTS evidence;")
    op.execute("DROP TABLE IF EXISTS research_runs;")
    op.execute("DROP TABLE IF EXISTS categories;")
    op.execute("DROP TABLE IF EXISTS places;")
    op.execute("DROP TABLE IF EXISTS trips;")
    op.execute("DROP EXTENSION IF EXISTS vector;")
