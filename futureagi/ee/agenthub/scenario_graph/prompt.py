VOICE_GRAPH_PROMPT = """
You are an expert "Agent Definition → Conversation Workflow Graph" compiler for AI voice agents.

You will be given:
- AGENT_DETAILS (the agent's full definition/prompt persona, capabilities, policies, conversation flow, limitations, knowledge base, etc.): {agent_details}
CONVERSATION TYPE GUIDELINES: {conversation_type}

Your job:
Generate a COMPLETE workflow graph JSON that covers ALL distinct workflows explicitly present in the agent definition, expressed as nodes (what to say/do) and edges (conditions that route to the right node). The graph should be realistic, concise, and cover the most important scenarios without being overly complex.

NON-NEGOTIABLE GROUNDING RULES:
1) Use ONLY information from AGENT_DETAILS. Do NOT assume missing business rules, timelines, fees, hours, policies, systems, provider names, or domain facts.
2) Every workflow branch must be traceable to something in AGENT_DETAILS. If a workflow is not explicitly present, do not add it.
3) Edge conditions must be grounded and concrete (e.g., "User requests appointment scheduling", "User asks general health question", "User describes emergency symptoms", "User refuses to verify identity"). Do NOT invent conditions like "user is too busy" unless the agent definition says so.

PRIMARY QUALITY TARGET:
- Maximize COVERAGE of workflows in the agent definition while keeping the graph readable and well-laid out.
- Prefer completeness over arbitrary size limits.
- Choose the number of nodes/branches needed to cover ALL workflows from the agent definition with minimal redundancy.


REQUIREMENTS FOR FOCUSED GRAPH:
**ESSENTIAL NODES ONLY**: Include only the most important conversation stages:
   - Start node 
   - Main conversation flow 
   - Qualification/discovery 
   - Resolution/solution
   - Closing/transfer 
   - Tool nodes 


NODE TYPES TO INCLUDE:
- conversation: Main conversation flow nodes with firstMessage, prompt, model settings
- tool: Action nodes with specific tool types from this list:
  * endCall: For ending the call (use sparingly, only when conversation naturally ends)
  * transferCall: For transferring to a human agent
- global: Global nodes that can be accessed from anywhere (isGlobal: true)

IMPORTANT: For tool nodes, the "type" field must exactly match one of the valid tool types listed above.

NODE STRUCTURE REQUIREMENTS:
- Each node should have a clear, descriptive name
- Prompts should be detailed and include specific instructions
- Include variable extraction for important data points

CONNECTIVITY REQUIREMENTS (STRICT, ENFORCE WITH VALIDATION BEFORE RETURNING JSON):
- Exactly one start node (isStart: true).
- Every non-global node must be reachable from the start node by following edges.
- Every non-start, non-global node must have at least one incoming edge.
- Every conversation node MUST have at least one outgoing edge.
  - The ONLY exception is if the conversation node is explicitly terminal in AGENT_DETAILS AND it has exactly one outgoing edge to an endCall tool node.
- Tool nodes do not require outgoing edges.
- Global nodes might or might not have incoming or outgoing edges. These nodes are accessible from anywhere in the graph. They might be connected to tool nodes or conversation nodes as per the agent definition. Example: User goes off topic and the agent routes to a global node for handling off-topic queries.
- NO ORPHAN NODES: no node may be disconnected.
- NO OPEN CONVERSATION NODES: no conversation node may end without routing to another node or a tool node.

MANDATORY POST-GENERATION CHECKLIST (DO THIS SILENTLY BEFORE YOU OUTPUT JSON):
A) Build a list of all nodes by name.
B) Build outgoing edge counts for each node (from edges[].from).
C) For every node where type=="conversation" AND NOT is global:
   - if isStart==true: ensure outgoing_count >= 1
   - else: ensure outgoing_count >= 1
D) For every node where type=="tool": outgoing_count may be 0.
E) For every node where type=="conversation" AND NOT isStart AND NOT global: ensure incoming_count >= 1.
F) Ensure every non-global node is reachable from start via edges traversal.
G) If any rule fails, FIX THE GRAPH by adding/adjusting edges or adding a minimal "clarify_or_next" conversation node that routes forward, while staying grounded in AGENT_DETAILS.

WORKFLOW COVERAGE REQUIREMENTS:
From the agent definition, identify and model:
- Entry/intro (exact opening if provided)
- Authentication / privacy gating (if described)
- Purpose determination / intent routing
- Each distinct capability workflow (e.g., scheduling, rescheduling, cancellation, billing, troubleshooting, refunds, order tracking, etc.) as present
- Disclaimers/limitations workflows (e.g., not medical advice, not legal advice, not diagnosis) as explicit branches when user requests disallowed content
- Urgent/emergency escalation workflows if present
- Lookups / "please hold while I check" workflows if present
- Distressed caller handling if present
- Human handoff workflow if present
- Follow-up and closing as present

VARIABLE EXTRACTION RULES:
- Add variableExtractionPlan ONLY when the agent definition explicitly requires collecting information.
- Extract the MINIMAL set of variables necessary to perform the workflow described.
- For each variable:
  - title: snake_case
  - type: "string" unless the agent definition implies another basic type
  - enum: include ONLY if the agent definition provides explicit options; otherwise omit enum or set [].
- Do NOT add variable extraction just to be thorough.


CONVERSATION NODE STRUCTURE EXAMPLES:
{{
      "name": "start",
      "type": "conversation",
      "isStart": true,
      "isGlobal": false,
      "metadata": {{
        "position": {{
          "x": -705.8237557575243,
          "y": -740.9114717829991
        }}
      }},
      "prompt": "You are Riley, appointment scheduling assistant for Wellness Partners health clinic. Start with: 'Thank you for calling Wellness Partners. This is Riley, your scheduling assistant. How may I help you today?' Listen for scheduling, rescheduling, canceling, or general questions.",
      "messagePlan": {{
        "firstMessage": "Thank you for calling Wellness Partners. This is Riley, your scheduling assistant. How may I help you today?"
      }}
    }}

{{
      "name": "collect_info_urgent",
      "type": "conversation",
      "isGlobal": false,
      "metadata": {{
        "position": {{
          "x": -1618.942009199001,
          "y": 1126.5941398711234
        }}
      }},
      "prompt": "Collect patient details for urgent appointment. For new patients: 'I need your full name, date of birth, and phone number for this urgent appointment.' For existing patients: 'I need your name and date of birth to access your record.'",
      "variableExtractionPlan": {{
        "output": [
          {{
            "enum": [],
            "type": "string",
            "title": "patient_name",
            "description": "Patient's full name"
          }},
          {{
            "enum": [],
            "type": "string",
            "title": "date_of_birth",
            "description": "Patient's date of birth"
          }},
          {{
            "enum": [],
            "type": "string",
            "title": "phone_number",
            "description": "Contact phone number"
          }}
        ]
      }}
    }}


{{
      "name": "solution_alignment",
      "type": "conversation",
      "isGlobal": false,
      "metadata": {{
        "position": {{
          "x": 125.63713417374936,
          "y": 905.3346543965437
        }}
      }},
      "prompt": "Based on their pain points  and industry , highlight relevant GrowthPartners capabilities. Mention specific solutions: OperationsOS for workflow automation, InsightAnalytics for data analysis, or CustomerConnect for client relationship management. Share a relevant success story from a similar company. Explain key differentiators like customization, implementation support, and integration capabilities. Be brief."
    }}


{{
      "name": "node_1748494934592",
      "type": "conversation",
      "isGlobal": true,
      "metadata": {{
        "position": {{
          "x": 2004.5298396262576,
          "y": -711.2034330358723
        }}
      }},

      "prompt": "Confirm that the user wants to speak to a human and ask them what they would like to speak to the human about",
      "globalNodePlan": {{
        "enabled": true,
        "enterCondition": "User wants to speak to a human"
      }}
    }}


TOOL NODE STRUCTURE EXAMPLES:

END CALL NODE (use sparingly, only when conversation naturally ends):
{{
  "name": "End Call - Success",
  "type": "tool",
  "metadata": {{
    "position": {{"x": 0, "y": 0}}
  }},
  "tool": {{
    "type": "endCall",
    "function": {{
      "name": "end_call",
      "parameters": {{
        "type": "object",
        "required": [],
        "properties": {{}}
      }}
    }},
    "messages": [
      {{
        "type": "request-start",
        "content": "Thank you for calling! Have a great day!",
        "blocking": true
      }}
    ]
  }}
}}

TRANSFER CALL NODE:
{{
  "name": "Transfer to Sales",
  "type": "tool",
  "metadata": {{
    "position": {{"x": 0, "y": 0}}
  }},
  "tool": {{
    "type": "transferCall",
    "function": {{
      "name": "transfer_call",
      "parameters": {{
        "type": "object",
        "required": [],
        "properties": {{}}
      }}
    }},
    "destinations": []
  }}
}}

PROMPT WRITING RULES (NODE PROMPTS):
- Each node.prompt must be operational: what to say, what to ask, what to confirm, what to do next.
- Preserve any MUST-use phrases from agent definition verbatim (especially openers, disclaimers, HIPAA/privacy statements, safety language).
- Keep prompts concise but complete enough for the node's purpose.
- Include explicit confirmation steps only if stated in the agent definition.

EDGE CONDITION RULES:
- Each edge.condition must be:
  {{"type":"ai","prompt":"..."}}
- The condition prompt must describe the trigger in plain language (user intent, user response, or state) grounded in agent definition.
- Add fallback edges for ambiguous/unclear responses where the agent definition implies clarification behavior (e.g., "User response unclear; ask clarifying question").
- Do NOT add excessive error handling beyond what the agent definition implies.

GLOBAL PROMPT:
- Set "globalPrompt" to a compact set of universal guardrails ONLY if explicitly present in agent definition (e.g., privacy compliance, tone, safety, non-diagnostic limits).
- Do not invent new policies.


POSITIONING GUIDELINES - CRITICAL FOR VISUAL LAYOUT:
When assigning x and y coordinates for node positions, ensure EXCELLENT SPACING to create a clean, professional graph layout:

**ENHANCED SPACING REQUIREMENTS:**
- Node dimensions: 350x350 pixels each
- Minimum distance between ANY two nodes: 200 pixels (edge spacing)
- Horizontal spacing: At least 550 pixels between nodes in the same row (350px node + 200px spacing)
- Vertical spacing: At least 550 pixels between nodes in the same column (350px node + 200px spacing)
- Use hierarchical layout based on node levels from start nodes
- Use the full canvas area efficiently - spread nodes across the entire available space

**HIERARCHICAL POSITIONING STRATEGY:**
- Start node: Top-center area (y: -1000, x: 0)
- Level 1 nodes: Second row (y: -250, x: distributed horizontally with 550px spacing)
- Level 2 nodes: Third row (y: 300, x: distributed horizontally with 550px spacing)
- Level 3 nodes: Fourth row (y: 850, x: distributed horizontally with 550px spacing)
- Tool nodes: Bottom area, distributed with proper spacing
- Global nodes: Top-right area (y: -1000 to -450, x: 800+)

**SPECIFIC COORDINATE EXAMPLES WITH PROPER SPACING:**
- Start node: {{"x": 0, "y": -1000}}
- Level 1 nodes: {{"x": -550, "y": -250}}, {{"x": 0, "y": -250}}, {{"x": 550, "y": -250}}
- Level 2 nodes: {{"x": -825, "y": 300}}, {{"x": -275, "y": 300}}, {{"x": 275, "y": 300}}, {{"x": 825, "y": 300}}
- Level 3 nodes: {{"x": -1100, "y": 850}}, {{"x": -550, "y": 850}}, {{"x": 0, "y": 850}}, {{"x": 550, "y": 850}}, {{"x": 1100, "y": 850}}
- Tool nodes: {{"x": -825, "y": 1400}}, {{"x": -275, "y": 1400}}, {{"x": 275, "y": 1400}}, {{"x": 825, "y": 1400}}
- Global nodes: {{"x": 1100, "y": -1000}}, {{"x": 1650, "y": -450}}

**LAYOUT PRINCIPLES:**
- Use the entire canvas space efficiently
- Create clear visual flow from top-left to bottom-right
- Group related nodes in logical clusters with adequate spacing
- Avoid any overlapping or clustering
- Ensure edges have clear paths without crossing through other nodes

Return JSON with this EXACT structure:
{{
  "name": "Agent Name - Conversation Graph",
  "nodes": [
    {{
      "name": "node_name",
      "type": "conversation",
      "isStart": true,
      "metadata": {{
        "position": {{"x": 0, "y": 0}}
      }},
      "prompt": "Detailed, specific prompt for this conversation stage...",
      "messagePlan": {{
        "firstMessage": "Suggested opening message for this node"
      }},
      "variableExtractionPlan": {{
        "output": [
          {{"type": "string", "title": "variable_name", "description": "What to extract", "enum": ["option1", "option2"]}}
        ]
      }}
    }},
    {{
      "name": "tool_node_name",
      "type": "tool",
      "metadata": {{
        "position": {{"x": 100, "y": 100}}
      }},
      "tool": {{
        "type": "endCall",
        "function": {{
          "name": "end_call",
          "parameters": {{
            "type": "object",
            "required": [],
            "properties": {{}}
          }}
        }},
        "messages": [
          {{
            "type": "request-start",
            "content": "Thank you for calling. Have a great day!",
            "blocking": true
          }}
        ]
      }}
    }},
    {{
      "name": "global_node_name",
      "type": "conversation",
      "isGlobal": true,
      "metadata": {{
        "position": {{"x": -100, "y": -100}}
      }},
      "prompt": "Global node prompt...",
      "globalNodePlan": {{
        "enabled": true,
        "enterCondition": "Condition to enter this global node"
      }}
    }}
  ],
  "edges": [
    {{
      "from": "node_name",
      "to": "next_node",
      "condition": {{
        "type": "ai",
        "prompt": "Specific condition that triggers this transition"
      }}
    }}
  ],
  "globalPrompt": ""
}}

Start node should have agent's opening greeting as firstMessage.


**CRITICAL BRANCH ENDING REQUIREMENTS:**
- **NO OPEN NODES**: Every conversation node must have outgoing edges
- **NO DEAD ENDS**: Avoid conversation nodes without outgoing edges

***NO CONVERSATION NODE MAY END WITHOUT ROUTING TO ANOTHER NODE OR A TOOL NODE.***

FINAL INSTRUCTION:
Generate the most complete workflow graph possible from the given agent definition, with grounded branches and strong connectivity, while preserving clean formatting and spacing. 
Return only the JSON response with no extra text or comments.
"""


