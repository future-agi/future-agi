"""
Prompts for Tool Evaluation Agent
Evaluates tool calls in conversation transcripts for correctness, timing, and result accuracy.
"""

# Planning prompt for tool evaluation
TOOL_EVAL_PLANNING_PROMPT = """You are a tool evaluation planning expert. Your task is to create a detailed framework for evaluating tool calls within a conversation transcript.

**Conversation Context:**
{conversation_context}

**Tool Call to Evaluate:**
{tool_call_info}

**Note:** Evaluation is based solely on the conversation flow and transcript data. No external documentation is available.

Create a systematic evaluation framework following these steps:

1. **Context Analysis Framework:**
   - Conversation Flow Analysis:
     * Identify the conversation stage when tool was called
     * Understand user's intent leading to the tool call
     * Analyze assistant's decision-making process
     * Note conversation history and context
     * Document any prerequisites or dependencies
   
   - Tool Call Timing Analysis:
     * Assess if the tool call timing is appropriate
     * Check if required information was available
     * Evaluate if earlier/later call would be better
     * Note any timing-related issues

2. **Tool Selection Framework:**
   - Tool Appropriateness:
     * Evaluate if correct tool was selected
     * Check against available alternative tools
     * Assess tool capability vs requirement match
     * Identify any tool selection issues
     * Consider if multiple tools needed
   
   - Parameter Analysis:
     * Define expected parameter requirements
     * Identify parameter sources (conversation, context, defaults)
     * Establish parameter validation criteria
     * Note parameter dependencies

3. **Result Evaluation Framework:**
   - Expected Outcome Definition:
     * Define what constitutes a successful result
     * List expected result structure/format
     * Identify key result components
     * Note acceptable result variations
   
   - Error Handling:
     * Define expected error scenarios
     * Assess error message appropriateness
     * Evaluate error recovery strategies

Your framework should follow this structure:
<plan>
1. **Context Evaluation Plan:**
   - Conversation Stage: [Describe stage]
   - User Intent: [Describe intent]
   - Prerequisites: [List prerequisites]
   - Dependencies: [List dependencies]

2. **Tool Selection Evaluation Plan:**
   - Tool Appropriateness Criteria: [List criteria]
   - Parameter Requirements: [List expected parameters]
   - Alternative Tools: [List alternatives]
   - Selection Issues: [Note potential issues]

3. **Result Evaluation Plan:**
   - Success Criteria: [Define success]
   - Expected Structure: [Define structure]
   - Error Scenarios: [List scenarios]
   - Validation Methods: [Define methods]
</plan>

This framework should provide a clear, systematic approach to evaluate the tool call holistically.
"""

