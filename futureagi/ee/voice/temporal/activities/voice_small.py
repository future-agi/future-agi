"""
Voice-specific small queue activities (tasks_s).

Extracted from simulate.temporal.activities.small — contains phone number
acquisition/release, call preparation, and phone-number dispatcher activities.

All activities use async functions with Django's async ORM for non-blocking operations.

IMPORTANT: Each activity calls _close_old_connections() at the start to prevent
connection pool exhaustion when using PgBouncer. Without this, connections
accumulate and hit PgBouncer's pool limit (~20 by default).
"""

import json

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import close_old_connections
from temporalio import activity

from simulate.models.test_execution import CallExecution
from simulate.temporal.types.activities import (
    AcquireAndSignalPhoneNumbersBatchInput,
    AcquireAndSignalPhoneNumbersBatchOutput,
    AcquirePhoneInput,
    AcquirePhoneOutput,
    PrepareCallInput,
    PrepareCallOutput,
    ReleasePhoneInput,
    ReleasePhoneNumberSlotInput,
    RequestPhoneNumberInput,
)


@activity.defn(name="acquire_phone_number")
async def acquire_phone_number(input: AcquirePhoneInput) -> AcquirePhoneOutput:
    """
    Acquire a phone number from the pool for a call.

    For outbound calls, continuously polls until a phone becomes available.
    Uses heartbeats to keep the activity alive during long waits.
    For inbound calls, returns success without acquiring a phone.

    Timeout: Relies on heartbeat_timeout (not start_to_close_timeout)
    Queue: tasks_s
    """
    import asyncio
    import time

    from tfc.temporal.common.heartbeat import Heartbeater

    # Release stale DB connections to prevent PgBouncer pool exhaustion
    close_old_connections()

    # Inbound calls don't need phone number acquisition
    if input.call_direction == "inbound":
        return AcquirePhoneOutput(
            success=True,
            phone_id=None,
            phone_number=None,
        )

    activity.logger.info(
        f"Acquiring phone number for call_id={input.call_id}, "
        f"direction={input.call_direction}, "
        f"poll_interval={input.poll_interval_seconds}s"
    )

    from ee.voice.services.phone_number_service import PhoneNumberService

    # Fetch CallExecution instance once (outside the loop)
    call_execution = await CallExecution.objects.aget(id=input.call_id)

    start_time = time.time()
    attempt = 0

    # Use Heartbeater to send background heartbeats during polling
    # factor=4 with 1-minute heartbeat_timeout = heartbeat every 15 seconds
    async with Heartbeater(factor=4) as heartbeater:
        while True:
            attempt += 1
            elapsed = time.time() - start_time

            # Update heartbeat details for debugging
            heartbeater.details = (
                f"waiting for phone {input.call_id}, attempt={attempt}, elapsed={elapsed:.0f}s",
            )

            try:
                # Try to acquire a phone number (service uses sync ORM with transactions)
                phone_number = await sync_to_async(
                    PhoneNumberService.acquire_phone_number
                )(
                    call_direction=input.call_direction,
                    call_execution=call_execution,
                )

                if phone_number:
                    activity.logger.info(
                        f"Acquired phone number {phone_number.phone_number} "
                        f"(provider_id={phone_number.provider_phone_id}) "
                        f"for call_id={input.call_id} after {attempt} attempts ({elapsed:.0f}s)"
                    )

                    return AcquirePhoneOutput(
                        success=True,
                        phone_id=str(phone_number.id),
                        phone_number=phone_number.phone_number,
                        provider_phone_id=phone_number.provider_phone_id,
                    )

                # No phone available, log and wait before retrying
                activity.logger.debug(
                    f"No phone available for call_id={input.call_id}, "
                    f"attempt={attempt}, elapsed={elapsed:.0f}s, retrying..."
                )

            except Exception as e:
                # Log error but continue polling (transient errors shouldn't fail acquisition)
                activity.logger.warning(
                    f"Error acquiring phone for call_id={input.call_id}: {str(e)}, retrying..."
                )

            # Wait before next attempt
            await asyncio.sleep(input.poll_interval_seconds)


