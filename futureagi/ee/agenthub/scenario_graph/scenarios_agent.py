import json
import traceback
import uuid

from ee.agenthub.synthetic_data_agent.synthetic_data_agent import (
    SyntheticDataAgent,
)
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from simulate.models import (
    AgentDefinition,
    Scenarios,
)
from simulate.models.scenario_graph import NodeType, ScenarioGraph


class ScenariosAgent:
    """Agent that generates GRAPH-type scenarios from an AgentDefinition.

    The graph consists of only four node types per requirement:
    - start: entry point for all branches
    - conditional: decision points
    - message: communication/response nodes
    - end: terminal nodes

    Each case/branch carries metadata columns captured from the agent plan:
    - persona: role/identity of the agent in the interaction
    - situation: the scenario or context addressed by the branch
    - outcome: the final resolution for the branch

    Branch metadata is persisted on edges as `condition.branch` and on the
    terminal End node config to enable downstream analytics.
    The resultant JSON is saved into `Scenarios.source` and normalized rows
    are created under `ScenarioGraph`, `ScenarioNode`, and `ScenarioEdge`.
    """

    def __init__(self, agent_definition_id: str):
        self.agent_definition = AgentDefinition.no_workspace_objects.get(id=agent_definition_id)

    # --------- Public API ---------
    def run(self, name: str, description: str = "", user_requirements: dict | None = None) -> tuple[Scenarios, Dataset, ScenarioGraph]:
        """Create a GRAPH scenario using agent definition and user requirements.

        Pipeline:
        1) Use SDA agent to generate persona, situation, and outcome data
        2) Create individual cases with complete conversation flows
        3) Store all data in Dataset table with scenario-specific columns
        4) Create unified graph structure from all cases
        5) Store unified graph in ScenarioGraph model
        """
        scenario = self._ensure_scenario(name=name, description=description)
        cases = self._plan_cases(user_requirements or {})
        print(cases, "cases123")

        # Create Dataset with scenario data
        dataset = self._create_scenario_dataset(scenario, cases, name, description)


        # Update scenario source with both dataset and graph references
        scenario.source = json.dumps({
            "dataset_id": str(dataset.id),
            "scenario_type": "graph",
            "created_at": dataset.created_at.isoformat()
        }, indent=2)
        scenario.scenario_type = Scenarios.ScenarioTypes.GRAPH
        scenario.dataset = dataset
        scenario.save()

        return scenario, dataset

    # --------- Scenario creation helpers ---------
    def _ensure_scenario(self, name: str, description: str) -> Scenarios:
        return Scenarios.objects.create(
            name=name,
            description=description,
            source="{}",
            scenario_type=Scenarios.ScenarioTypes.GRAPH,
            organization=self.agent_definition.organization,
            workspace=self.agent_definition.workspace,
        )

    def _create_scenario_dataset(self, scenario: Scenarios, cases: list[dict], name: str, description: str) -> Dataset:
        """Create a Dataset with scenario data stored as rows."""
        # Create the dataset
        dataset = Dataset.objects.create(
            id=uuid.uuid4(),
            name=f"{name} - Scenario Dataset",
            organization=scenario.organization,
            workspace=scenario.workspace,
            # model_type="generative_llm",  # Default model type
            source=DatasetSourceChoices.SCENARIO.value,
            column_order=[],  # Will be populated with column UUIDs
            column_config={},  # Will be populated with column configs
            dataset_config={
                "scenario_type": "graph",
                "agent_definition_id": str(self.agent_definition.id),
                "agent_name": self.agent_definition.agent_name,
                "description": description,
                "total_cases": len(cases)
            }
        )

        # Define column schema
        column_schema = {
            "persona": {
                "data_type": DataTypeChoices.TEXT.value,
                "description": "Customer persona profile"
            },
            "situation": {
                "data_type": DataTypeChoices.TEXT.value,
                "description": "Customer situation or scenario"
            },
            "outcome": {
                "data_type": DataTypeChoices.TEXT.value,
                "description": "Conversation outcome"
            },
            "conversation_flow": {
                "data_type": DataTypeChoices.JSON.value,
                "description": "Complete conversation flow as JSON array of nodes"
            }
        }

        # Create columns and store their UUIDs
        column_uuids = []
        column_config = {}

        for col_name, col_info in column_schema.items():
            print(col_name, col_info, "col_name, col_info")
            column = Column.objects.create(
                id=uuid.uuid4(),
                dataset=dataset,
                name=col_name,
                data_type=col_info["data_type"],
                source=SourceChoices.OTHERS.value,
                metadata={"description": col_info["description"]}
            )
            column_uuids.append(str(column.id))
            column_config[str(column.id)] = {
                "name": col_name,
                "type": col_info["data_type"],
                "description": col_info["description"]
            }

        # Update dataset with column_order and column_config
        dataset.column_order = column_uuids
        dataset.column_config = column_config
        dataset.save()

        # Create rows and cells for each case
        for i, case in enumerate(cases):
            print(case, "case321")
            # Create a row for this case
            row = Row.objects.create(
                id=uuid.uuid4(),
                dataset=dataset,
                order=i
            )

            # Extract the conversation flow from the case
            conversation_flow = case.get("nodes", [])

            # Create cells for each column
            for _j, column_uuid in enumerate(column_uuids):
                print(column_uuid, "column_uuid321")
                column = Column.objects.get(id=column_uuid)
                col_name = column.name

                # Get the value for this column
                if col_name == "persona":
                    value = case.get("persona", "")
                elif col_name == "situation":
                    value = case.get("situation", "")
                elif col_name == "outcome":
                    value = case.get("outcome", [""])[0] if case.get("outcome") else ""
                elif col_name == "conversation_flow":
                    value = json.dumps(conversation_flow)
                else:
                    value = ""

                # Create the cell
                Cell.objects.create(
                    id=uuid.uuid4(),
                    dataset=dataset,
                    column=column,
                    row=row,
                    value=value
                )

        return dataset


    # --------- Case planning ---------
    def _plan_cases(self, req: dict) -> list[dict]:
        """Plan scenario branches using an LLM-driven planner.

        Output cases must only use node types: start (implicit), conditional, message, end
        and include persona, situation, and outcome fields per case.
        """
        return self._plan_cases_via_llm(req)

    def _plan_cases_via_llm(self, req: dict) -> list[dict]:
        """Use SDA agent to generate persona, situation, and outcome data, then create nodes for each situation."""
        try:
            sda = SyntheticDataAgent()

            # Create SDA payload for generating persona, situation, and outcome data
            sda_payload = {
                'requirements': {
                    'Dataset Name': f'{self.agent_definition.agent_name.lower().replace(" ", "_")}_persona_dataset',
                    'Dataset Description': f'Create realistic customer personas and scenarios for {self.agent_definition.agent_name}, '
                                         f'supporting languages {self.agent_definition.languages} AI voice agent. '
                                         f'Agent Purpose: {self.agent_definition.description}. '
                                         f'Contact: {self.agent_definition.contact_number}. '
                                         f'Call Type: {"Inbound" if self.agent_definition.inbound else "Outbound"}. '
                                         f'Generate diverse customer variations that would realistically interact with this specific agent.',
                    'Objective': f'Generate comprehensive training data for {self.agent_definition.agent_name} to simulate '
                               f'real-world customer interactions and improve agent performance',
                    'patterns': f'Focus on cultural context, '
                              f'and scenarios relevant to {self.agent_definition.agent_name}\'s domain'
                },
                'constraints': [
                    {
                        'field': 'persona',
                        'type': 'text',
                        'content': f'Detailed customer persona that would realistically call {self.agent_definition.agent_name}. '
                                 f'Include demographics, background, personality traits, communication style, '
                                 f'technical proficiency, emotional state, and specific needs. '
                                 f'Consider the agent\'s purpose: {self.agent_definition.description}. '
                                 f'Examples: "Tech-savvy millennial from Mumbai, works in finance, impatient with delays, '
                                 f'prefers quick solutions, speaks {self.agent_definition.languages} fluently" or '
                                 f'"Elderly customer from rural area, limited tech experience, needs patient guidance, '
                                 f'concerned about security, speaks {self.agent_definition.languages} with regional accent".',
                        'name': 'persona',
                        'description': f'Comprehensive customer persona profile for {self.agent_definition.agent_name} interactions. '
                                     f'Should reflect realistic demographics, personality, and communication preferences '
                                     f'that align with the agent\'s purpose and target audience.',
                        'data_type': 'text',
                        'property': {
                            'min_length': 50,
                            'max_length': 500,
                            'required_fields': ['age_group', 'location', 'profession', 'personality', 'communication_style', 'technical_level']
                        }
                    },
                    {
                        'field': 'situation',
                        'type': 'text',
                        'content': f'Specific situation or scenario that would prompt a customer to call {self.agent_definition.agent_name}. '
                                 f'Based on agent purpose: {self.agent_definition.description}. '
                                 f'Include context, urgency level, previous interactions, and specific customer needs. '
                                 f'Consider {self.agent_definition.languages} language context and cultural factors. '
                                 f'Examples: "Customer received damaged product, called 3 times already, frustrated with delays, '
                                 f'needs immediate replacement" or "New customer wants to understand pricing, confused about options, '
                                 f'needs detailed explanation in {self.agent_definition.languages}". '
                                 f'Make situations realistic and relevant to the agent\'s domain.',
                        'name': 'situation',
                        'description': f'Realistic customer situation that would lead to calling {self.agent_definition.agent_name}. '
                                     f'Should be contextually relevant to the agent\'s purpose and include specific details '
                                     f'about the customer\'s problem, urgency, and expectations.',
                        'data_type': 'text',
                        'property': {
                            'min_length': 30,
                            'max_length': 300,
                            'required_elements': ['problem_description', 'urgency_level', 'customer_context', 'expected_resolution']
                        }
                    },
                    {
                        'field': 'outcome',
                        'type': 'array',
                        'content': f'List of all possible conversation outcomes for {self.agent_definition.agent_name} interactions. '
                                 f'Based on agent purpose: {self.agent_definition.description}. '
                                 f'Include both positive and negative outcomes, escalation scenarios, and edge cases. '
                                 f'Consider the agent\'s capabilities and limitations. '
                                 f'Examples: ["Customer issue resolved successfully, satisfied with service", '
                                 f'"Customer escalated to human agent due to complex issue", '
                                 f'"Customer hung up frustrated due to agent misunderstanding", '
                                 f'"Customer requested callback but didn\'t answer", '
                                 f'"Customer provided incomplete information, call ended inconclusively"]. '
                                 f'Each outcome should be specific and measurable.',
                        'name': 'outcome',
                        'description': f'Comprehensive list of possible conversation outcomes for {self.agent_definition.agent_name}. '
                                     f'Should cover success scenarios, failure cases, escalations, and edge cases '
                                     f'relevant to the agent\'s specific purpose and capabilities.',
                        'data_type': 'array',
                        'property': {
                            'min_items': 3,
                            'max_items': 8,
                            'item_type': 'string',
                            'item_min_length': 20,
                            'item_max_length': 150
                        }
                    }
                ],
                'schema': {
                    'persona': {'type': 'text'},
                    'situation': {'type': 'text'},
                    'outcome': {'type': 'array'}
                },
                'batch_size': 5
            }

            # Generate data using SDA agent
            generated_data = sda.generate_and_validate(sda_payload)

            print(generated_data, "generated_data")

            # Save generated data to file
            import os
            from datetime import datetime

            # Create logs directory if it doesn't exist
            logs_dir = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'logs')
            os.makedirs(logs_dir, exist_ok=True)

            # Generate filename with timestamp
            datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = "generated_data.json"
            filepath = os.path.join(filename)

            # Save the data as JSON
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(generated_data, f, indent=2, ensure_ascii=False, default=str)

            print(f"Generated data saved to: {filepath}")

            # Convert SDA results to scenario cases
            cases = self._convert_sda_data_to_cases(generated_data)
            print(cases, "cases123")
            return self._normalize_cases(cases, req)

        except Exception as e:
            traceback.print_exc()
            print(f"Error in SDA generation: {e}")
            # Fallback to minimal examples
            fallback = [

            ]
            return self._normalize_cases(fallback, req)

    def _convert_sda_data_to_cases(self, generated_data) -> list[dict]:
        """Convert SDA generated data into scenario cases with nodes for each situation."""
        cases: list[Any] = []

        try:
            # Extract the generated data from SDA response
            if isinstance(generated_data, dict) and 'data' in generated_data:
                data_rows = generated_data['data']
            elif isinstance(generated_data, list):
                data_rows = generated_data
            else:
                # If it's a DataFrame or other format, convert to list of dicts
                if hasattr(generated_data, 'to_dict'):
                    data_rows = generated_data.to_dict('records')
                else:
                    data_rows = generated_data

            for i, row in enumerate(data_rows):
                persona = row.get('persona', '')
                situation = row.get('situation', '')
                outcomes = row.get('outcome', [])

                # Ensure outcomes is a list
                if isinstance(outcomes, str):
                    try:
                        outcomes = json.loads(outcomes)
                    except:
                        outcomes = [outcomes]
                elif not isinstance(outcomes, list):
                    outcomes = [str(outcomes)]

                # Create flows for this situation - each outcome gets its own flow
                flows = self._create_nodes_for_situation(situation, outcomes)
                print(flows, "flows123")

                # Create a case for each flow (each outcome)
                for j, flow in enumerate(flows):
                    case = {
                        "name": f"Case_{i+1}_{j+1}_{situation[:20]}_{flow['outcome'][:20]}",
                        "persona": persona,
                        "situation": situation,
                        "outcomes": [flow['outcome']],  # Single outcome per case
                        "nodes": flow['nodes']
                    }
                    cases.append(case)
                if i == 2:
                    break
        except Exception as e:
            print(f"Error converting SDA data to cases: {e}")



        return cases

    def _create_nodes_for_situation(self, situation: str, outcomes: list[str]) -> list[dict]:
        """Use LLM to create appropriate nodes for a given situation and its possible outcomes."""
        try:
            sda = SyntheticDataAgent()
            llm = sda.llm

            prompt = f"""
You are designing detailed conversation flows for an AI voice agent to handle a specific customer situation.

Situation: {situation}
Possible Outcomes: {json.dumps(outcomes, indent=2)}
Agent Description: {getattr(self.agent_definition, 'description', '')}
Language: {getattr(self.agent_definition, 'language', '')}

Create SEPARATE, DETAILED conversation flows for EACH possible outcome. Each flow should be a realistic, natural conversation that gradually builds up to that specific outcome.

CRITICAL REQUIREMENTS:
- Create ONE complete flow per outcome (no complex conditionals)
- Each flow should be 6-12 messages long (alternating between user and assistant)
- Make conversations feel natural, realistic, and emotionally authentic
- Show the progression of emotions, reactions, and responses
- Include realistic details, specific information, and natural language patterns
- Each flow must end with an End node
- Use ONLY these node types: message, end

For each outcome, create a flow that shows:
1. Assistant's initial response to the situation
2. User's emotional reaction and specific concerns
3. Assistant's empathetic response and attempt to help
4. User's continued concerns or new information
5. Assistant's detailed solution or next steps
6. User's response to the solution
7. Assistant's follow-up or clarification
8. User's final response (leading to the outcome)
9. End node with the outcome reason

Make each conversation feel like a real customer service interaction with:
- Specific details (order numbers, product names, dates, etc.)
- Emotional progression (frustration, hope, satisfaction, etc.)
- Natural language patterns and realistic responses
- Detailed explanations and solutions
- Authentic customer concerns and agent responses

Return strict JSON (no markdown fences) with this schema:
{{
  "flows": [
    {{
      "outcome": "Outcome 1 description",
      "nodes": [
        {{"type": "message", "label": "Assistant Initial Response", "config": {{"text": "...", "speaker": "assistant"}}}},
        {{"type": "message", "label": "User Emotional Reaction", "config": {{"text": "...", "speaker": "user"}}}},
        {{"type": "message", "label": "Assistant Empathetic Response", "config": {{"text": "...", "speaker": "assistant"}}}},
        {{"type": "message", "label": "User Specific Concerns", "config": {{"text": "...", "speaker": "user"}}}},
        {{"type": "message", "label": "Assistant Detailed Solution", "config": {{"text": "...", "speaker": "assistant"}}}},
        {{"type": "message", "label": "User Response to Solution", "config": {{"text": "...", "speaker": "user"}}}},
        {{"type": "message", "label": "Assistant Follow-up", "config": {{"text": "...", "speaker": "assistant"}}}},
        {{"type": "message", "label": "User Final Response", "config": {{"text": "...", "speaker": "user"}}}},
        {{"type": "end", "label": "End", "config": {{"reason": "Outcome 1 description"}}, "terminal": true}}
      ]
    }},
    {{
      "outcome": "Outcome 2 description",
      "nodes": [
        {{"type": "message", "label": "Assistant Initial Response", "config": {{"text": "...", "speaker": "assistant"}}}},
        {{"type": "message", "label": "User Emotional Reaction", "config": {{"text": "...", "speaker": "user"}}}},
        {{"type": "message", "label": "Assistant Empathetic Response", "config": {{"text": "...", "speaker": "assistant"}}}},
        {{"type": "message", "label": "User Specific Concerns", "config": {{"text": "...", "speaker": "user"}}}},
        {{"type": "message", "label": "Assistant Detailed Solution", "config": {{"text": "...", "speaker": "assistant"}}}},
        {{"type": "message", "label": "User Response to Solution", "config": {{"text": "...", "speaker": "user"}}}},
        {{"type": "end", "label": "End", "config": {{"reason": "Outcome 2 description"}}, "terminal": true}}
      ]
    }}
  ]
}}
"""

            response = llm._get_completion_content(messages=[{"role": "user", "content": prompt}])

            try:
                data = json.loads(response)
            except Exception:
                response = response.strip()
                if response.startswith("```json"):
                    response = response[7:]
                if response.endswith("```"):
                    response = response[:-3]
                data = json.loads(response)

            flows = data.get("flows", [])

            # Ensure we have at least one flow
            if not flows:
                raise Exception("No flows generated")

            # Return all flows - each outcome should have its own complete flow
            # The calling method will handle multiple flows appropriately
            all_flows = []
            for flow in flows:
                nodes = flow.get("nodes", [])
                if nodes:
                    all_flows.append({
                        "outcome": flow.get("outcome", "Unknown outcome"),
                        "nodes": nodes
                    })

            if not all_flows:
                raise Exception("No valid flows generated")

            return all_flows

        except Exception as e:
            print(f"Error creating nodes with LLM: {e}")
            # Fallback to a simple flow
            return [

            ]

    def _ensure_all_branches_end(self, nodes: list[dict], outcomes: list[str]) -> list[dict]:
        """Ensure all branches in conditional nodes end with End nodes."""
        for node in nodes:
            if node.get("type") == "conditional" and "config" in node:
                branches = node["config"].get("branches", [])
                for branch in branches:
                    # Check if branch has a "then" array and if it ends with an End node
                    then_nodes = branch.get("then", [])
                    if not then_nodes or not self._ends_with_end_node(then_nodes):
                        # Add an End node to this branch
                        end_node = {
                            "type": "end",
                            "label": "End",
                            "config": {
                                "reason": branch.get("situation", "completed")
                            },
                            "terminal": True
                        }
                        then_nodes.append(end_node)
                        branch["then"] = then_nodes

        return nodes

    def _ends_with_end_node(self, nodes: list[dict]) -> bool:
        """Check if a list of nodes ends with an End node."""
        if not nodes:
            return False
        last_node = nodes[-1]
        return (NodeType.is_terminal(last_node.get("type", "")) or
                last_node.get("terminal", False))

    def _normalize_cases(self, cases: list[dict], req: dict) -> list[dict]:
        """Normalize cases to allowed node types and ensure metadata + terminal End.

        Allowed node types in output: message, conditional, end.
        Start is implicit and removed if present.
        Persona can be either a string or an object with richer metadata
        such as {"role": "...", "age": 32, "tone": "warm", "region": "IN"}.
        Multiple situations can be supplied via `situations: [ ... ]` in
        addition to a singular `situation` string.
        """
        normalized: list[dict] = []
        allowed_types = {NodeType.START, NodeType.MESSAGE, "conditional", NodeType.END}
        for c in cases:
            name = str(c.get("name", "Case")).strip() or "Case"
            persona_raw = c.get("persona", "")
            if isinstance(persona_raw, dict):
                persona = persona_raw
            else:
                persona = str(persona_raw).strip()
            situations_list = c.get("situations") or []
            if isinstance(situations_list, str):
                situations_list = [situations_list]
            single_situation = str(c.get("situation", "")).strip()
            if single_situation and single_situation not in situations_list:
                situations_list.append(single_situation)
            outcome = str(c.get("outcomes", "")).strip()
            nodes: list[dict] = []
            for n in c.get("nodes", []):
                t_raw = str(n.get("type", "")).lower()
                if t_raw == NodeType.START:
                    # Skip explicit start nodes; start is added globally
                    continue
                if t_raw not in allowed_types and t_raw != "condition":
                    # Drop unsupported types
                    continue
                # Map to string constants
                if t_raw in ("condition", "conditional"):
                    t = NodeType.CONDITION
                elif t_raw == NodeType.MESSAGE:
                    t = NodeType.MESSAGE
                elif t_raw == NodeType.END:
                    t = NodeType.END
                elif t_raw == NodeType.START:
                    t = NodeType.START
                else:
                    # Defensive: skip unknown
                    continue
                nodes.append({
                    "type": t,
                    "label": n.get("label", ("End" if t == NodeType.END else NodeType.get_display_name(t))),
                    "config": n.get("config") or {},
                    "terminal": bool(n.get("terminal", NodeType.is_terminal(t))),
                })
            # Ensure branch ends with an End node
            if not nodes or not NodeType.is_terminal(nodes[-1]["type"]):
                nodes.append({
                    "type": NodeType.END,
                    "label": "End",
                    "config": {"reason": outcome or "completed"},
                    "terminal": True,
                })
            normalized.append({
                "name": name,
                "persona": persona,
                # "situations": situations_list,
                "situation": single_situation,
                "outcome": outcome,
                "nodes": nodes,
            })
        print(normalized, "normalized")
        return normalized

    # The prior schema-driven generation path was removed in favor of a
    # simpler, explicit branch planner per new requirements.

