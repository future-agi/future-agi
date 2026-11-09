"""Generation-owned model boundary for the allowlisted trace tools.

The guard carries the worker's witness, so an auxiliary call is neither a new
budget nor a new generation. Absence of that binding is a refusal, not permission
to use the foreground transport. This module is never imported by foreground
execution. Failures remain sticky even if a tool dispatcher catches exceptions.
"""


def chauffeur_content(messages, guard):
    from tracer.services.analysis_result_completion import (
        NORMAL_FINISHES,
        IncompleteAnalysis,
    )

    witness = getattr(guard, "completion_witness", None)
    if witness is None:
        raise IncompleteAnalysis()
    try:
        from agentic_eval.core.utils.model_config import ModelConfigs
        from ee.agenthub.traceerroragent.background_llm import BackgroundLLM

        guard.check()
        config = ModelConfigs.HAIKU_4_5_BEDROCK_ARN
        client = BackgroundLLM(
            witness=witness,
            provider=config.provider,
            model_name=config.model_name,
            temperature=0.2,
            max_tokens=16000,
        )
        response = client._get_completion_with_tools(
            messages, tools=[], timeout_ms=guard.remaining * 1000
        )
        guard.check()
        content = response.choices[0].message.content
        if client.last_finish_reason not in NORMAL_FINISHES or not content:
            raise IncompleteAnalysis()
        return content
    except BaseException:
        witness.failed = True
        raise


def reject_chauffeur(guard):
    """Sticky refusal for any degraded allowed tool (name retained for callers)."""
    from tracer.services.analysis_result_completion import IncompleteAnalysis

    witness = getattr(guard, "completion_witness", None)
    if witness is not None:
        witness.failed = True
        witness.fallback_used = True
    guard.check()  # retain an already-aborted scope's typed refusal
    raise IncompleteAnalysis()