@activity.defn(name="release_phone_number")
async def release_phone_number(input: ReleasePhoneInput) -> None:
    """
    Release a phone number back to the pool.

    Marks the phone number as available after call completes.

    Timeout: 30 seconds
    Queue: tasks_s
    """
    # Release stale DB connections to prevent PgBouncer pool exhaustion
    close_old_connections()

    try:
        activity.logger.info(f"Releasing phone number: {input.phone_id}")

        from ee.voice.services.phone_number_service import PhoneNumberService

        # Release phone number (service uses sync ORM with transactions)
        await sync_to_async(PhoneNumberService.release_phone_number)(input.phone_id)

        activity.logger.info(f"Released phone number: {input.phone_id}")

    except Exception as e:
        activity.logger.exception(f"Failed to release phone number: {str(e)}")
        raise


@activity.defn(name="prepare_call")
async def prepare_call(input: PrepareCallInput) -> PrepareCallOutput:
    """
    Prepare call execution by loading all necessary data from database.

    Fetches:
    - CallExecution with scenario, agent_version, test_execution
    - System prompt with persona formatting
    - Voice settings from call_metadata
    - Client credentials (if outbound with own provider)
    - Builds FAGICallData dict for call initiation

    Validates workspace consistency for multi-tenancy isolation.

    Timeout: 2 minutes
    Queue: tasks_s
    """
    # Release stale DB connections to prevent PgBouncer pool exhaustion
    close_old_connections()

    try:
        activity.logger.info(
            f"Preparing call: call_id={input.call_id}, workspace_id={input.workspace_id}"
        )

        from ee.voice.semantics import FAGICallData
        from simulate.semantics import CallExecutionStatus, CallType
        from tracer.models.observability_provider import ProviderChoices

        # Fetch call execution with all related data
        call = await CallExecution.objects.select_related(
            "test_execution__run_test__workspace",
            "test_execution__run_test__organization",
            "test_execution__agent_definition",
            "scenario__workspace",
            "scenario__simulator_agent",
            "agent_version",
            "agent_version__agent_definition",
            "agent_version__credentials",
        ).aget(id=input.call_id)

        # Validate workspace consistency (multi-tenancy isolation)
        # Only validate if the RunTest has a workspace assigned.
        # Workspace is optional on RunTest - if None, skip validation.
        workspace = call.test_execution.run_test.workspace
        if workspace:
            if str(workspace.id) != input.workspace_id:
                error_msg = (
                    f"Workspace mismatch for call {input.call_id}: "
                    f"expected {input.workspace_id}, got {workspace.id}"
                )
                activity.logger.error(error_msg)
                return PrepareCallOutput(
                    is_outbound=False,
                    error=error_msg,
                )
        # If workspace is None, skip workspace validation (workspace is optional for RunTest)

        # Also validate scenario workspace consistency (scenario workspace is also optional)
        # Only validate if the scenario has a workspace assigned.
        scenario_workspace = call.scenario.workspace if call.scenario else None
        if scenario_workspace:
            if str(scenario_workspace.id) != input.workspace_id:
                error_msg = (
                    f"Scenario workspace mismatch for call {input.call_id}: "
                    f"expected {input.workspace_id}, got {scenario_workspace.id}"
                )
                activity.logger.error(error_msg)
                return PrepareCallOutput(
                    is_outbound=False,
                    error=error_msg,
                )
        # If scenario workspace is None, skip scenario workspace validation

        # Determine call direction from call_metadata
        call_metadata = call.call_metadata or {}
        is_outbound = call_metadata.get("call_direction") == "outbound"

        # Ensure agent_definition_id is in metadata (required by create_phone_call for scenario_flow)
        agent_definition = call.test_execution.agent_definition
        if agent_definition and "agent_definition_id" not in call_metadata:
            call_metadata["agent_definition_id"] = str(agent_definition.id)

        # Get configuration snapshot from agent version
        snapshot = (
            call.agent_version.configuration_snapshot if call.agent_version else {}
        )
        # For inbound: destination is the user's phone from the snapshot.
        # For outbound: destination is the system phone acquired later by the
        # workflow's phone dispatcher — not known at prepare_call time.
        to_number = snapshot.get("contact_number") if not is_outbound else ""

        # Build system prompt with dynamic prompt generation
        system_prompt = ""
        if call.scenario and call.scenario.simulator_agent:
            from ee.voice.utils.prompt_builder import generate_dynamic_prompt

            base_prompt = call.scenario.simulator_agent.prompt
            row_data = call_metadata.get("row_data", {})

            # Generate dynamic prompt with persona formatting and variable substitution
            if row_data:
                system_prompt = generate_dynamic_prompt(
                    prompt_template=base_prompt,
                    row_data=row_data,
                    agent_version=call.agent_version,
                    call_type=call_metadata.get("call_direction", "inbound"),
                )
                activity.logger.info(
                    f"Generated dynamic prompt for call {input.call_id}"
                )
            else:
                # No row data, use base prompt directly
                system_prompt = base_prompt
                activity.logger.info(
                    f"Using base prompt (no row data) for call {input.call_id}"
                )

        # Extract voice settings from call_metadata
        voice_settings = call_metadata.get("voice_settings", {})
        # Ensure max_call_duration_in_minutes has a default value (15 minutes)
        voice_settings.setdefault("max_call_duration_in_minutes", 15)

        # Build FAGICallData for call initiation
        from ee.voice.utils.system_provider import resolve_system_voice_provider

        # Extract client provider credentials from ProviderCredentials
        # via the agent_version (configuration_snapshot no longer stores
        # credential fields since the ProviderCredentials refactor).
        from simulate.services.agent_definition import resolve_api_key_for_version

        agent_version = call.agent_version
        client_api_key = (
            await sync_to_async(resolve_api_key_for_version)(agent_version)
            if agent_version
            else None
        )
        client_assistant_id = (
            snapshot.get("assistant_id") or None
        )  # Convert empty string to None
        client_phone_number = (
            snapshot.get("contact_number") or None
        )  # Convert empty string to None
        client_uses_own_provider = bool(client_api_key)
        client_provider = snapshot.get("provider", "vapi")

        system_provider = resolve_system_voice_provider(client_provider)

        call_data_dict = {
            "call_id": str(call.id),
            "call_type": CallType.OUTBOUND if is_outbound else CallType.INBOUND,
            "status": CallExecutionStatus.PENDING,
            "assistant_id": call_metadata.get("system_assistant_id", ""),
            "system_phone_number": call_metadata.get("system_phone_number", ""),
            "customer_phone_number": to_number or "",
            "system_phone_number_id": call_metadata.get("system_phone_number_id", ""),
            "transcript_available": False,
            "recording_available": False,
            "raw_log": {system_provider: {}},
        }

        # Log credentials for debugging (mask sensitive data)
        activity.logger.info(
            f"Client credentials for call {input.call_id}: "
            f"api_key={'*****' if client_api_key else 'MISSING'}, "
            f"assistant_id={client_assistant_id or 'MISSING'}, "
            f"phone_number={client_phone_number or 'MISSING'}, "
            f"is_outbound={is_outbound}"
        )

        # System data for client call matching
        system_assistant_id = call_metadata.get("system_assistant_id")
        system_phone_number = call_metadata.get("system_phone_number")
        system_phone_number_id = call_metadata.get("system_phone_number_id")

        # Include agent_definition_id in metadata for concurrency tracking
        agent_definition = call.test_execution.agent_definition
        call_metadata["agent_definition_id"] = (
            str(agent_definition.id) if agent_definition else ""
        )

        # Determine connection type: phone (SIP) or web bridge
        if client_provider == "livekit_bridge":
            # LiveKit bridge: no phone needed
            connection_type = "web_livekit_bridge"
            # Pack LiveKit-specific credentials into metadata — read from
            # ProviderCredentials since snapshot no longer stores them.
            from simulate.models import AgentVersion
            from simulate.models.agent_definition import ProviderCredentials

            livekit_creds = None
            if agent_version:
                try:
                    livekit_creds = agent_version.credentials
                except AgentVersion.credentials.RelatedObjectDoesNotExist:
                    pass
            call_metadata["customer_livekit_url"] = (
                livekit_creds.server_url if livekit_creds else ""
            )
            call_metadata["customer_livekit_api_key"] = (
                livekit_creds.get_api_key() if livekit_creds else ""
            )
            call_metadata["customer_livekit_api_secret"] = (
                livekit_creds.get_api_secret() if livekit_creds else ""
            )
            call_metadata["livekit_config_json"] = json.dumps(
                snapshot.get("livekit_config_json") or {}
            )
            call_metadata["livekit_max_concurrency"] = (
                snapshot.get("livekit_max_concurrency")
                or settings.DEFAULT_LIVEKIT_MAX_CONCURRENCY
            )
            # Validate that required LiveKit credentials are present
            _missing_creds = [
                name
                for name, key in [
                    ("livekit_url", "customer_livekit_url"),
                    ("livekit_api_key", "customer_livekit_api_key"),
                    ("livekit_api_secret", "customer_livekit_api_secret"),
                ]
                if not call_metadata.get(key)
            ]
            if _missing_creds:
                activity.logger.warning(
                    "LiveKit bridge credentials incomplete for call %s: missing %s",
                    input.call_id,
                    ", ".join(_missing_creds),
                )
            # Use livekit_agent_name as the assistant_id
            client_assistant_id = (
                snapshot.get("livekit_agent_name") or client_assistant_id
            )
            # Set first_message_mode based on inbound/outbound
            # From the CUSTOMER'S AGENT perspective:
            #   Inbound (is_outbound=False) = agent receives call = simulator calls = simulator speaks first
            #   Outbound (is_outbound=True) = agent makes call = agent speaks first = simulator waits
            if is_outbound:
                voice_settings["first_message_mode"] = "assistant-waits-for-user"
            else:
                voice_settings["first_message_mode"] = "assistant-speaks-first"
            # Bridge calls have ~3s extra round-trip latency vs native
            # LiveKit. Bump endpointing and silence thresholds so the
            # simulator doesn't cut off the customer agent prematurely.
            voice_settings.setdefault("min_endpointing_delay", 3.0)
            voice_settings.setdefault("silence_timeout_seconds", 30)
            # Disable background sound for bridge calls.
            # BackgroundAudioPlayer publishes a 2nd audio track on the
            # simulator participant. The bridge's track_subscribed handler
            # can non-deterministically latch onto this ambient track
            # instead of the real TTS track, forwarding silence to the
            # customer agent and causing silence-timed-out.
            voice_settings["background_sound"] = "off"
            # DON'T override client_api_key — initiate_call uses it to
            # authenticate with OUR server, not the customer's.
            # Customer credentials are in call_metadata for the connector.
        elif client_phone_number:
            connection_type = None  # SIP path
        elif client_api_key and client_assistant_id:
            connection_type = f"web_{client_provider}"  # e.g. "web_vapi", "web_retell"
        else:
            connection_type = None  # fallback SIP

        # Fail fast on SIP outbound to a customer provider we can't drive. Under a
        # VAPI system simulator the customer's account is the data plane for the
        # dial trigger, status poll and result fetch, and only VAPI and Bland have
        # that wiring. Erroring here aborts before phone acquisition (the workflow
        # fail-fasts on prepare_result.error), so we never burn a phone slot — or,
        # worse, dial and bill the customer for a call we couldn't observe.
        outbound_sip_providers = {
            ProviderChoices.VAPI.value,
            ProviderChoices.BLAND.value,
        }
        if (
            is_outbound
            and connection_type is None
            and client_provider not in outbound_sip_providers
        ):
            error_msg = (
                f"Outbound simulation is not supported for customer provider "
                f"'{client_provider}' over a phone/SIP connection."
            )
            activity.logger.error(error_msg)
            return PrepareCallOutput(is_outbound=is_outbound, error=error_msg)

        # A customer provider with no web-bridge connector (e.g. Bland, which is
        # phone/SIP-only) must never be routed onto the web bridge: run_bridge
        # would raise an opaque RuntimeError deep in the call, after the slot is
        # acquired. Fail fast here — before phone acquisition — at parity with the
        # SIP guard above. Applies to both directions (Bland has no web path), so
        # a Bland agent configured without a phone number errors cleanly instead.
        if connection_type and connection_type.startswith("web_"):
            from ee.voice.services.livekit.bridge.connector import (
                get_connector_registry,
            )

            if connection_type not in get_connector_registry():
                error_msg = (
                    f"Customer provider '{client_provider}' has no web connector; "
                    f"it requires a phone number for simulation."
                )
                activity.logger.error(error_msg)
                return PrepareCallOutput(is_outbound=is_outbound, error=error_msg)

        activity.logger.info(
            f"Prepared call {input.call_id}: is_outbound={is_outbound}, "
            f"to_number={to_number}, client_provider={client_uses_own_provider}, "
            f"connection_type={connection_type}"
        )

        return PrepareCallOutput(
            is_outbound=is_outbound,
            to_number=to_number,
            system_prompt=system_prompt,
            voice_settings=voice_settings,
            metadata=call_metadata,
            provider=system_provider,
            provider_config={},
            max_duration_minutes=voice_settings.get("max_call_duration_in_minutes", 30),
            situation_text=None,
            call_data=call_data_dict,
            client_uses_own_provider=client_uses_own_provider,
            client_api_key=client_api_key,
            client_assistant_id=client_assistant_id,
            client_phone_number=client_phone_number,  # User's phone to call FROM in outbound
            client_provider=client_provider,
            system_assistant_id=system_assistant_id,
            system_phone_number=system_phone_number,
            customer_phone_number=to_number,
            system_phone_number_id=system_phone_number_id,
            enable_tool_evaluation=call.test_execution.run_test.enable_tool_evaluation,
            connection_type=connection_type,
        )

    except CallExecution.DoesNotExist:
        error_msg = f"Call not found: {input.call_id}"
        activity.logger.error(error_msg)
        activity.logger.exception(error_msg)
        return PrepareCallOutput(
            is_outbound=False,
            error=error_msg,
        )

    except Exception as e:
        error_msg = f"Failed to prepare call: {str(e)}"
        activity.logger.error(error_msg)
        activity.logger.exception(error_msg)
        return PrepareCallOutput(
            is_outbound=False,
            error=error_msg,
        )