CHAT_GRAPH_PROMPT = """
You are an expert "Agent Definition → Conversation Workflow Graph" compiler for AI chat agents.

You will be given:
- AGENT_DETAILS (the agent's full definition/prompt persona, capabilities, policies, conversation flow, limitations, knowledge base, etc.): {agent_details}
CONVERSATION TYPE GUIDELINES: {conversation_type}

Your job:
Generate a COMPLETE workflow graph JSON that covers ALL distinct workflows explicitly present in the agent definition, expressed as nodes (what to say/do) and edges (conditions that route to the right node). The graph should be realistic, concise, and cover the most important scenarios without being overly complex.

NON-NEGOTIABLE GROUNDING RULES:
1) Use ONLY information from AGENT_DETAILS. Do NOT assume missing business rules, timelines, fees, hours, policies, systems, provider names, or domain facts.
2) Every workflow branch must be traceable to something in AGENT_DETAILS. If a workflow is not explicitly present, do not add it.
3) Edge conditions must be grounded and concrete (e.g., "User requests appointment scheduling", "User asks general health question", "User describes emergency symptoms", "User refuses to verify identity"). Do NOT invent conditions like "user is too busy" unless the agent definition says so.

PRIMARY QUALITY TARGET:
- Maximize COVERAGE of workflows in the agent definition while keeping the graph readable and well-laid out.
- Prefer completeness over arbitrary size limits.
- Choose the number of nodes/branches needed to cover ALL workflows from the agent definition with minimal redundancy.


REQUIREMENTS FOR FOCUSED GRAPH:
**ESSENTIAL NODES ONLY**: Include only the most important conversation stages:
   - Start node 
   - Main conversation flow 
   - Qualification/discovery 
   - Resolution/solution
   - Closing/transfer 
   - Tool nodes 


NODE TYPES TO INCLUDE:
- conversation: Main conversation flow nodes with firstMessage, prompt, model settings
- tool: Action nodes with specific tool types from this list:
  * endChat: For ending the chat (use sparingly, only when conversation naturally ends)
  * transferChat: For transferring to a human agent. **USE SPARINGLY.**
- global: Global nodes that can be accessed from anywhere (isGlobal: true)

IMPORTANT: For tool nodes, the "type" field must exactly match one of the valid tool types listed above.

NODE STRUCTURE REQUIREMENTS:
- Each node should have a clear, descriptive name
- Prompts should be detailed and include specific instructions
- Include variable extraction for important data points

CONNECTIVITY REQUIREMENTS (STRICT, ENFORCE WITH VALIDATION BEFORE RETURNING JSON):
- Exactly one start node (isStart: true).
- Every non-global node must be reachable from the start node by following edges.
- Every non-start, non-global node must have at least one incoming edge.
- Every conversation node MUST have at least one outgoing edge.
  - The ONLY exception is if the conversation node is explicitly terminal in AGENT_DETAILS AND it has exactly one outgoing edge to an endChat tool node.
- Tool nodes do not require outgoing edges.
- Global nodes might or might not have incoming or outgoing edges. These nodes are accessible from anywhere in the graph. They might be connected to tool nodes or conversation nodes as per the agent definition. Example: User goes off topic and the agent routes to a global node for handling off-topic queries.
- NO ORPHAN NODES: no node may be disconnected.
- NO OPEN CONVERSATION NODES: no conversation node may end without routing to another node or a tool node.

MANDATORY POST-GENERATION CHECKLIST (DO THIS SILENTLY BEFORE YOU OUTPUT JSON):
A) Build a list of all nodes by name.
B) Build outgoing edge counts for each node (from edges[].from).
C) For every node where type=="conversation" AND NOT is global:
   - if isStart==true: ensure outgoing_count >= 1
   - else: ensure outgoing_count >= 1
D) For every node where type=="tool": outgoing_count may be 0.
E) For every node where type=="conversation" AND NOT isStart AND NOT global: ensure incoming_count >= 1.
F) Ensure every non-global node is reachable from start via edges traversal.
G) If any rule fails, FIX THE GRAPH by adding/adjusting edges or adding a minimal "clarify_or_next" conversation node that routes forward, while staying grounded in AGENT_DETAILS.

WORKFLOW COVERAGE REQUIREMENTS:
From the agent definition, identify and model:
- Entry/intro (exact opening if provided)
- Authentication / privacy gating (if described)
- Purpose determination / intent routing
- Each distinct capability workflow (e.g., scheduling, rescheduling, cancellation, billing, troubleshooting, refunds, order tracking, etc.) as present
- Disclaimers/limitations workflows (e.g., not medical advice, not legal advice, not diagnosis) as explicit branches when user requests disallowed content
- Urgent/emergency escalation workflows if present
- Lookups / "please hold while I check" workflows if present
- Distressed customer handling if present
- Human handoff workflow if present
- Follow-up and closing as present

VARIABLE EXTRACTION RULES:
- Add variableExtractionPlan ONLY when the agent definition explicitly requires collecting information.
- Extract the MINIMAL set of variables necessary to perform the workflow described.
- For each variable:
  - title: snake_case
  - type: "string" unless the agent definition implies another basic type
  - enum: include ONLY if the agent definition provides explicit options; otherwise omit enum or set [].
- Do NOT add variable extraction just to be thorough.


CONVERSATION NODE STRUCTURE EXAMPLES:
{{
      "name": "start",
      "type": "conversation",
      "isStart": true,
      "isGlobal": false,
      "metadata": {{
        "position": {{
          "x": -705.8237557575243,
          "y": -740.9114717829991
        }}
      }},
      "prompt": "You are Riley, appointment scheduling assistant for Wellness Partners. Start with: 'Hi! This is Riley, your scheduling assistant. How may I help you today?' Listen for scheduling, rescheduling, canceling, or general questions.",
      "messagePlan": {{
        "firstMessage": "Hi! This is Riley, your scheduling assistant. How may I help you today?"
      }}
    }}

{{
      "name": "collect_info_urgent",
      "type": "conversation",
      "isGlobal": false,
      "metadata": {{
        "position": {{
          "x": -1618.942009199001,
          "y": 1126.5941398711234
        }}
      }},
      "prompt": "Collect patient details for urgent appointment. For new patients: 'I need your full name, date of birth, and phone number.' For existing patients: 'I need your name and date of birth to access your record.'",
      "variableExtractionPlan": {{
        "output": [
          {{
            "enum": [],
            "type": "string",
            "title": "patient_name",
            "description": "Patient's full name"
          }},
          {{
            "enum": [],
            "type": "string",
            "title": "date_of_birth",
            "description": "Patient's date of birth"
          }},
          {{
            "enum": [],
            "type": "string",
            "title": "phone_number",
            "description": "Contact phone number"
          }}
        ]
      }}
    }}

TOOL NODE STRUCTURE EXAMPLES:

END CHAT NODE (use sparingly, only when conversation naturally ends):
{{
  "name": "End Chat - Success",
  "type": "tool",
  "metadata": {{
    "position": {{"x": 0, "y": 0}}
  }},
  "tool": {{
    "type": "endChat",
    "function": {{
      "name": "end_chat",
      "parameters": {{
        "type": "object",
        "required": [],
        "properties": {{}}
      }}
    }},
    "messages": [
      {{
        "type": "request-start",
        "content": "Thank you for chatting with us! Have a great day!",
        "blocking": true
      }}
    ]
  }}
}}

TRANSFER CHAT NODE:
{{
  "name": "Transfer to Sales",
  "type": "tool",
  "metadata": {{
    "position": {{"x": 0, "y": 0}}
  }},
  "tool": {{
    "type": "transferChat",
    "function": {{
      "name": "transfer_chat",
      "parameters": {{
        "type": "object",
        "required": [],
        "properties": {{}}
      }}
    }},
    "destinations": []
  }}
}}

PROMPT WRITING RULES (NODE PROMPTS):
- Each node.prompt must be operational: what to say, what to ask, what to confirm, what to do next.
- Preserve any MUST-use phrases from agent definition verbatim (especially openers, disclaimers, HIPAA/privacy statements, safety language).
- Keep prompts concise but complete enough for the node's purpose.
- Include explicit confirmation steps only if stated in the agent definition.

EDGE CONDITION RULES:
- Each edge.condition must be:
  {{"type":"ai","prompt":"..."}}
- The condition prompt must describe the trigger in plain language (user intent, user response, or state) grounded in agent definition.
- Add fallback edges for ambiguous/unclear responses where the agent definition implies clarification behavior (e.g., "User response unclear; ask clarifying question").
- Do NOT add excessive error handling beyond what the agent definition implies.

GLOBAL PROMPT:
- Set "globalPrompt" to a compact set of universal guardrails ONLY if explicitly present in agent definition (e.g., privacy compliance, tone, safety, non-diagnostic limits).
- Do not invent new policies.


POSITIONING GUIDELINES - CRITICAL FOR VISUAL LAYOUT:
When assigning x and y coordinates for node positions, ensure EXCELLENT SPACING to create a clean, professional graph layout:

**ENHANCED SPACING REQUIREMENTS:**
- Node dimensions: 350x350 pixels each
- Minimum distance between ANY two nodes: 200 pixels (edge spacing)
- Horizontal spacing: At least 550 pixels between nodes in the same row (350px node + 200px spacing)
- Vertical spacing: At least 550 pixels between nodes in the same column (350px node + 200px spacing)
- Use hierarchical layout based on node levels from start nodes
- Use the full canvas area efficiently - spread nodes across the entire available space

**HIERARCHICAL POSITIONING STRATEGY:**
- Start node: Top-center area (y: -1000, x: 0)
- Level 1 nodes: Second row (y: -250, x: distributed horizontally with 550px spacing)
- Level 2 nodes: Third row (y: 300, x: distributed horizontally with 550px spacing)
- Level 3 nodes: Fourth row (y: 850, x: distributed horizontally with 550px spacing)
- Tool nodes: Bottom area, distributed with proper spacing
- Global nodes: Top-right area (y: -1000 to -450, x: 800+)

**SPECIFIC COORDINATE EXAMPLES WITH PROPER SPACING:**
- Start node: {{"x": 0, "y": -1000}}
- Level 1 nodes: {{"x": -550, "y": -250}}, {{"x": 0, "y": -250}}, {{"x": 550, "y": -250}}
- Level 2 nodes: {{"x": -825, "y": 300}}, {{"x": -275, "y": 300}}, {{"x": 275, "y": 300}}, {{"x": 825, "y": 300}}
- Level 3 nodes: {{"x": -1100, "y": 850}}, {{"x": -550, "y": 850}}, {{"x": 0, "y": 850}}, {{"x": 550, "y": 850}}, {{"x": 1100, "y": 850}}
- Tool nodes: {{"x": -825, "y": 1400}}, {{"x": -275, "y": 1400}}, {{"x": 275, "y": 1400}}, {{"x": 825, "y": 1400}}
- Global nodes: {{"x": 1100, "y": -1000}}, {{"x": 1650, "y": -450}}

**LAYOUT PRINCIPLES:**
- Use the entire canvas space efficiently
- Create clear visual flow from top-left to bottom-right
- Group related nodes in logical clusters with adequate spacing
- Avoid any overlapping or clustering
- Ensure edges have clear paths without crossing through other nodes

Return JSON with this EXACT structure:
{{
  "name": "Agent Name - Conversation Graph",
  "nodes": [
    {{
      "name": "node_name",
      "type": "conversation",
      "isStart": true,
      "isGlobal": false,
      "metadata": {{
        "position": {{"x": 0, "y": 0}}
      }},
      "prompt": "Detailed, specific prompt for this conversation stage...",
      "messagePlan": {{
        "firstMessage": "Suggested opening message for this node"
      }},
      "variableExtractionPlan": {{
        "output": [
          {{"type": "string", "title": "variable_name", "description": "What to extract", "enum": ["option1", "option2"]}}
        ]
      }}
    }},
    {{
      "name": "tool_node_name",
      "type": "tool",
      "metadata": {{
        "position": {{"x": 100, "y": 100}}
      }},
      "tool": {{
        "type": "endChat",
        "function": {{
          "name": "end_chat",
          "parameters": {{
            "type": "object",
            "required": [],
            "properties": {{}}
          }}
        }},
        "messages": [
          {{
            "type": "request-start",
            "content": "Thank you for contacting. Have a great day!",
            "blocking": true
          }}
        ]
      }}
    }},
    {{
      "name": "global_node_name",
      "type": "conversation",
      "isGlobal": true,
      "metadata": {{
        "position": {{"x": -100, "y": -100}}
      }},
      "prompt": "Global node prompt...",
      "globalNodePlan": {{
        "enabled": true,
        "enterCondition": "Condition to enter this global node"
      }}
    }}
  ],
  "edges": [
    {{
      "from": "node_name",
      "to": "next_node",
      "condition": {{
        "type": "ai",
        "prompt": "Specific condition that triggers this transition"
      }}
    }}
  ],
  "globalPrompt": ""
}}

Start node should have agent's opening greeting as firstMessage.


**CRITICAL BRANCH ENDING REQUIREMENTS:**
- **NO OPEN NODES**: Every conversation node must have outgoing edges
- **NO DEAD ENDS**: Avoid conversation nodes without outgoing edges

***NO CONVERSATION NODE MAY END WITHOUT ROUTING TO ANOTHER NODE OR A TOOL NODE.***

FINAL INSTRUCTION:
Generate the most complete workflow graph possible from the given agent definition, with grounded branches and strong connectivity, while preserving clean formatting and spacing. 
Return only the JSON response with no extra text or comments.
"""


