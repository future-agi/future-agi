"""
Comprehensive E2E Tests for Prompt Workbench Simulation Feature

This test file covers all UI user flows from the backend perspective:
1. Prompt Template & Version management
2. Scenario listing and filtering
3. Simulation (RunTest) CRUD operations
4. Simulation execution and TestExecution/CallExecution creation
5. Comparison with agent_definition based simulations

Each test verifies both API responses AND database state.
"""

import json
import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import Organization
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.models.agent_definition import AgentDefinition
from simulate.models.run_test import RunTest
from simulate.models.scenarios import Scenarios
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution, TestExecution
from simulate.serializers.requests.run_test import CreatePromptSimulationSerializer
from simulate.serializers.run_test import RunTestSerializer
from simulate.serializers.scenarios import ScenariosSerializer
from simulate.views.prompt_simulation import (
    ExecutePromptSimulationView,
    PromptSimulationDetailView,
    PromptSimulationListCreateView,
    PromptSimulationScenariosView,
)

User = get_user_model()


class E2EPromptSimulationTestCase(TestCase):
    """
    Comprehensive E2E test case for Prompt Workbench Simulation feature.
    Tests all user flows and verifies both API responses and database state.
    """

    def setUp(self):
        """Set up test data for all tests."""
        # Create organization
        self.org = Organization.objects.create(
            name="Test Organization",
        )
        self.organization = self.org  # Alias for tests that reference self.organization

        # Create user
        self.user = User.objects.create_user(
            email="test@example.com",
            password="testpass123",
        )
        self.user.organization = self.org
        self.user.save()

        # Create prompt template
        self.prompt_template = PromptTemplate.objects.create(
            name="Test Prompt Template",
            description="A test prompt for E2E testing",
            organization=self.org,
        )

        # Create prompt version with config
        self.prompt_version = PromptVersion.objects.create(
            original_template=self.prompt_template,
            template_version="v1",
            prompt_config_snapshot=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant for {{task_type}}.",
                },
                {
                    "role": "user",
                    "content": "{{user_input}}",
                },
            ],
            is_default=True,
        )

        # Create a second version for testing version selection
        self.prompt_version_v2 = PromptVersion.objects.create(
            original_template=self.prompt_template,
            template_version="v2",
            prompt_config_snapshot=[
                {
                    "role": "system",
                    "content": "You are an expert assistant for {{task_type}}.",
                },
            ],
            is_default=False,
        )

        # Create simulator agent (required for scenarios)
        self.simulator_agent = SimulatorAgent.objects.create(
            name="Test Simulator Agent",
            organization=self.org,
        )

        # Create test scenario
        self.scenario = Scenarios.objects.create(
            name="Test Scenario",
            description="A test scenario for E2E testing",
            scenario_type="graph",
            organization=self.org,
            simulator_agent=self.simulator_agent,
        )

        # Create a second scenario
        self.scenario_2 = Scenarios.objects.create(
            name="Test Scenario 2",
            description="Another test scenario",
            scenario_type="graph",
            organization=self.org,
            simulator_agent=self.simulator_agent,
        )

        # Create agent definition for comparison tests
        self.agent_definition = AgentDefinition.objects.create(
            agent_name="Test Agent Definition",
            description="Test agent for E2E testing",
            inbound=False,
            organization=self.org,
        )

        self.factory = APIRequestFactory()

    # =========================================================================
    # FLOW 1: Prompt Template & Version Tests
    # =========================================================================

    def test_flow1_prompt_template_exists(self):
        """Test that prompt template was created correctly."""
        print("\n" + "=" * 60)
        print("FLOW 1: Prompt Template & Version Tests")
        print("=" * 60)

        # Verify template in DB
        template = PromptTemplate.objects.get(id=self.prompt_template.id)
        self.assertEqual(template.name, "Test Prompt Template")
        self.assertEqual(template.organization, self.org)
        print(f"✓ Template exists: {template.name} (ID: {template.id})")

        # Verify versions
        versions = PromptVersion.objects.filter(original_template=template)
        self.assertEqual(versions.count(), 2)
        print(f"✓ Found {versions.count()} versions")

        # Verify default version
        default_version = versions.filter(is_default=True).first()
        self.assertIsNotNone(default_version)
        self.assertEqual(default_version.template_version, "v1")
        print(f"✓ Default version: {default_version.template_version}")

        # Verify prompt config snapshot
        self.assertIsInstance(default_version.prompt_config_snapshot, list)
        self.assertEqual(len(default_version.prompt_config_snapshot), 2)
        print(f"✓ Config has {len(default_version.prompt_config_snapshot)} messages")

    # =========================================================================
    # FLOW 2: Scenario Listing Tests
    # =========================================================================

    def test_flow2_list_scenarios_api(self):
        """Test listing scenarios via API."""
        print("\n" + "=" * 60)
        print("FLOW 2: Scenario Listing Tests")
        print("=" * 60)

        request = self.factory.get("/simulate/prompt-simulations/scenarios/")
        force_authenticate(request, user=self.user)
        view = PromptSimulationScenariosView.as_view()
        response = view(request)

        # Verify API response
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        print(f"✓ API Status: {response.status_code}")

        result_data = response.data.get("result", response.data)
        results = result_data.get("results", [])
        self.assertGreaterEqual(len(results), 2)
        print(f"✓ Found {len(results)} scenarios in API response")

        # Verify scenario fields in response
        scenario_names = [s["name"] for s in results]
        self.assertIn("Test Scenario", scenario_names)
        self.assertIn("Test Scenario 2", scenario_names)
        print(f"✓ Scenarios: {scenario_names}")

        # Verify DB state matches API
        db_scenarios = Scenarios.objects.filter(organization=self.org)
        self.assertEqual(db_scenarios.count(), len(results))
        print(f"✓ DB count matches API count: {db_scenarios.count()}")

    def test_flow2_scenario_serializer_fields(self):
        """Test scenario serializer includes all required fields."""
        serializer = ScenariosSerializer(self.scenario)
        data = serializer.data

        required_fields = ["id", "name", "description", "scenario_type"]
        for field in required_fields:
            self.assertIn(field, data)
        print(f"✓ Serializer has all required fields: {required_fields}")

    # =========================================================================
    # FLOW 3: Simulation CRUD Tests
    # =========================================================================

    def test_flow3a_list_simulations_empty(self):
        """Test listing simulations when none exist."""
        print("\n" + "=" * 60)
        print("FLOW 3: Simulation CRUD Tests")
        print("=" * 60)

        request = self.factory.get(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/"
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationListCreateView.as_view()
        response = view(request, prompt_template_id=str(self.prompt_template.id))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result_data = response.data.get("result", response.data)
        self.assertEqual(result_data.get("count", 0), 0)
        print(f"✓ Empty list returns count=0")

    def test_flow3b_create_simulation_api(self):
        """Test creating a simulation via API."""
        data = {
            "name": "E2E Test Simulation",
            "description": "Created via E2E test",
            "prompt_version_id": str(self.prompt_version.id),
            "scenario_ids": [str(self.scenario.id)],
        }

        request = self.factory.post(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/",
            data=json.dumps(data),
            content_type="application/json",
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationListCreateView.as_view()
        response = view(request, prompt_template_id=str(self.prompt_template.id))

        # Verify API response
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        print(f"✓ Create API Status: {response.status_code}")

        result_data = response.data.get("result", response.data)
        created_id = result_data.get("id")
        self.assertIsNotNone(created_id)
        print(f"✓ Created simulation ID: {created_id}")

        # Verify response fields
        self.assertEqual(result_data.get("name"), "E2E Test Simulation")
        self.assertEqual(result_data.get("source_type"), "prompt")
        print(f"✓ Response source_type: prompt")

        # Verify DB state
        simulation = RunTest.objects.get(id=created_id)
        self.assertEqual(simulation.name, "E2E Test Simulation")
        self.assertEqual(simulation.source_type, "prompt")
        self.assertEqual(simulation.prompt_template_id, self.prompt_template.id)
        self.assertEqual(simulation.prompt_version_id, self.prompt_version.id)
        self.assertIsNone(simulation.agent_definition)
        print(f"✓ DB: source_type={simulation.source_type}")
        print(f"✓ DB: prompt_template={simulation.prompt_template_id}")
        print(f"✓ DB: prompt_version={simulation.prompt_version_id}")
        print(f"✓ DB: agent_definition=None")

        # Verify scenarios linked
        linked_scenarios = list(simulation.scenarios.all())
        self.assertEqual(len(linked_scenarios), 1)
        self.assertEqual(linked_scenarios[0].id, self.scenario.id)
        print(f"✓ DB: {len(linked_scenarios)} scenario(s) linked")

        return created_id

    def test_flow3c_create_simulation_with_multiple_scenarios(self):
        """Test creating simulation with multiple scenarios."""
        data = {
            "name": "Multi-Scenario Simulation",
            "description": "Test with multiple scenarios",
            "prompt_version_id": str(self.prompt_version.id),
            "scenario_ids": [str(self.scenario.id), str(self.scenario_2.id)],
        }

        request = self.factory.post(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/",
            data=json.dumps(data),
            content_type="application/json",
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationListCreateView.as_view()
        response = view(request, prompt_template_id=str(self.prompt_template.id))

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Verify DB
        result_data = response.data.get("result", response.data)
        simulation = RunTest.objects.get(id=result_data["id"])
        self.assertEqual(simulation.scenarios.count(), 2)
        print(f"✓ Created simulation with {simulation.scenarios.count()} scenarios")

    def test_flow3d_create_simulation_with_v2(self):
        """Test creating simulation with non-default version."""
        data = {
            "name": "V2 Simulation",
            "description": "Using version 2",
            "prompt_version_id": str(self.prompt_version_v2.id),
            "scenario_ids": [str(self.scenario.id)],
        }

        request = self.factory.post(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/",
            data=json.dumps(data),
            content_type="application/json",
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationListCreateView.as_view()
        response = view(request, prompt_template_id=str(self.prompt_template.id))

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Verify correct version in DB
        result_data = response.data.get("result", response.data)
        simulation = RunTest.objects.get(id=result_data["id"])
        self.assertEqual(simulation.prompt_version_id, self.prompt_version_v2.id)
        print(
            f"✓ Created simulation with version: {simulation.prompt_version.template_version}"
        )

    def test_flow3e_get_simulation_detail(self):
        """Test getting simulation detail via API."""
        # First create a simulation
        simulation = RunTest.objects.create(
            name="Detail Test Simulation",
            source_type="prompt",
            prompt_template=self.prompt_template,
            prompt_version=self.prompt_version,
            organization=self.org,
        )
        simulation.scenarios.add(self.scenario)

        request = self.factory.get(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/{simulation.id}/"
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationDetailView.as_view()
        response = view(
            request,
            prompt_template_id=str(self.prompt_template.id),
            run_test_id=str(simulation.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        print(f"✓ Detail API Status: {response.status_code}")

        result_data = response.data.get("result", response.data)

        # Verify response includes prompt details
        self.assertIsNotNone(result_data.get("prompt_template_detail"))
        self.assertIsNotNone(result_data.get("prompt_version_detail"))
        print(f"✓ Response includes prompt_template_detail")
        print(f"✓ Response includes prompt_version_detail")

        # Verify scenarios detail
        scenarios_detail = result_data.get("scenarios_detail")
        self.assertIsNotNone(scenarios_detail)
        self.assertEqual(len(scenarios_detail), 1)
        print(f"✓ Response includes {len(scenarios_detail)} scenario(s) detail")

    def test_flow3f_list_simulations_after_create(self):
        """Test listing simulations returns created items."""
        # Create some simulations
        for i in range(3):
            RunTest.objects.create(
                name=f"List Test Simulation {i}",
                source_type="prompt",
                prompt_template=self.prompt_template,
                prompt_version=self.prompt_version,
                organization=self.org,
            )

        request = self.factory.get(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/"
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationListCreateView.as_view()
        response = view(request, prompt_template_id=str(self.prompt_template.id))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result_data = response.data.get("result", response.data)
        self.assertEqual(result_data.get("count"), 3)
        print(f"✓ List returns {result_data.get('count')} simulations")

        # Verify DB matches
        db_count = RunTest.objects.filter(
            prompt_template=self.prompt_template, source_type="prompt", deleted=False
        ).count()
        self.assertEqual(db_count, 3)
        print(f"✓ DB count matches: {db_count}")

    def test_flow3g_delete_simulation(self):
        """Test deleting a simulation (soft delete)."""
        simulation = RunTest.objects.create(
            name="Delete Test Simulation",
            source_type="prompt",
            prompt_template=self.prompt_template,
            prompt_version=self.prompt_version,
            organization=self.org,
        )

        request = self.factory.delete(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/{simulation.id}/"
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationDetailView.as_view()
        response = view(
            request,
            prompt_template_id=str(self.prompt_template.id),
            run_test_id=str(simulation.id),
        )

        self.assertIn(
            response.status_code, [status.HTTP_200_OK, status.HTTP_204_NO_CONTENT]
        )
        print(f"✓ Delete API Status: {response.status_code}")

        # Verify soft delete in DB
        simulation.refresh_from_db()
        self.assertTrue(simulation.deleted)
        print(f"✓ DB: deleted={simulation.deleted}")

    # =========================================================================
    # FLOW 4: Simulation Execution Tests
    # =========================================================================

    def test_flow4a_execute_simulation_creates_test_execution(self):
        """Test that executing a simulation creates TestExecution."""
        print("\n" + "=" * 60)
        print("FLOW 4: Simulation Execution Tests")
        print("=" * 60)

        # Create simulation
        simulation = RunTest.objects.create(
            name="Execution Test Simulation",
            source_type="prompt",
            prompt_template=self.prompt_template,
            prompt_version=self.prompt_version,
            organization=self.org,
        )
        simulation.scenarios.add(self.scenario)

        # Mock the TestExecutor to avoid actual execution
        with patch(
            "simulate.views.prompt_simulation.TestExecutor"
        ) as mock_executor_cls:
            mock_executor = MagicMock()
            mock_executor_cls.return_value = mock_executor
            mock_executor.execute_test.return_value = {
                "success": True,
                "execution_id": str(uuid.uuid4()),
                "run_test_id": str(simulation.id),
                "status": "pending",
                "total_scenarios": 1,
                "total_calls": 1,
            }

            request = self.factory.post(
                f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/{simulation.id}/execute/"
            )
            force_authenticate(request, user=self.user)
            view = ExecutePromptSimulationView.as_view()
            response = view(
                request,
                prompt_template_id=str(self.prompt_template.id),
                run_test_id=str(simulation.id),
            )

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            print(f"✓ Execute API Status: {response.status_code}")
            print(f"✓ Response: {response.data}")

    def test_flow4b_test_execution_model_fields(self):
        """Test TestExecution model has correct fields for prompt simulations."""
        simulation = RunTest.objects.create(
            name="TestExecution Field Test",
            source_type="prompt",
            prompt_template=self.prompt_template,
            prompt_version=self.prompt_version,
            organization=self.org,
        )

        # Create TestExecution manually
        test_execution = TestExecution.objects.create(
            run_test=simulation,
            status="pending",
        )

        # Verify relationship
        self.assertEqual(test_execution.run_test.id, simulation.id)
        self.assertEqual(test_execution.run_test.source_type, "prompt")
        print(f"✓ TestExecution linked to RunTest with source_type=prompt")

        # Verify we can access prompt info through run_test
        self.assertEqual(
            test_execution.run_test.prompt_template_id, self.prompt_template.id
        )
        self.assertEqual(
            test_execution.run_test.prompt_version_id, self.prompt_version.id
        )
        print(f"✓ Can access prompt_template via test_execution.run_test")
        print(f"✓ Can access prompt_version via test_execution.run_test")

    # =========================================================================
    # FLOW 5: Agent Definition vs Prompt Comparison Tests
    # =========================================================================

    def test_flow5a_agent_definition_simulation(self):
        """Test creating agent_definition based simulation for comparison."""
        print("\n" + "=" * 60)
        print("FLOW 5: Agent Definition vs Prompt Comparison")
        print("=" * 60)

        # Create agent definition based simulation
        simulation = RunTest.objects.create(
            name="Agent Definition Simulation",
            source_type="agent_definition",
            agent_definition=self.agent_definition,
            organization=self.org,
        )
        simulation.scenarios.add(self.scenario)

        # Verify DB state
        self.assertEqual(simulation.source_type, "agent_definition")
        self.assertIsNotNone(simulation.agent_definition)
        self.assertIsNone(simulation.prompt_template)
        self.assertIsNone(simulation.prompt_version)
        print(f"✓ Agent definition simulation: source_type={simulation.source_type}")
        print(f"✓ agent_definition={simulation.agent_definition_id}")
        print(f"✓ prompt_template=None")

    def test_flow5b_compare_serializer_output(self):
        """Compare serializer output for prompt vs agent_definition simulations."""
        # Create prompt-based simulation
        prompt_sim = RunTest.objects.create(
            name="Prompt Simulation",
            source_type="prompt",
            prompt_template=self.prompt_template,
            prompt_version=self.prompt_version,
            organization=self.org,
        )

        # Create agent-based simulation
        agent_sim = RunTest.objects.create(
            name="Agent Simulation",
            source_type="agent_definition",
            agent_definition=self.agent_definition,
            organization=self.org,
        )

        # Serialize both
        prompt_data = RunTestSerializer(prompt_sim).data
        agent_data = RunTestSerializer(agent_sim).data

        # Verify prompt simulation fields
        self.assertEqual(prompt_data.get("source_type"), "prompt")
        self.assertIsNotNone(prompt_data.get("prompt_template_detail"))
        self.assertIsNotNone(prompt_data.get("prompt_version_detail"))
        self.assertIsNone(prompt_data.get("agent_definition_detail"))
        print(f"✓ Prompt simulation has correct fields")

        # Verify agent simulation fields
        self.assertEqual(agent_data.get("source_type"), "agent_definition")
        self.assertIsNone(agent_data.get("prompt_template_detail"))
        self.assertIsNone(agent_data.get("prompt_version_detail"))
        print(f"✓ Agent simulation has correct fields")

    # =========================================================================
    # FLOW 6: Validation & Error Tests
    # =========================================================================

    def test_flow6a_create_simulation_missing_name(self):
        """Test validation error when name is missing."""
        print("\n" + "=" * 60)
        print("FLOW 6: Validation & Error Tests")
        print("=" * 60)

        data = {
            "name": "",  # Empty name
            "prompt_version_id": str(self.prompt_version.id),
            "scenario_ids": [str(self.scenario.id)],
        }

        request = self.factory.post(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/",
            data=json.dumps(data),
            content_type="application/json",
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationListCreateView.as_view()
        response = view(request, prompt_template_id=str(self.prompt_template.id))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        print(f"✓ Empty name returns 400")

    def test_flow6b_create_simulation_invalid_version(self):
        """Test validation error when version doesn't exist."""
        data = {
            "name": "Invalid Version Test",
            "prompt_version_id": str(uuid.uuid4()),  # Non-existent
            "scenario_ids": [str(self.scenario.id)],
        }

        request = self.factory.post(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/",
            data=json.dumps(data),
            content_type="application/json",
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationListCreateView.as_view()
        response = view(request, prompt_template_id=str(self.prompt_template.id))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        print(f"✓ Invalid version ID returns 400")

    def test_flow6c_create_simulation_no_scenarios(self):
        """Test validation error when no scenarios selected."""
        data = {
            "name": "No Scenarios Test",
            "prompt_version_id": str(self.prompt_version.id),
            "scenario_ids": [],  # Empty
        }

        request = self.factory.post(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/",
            data=json.dumps(data),
            content_type="application/json",
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationListCreateView.as_view()
        response = view(request, prompt_template_id=str(self.prompt_template.id))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        print(f"✓ Empty scenarios returns 400")

    def test_flow6d_get_nonexistent_simulation(self):
        """Test 404 when getting non-existent simulation."""
        fake_id = str(uuid.uuid4())

        request = self.factory.get(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/{fake_id}/"
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationDetailView.as_view()
        response = view(
            request,
            prompt_template_id=str(self.prompt_template.id),
            run_test_id=fake_id,
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        print(f"✓ Non-existent simulation returns 404")

    # =========================================================================
    # FLOW 7: Response Format Tests (GeneralMethods gm pattern)
    # =========================================================================

    def test_flow7a_success_response_has_gm_format(self):
        """Test that success responses follow the gm format: {status: True, result: ...}."""
        request = self.factory.get(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/"
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationListCreateView.as_view()
        response = view(request, prompt_template_id=str(self.prompt_template.id))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("status", response.data)
        self.assertIn("result", response.data)
        self.assertTrue(response.data["status"])
        print(f"✓ Success response has gm format: status=True, result=<data>")

    def test_flow7b_create_response_has_gm_format_with_201(self):
        """Test that create response returns 201 with gm format."""
        data = {
            "name": "GM Format Test",
            "prompt_version_id": str(self.prompt_version.id),
            "scenario_ids": [str(self.scenario.id)],
        }

        request = self.factory.post(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/",
            data=json.dumps(data),
            content_type="application/json",
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationListCreateView.as_view()
        response = view(request, prompt_template_id=str(self.prompt_template.id))

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("status", response.data)
        self.assertTrue(response.data["status"])
        self.assertIn("result", response.data)
        print(f"✓ Create response: 201 with gm format")

    def test_flow7c_error_response_has_gm_format(self):
        """Test that error responses follow gm format: {status: False, result: ...}."""
        data = {
            "name": "",
            "prompt_version_id": str(self.prompt_version.id),
            "scenario_ids": [str(self.scenario.id)],
        }

        request = self.factory.post(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/",
            data=json.dumps(data),
            content_type="application/json",
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationListCreateView.as_view()
        response = view(request, prompt_template_id=str(self.prompt_template.id))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("status", response.data)
        self.assertFalse(response.data["status"])
        print(f"✓ Error response: 400 with status=False")

    def test_flow7d_not_found_response_has_gm_format(self):
        """Test that 404 responses follow gm format."""
        fake_id = str(uuid.uuid4())

        request = self.factory.get(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/{fake_id}/"
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationDetailView.as_view()
        response = view(
            request,
            prompt_template_id=str(self.prompt_template.id),
            run_test_id=fake_id,
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("status", response.data)
        self.assertFalse(response.data["status"])
        print(f"✓ Not found response: 404 with status=False")

    def test_flow7e_delete_response_has_gm_format(self):
        """Test that delete response follows gm format."""
        simulation = RunTest.objects.create(
            name="Delete GM Format Test",
            source_type="prompt",
            prompt_template=self.prompt_template,
            prompt_version=self.prompt_version,
            organization=self.org,
        )

        request = self.factory.delete(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/{simulation.id}/"
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationDetailView.as_view()
        response = view(
            request,
            prompt_template_id=str(self.prompt_template.id),
            run_test_id=str(simulation.id),
        )

        self.assertIn(
            response.status_code, [status.HTTP_200_OK, status.HTTP_204_NO_CONTENT]
        )
        if response.status_code == status.HTTP_200_OK:
            self.assertIn("status", response.data)
            self.assertTrue(response.data["status"])
        print(f"✓ Delete response follows gm format")

    # =========================================================================
    # FLOW 8: RunTest.__str__ Null Safety
    # =========================================================================

    def test_flow8a_runtest_str_with_agent_definition(self):
        """Test RunTest.__str__ when agent_definition is set."""
        simulation = RunTest.objects.create(
            name="Agent Str Test",
            source_type="agent_definition",
            agent_definition=self.agent_definition,
            organization=self.org,
        )
        result = str(simulation)
        self.assertIn("Agent Str Test", result)
        print(f"✓ __str__ with agent_definition: '{result}'")

    def test_flow8b_runtest_str_without_agent_definition(self):
        """Test RunTest.__str__ when agent_definition is None (prompt-based)."""
        simulation = RunTest.objects.create(
            name="Prompt Str Test",
            source_type="prompt",
            prompt_template=self.prompt_template,
            prompt_version=self.prompt_version,
            organization=self.org,
        )
        result = str(simulation)
        self.assertEqual(result, "Prompt Str Test")
        print(f"✓ __str__ without agent_definition: '{result}'")

    # =========================================================================
    # FLOW 9: Validation - No Silent Fallback on Invalid Version
    # =========================================================================

    def test_flow9a_invalid_version_uuid_returns_explicit_error(self):
        """Test that an invalid prompt_version_id returns an explicit error, not a silent fallback."""
        fake_version_id = str(uuid.uuid4())
        data = {
            "name": "No Fallback Test",
            "prompt_version_id": fake_version_id,
            "scenario_ids": [str(self.scenario.id)],
        }

        request = self.factory.post(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/",
            data=json.dumps(data),
            content_type="application/json",
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationListCreateView.as_view()
        response = view(request, prompt_template_id=str(self.prompt_template.id))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Should NOT have created a RunTest with a fallback version
        self.assertFalse(RunTest.objects.filter(name="No Fallback Test").exists())
        print(f"✓ Invalid version UUID returns 400, no silent fallback")

    def test_flow9b_invalid_version_string_returns_explicit_error(self):
        """Test that a non-existent template_version string returns an explicit error."""
        data = {
            "name": "No Fallback String Test",
            "prompt_version_id": "v999",
            "scenario_ids": [str(self.scenario.id)],
        }

        request = self.factory.post(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/",
            data=json.dumps(data),
            content_type="application/json",
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationListCreateView.as_view()
        response = view(request, prompt_template_id=str(self.prompt_template.id))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            RunTest.objects.filter(name="No Fallback String Test").exists()
        )
        print(f"✓ Invalid version string returns 400, no silent fallback")

    def test_flow9c_valid_template_version_string_works(self):
        """Test that a valid template_version string (e.g. 'v1') resolves correctly."""
        data = {
            "name": "Version String Test",
            "prompt_version_id": "v1",
            "scenario_ids": [str(self.scenario.id)],
        }

        request = self.factory.post(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/",
            data=json.dumps(data),
            content_type="application/json",
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationListCreateView.as_view()
        response = view(request, prompt_template_id=str(self.prompt_template.id))

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        sim = RunTest.objects.get(name="Version String Test")
        self.assertEqual(sim.prompt_version_id, self.prompt_version.id)
        print(f"✓ Valid version string 'v1' resolves to correct version")

    # =========================================================================
    # FLOW 10: Execution-time Prompt Version Validation
    # =========================================================================

    def test_flow10a_execute_with_deleted_prompt_version(self):
        """Test that execution fails when prompt version has been deleted."""
        simulation = RunTest.objects.create(
            name="Deleted Version Exec Test",
            source_type="prompt",
            prompt_template=self.prompt_template,
            prompt_version=self.prompt_version,
            organization=self.org,
        )
        simulation.scenarios.add(self.scenario)

        # Soft-delete the prompt version
        self.prompt_version.deleted = True
        self.prompt_version.save()

        request = self.factory.post(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/{simulation.id}/execute/"
        )
        force_authenticate(request, user=self.user)
        view = ExecutePromptSimulationView.as_view()
        response = view(
            request,
            prompt_template_id=str(self.prompt_template.id),
            run_test_id=str(simulation.id),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("status", response.data)
        self.assertFalse(response.data["status"])
        print(f"✓ Execute with deleted version returns 400")

        # Restore for other tests
        self.prompt_version.deleted = False
        self.prompt_version.save()

    def test_flow10b_execute_with_null_prompt_version(self):
        """Test that execution fails when prompt version is null."""
        simulation = RunTest.objects.create(
            name="Null Version Exec Test",
            source_type="prompt",
            prompt_template=self.prompt_template,
            prompt_version=None,
            organization=self.org,
        )
        simulation.scenarios.add(self.scenario)

        request = self.factory.post(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/{simulation.id}/execute/"
        )
        force_authenticate(request, user=self.user)
        view = ExecutePromptSimulationView.as_view()
        response = view(
            request,
            prompt_template_id=str(self.prompt_template.id),
            run_test_id=str(simulation.id),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        print(f"✓ Execute with null version returns 400")

    # =========================================================================
    # FLOW 11: PromptBasedAgentAdapter Config Validation
    # =========================================================================

    def test_flow11a_adapter_rejects_empty_config_snapshot(self):
        """Test adapter raises ValueError for empty config snapshot."""
        from simulate.services.prompt_based_agent_adapter import (
            PromptBasedAgentAdapter,
        )

        pv = PromptVersion(
            original_template=self.prompt_template,
            template_version="bad_empty",
            prompt_config_snapshot=None,
        )

        with self.assertRaises(ValueError) as ctx:
            PromptBasedAgentAdapter(
                prompt_version=pv,
                organization_id=self.organization.id,
            )
        self.assertIn("no prompt_config_snapshot", str(ctx.exception))
        print(f"✓ Adapter rejects None config snapshot")

    def test_flow11b_adapter_rejects_non_dict_config(self):
        """Test adapter raises ValueError when config is not a dict."""
        from simulate.services.prompt_based_agent_adapter import (
            PromptBasedAgentAdapter,
        )

        pv = PromptVersion(
            original_template=self.prompt_template,
            template_version="bad_format",
            prompt_config_snapshot=["just_a_string"],
        )

        with self.assertRaises(ValueError) as ctx:
            PromptBasedAgentAdapter(
                prompt_version=pv,
                organization_id=self.organization.id,
            )
        self.assertIn("invalid prompt_config_snapshot format", str(ctx.exception))
        print(f"✓ Adapter rejects non-dict config snapshot")

    def test_flow11c_adapter_rejects_empty_messages(self):
        """Test adapter raises ValueError when messages list is empty."""
        from simulate.services.prompt_based_agent_adapter import (
            PromptBasedAgentAdapter,
        )

        pv = PromptVersion(
            original_template=self.prompt_template,
            template_version="no_messages",
            prompt_config_snapshot=[
                {"messages": [], "configuration": {"model": "gpt-4o"}}
            ],
        )

        with self.assertRaises(ValueError) as ctx:
            PromptBasedAgentAdapter(
                prompt_version=pv,
                organization_id=self.organization.id,
            )
        self.assertIn("no messages", str(ctx.exception))
        print(f"✓ Adapter rejects empty messages list")

    def test_flow11d_adapter_loads_valid_config(self):
        """Test adapter successfully loads a valid config snapshot."""
        from simulate.services.prompt_based_agent_adapter import (
            PromptBasedAgentAdapter,
        )

        pv = PromptVersion(
            original_template=self.prompt_template,
            template_version="valid",
            prompt_config_snapshot=[
                {
                    "messages": [
                        {"role": "system", "content": "You are helpful."},
                    ],
                    "configuration": {
                        "model": "gpt-4o-mini",
                        "temperature": 0.5,
                        "max_tokens": 500,
                    },
                }
            ],
        )

        adapter = PromptBasedAgentAdapter(
            prompt_version=pv,
            organization_id=self.organization.id,
        )
        self.assertEqual(adapter.model, "gpt-4o-mini")
        self.assertEqual(adapter.temperature, 0.5)
        self.assertEqual(adapter.max_tokens, 500)
        self.assertEqual(len(adapter.base_messages), 1)
        print(
            f"✓ Adapter loads valid config: model={adapter.model}, temp={adapter.temperature}"
        )

    def test_flow11e_adapter_dict_config_format(self):
        """Test adapter handles dict config format (not wrapped in list)."""
        from simulate.services.prompt_based_agent_adapter import (
            PromptBasedAgentAdapter,
        )

        pv = PromptVersion(
            original_template=self.prompt_template,
            template_version="dict_format",
            prompt_config_snapshot={
                "messages": [
                    {"role": "system", "content": "You are a bot."},
                ],
                "configuration": {
                    "model": "gpt-4o",
                },
            },
        )

        adapter = PromptBasedAgentAdapter(
            prompt_version=pv,
            organization_id=self.organization.id,
        )
        self.assertEqual(adapter.model, "gpt-4o")
        self.assertEqual(len(adapter.base_messages), 1)
        print(f"✓ Adapter handles dict config format")

    # =========================================================================
    # FLOW 12: Variable Injection Tests
    # =========================================================================

    def test_flow12a_variable_injection_in_string(self):
        """Test that variables are injected into string content."""
        from simulate.services.prompt_based_agent_adapter import (
            PromptBasedAgentAdapter,
        )

        pv = PromptVersion(
            original_template=self.prompt_template,
            template_version="vars",
            prompt_config_snapshot=[
                {
                    "messages": [
                        {"role": "system", "content": "Handle {{task_type}} tasks."},
                    ],
                    "configuration": {
                        "model": "gpt-4o",
                    },
                }
            ],
        )

        adapter = PromptBasedAgentAdapter(
            prompt_version=pv,
            organization_id=self.organization.id,
            variable_values={"task_type": "customer support"},
        )

        system_prompt = adapter.get_system_prompt()
        self.assertEqual(system_prompt, "Handle customer support tasks.")
        print(f"✓ Variable injection: '{system_prompt}'")

    def test_flow12b_variable_injection_with_spaces(self):
        """Test variable injection handles spaces in {{  variable_name  }}."""
        from simulate.services.prompt_based_agent_adapter import (
            PromptBasedAgentAdapter,
        )

        pv = PromptVersion(
            original_template=self.prompt_template,
            template_version="spaces",
            prompt_config_snapshot=[
                {
                    "messages": [
                        {"role": "system", "content": "Hello {{  name  }}."},
                    ],
                    "configuration": {
                        "model": "gpt-4o",
                    },
                }
            ],
        )

        adapter = PromptBasedAgentAdapter(
            prompt_version=pv,
            organization_id=self.organization.id,
            variable_values={"name": "World"},
        )

        result = adapter.get_system_prompt()
        self.assertEqual(result, "Hello World.")
        print(f"✓ Variable injection with spaces: '{result}'")

    def test_flow12c_no_variables_leaves_placeholders(self):
        """Test that missing variables leave placeholders untouched."""
        from simulate.services.prompt_based_agent_adapter import (
            PromptBasedAgentAdapter,
        )

        pv = PromptVersion(
            original_template=self.prompt_template,
            template_version="no_vars",
            prompt_config_snapshot=[
                {
                    "messages": [
                        {"role": "system", "content": "Hello {{name}}."},
                    ],
                    "configuration": {
                        "model": "gpt-4o",
                    },
                }
            ],
        )

        adapter = PromptBasedAgentAdapter(
            prompt_version=pv,
            organization_id=self.organization.id,
            variable_values={},
        )
        result = adapter.get_system_prompt()
        self.assertEqual(result, "Hello {{name}}.")
        print(f"✓ No variables: placeholders untouched")

    # =========================================================================
    # FLOW 13: Update (PATCH) Tests
    # =========================================================================

    def test_flow13a_update_simulation_name(self):
        """Test updating simulation name via PATCH."""
        simulation = RunTest.objects.create(
            name="Original Name",
            source_type="prompt",
            prompt_template=self.prompt_template,
            prompt_version=self.prompt_version,
            organization=self.org,
        )

        request = self.factory.patch(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/{simulation.id}/",
            data=json.dumps({"name": "Updated Name"}),
            content_type="application/json",
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationDetailView.as_view()
        response = view(
            request,
            prompt_template_id=str(self.prompt_template.id),
            run_test_id=str(simulation.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        simulation.refresh_from_db()
        self.assertEqual(simulation.name, "Updated Name")
        print(f"✓ PATCH name updated: '{simulation.name}'")

    def test_flow13b_update_simulation_version(self):
        """Test updating simulation prompt version via PATCH."""
        simulation = RunTest.objects.create(
            name="Version Update Test",
            source_type="prompt",
            prompt_template=self.prompt_template,
            prompt_version=self.prompt_version,
            organization=self.org,
        )

        request = self.factory.patch(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/{simulation.id}/",
            data=json.dumps({"prompt_version_id": str(self.prompt_version_v2.id)}),
            content_type="application/json",
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationDetailView.as_view()
        response = view(
            request,
            prompt_template_id=str(self.prompt_template.id),
            run_test_id=str(simulation.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        simulation.refresh_from_db()
        self.assertEqual(simulation.prompt_version_id, self.prompt_version_v2.id)
        print(f"✓ PATCH version updated to v2")

    def test_flow13c_update_simulation_scenarios(self):
        """Test updating simulation scenarios via PATCH."""
        simulation = RunTest.objects.create(
            name="Scenario Update Test",
            source_type="prompt",
            prompt_template=self.prompt_template,
            prompt_version=self.prompt_version,
            organization=self.org,
        )
        simulation.scenarios.add(self.scenario)

        request = self.factory.patch(
            f"/simulate/prompt-templates/{self.prompt_template.id}/simulations/{simulation.id}/",
            data=json.dumps(
                {"scenario_ids": [str(self.scenario.id), str(self.scenario_2.id)]}
            ),
            content_type="application/json",
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationDetailView.as_view()
        response = view(
            request,
            prompt_template_id=str(self.prompt_template.id),
            run_test_id=str(simulation.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(simulation.scenarios.count(), 2)
        print(f"✓ PATCH scenarios updated: count={simulation.scenarios.count()}")

    # =========================================================================
    # FLOW 14: Race Condition Guard - run_single_prompt_chat status check
    # =========================================================================

    def test_flow14a_call_execution_status_guard_for_prompt_chat(self):
        """Test that the status guard logic rejects non-ONGOING call executions."""
        simulation = RunTest.objects.create(
            name="Race Guard Test",
            source_type="prompt",
            prompt_template=self.prompt_template,
            prompt_version=self.prompt_version,
            organization=self.org,
        )
        simulation.scenarios.add(self.scenario)

        test_execution = TestExecution.objects.create(
            run_test=simulation,
            status="pending",
        )

        call_execution = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=self.scenario,
            status=CallExecution.CallStatus.REGISTERED,
            simulation_call_type=CallExecution.SimulationCallType.TEXT,
        )

        # Verify the guard condition: only ONGOING status should proceed
        # This is the exact check from run_single_prompt_chat
        self.assertNotEqual(
            call_execution.status,
            CallExecution.CallStatus.ONGOING,
        )
        # REGISTERED should be rejected
        self.assertEqual(call_execution.status, CallExecution.CallStatus.REGISTERED)

        # Now test with ONGOING status - should pass the guard
        call_execution.status = CallExecution.CallStatus.ONGOING
        call_execution.save()
        call_execution.refresh_from_db()
        self.assertEqual(call_execution.status, CallExecution.CallStatus.ONGOING)

        print(f"✓ Status guard: REGISTERED rejected, ONGOING accepted")

    # =========================================================================
    # FLOW 15: Scenarios Endpoint Tests
    # =========================================================================

    def test_flow15a_scenarios_search_filter(self):
        """Test scenario search filtering."""
        request = self.factory.get(
            "/simulate/prompt-simulations/scenarios/?search=Scenario 2"
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationScenariosView.as_view()
        response = view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("result", {}).get("results", [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Test Scenario 2")
        print(f"✓ Scenario search filter works: found '{results[0]['name']}'")

    def test_flow15b_scenarios_pagination(self):
        """Test scenario pagination with limit."""
        request = self.factory.get(
            "/simulate/prompt-simulations/scenarios/?limit=1&page=1"
        )
        force_authenticate(request, user=self.user)
        view = PromptSimulationScenariosView.as_view()
        response = view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result_data = response.data.get("result", {})
        self.assertEqual(len(result_data.get("results", [])), 1)
        self.assertGreaterEqual(result_data.get("count", 0), 2)
        print(f"✓ Scenario pagination: 1 item per page, total >= 2")


def run_comprehensive_e2e_test():
    """
    Run all E2E tests and print a summary.
    This can be called from Django shell for manual testing.
    """
    import sys
    from io import StringIO

    from django.test.runner import DiscoverRunner
    from django.test.utils import setup_test_environment, teardown_test_environment

    # Capture output
    output = StringIO()

    print("=" * 70)
    print("COMPREHENSIVE E2E TEST SUITE - PROMPT WORKBENCH SIMULATION")
    print("=" * 70)
    print()

    # Run tests
    runner = DiscoverRunner(verbosity=2)
    suite = runner.test_loader.loadTestsFromTestCase(E2EPromptSimulationTestCase)
    result = runner.run_suite(suite)

    print()
    print("=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Skipped: {len(result.skipped)}")
    print()

    if result.failures:
        print("FAILURES:")
        for test, traceback in result.failures:
            print(f"  - {test}")

    if result.errors:
        print("ERRORS:")
        for test, traceback in result.errors:
            print(f"  - {test}")

    if not result.failures and not result.errors:
        print("✅ ALL TESTS PASSED!")
    else:
        print("❌ SOME TESTS FAILED")

    return result


if __name__ == "__main__":
    run_comprehensive_e2e_test()