async def _start_dispatcher(client, dispatcher_id: str) -> None:
    """
    Start the CallDispatcherWorkflow singleton.

    Uses ALLOW_DUPLICATE policy to handle race conditions where multiple
    activities try to start the dispatcher simultaneously.
    """
    import asyncio
    from datetime import timedelta

    from temporalio.common import WorkflowIDReusePolicy

    from ee.voice.temporal.workflows.call_dispatcher_workflow import (
        CallDispatcherWorkflow,
    )
    from simulate.temporal.constants import QUEUE_L

    await client.start_workflow(
        CallDispatcherWorkflow.run,
        None,  # No initial state
        id=dispatcher_id,
        task_queue=QUEUE_L,
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        # Longer timeout: default 10s is too short when the tasks_l worker
        # is loaded with many concurrent CallExecutionWorkflows.
        task_timeout=timedelta(seconds=60),
    )

    # Wait a moment for the workflow to start before signaling
    await asyncio.sleep(1)

    activity.logger.info(f"Started CallDispatcherWorkflow: {dispatcher_id}")


# =============================================================================
# Phone Number Dispatcher Activities
# =============================================================================


@activity.defn(name="request_phone_number_slot")
async def request_phone_number_slot(input: RequestPhoneNumberInput) -> None:
    """
    Request a phone number from the PhoneNumberDispatcherWorkflow.

    Auto-starts the dispatcher if it doesn't exist (singleton pattern).
    Workflow waits for SIGNAL_PHONE_NUMBER_GRANTED signal before proceeding.

    Timeout: 10 seconds
    Queue: tasks_s
    """
    try:
        activity.logger.info(
            f"Requesting phone number for call_id={input.call_id}, "
            f"workflow_id={input.workflow_id}, org_id={input.org_id}"
        )

        import asyncio as _asyncio

        from temporalio.common import WorkflowIDConflictPolicy
        from temporalio.service import RPCError

        from simulate.temporal.constants import (
            PHONE_NUMBER_DISPATCHER_WORKFLOW_ID,
            QUEUE_L,
        )
        from simulate.temporal.signals import SIGNAL_REQUEST_PHONE_NUMBER
        from tfc.temporal.common.client import get_client

        client = await get_client()

        max_attempts = 3
        last_error = None

        for attempt in range(max_attempts):
            try:
                handle = await client.start_workflow(
                    "PhoneNumberDispatcherWorkflow",
                    None,
                    id=PHONE_NUMBER_DISPATCHER_WORKFLOW_ID,
                    task_queue=QUEUE_L,
                    id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                )

                from ee.voice.temporal.types.phone_number_dispatcher import (
                    PhoneNumberRequest,
                )

                await handle.signal(
                    SIGNAL_REQUEST_PHONE_NUMBER,
                    PhoneNumberRequest(
                        workflow_id=input.workflow_id,
                        call_id=input.call_id,
                        org_id=input.org_id,
                        call_direction=input.call_direction,
                    ),
                )
                activity.logger.info(
                    f"Requested phone number for call_id={input.call_id}"
                )
                return

            except RPCError as e:
                last_error = e
                activity.logger.warning(
                    f"Failed to signal phone number dispatcher "
                    f"(attempt {attempt + 1}/{max_attempts}): {str(e)}"
                )
                if attempt < max_attempts - 1:
                    await _asyncio.sleep(0.5 * (attempt + 1))

        raise RuntimeError(
            f"Failed to signal phone number dispatcher after {max_attempts} attempts: {last_error}"
        )

    except Exception as e:
        activity.logger.exception(f"Failed to request phone number slot: {str(e)}")
        raise