UNIFIED_CATEGORY_PROMPT = """
You are an expert conversation designer and contact-center taxonomist.

Your task:
Given:
1) A conversation branch (sequence of node names in an agent workflow), and
2) A list of customer situations (short scenario descriptions),

identify ONE specific category that precisely describes the workflow AND problem type of this branch.

Instructions:
- Read the branch node names to identify the SPECIFIC workflow step(s) the branch passes through.
- Read the customer situations to understand the specific problem type.
- The category MUST reflect the specific workflow node(s) in the branch — branches with different key nodes MUST get different categories.
- The category should be:
  - Short (3-7 words),
  - SPECIFIC to the exact workflow in this branch, not a broad umbrella,
  - Include both the problem type AND the resolution method when the branch makes it clear (resolved vs escalated vs transferred).

CRITICAL RULES:
- NEVER output "miscellaneous", "general", "other", or any generic/catch-all category.
- Be SPECIFIC, not abstract. A category should distinguish THIS branch from other branches in the same graph.
- Two branches that go through different key workflow nodes MUST get different categories, even if some situations overlap.
- Include the resolution outcome when visible in the branch (e.g., a branch ending with a transfer node indicates escalation; a branch ending with a success/close node indicates direct resolution).

Output rules:
- Output ONLY the category string (3-7 words).
- Do NOT explain your reasoning.
- Do NOT return JSON.

<input>
branch:
{branch}

situations:
{situations}
</input>

Output:
"""


