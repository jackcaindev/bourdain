"""OpenAI embeddings wrapper for vector-store ingestion and retrieval."""

from openai import OpenAI, OpenAIError

from app.config import get_settings


EMBEDDING_MODEL = "text-embedding-3-small"


class EmbeddingError(RuntimeError):
    """Base exception for embedding generation failures."""


class EmbeddingAPIError(EmbeddingError):
    """Raised when the OpenAI embeddings API request fails."""


class EmbeddingResponseError(EmbeddingError):
    """Raised when the OpenAI embeddings API returns an unusable response."""


def _create_openai_client() -> OpenAI:
    settings = get_settings()
    return OpenAI(api_key=settings.openai_api_key.get_secret_value())


def create_embeddings(
    texts: list[str],
    *,
    client: OpenAI | None = None,
) -> list[list[float]]:
    """Embed all texts in one batched API request."""

    if not texts:
        return []

    if any(not isinstance(text, str) or not text.strip() for text in texts):
        raise ValueError("texts must contain only non-empty strings")

    openai_client = client or _create_openai_client()

    try:
        response = openai_client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=texts,
        )
    except OpenAIError as exc:
        raise EmbeddingAPIError("OpenAI embeddings request failed.") from exc

    embeddings_by_index: dict[int, list[float]] = {}
    for item in response.data:
        embedding = getattr(item, "embedding", None)
        index = getattr(item, "index", None)
        if not isinstance(index, int) or not isinstance(embedding, list):
            raise EmbeddingResponseError("OpenAI embeddings response was malformed.")
        embeddings_by_index[index] = embedding

    try:
        return [embeddings_by_index[index] for index in range(len(texts))]
    except KeyError as exc:
        raise EmbeddingResponseError(
            "OpenAI embeddings response did not include every input."
        ) from exc
