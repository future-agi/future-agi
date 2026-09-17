import json
import traceback
from dataclasses import dataclass
from enum import Enum
from typing import Any

import requests
from django.utils import timezone
from ee.agenthub.scenario_graph.prompt import (
    VOICE_GRAPH_PROMPT,
    CHAT_GRAPH_PROMPT,
)
from ee.agenthub.synthetic_data_agent.synthetic_data_agent import (
    SyntheticDataAgent,
)
from simulate.models import AgentDefinition, Scenarios
from simulate.models.agent_definition import AgentTypeChoices
from simulate.models.scenario_graph import ScenarioGraph

from ee.agenthub.scenario_graph.graph_refiner import (
    GraphRefiner,
    GraphNode,
    GraphEdge,
    NodeType,
    NodePosition,
    EdgeCondition,
)


class ConversationGraphGenerator:
    """Generates comprehensive conversation graphs from AgentDefinition using LLM-first approach"""

    def __init__(
        self,
        agent_definition_id: str = None,
        script_url: str | None = None,
        scenario: Scenarios | None = None,
        simulation_mode: str = None,
        agent_definition=None,
    ):
        """
        Initialize the ConversationGraphGenerator.

        Args:
            agent_definition_id: UUID of an AgentDefinition record in the database.
                Used for SDK-based agent simulations where the agent config is stored in DB.
            script_url: Optional URL to a script file for script-based graph generation.
            scenario: Optional Scenarios instance for metadata extraction (custom_instruction,
                agent_definition_version_id for configuration_snapshot).
            simulation_mode: Override mode ('voice' or 'chat'). If not provided,
                mode is determined from configuration_snapshot or agent_type.
            agent_definition: Pre-built agent definition object (adapter pattern).
                Used for prompt-based scenarios where we create an in-memory adapter
                that mimics the AgentDefinition interface without a database record.

        Note:
            Either `agent_definition_id` OR `agent_definition` must be provided.
            - Use `agent_definition_id` for SDK-based agents (fetches from DB)
            - Use `agent_definition` for prompt-based scenarios (adapter object)

            The adapter object should have these attributes:
            - agent_name: str
            - description: str
            - agent_type: 'voice' or 'text'
            - languages: list[str]
            - inbound: bool
            - organization: Organization
            - workspace: Workspace (optional)
            - contact_number: str (optional)
            - id: UUID (for logging/tracking)

            This adapter pattern allows prompt templates to reuse graph generation
            logic without creating temporary AgentDefinition records in the database.
        """
        self.agent_definition_id = agent_definition_id
        self.script_url = script_url
        self.script_content = None
        self.sda = SyntheticDataAgent()
        self.scenario = scenario

        if agent_definition is not None:
            # Use pre-built object (e.g. adapter for prompt-based scenarios)
            self.agent_definition = agent_definition
        elif agent_definition_id:
            self.agent_definition = AgentDefinition.no_workspace_objects.get(
                id=agent_definition_id
            )
        else:
            raise ValueError(
                "Either agent_definition_id or agent_definition is required."
            )

        # Extract optional metadata from scenario if available
        self.custom_instruction = None
        self.configuration_snapshot = None
        if self.scenario and self.scenario.metadata:
            metadata = self.scenario.metadata
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {}
            self.custom_instruction = metadata.get("custom_instruction")
            # Attempt to load configuration_snapshot using agent_definition_version_id if present
            agent_definition_version_id = metadata.get("agent_definition_version_id")
            if agent_definition_version_id:
                try:
                    from simulate.models.agent_version import AgentVersion

                    version = AgentVersion.objects.filter(
                        id=agent_definition_version_id
                    ).first()
                    if version:
                        self.configuration_snapshot = version.configuration_snapshot
                except Exception:
                    self.configuration_snapshot = None

        # Determine mode: prefer configuration_snapshot if available, else fall back to agent_definition
        if self.configuration_snapshot:
            agent_type = self.configuration_snapshot.get(
                "agent_type", AgentTypeChoices.VOICE
            )
        else:
            agent_type = getattr(
                self.agent_definition, "agent_type", AgentTypeChoices.VOICE
            )
        self.mode = "voice" if agent_type == AgentTypeChoices.VOICE else "chat"
        if simulation_mode:
            self.mode = simulation_mode

        # Graph components
        self.nodes: list[GraphNode] = []
        self.edges: list[GraphEdge] = []
        self.node_positions: dict[str, NodePosition] = {}

        # Download script content if URL is provided
        if self.script_url:
            if self._validate_script_url():
                self.script_content = self._download_script_content()
            else:
                print(f"Warning: Invalid script URL format: {self.script_url}")
                self.script_url = None

    def _validate_script_url(self) -> bool:
        """Validate that the script URL is properly formatted"""
        try:
            if not self.script_url:
                return False

            # Basic URL validation
            if not self.script_url.startswith(("http://", "https://")):
                return False

            # Check for common file extensions that might contain scripts
            valid_extensions = [".txt", ".json", ".js", ".py", ".md", ".csv", ".xml"]
            url_lower = self.script_url.lower()

            # If URL doesn't have an extension, assume it's valid (could be an API endpoint)
            if not any(url_lower.endswith(ext) for ext in valid_extensions):
                # Still valid if it's a proper URL format
                return "://" in self.script_url

            return True

        except Exception as e:
            print(f"Error validating script URL: {e}")
            return False

    def _download_script_content(self) -> str | None:
        """Download and return script content from the provided URL"""
        try:
            print(f"Downloading script content from: {self.script_url}")

            # Make request to download the script
            response = requests.get(self.script_url or "", timeout=30)
            response.raise_for_status()

            # Get the content
            content = response.text

            print(
                f"✓ Successfully downloaded script content ({len(content)} characters)"
            )
            return content

        except requests.exceptions.RequestException as e:
            print(f"Error downloading script from URL {self.script_url}: {e}")
            return None
        except Exception as e:
            print(f"Unexpected error downloading script: {e}")
            traceback.print_exc()
            return None

    def generate_graph(self, save_to_db: bool = True) -> dict[str, Any]:
        """
        Generate a complete conversation graph from the agent definition using LLM-first approach.

        This method:
        1. Uses LLM to generate all possible nodes and edges comprehensively
        2. Combines them into a complete graph structure
        3. Handles positioning and metadata automatically
        4. Optionally saves the graph to database for later retrieval

        Args:
            save_to_db: Whether to save the generated graph to the database

        Returns:
            Dict containing the complete graph structure with nodes and edges
        """
        try:
            # Clear previous data
            self.nodes = []
            self.edges = []
            self.node_positions = {}

            # Step 1: Use LLM to generate comprehensive graph structure
            print(
                f"Step 1: Generating comprehensive {self.mode} graph structure with LLM..."
            )
            graph_data = self._generate_comprehensive_graph_with_llm()

            # Step 2: Process, Validate, Refine, and Parse using GraphRefiner
            print("Step 2: Refining and validating graph...")
            refiner = GraphRefiner(
                simulation_mode=self.mode, agent_name=self.agent_definition.agent_name
            )
            process_result = refiner.process_graph(graph_data)

            # Update local state with processed objects
            self.nodes = process_result.nodes
            self.edges = process_result.edges
            self.node_positions = process_result.node_positions
            final_graph = process_result.final_graph

            # Step 3: Save to database if requested
            if save_to_db:
                print("Step 3: Saving graph to database...")
                graph_data = self._save_graph_to_database(final_graph)
                return graph_data

            print("✓ Graph generated successfully!")
            print(f"  - Nodes: {len(self.nodes)}")
            print(f"  - Edges: {len(self.edges)}")
    
            return final_graph

        except Exception as e:
            print(f"Error generating graph: {e}")
            traceback.print_exc()
            return self._get_fallback_graph()

    def _generate_comprehensive_graph_with_llm(self) -> dict[str, Any]:
        """Use LLM to generate comprehensive graph structure in one go"""
        try:
            agent_purpose = self.agent_definition.description
            conversation_type = self._classify_conversation_type(agent_purpose)

            # Parse script content if available

            # Build the prompt with agent details
            situation_type = (
                """- Call Type:""" if self.mode == "voice" else """- Chat Type:"""
            )
            agent_details = f"""
Agent Details:
- Name: {self.agent_definition.agent_name}
- Purpose: {self.agent_definition.description}
- Supported Languages: {self.agent_definition.languages}
{situation_type} {"Inbound" if self.agent_definition.inbound else "Outbound"}
- Contact: {self.agent_definition.contact_number}
- Conversation Type: {conversation_type}
"""

            # Build script-specific instructions
            script_instructions = ""
            if self.script_content:
                script_instructions = f"""

SCRIPT-BASED GRAPH GENERATION:
Since a script has been provided for this agent, you MUST incorporate the script content into your graph generation:

1. **Follow the Script Structure**: Use the provided script as the primary guide for conversation flow
2. **Extract Key Elements**: Identify main conversation points, transitions, and decision points from the script
3. **Preserve Script Intent**: Maintain the original purpose and flow intended by the script
4. **Enhance with Edge Cases**: Add additional nodes and edges for scenarios not covered in the script
5. **Script Integration**:
   - If the script is JSON structured, parse it and use the defined flow
   - If the script is text-based, extract conversation stages and key phrases
   - Create nodes that align with the script's conversation progression
   - Add edges that follow the script's logical flow

Script Content Preview:
{self.script_content}

IMPORTANT: The generated graph should primarily follow the script structure while adding comprehensive edge cases and error handling.
"""

            # Format the prompt
            prompt_template = (
                CHAT_GRAPH_PROMPT if self.mode == "chat" else VOICE_GRAPH_PROMPT
            )
            prompt = prompt_template.format(
                agent_details=agent_details,
                script_instructions=script_instructions,
                conversation_type=self._get_conversation_type_guidelines(
                    conversation_type
                ),
            )
            response = self.sda.llm._get_completion_content(
                messages=[{"role": "user", "content": prompt}]
            )
            # Debug: Print response length and first 200 chars
            print(f"LLM Response length: {len(response)}")
            print(f"LLM Response preview: {response}...")

            try:
                # Clean the response first
                response = response.strip()

                # Remove markdown code blocks if present
                if response.startswith("```json"):
                    response = response[7:]
                if response.startswith("```"):
                    response = response[3:]
                if response.endswith("```"):
                    response = response[:-3]

                # Try to parse the JSON
                data = json.loads(response)

                # Validate that we have the required structure
                if not isinstance(data, dict) or "nodes" not in data:
                    raise ValueError("Invalid JSON structure - missing 'nodes' key")

                return data

            except json.JSONDecodeError as e:
                response = self._fix_common_json_issues(response)

                print(f"JSON parsing error: {e}")
                print(f"Response preview: {response[:500]}...")
                print("Falling back to generated graph...")
                return json.loads(response)
            except Exception as e:
                print(f"Error parsing LLM response: {e}")
                print(f"Response preview: {response[:500]}...")
                print("Falling back to generated graph...")
                return self._get_fallback_graph()

        except Exception as e:
            print(f"Error generating comprehensive graph with LLM: {e}")
            traceback.print_exc()
            return self._get_fallback_graph()

    def _get_fallback_graph(self) -> dict[str, Any]:
        """Fallback graph if generation completely fails"""
        return {
            "name": f"{self.agent_definition.agent_name} - Basic Graph",
            "nodes": [
                {
                    "name": "introduction",
                    "type": "conversation",
                    "isStart": True,
                    "metadata": {"position": {"x": -400, "y": -100}},
                    "prompt": f"Hello, this is {self.agent_definition.agent_name}. How can I help you today?",
                },
                {
                    "name": "transfer_call"
                    if self.mode == "voice"
                    else "transfer_chat",
                    "type": "tool",
                    "metadata": {"position": {"x": 0, "y": 200}},
                    "tool": {
                        "type": "transferCall"
                        if self.mode == "voice"
                        else "transferChat",
                        "function": {
                            "name": "transfer_call"
                            if self.mode == "voice"
                            else "transfer_chat",
                            "parameters": {
                                "type": "object",
                                "required": [],
                                "properties": {},
                            },
                        },
                        "destinations": [],
                    },
                },
            ],
            "edges": [
                {
                    "from": "introduction",
                    "to": "transfer_call" if self.mode == "voice" else "transfer_chat",
                    "condition": {
                        "type": "ai",
                        "prompt": "User needs human assistance",
                    },
                }
            ],
            "globalPrompt": "",
        }

    def _get_graph_data_from_database(self, graph_id: str) -> dict[str, Any] | None:
        """Get graph data from database for a given graph ID"""
        try:
            scenario_graph = ScenarioGraph.no_workspace_objects.get(id=graph_id)
            graph_config = scenario_graph.graph_config or {}
            graph_data = graph_config.get("graph_data", {})
            return graph_data
        except ScenarioGraph.DoesNotExist:
            print(f"ScenarioGraph with id {graph_id} not found")
            return None
        except Exception as e:
            print(f"Error fetching graph data from database: {e}")
            return None

    def _convert_json_node_to_graph_node(self, json_node: dict[str, Any]) -> GraphNode:
        """Convert JSON node data to GraphNode format"""
        try:
            # Determine node type
            node_type = NodeType.CONVERSATION
            if json_node.get("type") == "tool":
                node_type = NodeType.TOOL
            elif json_node.get("globalNodePlan"):
                node_type = NodeType.GLOBAL

            # Create GraphNode with appropriate properties
            graph_node = GraphNode(
                name=json_node.get("name", ""),
                node_type=node_type,
                is_start=json_node.get("isStart", False),
                is_global=bool(json_node.get("globalNodePlan")),
                prompt=json_node.get("prompt", ""),
                message_plan=json_node.get("messagePlan"),
                variable_extraction_plan=json_node.get("variableExtractionPlan"),
                global_node_plan=json_node.get("globalNodePlan"),
                tool_config=json_node.get("tool"),
            )
            return graph_node
        except Exception as e:
            print(f"Error converting JSON node to graph node: {e}")
            # Return a basic node as fallback
            return GraphNode(
                name=json_node.get("name", "unknown"),
                node_type=NodeType.CONVERSATION,
                is_start=False,
                is_global=False,
                prompt="",
            )

    def _convert_json_edge_to_graph_edge(self, json_edge: dict[str, Any]) -> GraphEdge:
        """Convert JSON edge data to GraphEdge format"""
        try:
            # Extract condition from JSON edge
            condition_data = json_edge.get("condition", {})

            # Create GraphEdge with appropriate properties
            graph_edge = GraphEdge(
                from_node=json_edge.get("from", ""),
                to_node=json_edge.get("to", ""),
                condition=EdgeCondition(
                    type=condition_data.get("type", "ai"),
                    prompt=condition_data.get("prompt", ""),
                ),
            )
            return graph_edge
        except Exception as e:
            print(f"Error converting JSON edge to graph edge: {e}")
            # Return a basic edge as fallback
            return GraphEdge(
                from_node=json_edge.get("from", "unknown"),
                to_node=json_edge.get("to", "unknown"),
                condition=EdgeCondition(type="ai", prompt=""),
            )

    def get_branches(self, graph_id: str | None = None) -> list[dict[str, Any]]:
        """Extract all possible conversation branches from the graph"""
        try:
            branches: list[Any] = []

            # First try to get start nodes from self.nodes
            start_nodes = [node for node in self.nodes if node.is_start]

            # If no start nodes found in self.nodes and graph_id is provided, fetch from database
            if not start_nodes and graph_id:
                graph_data = self._get_graph_data_from_database(graph_id)
                if graph_data:
                    # Extract nodes and edges from JSON data
                    json_nodes = graph_data.get("nodes", [])
                    json_edges = graph_data.get("edges", [])

                    if json_nodes:
                        # Convert JSON nodes to GraphNode format for processing
                        start_nodes = [
                            self._convert_json_node_to_graph_node(node)
                            for node in json_nodes
                            if node.get("isStart", False)
                        ]
                        # Also update self.edges with JSON edges for path finding
                        self.edges.extend(
                            [
                                self._convert_json_edge_to_graph_edge(edge)
                                for edge in json_edges
                            ]
                        )

            for start_node in start_nodes:
                self._find_branches_from_node(
                    start_node.name, [start_node.name], branches, graph_id
                )

            return branches

        except Exception as e:
            print(f"Error extracting branches: {e}")
            traceback.print_exc()
            return []

    def get_branch_with_messages_and_prompts(
        self, branch: dict[str, Any], graph_id: str | None = None
    ) -> dict[str, Any]:
        """Get detailed information for each node in a branch including messages and prompts"""
        try:
            graph_data = (
                self._get_graph_data_from_database(graph_id) if graph_id else None
            )
            if not graph_data:
                print("No graph data found")
                return branch

            json_nodes = graph_data.get("nodes", [])
            json_edges = graph_data.get("edges", [])

            # Create a lookup dictionary for nodes
            node_lookup = {node["name"]: node for node in json_nodes}
            edge_lookup: dict[Any, Any] = {}

            # Create a lookup for edges (from_node -> list of edges)
            for edge in json_edges:
                from_node = edge["from"]
                if from_node not in edge_lookup:
                    edge_lookup[from_node] = []
                edge_lookup[from_node].append(edge)

            # Process each node in the path
            detailed_path = []
            path = branch.get("path", [])

            for i, node_name in enumerate(path):
                node_data = node_lookup.get(node_name, {})

                # Get the edge condition for this transition (if not the last node)
                edge_condition = None
                if i < len(path) - 1:
                    next_node = path[i + 1]
                    edges_from_current = edge_lookup.get(node_name, [])
                    for edge in edges_from_current:
                        if edge["to"] == next_node:
                            edge_condition = edge["condition"]
                            break

                # Extract node information
                node_info = {
                    "name": node_name,
                    "type": node_data.get("type", "unknown"),
                    "prompt": node_data.get("prompt", ""),
                    "isStart": node_data.get("isStart", False),
                    "isGlobal": node_data.get("isGlobal", False),
                    "messagePlan": node_data.get("messagePlan", {}),
                    "variableExtractionPlan": node_data.get(
                        "variableExtractionPlan", {}
                    ),
                    "globalNodePlan": node_data.get("globalNodePlan", {}),
                    "tool": node_data.get("tool", {}),
                    "metadata": node_data.get("metadata", {}),
                    "edgeCondition": edge_condition,
                }

                # Extract first message if available
                if (
                    node_info["messagePlan"]
                    and "firstMessage" in node_info["messagePlan"]
                ):
                    node_info["firstMessage"] = node_info["messagePlan"]["firstMessage"]

                # Extract tool messages if available
                if node_info["tool"] and "messages" in node_info["tool"]:
                    tool_messages = node_info["tool"]["messages"]
                    if tool_messages:
                        node_info["toolMessage"] = tool_messages[0].get("content", "")

                detailed_path.append(node_info)

            # Return enhanced branch with detailed node information
            enhanced_branch = branch.copy()
            enhanced_branch["detailedPath"] = detailed_path

            return enhanced_branch

        except Exception as e:
            print(f"Error getting branch with messages and prompts: {e}")
            traceback.print_exc()
            return branch

    def get_all_branches_with_details(
        self, graph_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Get all branches with detailed messages and prompts for each node"""
        try:
            branches = self.get_branches(graph_id)
            detailed_branches = []

            for branch in branches:
                detailed_branch = self.get_branch_with_messages_and_prompts(
                    branch, graph_id
                )
                detailed_branches.append(detailed_branch)

            return detailed_branches

        except Exception as e:
            print(f"Error getting all branches with details: {e}")
            traceback.print_exc()
            return []

    def _find_branches_from_node(
        self,
        current_node: str,
        path: list[str],
        branches: list[dict[str, Any]],
        graph_id: str | None = None,
    ):
        """Recursively find all branches from a given node"""
        try:
            # Find outgoing edges from current node
            outgoing_edges = [
                edge for edge in self.edges if edge.from_node == current_node
            ]

            # If no outgoing edges found and graph_id is provided, try to fetch from database
            if not outgoing_edges and graph_id:
                try:
                    graph_data = self._get_graph_data_from_database(graph_id)
                    if graph_data:
                        json_edges = graph_data.get("edges", [])
                        # Find edges from JSON data that start from current node
                        for json_edge in json_edges:
                            if json_edge.get("from") == current_node:
                                # Convert and add to self.edges for processing
                                graph_edge = self._convert_json_edge_to_graph_edge(
                                    json_edge
                                )
                                if graph_edge not in self.edges:
                                    self.edges.append(graph_edge)
                        # Re-fetch outgoing edges after adding JSON edges
                        outgoing_edges = [
                            edge
                            for edge in self.edges
                            if edge.from_node == current_node
                        ]
                except Exception as e:
                    print(
                        f"Error fetching edges from database for node {current_node}: {e}"
                    )

            if not outgoing_edges:
                # This is an end node - create a branch
                branch = {
                    "start_node": path[0],
                    "end_node": current_node,
                    "path": path.copy(),
                    "length": len(path),
                }
                branches.append(branch)
                return

            # Continue from each outgoing edge
            for edge in outgoing_edges:
                if edge.to_node not in path:  # Avoid cycles
                    new_path = path + [edge.to_node]
                    self._find_branches_from_node(
                        edge.to_node, new_path, branches, graph_id
                    )

        except Exception as e:
            print(f"Error finding branches from node {current_node}: {e}")
            traceback.print_exc()

    def _save_graph_to_database(
        self, graph_data: dict[str, Any]
    ) -> ScenarioGraph | None:
        """Save the generated graph to the database (JSON-only approach)"""
        try:
            # Add metadata to graph_data
            graph_data["generated_at"] = timezone.now().isoformat()
            graph_data["agent_definition_id"] = str(self.agent_definition.id)
            graph_data["agent_name"] = self.agent_definition.agent_name

            # Add script information if available
            if self.script_url:
                graph_data["script_url"] = self.script_url

                if self.script_content:
                    graph_data["script_info"] = {
                        # "content_preview": script_info.get("content_preview"),
                        "has_script": True
                    }
                else:
                    graph_data["script_info"] = {"has_script": False}
            else:
                graph_data["script_info"] = {"has_script": False}

            # Create or get a scenario for this agent

            # Create or get a scenario graph for this agent
            scenario_graph = ScenarioGraph.objects.create(
                name=f"{self.agent_definition.agent_name} - Generated Graph",
                scenario=self.scenario,
                organization=self.agent_definition.organization,
                description=f"Auto-generated conversation graph for {self.agent_definition.agent_name}",
                graph_config={
                    "agent_definition_id": str(self.agent_definition.id),
                    "agent_name": self.agent_definition.agent_name,
                    "generated_at": graph_data["generated_at"],
                    "total_nodes": len(graph_data.get("nodes", [])),
                    "total_edges": len(graph_data.get("edges", [])),
                    "graph_type": "conversation_flow",
                    "graph_data": graph_data,  # Store complete graph as JSON
                },
            )

            print(f"✓ Graph saved to database with ID: {scenario_graph.id}")
            print(f"  - Scenario ID: {self.scenario.id}")
            print(f"  - Nodes: {len(graph_data.get('nodes', []))}")
            print(f"  - Edges: {len(graph_data.get('edges', []))}")
            return scenario_graph

        except Exception as e:
            print(f"Error saving graph to database: {e}")
            traceback.print_exc()
            return None

    @classmethod
    def load_graph_from_database(
        cls, agent_definition_id: str
    ) -> dict[str, Any] | None:
        """Load a previously generated graph from the database"""
        try:
            agent_definition = AgentDefinition.no_workspace_objects.get(
                id=agent_definition_id
            )

            # Find the most recent graph for this agent
            scenario_graph = (
                ScenarioGraph.objects.filter(
                    organization=agent_definition.organization,
                    graph_config__agent_definition_id=str(agent_definition_id),
                )
                .order_by("-created_at")
                .first()
            )

            if scenario_graph and scenario_graph.graph_config.get("graph_data"):
                print(f"✓ Loaded graph from database: {scenario_graph.name}")
                print(f"  - Scenario: {scenario_graph.scenario.name}")
                return scenario_graph.graph_config["graph_data"]
            else:
                print("No saved graph found in database")
                return None

        except Exception as e:
            print(f"Error loading graph from database: {e}")
            traceback.print_exc()
            return None

    @classmethod
    def get_saved_graphs_for_agent(
        cls, agent_definition_id: str
    ) -> list[dict[str, Any]]:
        """Get all saved graphs for a specific agent"""
        try:
            agent_definition = AgentDefinition.no_workspace_objects.get(
                id=agent_definition_id
            )

            graphs = ScenarioGraph.objects.filter(
                organization=agent_definition.organization,
                graph_config__agent_definition_id=str(agent_definition_id),
            ).order_by("-created_at")

            result = []
            for graph in graphs:
                if graph.graph_config.get("graph_data"):
                    result.append(
                        {
                            "id": str(graph.id),
                            "name": graph.name,
                            "description": graph.description,
                            "scenario_id": str(graph.scenario.id),
                            "scenario_name": graph.scenario.name,
                            "created_at": graph.created_at.isoformat(),
                            "total_nodes": graph.graph_config.get("total_nodes", 0),
                            "total_edges": graph.graph_config.get("total_edges", 0),
                            "graph_data": graph.graph_config["graph_data"],
                        }
                    )

            return result

        except Exception as e:
            print(f"Error getting saved graphs: {e}")
            traceback.print_exc()
            return []

    def save_branches_to_database(
        self, branches: list[dict[str, Any]], scenario_graph_id: str
    ) -> bool:
        """Save extracted branches to the database"""
        try:
            scenario_graph = ScenarioGraph.objects.get(id=scenario_graph_id)

            # Update graph_config with branches
            if "branches" not in scenario_graph.graph_config:
                scenario_graph.graph_config["branches"] = []

            scenario_graph.graph_config["branches"] = branches
            scenario_graph.graph_config["total_branches"] = len(branches)
            scenario_graph.save()

            print(f"✓ Saved {len(branches)} branches to database")
            return True

        except Exception as e:
            print(f"Error saving branches to database: {e}")
            traceback.print_exc()
            return False

    @classmethod
    def load_branches_from_database(
        cls, scenario_graph_id: str
    ) -> list[dict[str, Any]]:
        """Load branches from the database"""
        try:
            scenario_graph = ScenarioGraph.objects.get(id=scenario_graph_id)
            return scenario_graph.graph_config.get("branches", [])

        except Exception as e:
            print(f"Error loading branches from database: {e}")
            traceback.print_exc()
            return []

    def update_graph_in_database(
        self, scenario_graph_id: str, updated_graph_data: dict[str, Any]
    ) -> bool:
        """Update an existing graph in the database"""
        try:
            scenario_graph = ScenarioGraph.objects.get(id=scenario_graph_id)

            # Update the graph data
            scenario_graph.graph_config["graph_data"] = updated_graph_data
            scenario_graph.graph_config["total_nodes"] = len(
                updated_graph_data.get("nodes", [])
            )
            scenario_graph.graph_config["total_edges"] = len(
                updated_graph_data.get("edges", [])
            )
            scenario_graph.graph_config["updated_at"] = timezone.now().isoformat()
            scenario_graph.save()

            print(f"✓ Updated graph in database: {scenario_graph.name}")
            return True

        except Exception as e:
            print(f"Error updating graph in database: {e}")
            traceback.print_exc()
            return False

    def add_node_to_graph(
        self, scenario_graph_id: str, node_data: dict[str, Any]
    ) -> bool:
        """Add a new node to an existing graph"""
        try:
            scenario_graph = ScenarioGraph.objects.get(id=scenario_graph_id)
            graph_data = scenario_graph.graph_config.get("graph_data", {})

            if "nodes" not in graph_data:
                graph_data["nodes"] = []

            graph_data["nodes"].append(node_data)

            return self.update_graph_in_database(scenario_graph_id, graph_data)

        except Exception as e:
            print(f"Error adding node to graph: {e}")
            traceback.print_exc()
            return False

    def remove_node_from_graph(self, scenario_graph_id: str, node_name: str) -> bool:
        """Remove a node from an existing graph"""
        try:
            scenario_graph = ScenarioGraph.objects.get(id=scenario_graph_id)
            graph_data = scenario_graph.graph_config.get("graph_data", {})

            if "nodes" in graph_data:
                graph_data["nodes"] = [
                    node
                    for node in graph_data["nodes"]
                    if node.get("name") != node_name
                ]

            if "edges" in graph_data:
                graph_data["edges"] = [
                    edge
                    for edge in graph_data["edges"]
                    if edge.get("from") != node_name and edge.get("to") != node_name
                ]

            return self.update_graph_in_database(scenario_graph_id, graph_data)

        except Exception as e:
            print(f"Error removing node from graph: {e}")
            traceback.print_exc()
            return False

    def add_edge_to_graph(
        self, scenario_graph_id: str, edge_data: dict[str, Any]
    ) -> bool:
        """Add a new edge to an existing graph"""
        try:
            scenario_graph = ScenarioGraph.objects.get(id=scenario_graph_id)
            graph_data = scenario_graph.graph_config.get("graph_data", {})

            if "edges" not in graph_data:
                graph_data["edges"] = []

            graph_data["edges"].append(edge_data)

            return self.update_graph_in_database(scenario_graph_id, graph_data)

        except Exception as e:
            print(f"Error adding edge to graph: {e}")
            traceback.print_exc()
            return False

    def remove_edge_from_graph(
        self, scenario_graph_id: str, from_node: str, to_node: str
    ) -> bool:
        """Remove an edge from an existing graph"""
        try:
            scenario_graph = ScenarioGraph.objects.get(id=scenario_graph_id)
            graph_data = scenario_graph.graph_config.get("graph_data", {})

            if "edges" in graph_data:
                graph_data["edges"] = [
                    edge
                    for edge in graph_data["edges"]
                    if not (edge.get("from") == from_node and edge.get("to") == to_node)
                ]

            return self.update_graph_in_database(scenario_graph_id, graph_data)

        except Exception as e:
            print(f"Error removing edge from graph: {e}")
            traceback.print_exc()
            return False

    def extract_nodes_and_edges_from_detailed_path(
        self, detailed_path: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        Extract nodes and edges from a detailed_path structure.

        Args:
            detailed_path: List of node dictionaries with detailed information

        Returns:
            Dict containing 'nodes' and 'edges' lists in the standard graph format
        """
        try:
            nodes = []
            edges = []

            for i, node_data in enumerate(detailed_path):
                # Extract node information
                node = {
                    "name": node_data.get("name", ""),
                    "type": node_data.get("type", "conversation"),
                    "metadata": node_data.get("metadata", {}),
                }

                if node_data.get("isStart"):
                    node["isStart"] = node_data["isStart"]

                if node_data.get("prompt"):
                    node["prompt"] = node_data["prompt"]

                # Add optional properties if they exist
                if node_data.get("messagePlan"):
                    node["messagePlan"] = node_data["messagePlan"]

                if node_data.get("variableExtractionPlan"):
                    node["variableExtractionPlan"] = node_data["variableExtractionPlan"]

                if node_data.get("globalNodePlan"):
                    node["globalNodePlan"] = node_data["globalNodePlan"]

                if node_data.get("tool"):
                    node["tool"] = node_data["tool"]

                nodes.append(node)

                # Extract edge information (if not the last node)
                if i < len(detailed_path) - 1:
                    edge_condition = node_data.get("edgeCondition")
                    if edge_condition:
                        edge = {
                            "from": node_data.get("name", ""),
                            "to": detailed_path[i + 1].get("name", ""),
                            "condition": {
                                "type": edge_condition.get("type", "ai"),
                                "prompt": edge_condition.get("prompt", ""),
                            },
                        }
                        edges.append(edge)

            return {"nodes": nodes, "edges": edges}

        except Exception as e:
            print(f"Error extracting nodes and edges from detailed path: {e}")
            traceback.print_exc()
            return {"nodes": [], "edges": []}

    def _fix_common_json_issues(self, json_str: str) -> str:
        """Fix common JSON issues that LLMs might produce"""
        try:
            # Remove any trailing commas before closing braces/brackets
            import re

            json_str = re.sub(r",(\s*[}\]])", r"\1", json_str)

            # Fix unescaped quotes in strings
            json_str = re.sub(r'(?<!\\)"(?=.*":)', r'\\"', json_str)

            # Fix missing quotes around keys
            json_str = re.sub(r"(\w+):", r'"\1":', json_str)

            # Fix single quotes to double quotes
            json_str = json_str.replace("'", '"')

            return json_str
        except Exception as e:
            print(f"Error fixing JSON issues: {e}")
            return json_str

    def _classify_conversation_type(self, agent_purpose: str) -> str:
        """Classify the type of conversation based on agent purpose"""
        purpose_lower = agent_purpose.lower()

        if any(
            word in purpose_lower
            for word in ["sales", "sell", "purchase", "buy", "order"]
        ):
            return "sales"
        elif any(
            word in purpose_lower
            for word in ["support", "help", "issue", "problem", "troubleshoot"]
        ):
            return "support"
        elif any(
            word in purpose_lower
            for word in ["appointment", "schedule", "booking", "reservation"]
        ):
            return "appointment"
        elif any(word in purpose_lower for word in ["survey", "feedback", "research"]):
            return "survey"
        elif any(word in purpose_lower for word in ["lead", "qualify", "prospect"]):
            return "lead_qualification"
        else:
            return "general"

    def _get_conversation_type_guidelines(self, conversation_type: str) -> str:
        """Get specific guidelines based on conversation type"""
        guidelines = {
            "sales": """
- Focus on understanding customer needs and pain points
- Include objection handling and closing techniques
- Consider different buyer personas and decision stages
- Include follow-up and nurturing sequences
- Handle price objections and competitive comparisons
""",
            "support": """
- Prioritize quick problem resolution
- Include escalation paths for complex issues
- Focus on customer satisfaction and retention
- Handle frustrated customers with empathy
- Include knowledge base and self-service options
""",
            "appointment": """
- Streamline scheduling process
- Handle availability conflicts and rescheduling
- Include confirmation and reminder processes
- Consider different appointment types and durations
- Handle cancellations and no-shows
""",
            "survey": """
- Keep questions concise and engaging
- Include skip logic and branching
- Focus on data quality and completion rates
- Handle reluctant participants
- Include incentives and follow-up
""",
            "lead_qualification": """
- Focus on BANT (Budget, Authority, Need, Timeline)
- Include scoring and prioritization
- Handle unqualified leads gracefully
- Include nurturing sequences
- Consider different lead sources and quality
""",
            "general": """
- Focus on understanding customer intent
- Include multiple conversation paths
- Handle various customer types and needs
- Include escalation and transfer options
- Focus on customer satisfaction
""",
        }
        return guidelines.get(conversation_type, guidelines["general"])
