"""Safe logging shims.

Uses ``structlog`` (codebase convention) so log events flow through
the same observability pipeline as the rest of the agent evaluator.
A logger that raises (handler IO error, processor exception) must
never break the eval defense pipeline — every log call is wrapped
in ``try/except``.
"""

import structlog

logger = structlog.get_logger("ee.evals.llm.agent_evaluator.context")


def safe_warning(event: str, **kwargs) -> None:
    try:
        logger.warning(event, **kwargs)
    except Exception:
        pass


def safe_info(event: str, **kwargs) -> None:
    try:
        logger.info(event, **kwargs)
    except Exception:
        pass
