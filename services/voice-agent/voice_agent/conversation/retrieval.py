"""
RetrievalCoordinator — per-turn RAG retrieval for Phase 3.

Decides whether to call search_property_knowledge for a given caller turn,
builds the query, invokes the backend, and returns a populated KnowledgeSlot
ready to inject into the system prompt.

Design rules:
- Pure decision logic lives in should_retrieve() — no I/O.
- do_retrieve() makes the async backend call and returns a KnowledgeSlot.
- Top-K cap: at most 3 chunks injected into the prompt (even if 5 returned).
- Chunk text truncated to CHUNK_MAX_CHARS to bound prompt size.
- Source-metadata trace event emitted via BackendClient.create_call_event()
  with NO chunk text (PII-adjacent).
- When results are empty the knowledge_unavailable flag is set on the slot
  so the prompt builder can instruct Gemini to say "I don't have that info."
- Retrieval is SKIPPED during GREETING and CLOSING phases (latency not worth it).
- Retrieval is SKIPPED for pure confirmation turns (yes/no).

Usage in VoiceSession.handle_caller_turn():

    retrieval = RetrievalCoordinator(client=self._client)
    knowledge_slot = await retrieval.do_retrieve(
        caller_text=caller_text,
        phase=transition.new_phase.value,
        property_id=uuid.UUID(self.state.property_id),
        call_id=self._effective_call_id(),
    )
    # knowledge_slot is never None — may have zero chunks with unavailable flag.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import TYPE_CHECKING

from voice_agent.prompts.system_prompt import KnowledgeSlot

if TYPE_CHECKING:
    from voice_agent.tools.backend_client import BackendClient

log = logging.getLogger("voice_agent.conversation.retrieval")

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

# Maximum chunks to inject into the Gemini prompt.
TOP_K_REQUESTED: int = 5         # ask backend for this many
TOP_K_PROMPT_CAP: int = 3        # inject only the best N into the prompt

# Single-chunk text is truncated to this length to keep prompt size bounded.
CHUNK_MAX_CHARS: int = 400

# Minimum similarity score — chunks below this are silently dropped.
MIN_SIMILARITY_SCORE: float = 0.5

# Maximum characters of caller query logged (truncated to avoid PII leakage).
QUERY_LOG_MAX_CHARS: int = 80

# ---------------------------------------------------------------------------
# Phases that never benefit from retrieval (skip to save latency).
# ---------------------------------------------------------------------------

_SKIP_PHASES: frozenset[str] = frozenset(
    ["greeting", "closing", "ended", "escalation"]
)

# ---------------------------------------------------------------------------
# Information-seeking heuristic
# Caller turns that are information-seeking should trigger retrieval.
# Heuristics (applied after lower-casing):
#   1. Contains "?"
#   2. Starts with a question word
# ---------------------------------------------------------------------------

_QUESTION_STARTS: tuple[str, ...] = (
    "what",
    "where",
    "when",
    "how",
    "why",
    "can",
    "do",
    "is",
    "are",
    "does",
    "will",
    "would",
    "could",
    "should",
    "tell me",
    "i want to know",
    "i'd like to know",
)

# ---------------------------------------------------------------------------
# Pure confirmation turn patterns — retrieval skipped for these.
# ---------------------------------------------------------------------------

_CONFIRMATION_ONLY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s*(yes|yeah|yep|yup|correct|right|sure|ok|okay|confirmed?)\s*[.!]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*(no|nope|nah|wrong|incorrect|cancel)\s*[.!]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*(go ahead|book it|sounds good|do it|perfect|that.?s (right|fine|correct))\s*[.!]?\s*$", re.IGNORECASE),
]


def _is_info_seeking(caller_text: str) -> bool:
    """
    Heuristic: return True if the caller turn looks like an information-seeking
    question that would benefit from a knowledge lookup.

    Checks:
      - Presence of "?" anywhere in the text.
      - First significant word is a question word.

    Does NOT check for PII — the query sent to the backend is the raw
    caller text, which may contain names/addresses. The log truncates it.
    """
    stripped = caller_text.strip()
    if "?" in stripped:
        return True
    lower = stripped.lower()
    for start in _QUESTION_STARTS:
        if lower.startswith(start):
            return True
    return False


def _is_pure_confirmation(caller_text: str) -> bool:
    """Return True for turns that are pure yes/no confirmation responses."""
    for pattern in _CONFIRMATION_ONLY_PATTERNS:
        if pattern.match(caller_text.strip()):
            return True
    return False


class RetrievalCoordinator:
    """
    Orchestrates per-turn knowledge retrieval.

    Stateless: create once per session and call do_retrieve() each turn.
    The BackendClient is injected so it can be mocked in tests.
    """

    def __init__(self, client: "BackendClient") -> None:
        self._client = client

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def should_retrieve(self, caller_text: str, phase: str) -> bool:
        """
        Return True if retrieval should be attempted for this turn.

        Pure logic — no I/O. Called by do_retrieve() but also directly
        in unit tests.

        Rules (checked in order):
          1. Phase is in _SKIP_PHASES -> False (greeting, closing, ended, escalation)
          2. Pure confirmation turn -> False (yes/no handled by state machine)
          3. Information-seeking heuristic -> True/False
        """
        if phase in _SKIP_PHASES:
            return False
        if _is_pure_confirmation(caller_text):
            return False
        return _is_info_seeking(caller_text)

    async def do_retrieve(
        self,
        caller_text: str,
        phase: str,
        property_id: uuid.UUID,
        call_id: uuid.UUID,
    ) -> KnowledgeSlot:
        """
        Decide whether to retrieve, execute retrieval, and return a KnowledgeSlot.

        Always returns a KnowledgeSlot — never raises. On backend failure or
        empty results, the slot has knowledge_unavailable=True and no chunks,
        so the prompt builder instructs Gemini to say "I don't have that info."

        Args:
            caller_text:  Transcribed caller utterance (raw — may have PII).
            phase:        Current ConversationPhase.value (lowercase string).
            property_id:  Scopes the knowledge search to one property.
            call_id:      Used for the CallEvent emitted after retrieval.

        Returns:
            KnowledgeSlot ready for build_system_prompt().
        """
        if not self.should_retrieve(caller_text, phase):
            log.debug(
                "retrieval.skipped",
                extra={
                    "phase": phase,
                    "call_id": str(call_id),
                },
            )
            return KnowledgeSlot()

        # Query is the verbatim caller turn (Phase 3 spec).
        # Future: LLM-rewritten query in Phase 5 tuning.
        query = caller_text.strip()

        try:
            response = await self._client.search_property_knowledge(
                property_id=property_id,
                call_id=call_id,
                query=query,
                top_k=TOP_K_REQUESTED,
            )
        except Exception as exc:
            # Backend failure during RAG: log, return empty slot.
            # Do NOT crash the call — just tell the caller we don't have that info.
            log.warning(
                "retrieval.backend_error",
                extra={
                    "call_id": str(call_id),
                    "error": str(exc)[:200],
                },
            )
            return KnowledgeSlot(knowledge_unavailable=True, query_used=query)

        # Filter by minimum similarity score.
        raw_results = [
            r for r in response.results
            if r.similarity_score >= MIN_SIMILARITY_SCORE
        ]

        # Sort by score descending, cap at TOP_K_PROMPT_CAP.
        raw_results.sort(key=lambda r: r.similarity_score, reverse=True)
        selected = raw_results[:TOP_K_PROMPT_CAP]

        # Build source labels and truncated chunk texts.
        chunks: list[str] = []
        source_labels: list[str] = []
        top_score: float = selected[0].similarity_score if selected else 0.0

        for result in selected:
            label = result.source_label
            if result.page_number is not None:
                label = f"{result.source_label} p.{result.page_number}"
            source_labels.append(label)

            text = result.chunk_text
            if len(text) > CHUNK_MAX_CHARS:
                text = text[:CHUNK_MAX_CHARS] + "..."
            chunks.append(f"[{label}]\n{text}")

        knowledge_unavailable = len(chunks) == 0

        # Emit structured trace event — NO chunk text, NO query content beyond length.
        query_truncated = query[:QUERY_LOG_MAX_CHARS]
        log.info(
            "retrieval.completed",
            extra={
                "call_id": str(call_id),
                "query_len": len(query),
                "query_preview_chars": QUERY_LOG_MAX_CHARS,
                "raw_result_count": len(response.results),
                "filtered_count": len(raw_results),
                "injected_count": len(selected),
                "top_score": round(top_score, 3),
                "source_labels": source_labels,
                "knowledge_unavailable": knowledge_unavailable,
                # query text deliberately truncated — may contain caller words
                "query_truncated": query_truncated,
            },
        )

        # Emit knowledge_retrieved CallEvent for the dashboard.
        # Fire-and-forget: failure here must not crash retrieval.
        try:
            from voice_agent.tools.backend_client import CallEventType
            await self._client.create_call_event(
                call_id=call_id,
                event_type=CallEventType.knowledge_retrieved,
                payload={
                    "query_len": len(query),
                    "result_count": len(selected),
                    "top_score": round(top_score, 3),
                    "source_labels": source_labels,
                    "knowledge_unavailable": knowledge_unavailable,
                    # No chunk text in event payload — PII risk
                },
            )
        except Exception as exc:
            log.warning(
                "retrieval.event_emit_failed",
                extra={"call_id": str(call_id), "error": str(exc)[:200]},
            )

        return KnowledgeSlot(
            chunks=chunks,
            query_used=query,
            knowledge_unavailable=knowledge_unavailable,
        )
