import traceback
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Set, Optional


# Node Types
class NodeType(Enum):
    """Node types for the conversation graph"""

    CONVERSATION = "conversation"
    TOOL = "tool"
    GLOBAL = "conversation"  # Global nodes are also conversation type but with special properties


@dataclass
class NodePosition:
    """Position coordinates for graph visualization"""

    x: float
    y: float


@dataclass
class EdgeCondition:
    """Condition for transitioning between nodes"""

    type: str  # "ai" for AI-driven conditions
    prompt: str  # The condition description


@dataclass
class GraphNode:
    """Represents a node in the conversation graph"""

    name: str
    node_type: NodeType
    is_start: bool = False
    is_global: bool = False
    prompt: str = ""
    message_plan: dict[str, Any] | None = None
    variable_extraction_plan: dict[str, Any] | None = None
    global_node_plan: dict[str, Any] | None = None
    tool_config: dict[str, Any] | None = None


@dataclass
class GraphEdge:
    """Represents an edge in the conversation graph"""

    from_node: str
    to_node: str
    condition: EdgeCondition


@dataclass
class GraphProcessingResult:
    """Result of graph processing containing final structure and object models"""

    final_graph: Dict[str, Any]
    nodes: List[GraphNode]
    edges: List[GraphEdge]
    node_positions: Dict[str, NodePosition]


