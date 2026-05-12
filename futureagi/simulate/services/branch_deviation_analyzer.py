import json
import traceback
from dataclasses import dataclass
from typing import Any

import structlog
from django.conf import settings
from django.utils import timezone

from tfc.ee_stub import _ee_stub

try:
    from ee.agenthub.synthetic_data_agent.synthetic_data_agent import (
        SyntheticDataAgent,
    )
except ImportError:
    SyntheticDataAgent = _ee_stub("SyntheticDataAgent")
from simulate.models import ChatMessageModel
from simulate.models.scenario_graph import ScenarioGraph
from simulate.models.test_execution import CallExecution, CallTranscript

logger = structlog.get_logger(__name__)


@dataclass
class BranchDeviation:
    """Represents a deviation from the expected branch path"""

    expected_node: str
    actual_node: str
    deviation_point: int  # Index in the path where deviation occurred
    reason: str
    transcript_segment: str
    expected_prompt: str
    actual_prompt: str
    type: str = "branch"  # type can be: branch, persona, situation, outcome


@dataclass
class AgentPerformanceDeviation:
    """Represents agent performance deviations from expected behavior"""

    type: str  # persona_handling, situation_management, outcome_achievement
    expected: str
    actual: str
    transcript_segment: str
    severity: str  # low, medium, high
    agent_behavior_issue: str  # What the agent should have done differently


@dataclass
class BranchAnalysis:
    """Complete analysis of a CallExecution against graph branches"""

    new_nodes: list[dict[str, Any]]
    new_edges: list[dict[str, Any]]
    current_path: list[str]
    expected_path: list[str]
    analysis_summary: str


