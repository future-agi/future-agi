import structlog
from channels.db import database_sync_to_async
from django.db import close_old_connections

from accounts.models import Organization

from tfc.ee_stub import _ee_stub

try:
    from ee.agenthub.prompt_generate_agent.prompt_generate import PromptGenerator
except ImportError:
    PromptGenerator = _ee_stub("PromptGenerator")

logger = structlog.get_logger(__name__)
try:
    from ee.usage.models.usage import APICallStatusChoices
except ImportError:
    from tfc.oss_stubs.usage import APICallStatusChoices
try:
    from ee.usage.utils.usage_entries import APICallTypeChoices, count_text_tokens, log_and_deduct_cost_for_api_request
except ImportError:
    from tfc.oss_stubs.usage import APICallTypeChoices
    from tfc.oss_stubs.usage import count_text_tokens
    from tfc.oss_stubs.usage import log_and_deduct_cost_for_api_request


async def improve_prompt_async(
    original_prompt,
    improvement_suggestions,
    examples,
    improve_id,
    organization_id,
    user_id,
    uid,
    workspace,
    ws_manager,
):
    await database_sync_to_async(close_old_connections)()

    try:
        organization = await database_sync_to_async(Organization.objects.get)(
            id=organization_id
        )
    except Organization.DoesNotExist:
        organization = None

    try:
        try:
            from ee.usage.schemas.event_types import BillingEventType
        except ImportError:
            BillingEventType = None
        try:
            from ee.usage.services.metering import check_usage
        except ImportError:
            check_usage = None

        usage_check = await database_sync_to_async(check_usage)(
            str(organization_id), BillingEventType.AI_PROMPT_IMPROVEMENT
        )
        if not usage_check.allowed:
            await ws_manager.send_improve_prompt_error_message(
                improve_id=improve_id,
                error=usage_check.reason or "Usage limit exceeded",
            )
            return

        prompt_generator = PromptGenerator()
        prompt_generator.organization_id = organization_id

        # Create a call_log_row for tracking
        config = {
            "input_tokens": count_text_tokens(
                original_prompt + (improvement_suggestions or "")
            )
        }
        call_log_row = await database_sync_to_async(
            log_and_deduct_cost_for_api_request
        )(
            organization,
            APICallTypeChoices.PROMPT_BENCH.value,
            config=config,
            source="run_prompt_improve",
            workspace=workspace,
        )

        if (
            call_log_row is None
            or call_log_row.status != APICallStatusChoices.PROCESSING.value
        ):
            await ws_manager.send_improve_prompt_error_message(
                improve_id=improve_id,
                error="Insufficient credits",
            )
            return

        # Run the improve_prompt process with WebSocket manager
        # Use async version when ws_manager is provided (WebSocket context)
        await prompt_generator._improve_prompt_async(
            original_prompt=original_prompt,
            improvement_suggestions=improvement_suggestions,
            examples=examples,
            improve_id=improve_id,
            organization_id=organization_id,
            user_id=user_id,
            uid=uid,
            call_log_row=call_log_row,
            ws_manager=ws_manager,
        )

        # Emit cost-based usage event after improvement completes
        try:
            try:
                from ee.usage.schemas.event_types import BillingEventType
            except ImportError:
                BillingEventType = None
            try:
                from ee.usage.schemas.events import UsageEvent
            except ImportError:
                UsageEvent = None
            try:
                from ee.usage.services.config import BillingConfig
            except ImportError:
                BillingConfig = None
            try:
                from ee.usage.services.emitter import emit
            except ImportError:
                emit = None

            actual_cost = 0
            if hasattr(prompt_generator, "llm") and prompt_generator.llm:
                actual_cost = getattr(prompt_generator.llm, "cost", {}).get(
                    "total_cost", 0
                )
            credits = BillingConfig.get().calculate_ai_credits(actual_cost)

            emit(
                UsageEvent(
                    org_id=str(organization_id),
                    event_type=BillingEventType.AI_PROMPT_IMPROVEMENT,
                    amount=credits,
                    properties={
                        "source": "run_prompt_improve",
                        "source_id": str(improve_id),
                        "raw_cost_usd": str(actual_cost),
                    },
                )
            )
        except Exception:
            pass

    except Exception as e:
        logger.exception(f"Error in improve_prompt_async: {e}")
        await ws_manager.send_improve_prompt_error_message(
            improve_id=improve_id, error=str(e)
        )
    finally:
        await database_sync_to_async(close_old_connections)()