class GraphRefiner:
    """
    Handles validation, refinement, parsing, and positioning of conversation graphs.
    """

    def __init__(self, simulation_mode: str = "voice", agent_name: str = "Agent"):
        self.mode = simulation_mode
        self.agent_name = agent_name
        self.nodes: List[GraphNode] = []
        self.edges: List[GraphEdge] = []
        self.node_positions: Dict[str, NodePosition] = {}

        # Valid tool types based on mode
        self.valid_tool_types = ["endCall", "transferCall"]
        if self.mode == "chat":
            self.valid_tool_types = ["endChat", "transferChat"]

    def _tool_type_to_function_name(self, tool_type: str) -> str:
        return {
            "endCall": "end_call",
            "transferCall": "transfer_call",
            "endChat": "end_chat",
            "transferChat": "transfer_chat",
        }.get(tool_type, "untitled_tool")

    def _get_global_node_names(self, graph_data: Dict[str, Any]) -> set[str]:
        try:
            return {
                node["name"]
                for node in graph_data.get("nodes", [])
                if node.get("globalNodePlan") and node.get("type") != "tool"
            }
        except Exception:
            return set()

    def process_graph(self, raw_graph_data: Dict[str, Any]) -> GraphProcessingResult:
        """
        Process the raw graph data from LLM into a fully valid, positioned graph structure.
        Performs validation, parsing, and positioning in a single pipeline.
        """
        try:
            # 1. Validation and Fixes on dictionary structure
            print("Step 2.1: Validating and refining graph structure...")
            graph_data = self._validate_and_refine(raw_graph_data)

            # 2. Parse into objects
            print("Step 2.2: Parsing graph components...")
            self._parse_nodes(graph_data)
            self._parse_edges(graph_data)

            # 3. Calculate positions
            print("Step 2.3: Calculating node layouts...")
            self._calculate_node_positions()

            # 4. Build final structure
            print("Step 2.4: Building final graph payload...")
            final_graph = self._build_graph_structure()

            return GraphProcessingResult(
                final_graph=final_graph,
                nodes=self.nodes,
                edges=self.edges,
                node_positions=self.node_positions,
            )

        except Exception as e:
            print(f"Error in GraphRefiner process: {e}")
            traceback.print_exc()
            # Return empty/safe result or re-raise
            raise e

    def _validate_and_refine(self, graph_data: Dict[str, Any]) -> Dict[str, Any]:
        """Run clear sequence of validation and fix steps"""
        # Validate tools
        graph_data = self._validate_and_fix_tool_nodes(graph_data)

        # Validate orphans
        graph_data = self._validate_and_fix_orphan_nodes(graph_data)

        # Validate size (checking only, distinct from fixes mostly)
        self._validate_graph_size(graph_data)

        # Ensure proper termination
        graph_data = self._ensure_all_branches_end_with_termination(graph_data)

        return graph_data

    def _validate_and_fix_tool_nodes(
        self, graph_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Validate and fix tool nodes to ensure they use valid tool types for the mode"""
        try:
            end_tool_type = self.valid_tool_types[0]  # endCall/endChat
            transfer_tool_type = self.valid_tool_types[1]  # transferCall/transferChat

            for node in graph_data.get("nodes", []):
                if node.get("type") != "tool":
                    continue

                tool_config = node.get("tool") or {}
                if not isinstance(tool_config, dict):
                    tool_config = {}

                tool_type = tool_config.get("type") or ""
                if tool_type not in self.valid_tool_types:
                    fallback_type = end_tool_type
                    print(
                        f"Warning: Invalid tool type '{tool_type}' for node '{node.get('name', 'unknown')}'. Using '{fallback_type}' as fallback."
                    )
                    tool_type = fallback_type
                    tool_config["type"] = tool_type

                function_config = tool_config.get("function") or {}
                if not isinstance(function_config, dict):
                    function_config = {}

                function_config["name"] = self._tool_type_to_function_name(tool_type)
                parameters = function_config.get("parameters")
                if not isinstance(parameters, dict):
                    parameters = {
                        "type": "object",
                        "required": [],
                        "properties": {},
                    }
                else:
                    parameters.setdefault("type", "object")
                    parameters.setdefault("required", [])
                    parameters.setdefault("properties", {})
                function_config["parameters"] = parameters
                tool_config["function"] = function_config

                if tool_type == end_tool_type and "messages" not in tool_config:
                    tool_config["messages"] = [
                        {
                            "type": "request-start",
                            "content": f"Tool action: {node.get('name', 'Unknown')}",
                            "blocking": True,
                        }
                    ]
                if (
                    tool_type == transfer_tool_type
                    and "destinations" not in tool_config
                ):
                    tool_config["destinations"] = []

                node["tool"] = tool_config

            return graph_data
        except Exception as e:
            print(f"Error validating tool nodes: {e}")
            traceback.print_exc()
            return graph_data

    def _validate_and_fix_orphan_nodes(
        self, graph_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Validate and fix orphan nodes to ensure all nodes are connected from start nodes"""
        try:
            nodes = graph_data.get("nodes", [])
            edges = graph_data.get("edges", [])

            # Find start nodes
            start_nodes = [node["name"] for node in nodes if node.get("isStart", False)]
            if not start_nodes:
                print("Warning: No start nodes found!")
                return graph_data

            # Create adjacency lists for both directions
            outgoing_edges: Dict[str, List] = {}
            incoming_edges: Dict[str, List] = {}
            all_node_names = {node["name"] for node in nodes}

            for edge in edges:
                from_node = edge["from"]
                to_node = edge["to"]

                if from_node not in outgoing_edges:
                    outgoing_edges[from_node] = []
                outgoing_edges[from_node].append(to_node)

                if to_node not in incoming_edges:
                    incoming_edges[to_node] = []
                incoming_edges[to_node].append(from_node)

            # Find reachable nodes from start nodes using BFS
            reachable_nodes = set()
            queue = start_nodes.copy()

            while queue:
                current_node = queue.pop(0)
                if current_node in reachable_nodes:
                    continue
                reachable_nodes.add(current_node)

                # Add all outgoing nodes to queue
                for next_node in outgoing_edges.get(current_node, []):
                    if next_node not in reachable_nodes:
                        queue.append(next_node)

            # Find orphan nodes (nodes not reachable from start nodes)
            orphan_nodes = all_node_names - reachable_nodes

            if orphan_nodes:
                print(f"Found {len(orphan_nodes)} orphan nodes: {list(orphan_nodes)}")

                # Fix orphan nodes by connecting them to the nearest reachable node
                for orphan_node in orphan_nodes:
                    # Find the nearest reachable node (prefer start nodes)
                    nearest_node = (
                        start_nodes[0]
                        if start_nodes
                        else list(reachable_nodes)[0]
                        if reachable_nodes
                        else None
                    )

                    if nearest_node:
                        # Create edge from nearest reachable node to orphan node
                        new_edge = {
                            "from": nearest_node,
                            "to": orphan_node,
                            "condition": {
                                "type": "ai",
                                "prompt": f"Transition to {orphan_node} when appropriate",
                            },
                        }
                        edges.append(new_edge)

                        # Update adjacency lists
                        if nearest_node not in outgoing_edges:
                            outgoing_edges[nearest_node] = []
                        outgoing_edges[nearest_node].append(orphan_node)

                        if orphan_node not in incoming_edges:
                            incoming_edges[orphan_node] = []
                        incoming_edges[orphan_node].append(nearest_node)

                        # Add orphan node to reachable nodes
                        reachable_nodes.add(orphan_node)

                        print(
                            f"  - Connected orphan node '{orphan_node}' to '{nearest_node}'"
                        )

            # Update the graph data
            graph_data["edges"] = edges

            # Verify all nodes are now reachable
            final_reachable = set()
            queue = start_nodes.copy()

            while queue:
                current_node = queue.pop(0)
                if current_node in final_reachable:
                    continue
                final_reachable.add(current_node)

                for next_node in outgoing_edges.get(current_node, []):
                    if next_node not in final_reachable:
                        queue.append(next_node)

            remaining_orphans = all_node_names - final_reachable
            if remaining_orphans:
                print(
                    f"Warning: Still have orphan nodes after fix: {list(remaining_orphans)}"
                )
            else:
                print(
                    f"✓ All {len(all_node_names)} nodes are now reachable from start nodes"
                )

            return graph_data

        except Exception as e:
            print(f"Error validating and fixing orphan nodes: {e}")
            traceback.print_exc()
            return graph_data

    def _validate_graph_size(self, graph_data: Dict[str, Any]) -> None:
        """Validate that the graph meets size requirements (15-18 nodes, 7-8 branches)"""
        try:
            nodes = graph_data.get("nodes", [])
            edges = graph_data.get("edges", [])

            total_nodes = len(nodes)
            print(f"Graph has {total_nodes} nodes")

            # Check if graph is within size limits
            if total_nodes < 15:
                print(
                    f"Warning: Graph has only {total_nodes} nodes (minimum 15 required)"
                )
                print("Consider adding more essential conversation nodes")
            elif total_nodes > 18:
                print(f"Warning: Graph has {total_nodes} nodes (maximum 18 allowed)")
                print("Consider removing less essential nodes to simplify the graph")
            else:
                print(
                    f"✓ Graph size is optimal: {total_nodes} nodes (within 15-18 range)"
                )

            # Count branches by analyzing paths from start nodes
            start_nodes = [node["name"] for node in nodes if node.get("isStart", False)]
            if not start_nodes:
                print("Warning: No start nodes found for branch counting")
                return

            # Create adjacency list
            outgoing_edges: Dict[str, List] = {}
            for edge in edges:
                from_node = edge["from"]
                if from_node not in outgoing_edges:
                    outgoing_edges[from_node] = []
                outgoing_edges[from_node].append(edge["to"])

            # Count branches using DFS
            branches: List[Any] = []
            self._count_branches_from_node(start_nodes[0], [], branches, outgoing_edges)

            branch_count = len(branches)
            print(f"Graph has {branch_count} branches")

            if branch_count < 7:
                print(
                    f"Warning: Graph has only {branch_count} branches (minimum 7 required)"
                )
                print("Consider adding more conversation paths")
            elif branch_count > 8:
                print(f"Warning: Graph has {branch_count} branches (maximum 8 allowed)")
                print("Consider consolidating similar conversation paths")
            else:
                print(
                    f"✓ Branch count is optimal: {branch_count} branches (within 7-8 range)"
                )

            # Provide recommendations if needed
            if (
                total_nodes < 15
                or total_nodes > 18
                or branch_count < 7
                or branch_count > 8
            ):
                print("\nRECOMMENDATIONS:")
                if total_nodes < 15:
                    print("- Add more conversation nodes for different user responses")
                    print("- Include additional qualification or discovery nodes")
                if total_nodes > 18:
                    print("- Remove less essential conversation nodes")
                    print("- Consolidate similar conversation stages")
                if branch_count < 7:
                    print("- Add more branching paths from key conversation nodes")
                    print("- Include additional user response scenarios")
                if branch_count > 8:
                    print("- Consolidate similar conversation paths")
                    print("- Remove redundant branching scenarios")

        except Exception as e:
            print(f"Error validating graph size: {e}")
            traceback.print_exc()

    def _count_branches_from_node(
        self,
        current_node: str,
        path: List[str],
        branches: List[List[str]],
        outgoing_edges: Dict[str, List[str]],
    ):
        """Recursively count branches from a given node"""
        try:
            current_path = path + [current_node]

            # Get outgoing edges
            next_nodes = outgoing_edges.get(current_node, [])

            if not next_nodes:
                # This is an end node - add the branch
                branches.append(current_path)
                return

            # Continue from each outgoing edge
            for next_node in next_nodes:
                if next_node not in current_path:  # Avoid cycles
                    self._count_branches_from_node(
                        next_node, current_path, branches, outgoing_edges
                    )

        except Exception as e:
            print(f"Error counting branches from node {current_node}: {e}")
            traceback.print_exc()

    def _ensure_all_branches_end_with_termination(
        self, graph_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Ensure all branches end with a proper termination tool node"""
        try:
            nodes = graph_data.get("nodes", [])
            edges = graph_data.get("edges", [])
            global_node_names = self._get_global_node_names(graph_data)

            node_names = {
                node["name"] for node in nodes if node["name"] not in global_node_names
            }
            outgoing_sources = {
                edge["from"] for edge in edges if edge["from"] in node_names
            }
            end_nodes = node_names - outgoing_sources

            # Check which end nodes are NOT termination tool nodes
            # Termination tools are endCall/transferCall (or endChat/transferChat)
            term_tool = self.valid_tool_types[0]  # endCall/endChat
            termination_tool_types = set(self.valid_tool_types)

            valid_end_nodes = set()
            for node in nodes:
                if (
                    node.get("type") == "tool"
                    and node.get("tool", {}).get("type") in termination_tool_types
                ):
                    valid_end_nodes.add(node["name"])

            bad_end_nodes = end_nodes - valid_end_nodes

            if bad_end_nodes:
                print(f"Fixing {len(bad_end_nodes)} open-ended branches...")
                existing_node_names = {node["name"] for node in nodes}
                for node_name in bad_end_nodes:
                    end_node_name = f"{node_name}_End"
                    suffix = 2
                    while end_node_name in existing_node_names:
                        end_node_name = f"{node_name}_End_{suffix}"
                        suffix += 1
                    existing_node_names.add(end_node_name)

                    # Create termination node
                    new_node = {
                        "name": end_node_name,
                        "type": "tool",
                        "tool": {
                            "type": term_tool,
                            "function": {
                                "name": self._tool_type_to_function_name(term_tool),
                                "parameters": {
                                    "type": "object",
                                    "required": [],
                                    "properties": {},
                                },
                            },
                            "messages": [
                                {
                                    "type": "request-start",
                                    "content": "Thank you, goodbye!",
                                    "blocking": True,
                                }
                            ],
                        },
                        "isEnd": True,
                    }
                    nodes.append(new_node)

                    # Link
                    edges.append(
                        {
                            "from": node_name,
                            "to": end_node_name,
                            "condition": {"type": "ai", "prompt": "End conversation"},
                        }
                    )

                graph_data["nodes"] = nodes
                graph_data["edges"] = edges

            return graph_data

        except Exception as e:
            print(f"Error ensuring termination: {e}")
            return graph_data

    def _parse_nodes(self, graph_data: Dict[str, Any]):
        """Parse dictionary nodes into GraphNode objects"""
        self.nodes = []
        try:
            for node_data in graph_data.get("nodes", []):
                n_type = NodeType.CONVERSATION
                if node_data.get("type") == "tool":
                    n_type = NodeType.TOOL
                elif node_data.get("isGlobal") or node_data.get("globalNodePlan"):
                    n_type = NodeType.GLOBAL

                node = GraphNode(
                    name=node_data["name"],
                    node_type=n_type,
                    is_start=node_data.get("isStart", False),
                    is_global=bool(
                        node_data.get("isGlobal", False)
                        or node_data.get("globalNodePlan")
                    )
                    and node_data.get("type") != "tool",
                    prompt=node_data.get("prompt", ""),
                    message_plan=node_data.get("messagePlan"),
                    variable_extraction_plan=node_data.get("variableExtractionPlan"),
                    global_node_plan=node_data.get("globalNodePlan"),
                    tool_config=node_data.get("tool"),
                )

                # Capture position metadata if exists (from previous generation or LLM guess)
                if "metadata" in node_data and "position" in node_data["metadata"]:
                    p = node_data["metadata"]["position"]
                    self.node_positions[node.name] = NodePosition(
                        p.get("x", 0), p.get("y", 0)
                    )

                self.nodes.append(node)
        except Exception as e:
            print(f"Error parsing nodes: {e}")
            traceback.print_exc()

    def _parse_edges(self, graph_data: Dict[str, Any]):
        """Parse dictionary edges into GraphEdge objects"""
        self.edges = []
        try:
            for edge_data in graph_data.get("edges", []):
                self.edges.append(
                    GraphEdge(
                        from_node=edge_data["from"],
                        to_node=edge_data["to"],
                        condition=EdgeCondition(
                            type=edge_data["condition"].get("type", "ai"),
                            prompt=edge_data["condition"].get("prompt", ""),
                        ),
                    )
                )
        except Exception as e:
            print(f"Error parsing edges: {e}")

    def _calculate_node_positions(self):
        """Calculate positions for graph visualization using a hierarchical layout"""
        try:
            # Check if positions were already provided by LLM
            positioned_nodes = [n for n in self.nodes if n.name in self.node_positions]
            unpositioned_nodes = [
                n for n in self.nodes if n.name not in self.node_positions
            ]

            if positioned_nodes and unpositioned_nodes:
                # Use LLM positions as reference and calculate missing ones
                self._calculate_missing_positions(positioned_nodes, unpositioned_nodes)
            else:
                # Calculate all positions using hierarchical layout
                self._calculate_hierarchical_positions()

        except Exception as e:
            print(f"Error calculating node positions: {e}")
            traceback.print_exc()

    def _calculate_hierarchical_positions(self):
        """Calculate positions using hierarchical layout algorithm with proper spacing"""
        try:
            # Constants for node and edge spacing
            NODE_WIDTH = 350
            NODE_HEIGHT = 350
            EDGE_SPACING = 200
            LEVEL_SPACING = NODE_WIDTH + EDGE_SPACING  # 550px between levels
            ROW_SPACING = NODE_HEIGHT + EDGE_SPACING  # 550px between rows

            # Group nodes by type and level
            start_nodes = [n for n in self.nodes if n.is_start]
            conversation_nodes = [
                n
                for n in self.nodes
                if n.node_type == NodeType.CONVERSATION
                and not n.is_start
                and not n.is_global
            ]
            tool_nodes = [n for n in self.nodes if n.node_type == NodeType.TOOL]
            global_nodes = [n for n in self.nodes if n.is_global]

            # Calculate node levels and organize by level
            levels = self._calculate_node_levels()
            nodes_by_level: Dict[Any, Any] = {}
            for node in conversation_nodes + tool_nodes:
                level = levels.get(node.name, 0)
                if level not in nodes_by_level:
                    nodes_by_level[level] = []
                nodes_by_level[level].append(node)

            # Position start nodes at top center
            for i, node in enumerate(start_nodes):
                x = 0
                y = -1000  # Top of canvas
                self.node_positions[node.name] = NodePosition(x, y)

            # Position nodes by level with proper spacing
            max_level = max(levels.values()) if levels else 0

            for level in range(max_level + 1):
                level_nodes = nodes_by_level.get(level, [])
                if not level_nodes:
                    continue

                # Calculate positions for this level
                level_x = level * LEVEL_SPACING - (max_level * LEVEL_SPACING) // 2

                # Distribute nodes in this level with proper spacing
                for i, node in enumerate(level_nodes):
                    # Calculate row position with proper spacing
                    row_y = -800 + (level * ROW_SPACING)

                    # For multiple nodes in same level, spread them horizontally
                    if len(level_nodes) > 1:
                        total_width = (len(level_nodes) - 1) * LEVEL_SPACING
                        start_x = level_x - total_width // 2
                        node_x = start_x + i * LEVEL_SPACING
                    else:
                        node_x = level_x

                    self.node_positions[node.name] = NodePosition(node_x, row_y)

            # Position global nodes at top right with proper spacing
            for i, node in enumerate(global_nodes):
                x = 800 + (i * LEVEL_SPACING)  # Right side of canvas
                y = -1000 + (i * ROW_SPACING)  # Top area
                self.node_positions[node.name] = NodePosition(x, y)

        except Exception as e:
            print(f"Error in hierarchical positioning: {e}")
            traceback.print_exc()

    def _calculate_node_levels(self) -> Dict[str, int]:
        """Calculate the level/depth of each node in the graph"""
        levels = {}
        visited = set()

        def dfs(node_name: str, level: int):
            if node_name in visited:
                return
            visited.add(node_name)
            levels[node_name] = level

            # Find outgoing edges
            for edge in self.edges:
                if edge.from_node == node_name:
                    dfs(edge.to_node, level + 1)

        # Start from start nodes
        for node in self.nodes:
            if node.is_start:
                dfs(node.name, 0)

        return levels

    def _calculate_missing_positions(
        self, positioned_nodes: List[GraphNode], unpositioned_nodes: List[GraphNode]
    ):
        """Calculate positions for unpositioned nodes based on positioned ones with proper spacing"""
        try:
            # Constants for proper spacing
            NODE_WIDTH = 350
            NODE_HEIGHT = 350
            EDGE_SPACING = 200
            LEVEL_SPACING = NODE_WIDTH + EDGE_SPACING  # 550px between levels
            ROW_SPACING = NODE_HEIGHT + EDGE_SPACING  # 550px between rows

            # Calculate levels for unpositioned nodes
            levels = self._calculate_node_levels()

            # Group unpositioned nodes by level
            nodes_by_level: Dict[Any, Any] = {}
            for node in unpositioned_nodes:
                level = levels.get(node.name, 0)
                if level not in nodes_by_level:
                    nodes_by_level[level] = []
                nodes_by_level[level].append(node)

            # Position nodes by level with proper spacing
            max_level = max(levels.values()) if levels else 0

            for level in range(max_level + 1):
                level_nodes = nodes_by_level.get(level, [])
                if not level_nodes:
                    continue

                # Calculate positions for this level
                level_x = level * LEVEL_SPACING - (max_level * LEVEL_SPACING) // 2

                # Distribute nodes in this level with proper spacing
                for i, node in enumerate(level_nodes):
                    # Calculate row position with proper spacing
                    row_y = -800 + (level * ROW_SPACING)

                    # For multiple nodes in same level, spread them horizontally
                    if len(level_nodes) > 1:
                        total_width = (len(level_nodes) - 1) * LEVEL_SPACING
                        start_x = level_x - total_width // 2
                        node_x = start_x + i * LEVEL_SPACING
                    else:
                        node_x = level_x

                    self.node_positions[node.name] = NodePosition(node_x, row_y)

        except Exception as e:
            print(f"Error calculating missing positions: {e}")
            traceback.print_exc()

    def _build_graph_structure(self) -> Dict[str, Any]:
        """Reconstruct final JSON from objects"""
        nodes_data = []
        for node in self.nodes:
            nd = {
                "name": node.name,
                "type": node.node_type.value,
                "isStart": node.is_start,
            }
            if node.name in self.node_positions:
                pos = self.node_positions[node.name]
                nd["metadata"] = {"position": {"x": pos.x, "y": pos.y}}

            if node.message_plan:
                nd["messagePlan"] = node.message_plan
            if node.variable_extraction_plan:
                nd["variableExtractionPlan"] = node.variable_extraction_plan
            if node.is_global:
                nd["isGlobal"] = True
            else:
                nd["isGlobal"] = False
            if node.global_node_plan and not node.tool_config:
                nd["globalNodePlan"] = node.global_node_plan
            if node.tool_config:
                nd["tool"] = node.tool_config
            if node.prompt:
                nd["prompt"] = node.prompt

            nodes_data.append(nd)

        edges_data = []
        for edge in self.edges:
            edges_data.append(
                {
                    "from": edge.from_node,
                    "to": edge.to_node,
                    "condition": {
                        "type": edge.condition.type,
                        "prompt": edge.condition.prompt,
                    },
                }
            )

        return {
            "name": f"{self.agent_name} - Generated Graph",
            "nodes": nodes_data,
            "edges": edges_data,
            "globalPrompt": "",
        }