# Analysis prompt for tool evaluation
TOOL_EVAL_ANALYSIS_PROMPT = """You are a tool call evaluation analyst. Your role is to systematically evaluate the tool call using the established framework.

**Conversation Context:**
{conversation_context}

**Tool Call to Evaluate:**
{tool_call_info}

**Note:** Evaluation is based solely on the conversation flow and transcript data. No external documentation is available.

**Evaluation Framework:**
{plan}

Based on the provided evaluation framework, conduct your analysis as follows:

1. **Context Evaluation Analysis:**
   - Timing Appropriateness:
     * Was the tool called at the right point in conversation?
     * Was all required information available?
     * Were there any missed opportunities for earlier/later calls?
     * Rate timing: PERFECT / GOOD / ACCEPTABLE / POOR
   
   - Conversation Flow:
     * Does tool call align with conversation flow?
     * Was user intent correctly understood?
     * Were prerequisites satisfied?
     * Rate flow alignment: EXCELLENT / GOOD / FAIR / POOR

2. **Tool Selection Analysis:**
   - Tool Appropriateness:
     * Was the correct tool selected for the task?
     * Are there better alternatives available?
     * Does tool capability match requirements?
     * Rate appropriateness: CORRECT / SUBOPTIMAL / INCORRECT
   
   - Parameter Analysis:
     * Were all required parameters provided?
     * Are parameter values correct and valid?
     * Are parameters sourced from correct context?
     * Missing parameters: [List if any]
     * Incorrect parameters: [List if any]
     * Rate parameters: PERFECT / GOOD / INCOMPLETE / INCORRECT

3. **Result Evaluation Analysis:**
   - Result Correctness:
     * Did tool return expected result structure?
     * Is result data accurate and complete?
     * Are there any errors or warnings?
     * Does result address user's need?
     * Rate correctness: CORRECT / PARTIALLY_CORRECT / INCORRECT / ERROR
   
   - Result Usage:
     * Was result properly used in conversation?
     * Did assistant interpret result correctly?
     * Were errors handled appropriately?
     * Rate usage: EXCELLENT / GOOD / POOR

Your analysis should be structured as follows:
<analysis>
{{
    "timing": {{
        "score": 0.0-1.0,
        "rating": "PERFECT/GOOD/ACCEPTABLE/POOR",
        "evidence": ["specific evidence points"],
        "issues": ["any timing issues identified"]
    }},
    "tool_selection": {{
        "score": 0.0-1.0,
        "rating": "CORRECT/SUBOPTIMAL/INCORRECT",
        "is_correct_tool": true/false,
        "alternative_tools": ["better alternatives if any"],
        "evidence": ["specific evidence points"]
    }},
    "parameters": {{
        "score": 0.0-1.0,
        "rating": "PERFECT/GOOD/INCOMPLETE/INCORRECT",
        "required_params": ["list of params"],
        "provided_params": ["list of provided params"],
        "missing_params": ["list if any"],
        "incorrect_params": ["list if any"],
        "evidence": ["specific evidence points"]
    }},
    "result": {{
        "score": 0.0-1.0,
        "rating": "CORRECT/PARTIALLY_CORRECT/INCORRECT/ERROR",
        "expected_structure": "description",
        "actual_structure": "description",
        "correctness_issues": ["any issues"],
        "evidence": ["specific evidence points"]
    }},
    "overall_notes": ["important observations"]
}}
</analysis>

This analysis should be thorough, objective, and based on concrete evidence from the conversation and tool call data.
"""

# Validation prompt for tool evaluation
TOOL_EVAL_VALIDATION_PROMPT = """You are a tool call evaluation validator. Your role is to make final determinations about the tool call quality based on analysis results.

**Conversation Context:**
{conversation_context}

**Tool Call to Evaluate:**
{tool_call_info}

**Note:** Evaluation is based solely on the conversation flow and transcript data. No external documentation is available.

**Analysis Results:**
{analysis}

Based on the analysis results, provide your final validation:

1. **Final Quality Assessment:**
   - Overall tool call quality rating
   - Key strengths identified
   - Key weaknesses identified
   - Critical issues that impact functionality
   - Impact on user experience

2. **Specific Findings:**
   - Timing correctness and appropriateness
   - Tool selection correctness
   - Parameter completeness and accuracy
   - Result correctness and usability
   - Error handling effectiveness

3. **Recommendations:**
   - Suggestions for improvement
   - Alternative approaches
   - Best practices to follow
   - Issues to fix

Your response must be a valid JSON object with this SIMPLE structure:
{{
    "result": Passed/Failed,
    "summary": "Clear explanation of whether tool call was correct or not, including issues and recommendations if failed"
}}

The "result" should be:
- Passed: Tool was called correctly (right timing, correct tool, valid parameters, expected result)
- Failed: Tool call had issues (wrong timing, wrong tool, bad parameters, or error in result)

The "summary" should briefly explain:
- If passed: Why it was correct
- If failed: What went wrong and how to fix it

Keep it concise and actionable.

Ensure your evaluation is:
1. Evidence-based and objective
2. Clear and actionable
3. Focused on what worked and what needs improvement

Example responses:

Successful tool call:
{{
    "result": Passed,
    "summary": "Tool called correctly when user requested weather information. All parameters (location, units) were provided from conversation context and result returned expected weather data successfully."
}}

Failed tool call:
{{
    "result": Failed,
    "summary": "Tool called without user request or context. Phone number and message were set arbitrarily without user input. Should only call SMS tool after explicit user request with validated parameters. Add user consent check before sending messages."
}}
"""

