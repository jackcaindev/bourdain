# Bourdain backend

## Local setup

Start the local pgvector database, then apply all versioned schema migrations:

```bash
docker compose up -d postgres
uv run alembic upgrade head
```

`DATABASE_URL` overrides the local Compose connection configured in
`alembic.ini`. Alembic converts standard PostgreSQL and asyncpg URLs to its
synchronous psycopg driver; application persistence continues to use asyncpg.
