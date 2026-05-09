import structlog
from django.shortcuts import get_object_or_404
from django.utils import timezone
from pydantic import ValidationError
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models.user import OrgApiKey
from simulate.constants.installation_guide import CHAT_SDK_CODE, INSTALLATION_GUIDE
from simulate.models import (
    CallExecution,
    RunTest,
    Scenarios,
    SimulatorAgent,
    TestExecution,
)
from simulate.pydantic_schemas.chat import ChatSendMessageViewResponse, SendChatRequest
from simulate.services.chat_sim import initiate_chat, send_message_to_chat
from simulate.services.test_executor import TestExecutor
from simulate.utils.scenario_completeness import check_scenarios_incomplete
from simulate.utils.test_execution_utils import generate_simulator_agent_prompt
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class RunTestNameView(APIView):
    """
    API View to get the id of a run test by name
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    def get(self, request, run_test_name, *args, **kwargs):
        try:
            # Get the organization from the authenticated user
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            qs = RunTest.no_workspace_objects.filter(
                name=run_test_name,
                organization=organization,
            )
            count = qs.count()
            if count > 1:
                logger.warning(
                    "duplicate_run_test_names_found",
                    run_test_name=run_test_name,
                    organization_id=str(organization.id),
                    count=count,
                )
            run_test = qs.first()
            if not run_test:
                return self.gm.bad_request("Run test not found")
            return self.gm.success_response(
                {
                    "run_test_id": str(run_test.id),
                    "run_test_name": str(run_test.name),
                }
            )
        except Exception as e:
            logger.exception(f"Error getting run test id by name: {str(e)}")
            return self.gm.internal_server_error_response(
                "Failed to get run test id by name"
            )


class RunTestChatExecutionView(APIView):
    """
    API View to execute a test run with all its scenarios
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    def post(self, request, run_test_id, *args, **kwargs):
        """Execute a test run"""
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Get the run test
            run_test = get_object_or_404(
                RunTest, id=run_test_id, organization=user_organization, deleted=False
            )

            scenarios = list(
                run_test.scenarios.filter(deleted=False).values_list("id", flat=True)
            )

            gate_response = check_scenarios_incomplete(scenarios, run_test)
            if gate_response is not None:
                return gate_response

            logger.info(f"Run test used here is.... {run_test_id}")

            logger.info(f"Total final scenario ids are.... {scenarios}")

            parsed_scenarios = []
            for scenario_id in scenarios:
                parsed_scenarios.append(str(scenario_id))

            simulator_agent = run_test.simulator_agent

            test_execution_record = TestExecution.objects.create(
                run_test=run_test,
                status=TestExecution.ExecutionStatus.PENDING,
                started_at=timezone.now(),
                total_scenarios=len(scenarios),
                scenario_ids=parsed_scenarios,
                picked_up_by_executor=False,
                simulator_agent=simulator_agent,
                agent_definition=run_test.agent_definition,
                agent_version=run_test.agent_version,
            )

            return self.gm.success_response(
                {
                    "message": "Test execution started successfully",
                    "execution_id": str(test_execution_record.id),
                    "run_test_id": str(run_test.id),
                    "status": str(test_execution_record.status),
                    "total_scenarios": parsed_scenarios,
                }
            )

        except Exception as e:
            logger.exception(f"Error executing test: {str(e)}")
            return self.gm.internal_server_error_response("Failed to execute test")


