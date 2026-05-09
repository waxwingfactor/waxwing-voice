"""
Conversation orchestration modules for the voice agent.

Modules:
  state_machine   — LeadCaptureStateMachine: drives the call through phases,
                    tracks captured fields, enforces confirmation gates.
  escalation      — EscalationDetector: inspects caller turns for keywords
                    and conditions that require human handoff.
  confidence      — ConfidenceEvaluator: scores LLM responses for hedging
                    and uncertainty language.
  summary_builder — SummaryBuilder: assembles SaveCallSummaryRequest at end
                    of call from CallState snapshot.
"""