SITUATION_NODE_GENERATION_PROMPT = """
You are designing detailed conversation flows for an AI voice agent to handle a specific customer situation.

Situation: {situation}
Ideal Outcomes: {outcome}
Agent Description: {description}
Language: {language}

Create SEPARATE, DETAILED conversation flows for EACH possible outcome. Each flow should be a realistic, natural conversation that gradually builds up to that specific outcome.

CRITICAL REQUIREMENTS:
- Create ONE complete flow for ideal outcome (no complex conditionals)
- Flow should be 6-12 messages long (alternating between user and assistant)
- Make conversations feel natural, realistic, and emotionally authentic
- Show the progression of emotions, reactions, and responses
- Include realistic details, specific information, and natural language patterns
- Flow must end with an End node
- Use ONLY these node types: message, end

For outcome, create a flow that shows:
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
  "flow": 
    {{
      "outcome": "Ideal Outcome description",
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
    }}
}}
"""

BRANCH_DESCRIPTION_PROMPT = """
Based on the conversation path nodes: {path_nodes}

Start node: {start_node}
End node: {end_node}

Generate a concise, descriptive phrase (2-4 words) that captures the essence of this conversation flow. 
Focus on the main purpose or outcome of this specific path.

Examples of good descriptions:
- "successful lead qualification"
- "order tracking assistance" 
- "customer support resolution"
- "sales inquiry handling"
- "technical issue escalation"

Return only the description phrase, nothing else.
"""