class TestExecutionChatBatchView(APIView):
    """
    Create a batch of CallExecution records for chat execution.

    This endpoint has side effects (creates CallExecution rows), so it is POST-only.
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    def post(self, request, test_execution_id, *args, **kwargs):
        """
        Create a batch of CallExecution records for chat execution (exactly 10 per API call).

        This follows the same flow as inbound/outbound calls:
        1. Resolve SimulatorAgent (scenario > run_test > fallback)
        2. Extract base_prompt from SimulatorAgent
        3. Handle dataset scenarios (create one CallExecution per row)
        4. Enhance prompt with row data if applicable
        5. Store proper metadata in CallExecution

        Returns exactly 10 CallExecution objects per API call.
        hasMore is true until ALL row_ids of ALL scenarios have CallExecution objects created.
        """
        try:
            try:
                test_execution = TestExecution.objects.select_related(
                    "run_test", "run_test__agent_definition", "agent_version"
                ).get(id=test_execution_id, deleted=False)
            except TestExecution.DoesNotExist:
                return self.gm.bad_request("Test execution not found")

            run_test = test_execution.run_test
            agent_definition = run_test.agent_definition
            selected_version = test_execution.agent_version
            if not selected_version:
                selected_version = agent_definition.latest_version

            snapshot = selected_version.configuration_snapshot or {}
            raw_inbound = snapshot.get("inbound", True)
            if isinstance(raw_inbound, str):
                is_inbound = raw_inbound.strip().lower() == "true"
            else:
                is_inbound = bool(raw_inbound)

            interaction_direction = "inbound" if is_inbound else "outbound"

            total_scenarios = test_execution.scenario_ids

            # Get already processed row_ids (for dataset scenarios) and scenario_ids (for non-dataset scenarios)
            existing_chat_calls = test_execution.calls.filter(
                simulation_call_type=CallExecution.SimulationCallType.TEXT
            )

            # Track processed row_ids per scenario
            processed_row_ids_by_scenario = {}
            processed_scenario_ids = set()

            for call in existing_chat_calls:
                scenario_id = str(call.scenario_id)
                if call.row_id:
                    # Dataset scenario - track row_ids
                    if scenario_id not in processed_row_ids_by_scenario:
                        processed_row_ids_by_scenario[scenario_id] = set()
                    processed_row_ids_by_scenario[scenario_id].add(str(call.row_id))
                else:
                    # Non-dataset scenario - track scenario_id
                    processed_scenario_ids.add(scenario_id)

            logger.info(
                f"Processed scenarios: {processed_scenario_ids}, "
                f"Processed row_ids by scenario: {processed_row_ids_by_scenario}"
            )

            # Initialize TestExecutor for helper methods
            test_executor = TestExecutor()

            # Cache for SimulatorAgent per scenario (to avoid repeated lookups)
            simulator_agent_cache = {}

            batched_call_executions = []
            processed_scenarios_in_batch = set()
            BATCH_SIZE = 9
            has_more = False

            # Process scenarios until we have 10 CallExecutions
            for scenario_id in total_scenarios:
                if len(batched_call_executions) > BATCH_SIZE:
                    has_more = True
                    break

                try:
                    scenario = Scenarios.objects.select_related(
                        "simulator_agent", "dataset", "agent_definition"
                    ).get(id=scenario_id, deleted=False)

                    # Step 1: Resolve SimulatorAgent (cache it per scenario)
                    if scenario_id not in simulator_agent_cache:
                        simulator_agent = (
                            scenario.simulator_agent
                            if scenario.simulator_agent
                            else run_test.simulator_agent
                        )

                        # Create fallback SimulatorAgent if needed
                        if not simulator_agent:
                            fallback_prompt = generate_simulator_agent_prompt(
                                agent_version=selected_version
                            )
                            simulator_agent = SimulatorAgent.objects.create(
                                name=scenario.name,
                                prompt=fallback_prompt,
                                voice_provider="vapi",
                                voice_name="marissa",
                                model="gpt-4",
                                llm_temperature=0.7,
                                initial_message="Hi!",
                                max_call_duration_in_minutes=30,
                                interrupt_sensitivity=0.5,
                                conversation_speed=1.0,
                                finished_speaking_sensitivity=0.5,
                                initial_message_delay=0,
                                organization=scenario.organization,
                                workspace=scenario.workspace,
                            )
                            scenario.simulator_agent = simulator_agent
                            scenario.save(update_fields=["simulator_agent"])

                        simulator_agent_cache[scenario_id] = simulator_agent
                    else:
                        simulator_agent = simulator_agent_cache[scenario_id]

                    base_prompt = simulator_agent.prompt

                    # Step 2: Handle dataset scenario - process rows until batch is full
                    if scenario.dataset:
                        # Parse dataset to get all row IDs
                        all_row_ids = test_executor._parse_dataset_scenario(scenario)

                        # Get unprocessed row_ids for this scenario
                        processed_rows = processed_row_ids_by_scenario.get(
                            scenario_id, set()
                        )
                        remaining_row_ids = [
                            row_id
                            for row_id in all_row_ids
                            if str(row_id) not in processed_rows
                        ]

                        # Process remaining rows until batch is full
                        for row_id in remaining_row_ids:
                            if len(batched_call_executions) > BATCH_SIZE:
                                has_more = True
                                break

                            # Get row data and generate dynamic prompt
                            row_data_info = (
                                test_executor._get_row_data_and_generate_prompt(
                                    row_id=row_id,
                                    base_prompt=base_prompt,
                                    agent_version=selected_version,
                                )
                            )

                            system_prompt = row_data_info.get(
                                "dynamic_prompt", base_prompt
                            )

                            # Create CallExecution with proper metadata
                            call_execution = CallExecution(
                                test_execution=test_execution,
                                scenario=scenario,
                                phone_number="",  # Not applicable for chat
                                status=CallExecution.CallStatus.PENDING,
                                simulation_call_type=CallExecution.SimulationCallType.TEXT,
                                agent_version=selected_version,
                                row_id=row_id,
                                call_metadata={
                                    "call_direction": interaction_direction,
                                    "call_channel": "chat",
                                    "row_id": row_id,
                                    "row_data": row_data_info.get("row_data", {}),
                                    "dataset_id": row_data_info.get("dataset_id"),
                                    "base_prompt": base_prompt,
                                    "agent_description": agent_definition.description,
                                    "dynamic_prompt": row_data_info.get(
                                        "dynamic_prompt"
                                    ),
                                    "language": "en",
                                    "initial_message": simulator_agent.initial_message,
                                    "voice_name": simulator_agent.voice_name,
                                    "conversation_speed": simulator_agent.conversation_speed,
                                    "interrupt_sensitivity": simulator_agent.interrupt_sensitivity,
                                    "finished_speaking_sensitivity": simulator_agent.finished_speaking_sensitivity,
                                    "max_call_duration_in_minutes": simulator_agent.max_call_duration_in_minutes,
                                    "initial_message_delay": simulator_agent.initial_message_delay,
                                    "system_prompt": system_prompt,  # Store enhanced prompt
                                },
                            )
                            batched_call_executions.append(call_execution)
                            processed_scenarios_in_batch.add(scenario_id)

                    else:
                        # No dataset - create single CallExecution for scenario (if not already processed)
                        if scenario_id not in processed_scenario_ids:
                            if len(batched_call_executions) >= BATCH_SIZE:
                                break

                            call_execution = CallExecution(
                                test_execution=test_execution,
                                scenario=scenario,
                                phone_number="",  # Not applicable for chat
                                status=CallExecution.CallStatus.PENDING,
                                simulation_call_type=CallExecution.SimulationCallType.TEXT,
                                agent_version=selected_version,
                                call_metadata={
                                    "call_direction": interaction_direction,
                                    "call_channel": "chat",
                                    "base_prompt": base_prompt,
                                    "agent_description": agent_definition.description,
                                    "dynamic_prompt": base_prompt,  # No enhancement
                                    "language": "en",
                                    "initial_message": simulator_agent.initial_message,
                                    "voice_name": simulator_agent.voice_name,
                                    "conversation_speed": simulator_agent.conversation_speed,
                                    "interrupt_sensitivity": simulator_agent.interrupt_sensitivity,
                                    "finished_speaking_sensitivity": simulator_agent.finished_speaking_sensitivity,
                                    "max_call_duration_in_minutes": simulator_agent.max_call_duration_in_minutes,
                                    "initial_message_delay": simulator_agent.initial_message_delay,
                                    "system_prompt": base_prompt,  # Use base prompt
                                },
                            )
                            batched_call_executions.append(call_execution)
                            processed_scenarios_in_batch.add(scenario_id)

                except Scenarios.DoesNotExist:
                    logger.warning(f"Scenario {scenario_id} not found, skipping")
                    continue
                except Exception as e:
                    logger.exception(
                        f"Error processing scenario {scenario_id}: {str(e)}"
                    )
                    continue

            if not batched_call_executions:
                return self.gm.bad_request(
                    "No remaining call executions to create. All scenarios and rows have been processed."
                )

            # Bulk create CallExecution records
            bulk_call_executions_created = CallExecution.objects.bulk_create(
                batched_call_executions, batch_size=BATCH_SIZE + 1
            )

            call_execution_ids = [str(ce.id) for ce in bulk_call_executions_created]

            logger.info(
                f"Bulk created {len(bulk_call_executions_created)} call executions for chat. "
                f"hasMore: {has_more}"
            )

            return self.gm.success_response(
                {
                    "call_execution_ids": call_execution_ids,
                    "has_more": has_more,
                    "batched_scenarios": list(processed_scenarios_in_batch),
                }
            )

        except Exception as e:
            logger.exception(f"Error getting test execution: {str(e)}")
            return self.gm.internal_server_error_response(
                "Failed to fetch scenarios batch for chat execution"
            )


class ChatSendMessageView(APIView):
    """
    API View to send a message to a chat execution
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    def post(self, request, call_execution_id, *args, **kwargs):
        """Send a message to a chat execution"""
        try:
            call_execution = get_object_or_404(
                CallExecution, id=call_execution_id, deleted=False
            )

            if (
                call_execution.simulation_call_type
                != CallExecution.SimulationCallType.TEXT
            ):
                return self.gm.bad_request("Call execution is not a chat execution")

            request_data = request.data

            if (
                request_data is None
                or (isinstance(request_data, dict) and not request_data)
                or len(request_data) == 0
            ):

                return self.gm.bad_request(
                    "Request data is required and must be of type SendChatRequest"
                )

            try:
                chat_request = SendChatRequest(**request_data)
            except ValidationError as e:
                logger.error(f"Invalid SendChatRequest data: {str(e)}")

                error_messages = []
                for error in e.errors():
                    field = " -> ".join(str(loc) for loc in error.get("loc", []))
                    msg = error.get("msg", "Validation error")
                    error_type = error.get("type", "unknown")
                    error_messages.append(f"{field}: {msg} (type: {error_type})")
                error_details = "; ".join(error_messages) if error_messages else str(e)
                return self.gm.bad_request(
                    f"Invalid request data format. Expected SendChatRequest schema. Errors: {error_details}"
                )

            if chat_request.initiate_chat:

                if call_execution.test_execution.status not in [
                    TestExecution.ExecutionStatus.RUNNING,
                    TestExecution.ExecutionStatus.EVALUATING,
                    TestExecution.ExecutionStatus.PENDING,
                ]:
                    return self.gm.bad_request(
                        "Test execution is not running, evaluating, or pending so no call to be made"
                    )

                message = initiate_chat(
                    call_execution=call_execution,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                    workspace=request.workspace,
                )
                if message is None:
                    return self.gm.bad_request("Failed to initiate chat")

                # Convert Pydantic model to dict for JSON serialization
                response_data = ChatSendMessageViewResponse(
                    output_message=message, message_history=message
                )
                return self.gm.success_response(
                    response_data.model_dump(exclude_none=True)
                )

            if call_execution.test_execution.status not in [
                TestExecution.ExecutionStatus.RUNNING,
                TestExecution.ExecutionStatus.EVALUATING,
            ]:
                return self.gm.bad_request(
                    "Test execution is not running or evaluating so no call to be made"
                )

            if not chat_request.messages:
                return self.gm.bad_request("Messages are required")

            # Validate that messages are not empty
            for msg in chat_request.messages:
                if not msg.content or (
                    isinstance(msg.content, str) and not msg.content.strip()
                ):
                    logger.warning(
                        "empty_message_rejected",
                        call_execution_id=str(call_execution.id),
                        role=msg.role if hasattr(msg, "role") else "unknown",
                    )
                    return self.gm.bad_request("Empty messages are not allowed")

            response = send_message_to_chat(
                call_execution=call_execution,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                workspace=request.workspace,
                messages=chat_request.messages,
                metrics=chat_request.metrics,
            )
            if response is None:
                return self.gm.bad_request("Failed to send message")

            # Convert Pydantic model to dict for JSON serialization
            response_data = ChatSendMessageViewResponse(
                input_message=response["input_message"],
                output_message=response["output_message"],
                message_history=response["message_history"],
                chat_ended=response["chat_ended"],
            )
            return self.gm.success_response(response_data.model_dump(exclude_none=True))

        except CallExecution.DoesNotExist:
            return self.gm.bad_request("Call execution not found")
        except ValidationError as e:
            return self.gm.bad_request(
                f"Invalid request data format. Expected SendChatRequest schema. Errors: {str(e)}"
            )
        except Exception as e:
            logger.exception(f"Error sending message to chat execution: {str(e)}")
            return self.gm.internal_server_error_response(
                f"Failed to send message: {str(e)}"
            )


