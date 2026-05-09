"""Embedding utilities wrapping OpenAI's text-embedding-ada-002.

Graceful degradation: if no API key is configured, zero vectors are returned so
the rest of the pipeline continues without crashing. Retrieval will return no
useful results until a real key is set, but the server stays healthy.
"""

_EMBEDDING_DIMENSIONS = 1536
_EMBEDDING_MODEL = "text-embedding-ada-002"


async def embed_texts(texts: list[str], api_key: str) -> list[list[float]]:
    """Embed a batch of texts using OpenAI's text-embedding-ada-002.

    If api_key is empty, returns a list of zero vectors (one per input text) so
    the caller can proceed without a real embedding. This allows the server to
    boot and serve requests in environments where OPENAI_API_KEY is not set.

    Args:
        texts: List of strings to embed. Must be non-empty if api_key is set.
        api_key: OpenAI API key. Pass an empty string to get zero-vector fallback.

    Returns:
        List of float lists, each of length 1536. Order matches the input list.

    Raises:
        openai.OpenAIError: If the API call fails (network error, auth error, etc.).
    """
    if not api_key:
        return [[0.0] * _EMBEDDING_DIMENSIONS for _ in texts]

    import openai  # local import — only resolved when an api_key is provided

    client = openai.AsyncOpenAI(api_key=api_key)
    response = await client.embeddings.create(
        model=_EMBEDDING_MODEL,
        input=texts,
    )
    # Response items are returned in the same order as the input.
    return [item.embedding for item in response.data]