USER_INTENT_PROMPT = """
You are a senior conversation intelligence analyst.

Your task:
Identify the user's PRIMARY INTENT based on the provided input.

Context:
- The input may be:
  - A single user message, OR
  - A multi-turn conversation between a user and an AI agent
- You will also be given the AGENT DEFINITION, which describes:
  - The agent's role
  - The type of problems it is designed to solve
  - Its domain and capabilities

You MUST interpret the user's intent relative to what this agent is meant to handle.

Instructions:
1. Carefully analyze the entire input (conversation or single message).
2. Infer the user's core objective — what they are ultimately trying to achieve.
3. Ignore:
   - Small talk
   - Politeness
   - Emotional expressions unless they change the intent
4. Focus on the most specific, actionable intent possible.
5. If multiple intents appear, select the dominant or final intent driving the interaction.
6. Phrase the intent at a task or goal level, not as a narrative.

Granularity guidelines:
- Prefer concrete intents over abstract ones.
  - Good: “Resolve unexpected subscription charge”
  - Bad: “Billing issue”
- Include the object of the action when possible.
  - Good: “Reset account password after lockout”
  - Bad: “Account help”
- Do NOT include agent actions or system steps.

Output rules (STRICT):
- Output ONLY the user's intent.
- Use a concise phrase (3-7 words).
- Do NOT explain reasoning.
- Do NOT use bullet points.
- Do NOT return JSON.
- Do NOT mention the conversation, transcript, or agent explicitly.

Examples:

Example 1:
Input:
User: I was charged twice for my premium plan this month.

Output:
Dispute duplicate subscription charge

Example 2:
Input:
User: Hi
User: The app keeps crashing when I upload files

Output:
Fix app crash during file upload

Example 3:
Input:
User: Can you tell me if my data is safe after the recent update?

Output:
Verify data security after update

Example 4:
Input:
User: Hi, I need help with my account. Agent: Sure, what seems to be the issue? User: I can't log in, it says my password is incorrect. Agent: Have you tried resetting your password? User: Yes, but I never received the reset email. Agent: Let me check your account status. User: Thank you. 

Output: 
User is trying to recover account access, but is facing issues with the password reset process.

<input>
Agent Definition:
{agent_definition}

Conversation or Message:
{transcript}
</input>

Output:
"""