class ChatSDKCodeView(APIView):
    """
    API View to get the SDK code template with placeholders filled in
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    def get(self, request, run_test_id, *args, **kwargs):
        """Get the SDK code with placeholders filled"""
        try:
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return self.gm.bad_request("Organization not found for the user")

            # Get run_test by ID
            try:
                run_test = RunTest.objects.get(
                    id=run_test_id,
                    organization=user_organization,
                    deleted=False,
                )
            except RunTest.DoesNotExist:
                return self.gm.bad_request("Run test not found")

            # Get organization API keys (system API key)
            try:
                org_api_key = OrgApiKey.objects.filter(
                    organization=user_organization,
                    type="user",
                    enabled=True,
                ).first()

                if not org_api_key:
                    # Create one if it doesn't exist
                    org_api_key = OrgApiKey.objects.create(
                        organization=user_organization, type="user", user=request.user
                    )

                fi_api_key = org_api_key.api_key
                fi_secret_key = org_api_key.secret_key
            except Exception as e:
                logger.exception(f"Error retrieving API keys: {str(e)}")
                return self.gm.bad_request("Failed to retrieve API keys")

            # Render the SDK code template
            rendered_code = CHAT_SDK_CODE.format(
                fi_api_key=fi_api_key,
                fi_secret_key=fi_secret_key,
                run_test_name=run_test.name,
            )

            return self.gm.success_response(
                {
                    "installation_guide": INSTALLATION_GUIDE,
                    "sdk_code": rendered_code,
                    "run_test_id": str(run_test.id),
                    "run_test_name": run_test.name,
                }
            )

        except Exception as e:
            logger.exception(f"Error generating SDK code: {str(e)}")
            return self.gm.bad_request(f"Failed to generate SDK code: {str(e)}")