class BranchDeviationAnalyzer:
    """Service to analyze CallExecution transcripts against graph branches using LLM"""

    def __init__(self):
        self.sda = SyntheticDataAgent()

    def analyze_call_execution_branch(
        self, call_execution: CallExecution
    ) -> BranchAnalysis:
        """
        Analyze a CallExecution to determine which branch was followed and identify deviations

        Args:
            call_execution: The CallExecution to analyze

        Returns:
            BranchAnalysis containing matched branch, deviations, and analysis summary
        """
        try:
            print(
                f"Analyzing call execution branch for call execution: {call_execution.id}"
            )
            # Get scenario graph for this call execution
            scenario_graph = self._get_scenario_graph(call_execution)
            if not scenario_graph:
                return self._create_empty_analysis("No scenario graph found")

            # Get all branches from the graph
            branches = self._get_graph_branches(scenario_graph)
            if not branches:
                return self._create_empty_analysis(
                    "No branches found in scenario graph"
                )

            # Get transcripts for this call execution
            transcripts = self._get_call_transcripts(call_execution)
            if not transcripts:
                return self._create_empty_analysis("No transcripts found")

            # Use LLM to analyze which branch was followed and identify deviations
            analysis_result = self._analyze_with_llm(
                transcripts, branches, scenario_graph, call_execution
            )

            return analysis_result

        except Exception as e:
            print(f"Error analyzing call execution branch: {e}")
            traceback.print_exc()
            return self._create_empty_analysis(f"Analysis failed: {str(e)}")

    def _get_scenario_graph(
        self, call_execution: CallExecution
    ) -> ScenarioGraph | None:
        """Get the scenario graph for the call execution"""
        try:
            print(f"Getting scenario graph for call execution: {call_execution.id}")
            if not call_execution.scenario:
                print(f"No scenario found for call execution {call_execution.id}")
                return None

            print(
                f"Looking for scenario graph for scenario: {call_execution.scenario.id}"
            )

            # Find scenario graph for this scenario
            scenario_graph = ScenarioGraph.objects.filter(
                scenario=call_execution.scenario, is_active=True
            ).first()

            if scenario_graph:
                print(
                    f"Found scenario graph: {scenario_graph.id} - {scenario_graph.name}"
                )
                return scenario_graph

            return None
        except Exception as e:
            print(f"Error getting scenario graph: {e}")
            import traceback

            traceback.print_exc()
            return None

    def _create_graph_from_scenario_flow(
        self, call_execution: CallExecution
    ) -> ScenarioGraph | None:
        """Create a scenario graph from scenario flow data in scenarioColumns"""
        try:
            # Look for scenario flow data in scenarioColumns (from serializer)
            # We need to get this from the serializer since it's processed there
            from simulate.serializers.test_execution import (
                CallExecutionDetailSerializer,
            )

            serializer = CallExecutionDetailSerializer(call_execution)
            scenario_columns = serializer.get_scenarioColumns(call_execution)

            # Check if we have scenario flow data
            scenario_flow_data = None
            for _key, value in scenario_columns.items():
                if isinstance(value, dict) and "value" in value:
                    try:
                        # Try to parse as JSON
                        import json

                        parsed_value = json.loads(value["value"])
                        if isinstance(parsed_value, list) and len(parsed_value) > 0:
                            # Check if this looks like scenario flow data
                            first_item = parsed_value[0]
                            if (
                                isinstance(first_item, dict)
                                and "name" in first_item
                                and "type" in first_item
                            ):
                                scenario_flow_data = parsed_value
                                print(
                                    f"Found scenario flow data with {len(scenario_flow_data)} nodes"
                                )
                                break
                    except (json.JSONDecodeError, TypeError):
                        continue

            if not scenario_flow_data:
                print("No scenario flow data found in scenarioColumns")
                return None

            # Create a temporary scenario graph from this data
            graph_data = self._convert_scenario_flow_to_graph_data(scenario_flow_data)

            # Create the scenario graph
            scenario_graph = ScenarioGraph.objects.create(
                name=f"{call_execution.scenario.name} - Generated from Flow",
                scenario=call_execution.scenario,
                organization=call_execution.test_execution.organization,
                description=f"Auto-generated graph from scenario flow data for call execution {call_execution.id}",
                graph_config={
                    "agent_definition_id": (
                        str(call_execution.test_execution.agent_definition.id)
                        if call_execution.test_execution.agent_definition
                        else ""
                    ),
                    "agent_name": (
                        call_execution.test_execution.agent_definition.agent_name
                        if call_execution.test_execution.agent_definition
                        else "Unknown"
                    ),
                    "generated_at": timezone.now().isoformat(),
                    "total_nodes": len(graph_data.get("nodes", [])),
                    "total_edges": len(graph_data.get("edges", [])),
                    "graph_type": "conversation_flow",
                    "graph_data": graph_data,
                    "source": "scenario_flow_data",
                },
            )

            print(f"Created scenario graph: {scenario_graph.id}")
            return scenario_graph

        except Exception as e:
            print(f"Error creating graph from scenario flow: {e}")
            import traceback

            traceback.print_exc()
            return None

    def _convert_scenario_flow_to_graph_data(
        self, scenario_flow_data: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Convert scenario flow data to graph data format"""
        try:
            nodes = []
            edges = []

            for i, node_data in enumerate(scenario_flow_data):
                # Convert node data
                node = {
                    "name": node_data.get("name", f"node_{i}"),
                    "type": node_data.get("type", "conversation"),
                    "metadata": node_data.get(
                        "metadata", {"position": {"x": 0, "y": 0}}
                    ),
                }

                if node_data.get("isStart"):
                    node["isStart"] = True

                if node_data.get("prompt"):
                    node["prompt"] = node_data["prompt"]

                if node_data.get("messagePlan"):
                    node["messagePlan"] = node_data["messagePlan"]

                if node_data.get("variableExtractionPlan"):
                    node["variableExtractionPlan"] = node_data["variableExtractionPlan"]

                if node_data.get("globalNodePlan"):
                    node["globalNodePlan"] = node_data["globalNodePlan"]

                if node_data.get("tool"):
                    node["tool"] = node_data["tool"]

                nodes.append(node)

                # Convert edge data if present
                edge_condition = node_data.get("edgeCondition")
                if edge_condition and i < len(scenario_flow_data) - 1:
                    next_node = scenario_flow_data[i + 1]
                    edge = {
                        "from": node_data.get("name", f"node_{i}"),
                        "to": next_node.get("name", f"node_{i+1}"),
                        "condition": {
                            "type": edge_condition.get("type", "ai"),
                            "prompt": edge_condition.get("prompt", ""),
                        },
                    }
                    edges.append(edge)

            return {
                "name": "Generated from Scenario Flow",
                "nodes": nodes,
                "edges": edges,
                "globalPrompt": "",
            }

        except Exception as e:
            print(f"Error converting scenario flow to graph data: {e}")
            import traceback

            traceback.print_exc()
            return {"nodes": [], "edges": [], "globalPrompt": ""}

    def _extract_current_path_from_transcript(
        self, transcripts: list[dict[str, Any]]
    ) -> list[str]:
        """
        Extract conversation flow path from transcript by analyzing conversation stages

        Args:
            transcripts: List of transcript segments

        Returns:
            List of conversation stage names representing the current path
        """
        try:
            current_path = []

            # Analyze transcript to identify conversation stages
            conversation_stages = []

            for transcript in transcripts:
                content = transcript.get("content", "").lower()
                speaker = transcript.get("speaker_role", "")

                # Identify conversation stages based on content patterns
                stage = self._identify_conversation_stage(content, speaker)
                if stage and stage not in conversation_stages:
                    conversation_stages.append(stage)

            # Convert stages to node names
            for stage in conversation_stages:
                node_name = self._convert_stage_to_node_name(stage)
                if node_name:
                    current_path.append(node_name)

            print(f"Extracted current path from transcript: {current_path}")
            return current_path

        except Exception as e:
            print(f"Error extracting current path from transcript: {e}")
            return []

    def _identify_conversation_stage(self, content: str, speaker: str) -> str | None:
        """
        Identify conversation stage based on content and speaker

        Args:
            content: Transcript content
            speaker: Speaker role (USER/ASSISTANT)

        Returns:
            Conversation stage name or None
        """
        # Greeting patterns
        if any(
            word in content
            for word in [
                "hello",
                "hi",
                "good morning",
                "good afternoon",
                "good evening",
            ]
        ):
            return "greeting"

        # Introduction patterns
        if any(
            word in content
            for word in ["introduce", "name is", "calling about", "reason for calling"]
        ):
            return "introduction"

        # Information gathering patterns
        if any(
            word in content
            for word in ["tell me", "what is", "can you", "do you have", "need to know"]
        ):
            return "information_gathering"

        # Problem identification patterns
        if any(
            word in content
            for word in ["problem", "issue", "trouble", "help with", "need help"]
        ):
            return "problem_identification"

        # Solution offering patterns
        if any(
            word in content
            for word in ["solution", "can help", "offer", "provide", "recommend"]
        ):
            return "solution_offering"

        # Qualification patterns
        if any(
            word in content
            for word in [
                "qualify",
                "budget",
                "timeline",
                "requirements",
                "specific needs",
            ]
        ):
            return "qualification"

        # Objection handling patterns
        if any(
            word in content
            for word in ["concern", "worry", "not sure", "hesitant", "think about"]
        ):
            return "objection_handling"

        # Closing patterns
        if any(
            word in content
            for word in [
                "next steps",
                "follow up",
                "schedule",
                "appointment",
                "call back",
            ]
        ):
            return "closing"

        # Transfer patterns
        if any(
            word in content
            for word in ["transfer", "speak to", "human", "manager", "supervisor"]
        ):
            return "transfer"

        return None

    def _convert_stage_to_node_name(self, stage: str) -> str | None:
        """
        Convert conversation stage to node name format

        Args:
            stage: Conversation stage name

        Returns:
            Node name or None
        """
        stage_mapping = {
            "greeting": "start",
            "introduction": "introduction",
            "information_gathering": "collect_info",
            "problem_identification": "identify_problem",
            "solution_offering": "offer_solution",
            "qualification": "qualify_customer",
            "objection_handling": "handle_objections",
            "closing": "close_conversation",
            "transfer": "transfer_call",
        }

        return stage_mapping.get(stage)

    def _find_most_aligned_expected_path(
        self, current_path: list[str], branches: list[dict[str, Any]]
    ) -> list[str]:
        """
        Find the most aligned expected path from available branches

        Args:
            current_path: Current conversation path from transcript
            branches: Available branches from the graph

        Returns:
            Most aligned expected path
        """
        try:
            if not branches:
                return []

            best_match = None
            best_score = 0

            for branch in branches:
                branch_path = branch.get("path", [])
                if not branch_path:
                    continue

                # Calculate alignment score
                score = self._calculate_path_alignment_score(current_path, branch_path)

                if score > best_score:
                    best_score = int(score)
                    best_match = branch_path

            print(
                f"Found most aligned expected path: {best_match} (score: {best_score})"
            )
            return best_match or []

        except Exception as e:
            print(f"Error finding most aligned expected path: {e}")
            return []

    def _calculate_path_alignment_score(
        self, current_path: list[str], branch_path: list[str]
    ) -> float:
        """
        Calculate alignment score between current path and branch path

        Args:
            current_path: Current conversation path
            branch_path: Branch path from graph

        Returns:
            Alignment score (0.0 to 1.0)
        """
        try:
            if not current_path or not branch_path:
                return 0.0

            # Calculate common elements
            common_elements = set(current_path) & set(branch_path)
            common_count = len(common_elements)

            # Calculate total elements
            total_elements = len(set(current_path) | set(branch_path))

            if total_elements == 0:
                return 0.0

            # Base alignment score
            alignment_score = common_count / total_elements

            # Bonus for sequence alignment
            sequence_bonus = self._calculate_sequence_alignment_bonus(
                current_path, branch_path
            )

            # Final score
            final_score = alignment_score + sequence_bonus

            return min(final_score, 1.0)

        except Exception as e:
            print(f"Error calculating path alignment score: {e}")
            return 0.0

    def _calculate_sequence_alignment_bonus(
        self, current_path: list[str], branch_path: list[str]
    ) -> float:
        """
        Calculate bonus score for sequence alignment

        Args:
            current_path: Current conversation path
            branch_path: Branch path from graph

        Returns:
            Sequence alignment bonus (0.0 to 0.2)
        """
        try:
            if not current_path or not branch_path:
                return 0.0

            # Find longest common subsequence
            lcs_length = self._longest_common_subsequence_length(
                current_path, branch_path
            )

            # Calculate bonus based on LCS length
            max_length = max(len(current_path), len(branch_path))
            if max_length == 0:
                return 0.0

            sequence_ratio = lcs_length / max_length

            # Return bonus (max 0.2)
            return sequence_ratio * 0.2

        except Exception as e:
            print(f"Error calculating sequence alignment bonus: {e}")
            return 0.0

    def _longest_common_subsequence_length(
        self, path1: list[str], path2: list[str]
    ) -> int:
        """
        Calculate the length of the longest common subsequence

        Args:
            path1: First path
            path2: Second path

        Returns:
            Length of longest common subsequence
        """
        try:
            m, n = len(path1), len(path2)
            dp = [[0] * (n + 1) for _ in range(m + 1)]

            for i in range(1, m + 1):
                for j in range(1, n + 1):
                    if path1[i - 1] == path2[j - 1]:
                        dp[i][j] = dp[i - 1][j - 1] + 1
                    else:
                        dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

            return dp[m][n]

        except Exception as e:
            print(f"Error calculating longest common subsequence: {e}")
            return 0

    def _get_graph_branches(
        self, scenario_graph: ScenarioGraph
    ) -> list[dict[str, Any]]:
        try:
            print(f"Getting branches for scenario graph: {scenario_graph.id}")
            print(f"Graph config keys: {list(scenario_graph.graph_config.keys())}")

            # Check if graph_data exists in graph_config
            graph_data = scenario_graph.graph_config.get("graph_data")
            if not graph_data:
                print("No graph_data found in graph_config")
                return []

            print(f"Graph data keys: {list(graph_data.keys())}")
            print(f"Number of nodes: {len(graph_data.get('nodes', []))}")
            print(f"Number of edges: {len(graph_data.get('edges', []))}")

            try:
                from ee.agenthub.scenario_graph.graph_generator import (
                    ConversationGraphGenerator,
                )
            except ImportError:
                if settings.DEBUG:
                    logger.warning("Could not import ee.agenthub.scenario_graph.graph_generator", exc_info=True)
                return None

            # Create graph generator instance
            graph_generator = ConversationGraphGenerator(
                agent_definition_id=str(
                    scenario_graph.graph_config.get("agent_definition_id", "")
                ),
                scenario=scenario_graph.scenario,
            )

            # Get branches with detailed information
            branches = graph_generator.get_all_branches_with_details(
                str(scenario_graph.id)
            )

            print(f"Found {len(branches)} branches")
            return branches
        except Exception as e:
            print(f"Error getting graph branches: {e}")
            import traceback

            traceback.print_exc()
            return []

    def _get_call_transcripts(
        self, call_execution: CallExecution
    ) -> list[dict[str, Any]]:
        """Get formatted transcripts for the call execution"""
        try:

            if (
                call_execution.simulation_call_type
                == CallExecution.SimulationCallType.TEXT
            ):

                messages = ChatMessageModel.objects.filter(
                    call_execution=call_execution,
                    role__in=[
                        ChatMessageModel.RoleChoices.ASSISTANT,
                        ChatMessageModel.RoleChoices.USER,
                    ],
                    deleted=False,
                ).order_by("created_at")

                logger.info(
                    f"Found {messages.count()} messages for call execution {call_execution.id}"
                )

                formatted_messages = []
                for message in messages:

                    text_messages = message.messages
                    formatted_text = ""
                    for text in text_messages:
                        if not isinstance(text, str):
                            text = str(text)

                        formatted_text = formatted_text + text + " "

                    formatted_text = formatted_text.strip()
                    formatted_messages.append(
                        {"speaker_role": message.role, "content": formatted_text}
                    )

                logger.info(
                    f"Formatted {len(formatted_messages)} messages for call execution {call_execution.id}"
                )

                return formatted_messages

            else:

                from ee.voice.utils.transcript_roles import SpeakerRoleResolver

                transcripts = CallTranscript.objects.filter(
                    call_execution=call_execution,
                    speaker_role__in=SpeakerRoleResolver.get_conversational_roles(),
                ).order_by("start_time_ms")

                logger.info(
                    f"Found {transcripts.count()} transcripts for call execution {call_execution.id}"
                )

                formatted_transcripts = []
                for transcript in transcripts:
                    formatted_transcripts.append(
                        {
                            "speaker_role": transcript.speaker_role,
                            "content": transcript.content,
                            "start_time_ms": transcript.start_time_ms,
                            "end_time_ms": transcript.end_time_ms,
                        }
                    )
                    logger.info(
                        f"  - {transcript.speaker_role}: {transcript.content[:100]}..."
                    )

                return formatted_transcripts
        except Exception as e:
            logger.exception(f"Error getting call transcripts: {e}")
            return []

    def _analyze_with_llm(
        self,
        transcripts: list[dict[str, Any]],
        branches: list[dict[str, Any]],
        scenario_graph: ScenarioGraph,
        call_execution: CallExecution,
    ) -> BranchAnalysis:
        """Use LLM to analyze transcripts against branches and identify deviations"""
        try:
            # Extract current path from transcript using robust logic
            current_path = self._extract_current_path_from_transcript(transcripts)

            # Find most aligned expected path from available branches
            expected_path = self._find_most_aligned_expected_path(
                current_path, branches
            )

            # Prepare transcript text
            transcript_text = self._format_transcripts_for_llm(transcripts)

            # Prepare branch information
            branch_info = self._format_branches_for_llm(branches)

            # Extract persona, situation, and outcome data for agent evaluation
            persona_data, situation_data, outcome_data = self._extract_psa_data(
                call_execution
            )

            # Create LLM prompt with pre-extracted paths
            prompt = self._create_analysis_prompt(
                transcript_text,
                branch_info,
                scenario_graph,
                persona_data,
                situation_data,
                outcome_data,
                current_path,
                expected_path,
            )

            # Get LLM response
            response = self.sda.llm._get_completion_content(
                messages=[{"role": "user", "content": prompt}]
            )

            # Parse LLM response
            analysis_result = self._parse_llm_response(response, branches)

            # Override with our robust path extraction if LLM didn't provide good paths
            if not analysis_result.current_path or len(
                analysis_result.current_path
            ) < len(current_path):
                analysis_result.current_path = current_path

            if not analysis_result.expected_path or len(
                analysis_result.expected_path
            ) < len(expected_path):
                analysis_result.expected_path = expected_path

            return analysis_result

        except Exception as e:
            print(f"Error in LLM analysis: {e}")
            traceback.print_exc()
            return self._create_empty_analysis(f"LLM analysis failed: {str(e)}")

    def _format_transcripts_for_llm(self, transcripts: list[dict[str, Any]]) -> str:
        """Format transcripts for LLM analysis"""
        formatted = []
        for i, transcript in enumerate(transcripts):
            speaker = transcript["speaker_role"].title()
            content = transcript["content"]
            formatted.append(f"{i+1}. {speaker}: {content}")

        return "\n".join(formatted)

    def _format_branches_for_llm(self, branches: list[dict[str, Any]]) -> str:
        """Format branch information for LLM analysis"""
        formatted_branches = []

        for i, branch in enumerate(branches):
            branch_info = f"Branch {i+1}:\n"
            branch_info += f"  Path: {' -> '.join(branch.get('path', []))}\n"

            # Add detailed node information
            detailed_path = branch.get("detailedPath", [])
            for j, node in enumerate(detailed_path):
                branch_info += f"  Node {j+1} ({node.get('name', 'unknown')}):\n"
                branch_info += f"    Type: {node.get('type', 'unknown')}\n"
                branch_info += f"    Prompt: {node.get('prompt', 'No prompt')}\n"

                if node.get("firstMessage"):
                    branch_info += f"    First Message: {node.get('firstMessage')}\n"

                if node.get("edgeCondition"):
                    branch_info += f"    Edge Condition: {node.get('edgeCondition', {}).get('prompt', 'No condition')}\n"

                branch_info += "\n"

            formatted_branches.append(branch_info)

        print(f"Formatted branches: {formatted_branches}")

        return str(formatted_branches) if formatted_branches else ""

    def _extract_psa_data(
        self, call_execution: CallExecution
    ) -> tuple[dict[str, Any], str, str]:
        """Extract persona, situation, and outcome data for agent performance evaluation"""
        try:
            persona_data = {}
            situation_data = ""
            outcome_data = ""

            # Get call metadata which contains row_data if available
            call_metadata = (
                call_execution.call_metadata if call_execution.call_metadata else {}
            )
            row_data = call_metadata.get("row_data", {})

            # Extract persona (customer characteristics for agent evaluation)
            if "persona" in row_data:
                persona_value = row_data["persona"]
                if isinstance(persona_value, str):
                    try:
                        import ast

                        persona_data = ast.literal_eval(persona_value)
                    except (ValueError, SyntaxError):
                        # If parsing fails, try JSON parsing
                        try:
                            import json

                            persona_data = json.loads(persona_value)
                        except json.JSONDecodeError:
                            persona_data = {}
                elif isinstance(persona_value, dict):
                    persona_data = persona_value

            # Extract situation (call context for agent evaluation)
            if "situation" in row_data:
                situation_data = (
                    str(row_data["situation"]) if row_data["situation"] else ""
                )

            # Extract outcome (expected result for agent to achieve)
            if "outcome" in row_data:
                outcome_data = str(row_data["outcome"]) if row_data["outcome"] else ""

            print(
                f"Extracted PSA data for agent evaluation - Customer profile: {persona_data}, Context: {situation_data[:50]}..., Expected outcome: {outcome_data[:50]}..."
            )

            return persona_data, situation_data, outcome_data

        except Exception as e:
            print(f"Error extracting PSA data for agent evaluation: {e}")
            import traceback

            traceback.print_exc()
            return {}, "", ""

    def _create_analysis_prompt(
        self,
        transcript_text: str,
        branch_info: str,
        scenario_graph: ScenarioGraph,
        persona_data: dict[str, Any] | None = None,
        situation_data: str = "",
        outcome_data: str = "",
        current_path: list[str] | None = None,
        expected_path: list[str] | None = None,
    ) -> str:
        """Create LLM prompt for agent conversation analysis against PSA context"""
        # Handle None persona_data
        persona_data = persona_data or {}

        # Format customer profile for agent evaluation
        customer_context = ""
        if persona_data:
            customer_profile = ""
            if "name" in persona_data:
                customer_profile += f"Customer named: {persona_data['name']}"
            if "age_group" in persona_data:
                customer_profile += f", Age: {persona_data['age_group']}"
            if "profession" in persona_data:
                customer_profile += f", Profession: {persona_data['profession']}"
            if "location" in persona_data:
                customer_profile += f", Location: {persona_data['location']}"
            if "communication_style" in persona_data:
                customer_profile += (
                    f", Communication style: {persona_data['communication_style']}"
                )
            if "personality" in persona_data:
                customer_profile += f", Personality: {persona_data['personality']}"

            if customer_profile:
                customer_context = (
                    f"\nCUSTOMER PROFILE (for agent to adapt to):\n{customer_profile}"
                )

        # Format situation and outcome for agent evaluation
        agent_requirements = ""
        if situation_data:
            agent_requirements += (
                f"\nCALL SITUATION (for agent to handle):\n{situation_data}"
            )
        if outcome_data:
            agent_requirements += (
                f"\nEXPECTED OUTCOME (for agent to achieve):\n{outcome_data}"
            )

        # Add pre-extracted path information
        path_context = ""
        if current_path:
            path_context += f"\nPRE-EXTRACTED CURRENT PATH: {current_path}"
        if expected_path:
            path_context += f"\nPRE-EXTRACTED EXPECTED PATH: {expected_path}"

        return f"""
You are analyzing a call execution transcript to evaluate AGENT PERFORMANCE against conversation flow branches and customer context requirements.

SCENARIO GRAPH CONTEXT:
- Graph Name: {scenario_graph.name}
- Scenario: {scenario_graph.scenario.name if scenario_graph.scenario else 'Unknown'}{customer_context}{agent_requirements}{path_context}

TRANSCRIPT TO ANALYZE:
{transcript_text}

AVAILABLE BRANCHES:
{branch_info}

ANALYSIS TASK (AGENT-PERFORMANCE FOCUSED):
1. **Path Validation**:
   - Verify the PRE-EXTRACTED CURRENT PATH accurately represents the actual conversation flow
   - Verify the PRE-EXTRACTED EXPECTED PATH is the most aligned branch from available branches
   - Use these paths as your primary reference unless you find significant issues
2. **New Node Generation**: Create new conversation nodes for:
   - Deviations from expected path
   - Agent performance issues that need correction
   - Missing conversation stages that should have occurred
3. **New Edge Generation**: Create edges connecting:
   - New nodes to existing graph structure
   - Transitions that occurred in the actual conversation
   - Corrective paths for better conversation flow
4. **Agent Performance Analysis**: Evaluate how well the agent adapted to customer context

PERFORMANCE ANALYSIS REQUIREMENTS:
- **currentPath**: Use the PRE-EXTRACTED CURRENT PATH as your primary reference, only modify if you find significant issues
- **expectedPath**: Use ONLY existing graph branches from AVAILABLE BRANCHES. Do NOT include new nodes in expectedPath. This should represent the ideal path through existing graph structure.
- **newNodes**: Generate conversation nodes for:
  * Missing conversation stages that should have occurred
  * Agent performance corrections needed
  * Deviations that created new conversation paths
  * Additional context nodes for better conversation flow
- **newEdges**: Create edges for:
  * Connecting new nodes to existing graph structure
  * Representing actual transitions that occurred
  * Providing corrective paths for better conversation flow
- **Node Structure**: Each new node should include:
  * Clear conversation purpose and context
  * Appropriate prompts for the agent
  * Variable extraction plans where relevant
  * Message plans for natural conversation flow

OUTPUT FORMAT:
Return a JSON response with this exact structure:

{{
  "newNodes": [
    {{
      "name": "node_name",
      "type": "conversation",
      "metadata": {{
        "position": {{"x": 0, "y": 0}}
      }},
      "prompt": "Detailed prompt for this conversation stage",
      "messagePlan": {{
        "firstMessage": "Suggested opening message"
      }},
      "variableExtractionPlan": {{
        "output": [
          {{"type": "string", "title": "variable_name", "description": "What to extract"}}
        ]
      }}
    }}
  ],
  "newEdges": [
    {{
      "from": "source_node",
      "to": "target_node",
      "condition": {{
        "type": "ai",
        "prompt": "Condition that triggers this transition"
      }}
    }}
  ],
  "currentPath": ["node1", "node2", "node3"],
  "expectedPath": ["expected_node1", "expected_node2", "expected_node3"],
  "analysis_summary": "Overall agent performance evaluation with specific recommendations"
}}

IMPORTANT ANALYSIS FOCUS:
- **Path Extraction**: Carefully analyze the transcript to identify the actual conversation flow (currentPath)
- **Branch Alignment**: Compare the actual flow against available branches to find the most aligned expected path using ONLY existing graph branches
- **Node Generation**: Create meaningful conversation nodes that represent:
  * Actual conversation stages that occurred
  * Missing stages that should have happened
  * Performance improvement opportunities
- **Edge Creation**: Generate logical edges that connect nodes in a natural conversation flow
- **Graph Integration**: Ensure new nodes and edges can be integrated with the existing graph structure
- **Conversation Quality**: Focus on creating nodes that improve conversation flow and agent performance
- **CRITICAL**: expectedPath must contain ONLY existing branch names from the AVAILABLE BRANCHES section, never new node names

Return only the JSON response with no additional text.
"""

    def _parse_llm_response(
        self, response: str, branches: list[dict[str, Any]]
    ) -> BranchAnalysis:
        """Parse LLM response and create BranchAnalysis object"""
        try:
            # Clean the response
            response = response.strip()
            if response.startswith("```json"):
                response = response[7:]
            if response.startswith("```"):
                response = response[3:]
            if response.endswith("```"):
                response = response[:-3]

            # Parse JSON
            analysis_data = json.loads(response)

            # Extract the new fields from the response
            new_nodes = analysis_data.get("newNodes", [])
            new_edges = analysis_data.get("newEdges", [])
            current_path = analysis_data.get("currentPath", [])
            expected_path = analysis_data.get("expectedPath", [])
            analysis_summary = analysis_data.get("analysis_summary", "")

            # Create BranchAnalysis with new structure
            analysis = BranchAnalysis(
                new_nodes=new_nodes,
                new_edges=new_edges,
                current_path=current_path,
                expected_path=expected_path,
                analysis_summary=analysis_summary,
            )

            return analysis

        except Exception as e:
            print(f"Error parsing LLM response: {e}")
            print(f"Response: {response}")
            return self._create_empty_analysis(
                f"Failed to parse LLM response: {str(e)}"
            )

    def _create_empty_analysis(self, reason: str) -> BranchAnalysis:
        """Create an empty analysis result"""
        return BranchAnalysis(
            new_nodes=[],
            new_edges=[],
            current_path=[],
            expected_path=[],
            analysis_summary=reason,
        )

    def create_deviation_nodes_and_edges(
        self, analysis: BranchAnalysis, scenario_graph: ScenarioGraph
    ) -> dict[str, Any]:
        """
        Return the new nodes and edges from the analysis

        Args:
            analysis: The branch analysis result
            scenario_graph: The scenario graph (for context)

        Returns:
            Dict containing new nodes and edges from the analysis
        """
        try:
            return {
                "nodes": analysis.new_nodes,
                "edges": analysis.new_edges,
                "current_path": analysis.current_path,
                "expected_path": analysis.expected_path,
                "analysis_summary": analysis.analysis_summary,
            }

        except Exception as e:
            print(f"Error creating deviation nodes and edges: {e}")
            traceback.print_exc()
            return {
                "nodes": [],
                "edges": [],
                "current_path": [],
                "expected_path": [],
                "analysis_summary": f"Error: {str(e)}",
            }