BATCH_CATEGORY_PROMPT = """
You are an expert conversation designer and contact-center taxonomist.

Your task:
Given a list of conversation branches (each with associated customer situations),
assign ONE specific category to EACH branch that precisely describes its workflow and problem type.

Instructions:
- For each branch, identify the KEY workflow node(s) that distinguish it from other branches.
- Different key workflow nodes MUST produce different categories.
- Include the resolution outcome from the branch ending: a branch ending with a transfer/escalation node should be categorized differently than one ending with a success/close node, even for the same workflow.
- Each category should be short (3-7 words), specific and descriptive.
- Derive the category from the ACTUAL branch nodes and situations — do not use generic umbrella terms.

CRITICAL RULES:
- NEVER assign "miscellaneous", "general", "other", or any generic/catch-all category.
- Branches with DIFFERENT key workflow nodes MUST get DIFFERENT categories — never lump them together.
- Be specific: the category should distinguish THIS branch from other branches in the same graph.
- Distinguish resolution paths: a branch ending in a transfer node should have a different category suffix than one ending in a success/close node for the same workflow type.

Output rules:
- Return STRICT JSON only: a JSON object mapping each branch name to its category string (3-7 words each).
- Do NOT explain your reasoning.
- Do NOT include any text outside the JSON object.

<input>
{branches_json}
</input>

Output:
"""


DEDUPLICATE_CATEGORIES_PROMPT = """
You are given a list of category labels. Some may be exact rephrasings of the same concept.

Your task:
- ONLY merge categories that are exact rephrasings of the same thing (same workflow, same problem type, same resolution — just worded differently).
- Do NOT merge categories that describe different workflows, different problem types, or different resolution paths.
- For each group of exact rephrasings, pick ONE canonical label (the clearest version).
- Categories that are already unique MUST map to themselves.
- Do NOT invent new categories. Pick from the existing list.

CRITICAL: Be CONSERVATIVE. When in doubt, keep categories separate. It is far better to have two similar-but-distinct categories than to incorrectly merge different workflows. Only merge when you are certain the two labels mean exactly the same thing.

Output rules:
- Return STRICT JSON only: a JSON object mapping each original category to its canonical category.
- Every input category MUST appear as a key.
- Do NOT explain your reasoning.
- Do NOT include any text outside the JSON object.

<input>
{categories_json}
</input>

Output:
"""
