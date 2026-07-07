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
from tfc.billing.boundary import get_billing, BillingEventType
from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices


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
        billing = get_billing()
        usage_check = await database_sync_to_async(billing.check_usage)(
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
            "input_tokens": billing.count_tokens(
                original_prompt + (improvement_suggestions or "")
            )
        }
        call_log_row = await database_sync_to_async(billing.log_and_deduct)(
            organization=organization,
            api_call_type=APICallTypeChoices.PROMPT_BENCH.value,
            config=config,
            source="run_prompt_improve",
            workspace=workspace,
        )

        if (
            call_log_row is not None
            and call_log_row.status != APICallStatusChoices.PROCESSING.value
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
            actual_cost = 0
            if hasattr(prompt_generator, "llm") and prompt_generator.llm:
                actual_cost = getattr(prompt_generator.llm, "cost", {}).get(
                    "total_cost", 0
                )
            credits = billing.ai_credits(actual_cost)
            billing.record_usage(
                str(organization_id),
                BillingEventType.AI_PROMPT_IMPROVEMENT,
                amount=credits,
                source="run_prompt_improve",
                source_id=str(improve_id),
                raw_cost_usd=str(actual_cost),
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