@activity.defn(name="release_phone_number_slot")
async def release_phone_number_slot(input: ReleasePhoneNumberSlotInput) -> None:
    """
    Signal phone number dispatcher to release a slot.

    The dispatcher owns all DB operations — it will release the phone back
    to the DB pool if one was acquired for this call_id.

    Timeout: 10 seconds
    Queue: tasks_l
    """
    close_old_connections()

    try:
        activity.logger.info(f"Releasing phone number slot for call_id={input.call_id}")

        from simulate.temporal.constants import PHONE_NUMBER_DISPATCHER_WORKFLOW_ID
        from simulate.temporal.signals import SIGNAL_RELEASE_PHONE_NUMBER
        from tfc.temporal.common.client import get_client

        client = await get_client()

        from ee.voice.temporal.types.phone_number_dispatcher import (
            PhoneNumberReleaseSignal,
        )

        handle = client.get_workflow_handle(PHONE_NUMBER_DISPATCHER_WORKFLOW_ID)
        await handle.signal(
            SIGNAL_RELEASE_PHONE_NUMBER,
            PhoneNumberReleaseSignal(call_id=input.call_id),
        )

        activity.logger.info(f"Released phone number slot for call_id={input.call_id}")

    except Exception as e:
        activity.logger.exception(f"Failed to release phone number slot: {str(e)}")
        raise


