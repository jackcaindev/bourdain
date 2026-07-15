CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS local_guide_snippets (
    id uuid PRIMARY KEY,
    name text NOT NULL,
    content text NOT NULL,
    category text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(1536) NOT NULL
);
