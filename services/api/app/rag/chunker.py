"""Text chunking for the RAG pipeline.

Chunks are sized to fit comfortably within the context window of the embedding
model (text-embedding-ada-002 max: 8191 tokens). At ~4 chars/token, 1800 chars
is well within that limit while keeping chunks semantically coherent.
"""

_MIN_CHUNK_CHARS = 50


def chunk_text(
    text: str,
    source_label: str,
    chunk_size: int = 1800,
) -> list[dict]:
    """Split text into overlapping chunks, breaking at newline boundaries.

    The splitter tries to break at the last newline within the chunk_size window
    so that sentence/paragraph boundaries are preserved. If no newline exists in
    the window, the chunk is split at exactly chunk_size characters.

    Chunks shorter than _MIN_CHUNK_CHARS (50 chars) are discarded — they are
    typically headings, page numbers, or other noise that would degrade retrieval
    quality without providing signal.

    Args:
        text: Full extracted text to chunk.
        source_label: Human-readable label attached to every chunk (e.g. the
            original filename). Surfaced to the voice agent as a citation.
        chunk_size: Target maximum character count per chunk (default 1800).

    Returns:
        List of dicts with keys:
            chunk_text  (str)  — the chunk body
            source_label (str) — citation label (same for all chunks from one doc)
            page_number  (None) — not tracked at character-split level
    """
    if not text or not text.strip():
        return []

    chunks: list[dict] = []
    start = 0
    length = len(text)

    while start < length:
        end = min(start + chunk_size, length)

        # Try to snap the boundary back to the last newline in this window so we
        # don't cut mid-sentence. Only do this when there is more text remaining.
        if end < length:
            last_newline = text.rfind("\n", start, end)
            if last_newline > start:
                end = last_newline + 1  # include the newline in the preceding chunk

        raw = text[start:end].strip()

        if len(raw) >= _MIN_CHUNK_CHARS:
            chunks.append(
                {
                    "chunk_text": raw,
                    "source_label": source_label,
                    "page_number": None,
                }
            )

        start = end

    return chunks