@activity.defn(name="sync_available_phone_numbers")
async def sync_available_phone_numbers() -> int:
    """
    Count total outbound phone numbers available in the pool.

    Returns the count of non-disabled outbound SimulationPhoneNumbers.
    Used by PhoneNumberDispatcherWorkflow to set app_limit.

    Timeout: 10 seconds
    Queue: tasks_s
    """
    close_old_connections()

    from simulate.models.simulation_phone_number import SimulationPhoneNumber

    count = (
        await SimulationPhoneNumber.objects.filter(
            call_direction=SimulationPhoneNumber.CallDirection.OUTBOUND,
        )
        .exclude(
            status=SimulationPhoneNumber.PhoneStatus.DISABLED,
        )
        .acount()
    )

    activity.logger.info(f"Synced available phone numbers: {count}")
    return count


@activity.defn(name="acquire_and_signal_phone_numbers_batch")
async def acquire_and_signal_phone_numbers_batch(
    input: AcquireAndSignalPhoneNumbersBatchInput,
) -> AcquireAndSignalPhoneNumbersBatchOutput:
    """
    Acquire phone numbers from DB pool and signal workflows with phone number details.

    For each grant:
    1. Acquire phone number from DB via PhoneNumberService
    2. Signal CallExecutionWorkflow with phone number details (SIGNAL_PHONE_NUMBER_GRANTED)

    Returns failed_call_ids in output so the dispatcher can retry them.

    Timeout: 60 seconds
    Queue: tasks_s
    """
    import asyncio

    close_old_connections()

    try:
        activity.logger.info(
            f"Acquiring and signaling {len(input.grants)} phone numbers"
        )

        from simulate.temporal.signals import SIGNAL_PHONE_NUMBER_GRANTED
        from tfc.temporal.common.client import get_client

        client = await get_client()

        from ee.voice.temporal.types.phone_number_dispatcher import (
            PhoneNumberGrantedSignal,
        )

        async def process_one(grant: dict) -> tuple[str, bool, str]:
            """Acquire one phone number and signal the workflow.

            Returns (call_id, success, error).
            """
            call_id = grant["call_id"]
            phone = None
            try:
                from ee.voice.services.phone_number_service import PhoneNumberService

                call_execution = await CallExecution.objects.aget(id=call_id)
                phone = await sync_to_async(PhoneNumberService.acquire_phone_number)(
                    call_direction=grant.get("call_direction", "outbound"),
                    call_execution=call_execution,
                )

                phone_granted = PhoneNumberGrantedSignal(
                    call_id=call_id,
                    phone_id=str(phone.id),
                    phone_number=phone.phone_number,
                    phone_number_id=phone.provider_phone_id,
                )

                # Signal calling workflow with phone number details
                handle = client.get_workflow_handle(grant["workflow_id"])
                await handle.signal(SIGNAL_PHONE_NUMBER_GRANTED, phone_granted)

                return (call_id, True, str(phone.id))

            except Exception as e:
                activity.logger.error(
                    f"Failed to acquire/signal phone number for call_id={call_id}: {e}"
                )
                # If phone was acquired but signaling failed, release it back to DB
                if phone:
                    try:
                        from ee.voice.services.phone_number_service import (
                            PhoneNumberService,
                        )

                        await sync_to_async(PhoneNumberService.release_phone_number)(
                            str(phone.id)
                        )
                    except Exception as release_err:
                        activity.logger.error(
                            f"Failed to release phone number after signal failure: {release_err}"
                        )
                return (call_id, False, str(e))

        # Signal all workflows in parallel
        results = await asyncio.gather(
            *[process_one(grant) for grant in input.grants],
            return_exceptions=True,
        )

        # Log results and collect failed/successful grant details
        success_count = 0
        failed_call_ids = []
        successful_grants = {}
        for result in results:
            if isinstance(result, Exception):
                activity.logger.error(
                    f"Phone number acquisition task failed with exception: {result}"
                )
            elif result[1]:
                success_count += 1
                # result is (call_id, success, phone_id)
                successful_grants[result[0]] = result[2]
            else:
                activity.logger.error(
                    f"Failed to acquire/signal phone number for call {result[0]}: {result[2]}"
                )
                failed_call_ids.append(result[0])

        activity.logger.info(
            f"Completed phone number acquisition: "
            f"{success_count}/{len(input.grants)} successful"
        )

        return AcquireAndSignalPhoneNumbersBatchOutput(
            success_count=success_count,
            failed_count=len(failed_call_ids),
            failed_call_ids=failed_call_ids,
            successful_grants=successful_grants,
        )

    except Exception as e:
        activity.logger.exception(
            f"Failed to acquire/signal phone numbers batch: {str(e)}"
        )
        raise
