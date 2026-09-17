text_template_plan_prompt_whole = """

        Judging Criteria: {eval_instruction}

        Chat History: {chat_history}

        Task: Develop a step-by-step plan to evaluate how much of the chat history is relevant to the conversation, ensuring alignment with the Judging Criteria. The plan should be clear and broken down into actionable subtasks.

        Plan:

        1.	Analyze Judging Criteria: Review the criteria to understand the key factors for evaluation.
        2.	Identify Key Elements: Extract essential details from the chat history.
        3.	Break Down Criteria: Decompose the Judging Criteria into specific tasks for focused analysis.
        4.	Establish Sequence: Determine the order in which tasks should be addressed.
        5.	Evaluate Subtasks: Address each subtask using the relevant information from the context.
        6.	Synthesize Findings: Combine the results from each subtask to form a complete assessment.
        7.	Validate Results: Ensure the final assessment meets the Judging Criteria and accurately evaluates how much of the chat history is relevant to the conversation.

        Elaboration: Provide detailed steps for each phase, using the key information extracted from the chat_history and eval_instruction.
        """

text_template_cot_response_prompt_whole = """

        Judging Criteria: {eval_instruction}

        Chat History: {chat_history}

        Task: Utilize the provided plan to assess the relevance of the chat history to the conversation, according to the Judging Criteria. The final Judgment should include a comprehensive explanation.

        Plan:
        {plan}

        Execution:
        Follow the steps in the plan to systematically evaluate the relevance of the chat history. Provide a final Judgment with a thorough explanation based on this evaluation.
        """


text_template_plan_prompt="""
Task: You are tasked with generating a detailed plan to evaluate how much of a given output is relevant to a given input and past conversation (if given) based on specific judging criteria. Follow these steps carefully to create the plan:

1. First, review the Judging Criteria:
Judging Criteria: {eval_instruction}

2. Next, examine the Output , Input and Past Conversation (if given):

Past Conversation: {past_conversation}

Input: {input}

Output: {output}


3. Analyze the Judging Criteria to get key factors that will be used for evaluation.


4. Identify Key Elements in the Past Conversation (if given) and Input to highlight or note down important details, topics.


5. Break Down the Judging Criteria into specific, actionable subtasks.


6. Establish a most logical order to address the subtasks.


7. Evaluate Subtasks to Address each subtask systematically.Refer back to the past conversation(if given), input and output for relevant information.

8. Generate a step by step plan to evaluate the output on judging criteria looking at past conversation (if given) and input. Your response should be structured as follows:

<plan>
[provide your detailed plan here]
<plan>

Remember, your output should be list of step By step detailed plan to follow to evaluate the output.
"""


text_template_cot_response_prompt = """
Task: You are tasked with evaluating how much of a given output is relevant to a given input and past conversation (if given) based on specific judging criteria. Follow the plan to provide the judgement.

First, review the evaluation criteria:
Judging Criteria: {eval_instruction}

Next, examine the Output , Input and Past Conversation:
Output: {output}

Input: {input}

Past Conversation: {past_conversation}

Now, consider the following plan for evaluating the chat history:
Plan: {plan}

To complete this task:

1. Follow each step of the plan methodically, analyzing the past conversation(if given), input and output in relation to the evaluation criteria.
2. As you go through the plan, make notes on how well the output meets each aspect of the criteria.


After completing your analysis, provide your final judgment and explanation. Your response should be structured as follows:

<judgment>
[Provide a clear, concise statement of your overall judgment on the relevance of the past conversation(if given), input and output according to the evaluation criteria]
</judgment>

<explanation>
[Offer a detailed explanation of your judgment, referencing specific aspects of the evaluation criteria and examples from the past conversation(if given), input and output. Discuss both strengths and weaknesses in the relevance. Your explanation should be thorough and well-reasoned, demonstrating a clear connection between the past conversation(if given), input and output, the evaluation criteria, and your final judgment.]
</explanation>

Remember to be objective and base your evaluation solely on the provided chat history and evaluation criteria. Do not introduce external information or make assumptions beyond what is presented in the task.
"""



generic_text_template_plan_prompt = """
Task: Develop a comprehensive plan to evaluate the relevance of a given output based on detailed judging criteria. Follow these steps to create an effective evaluation plan:

1. Thoroughly review the Judging Criteria:
   - Judging Criteria: {eval_instruction}
   - Identify key factors and specific instructions for evaluation.

{%- if input -%}
2. Examine the Input:
   - Input: {input}
   - Note any critical elements that should align with the output.
{%- endif -%}

{%- if context -%}
3. Examine the Context:
   - Context: {context}
   - Understand the background and its influence on the output.
{%- endif -%}

4. Examine the Output:
   - Output: {output}
   - Identify elements that should be evaluated against the criteria.

5. Analyze the Judging Criteria:
   - Extract key factors for evaluation.
   - Ensure understanding of each criterion's intent.

6. Break Down the Judging Criteria:
   - Divide into specific, actionable subtasks.
   - Ensure each subtask addresses a distinct evaluation aspect.

7. Establish a Logical Order:
   - Determine the sequence for addressing subtasks.
   - Consider dependencies and logical flow.

8. Generate a Step-by-Step Plan:
   - Structure your response as follows:

<plan>
[Provide your detailed plan here]
</plan>

Ensure your output is a detailed, step-by-step plan to evaluate the output effectively.
"""

generic_text_template_cot_response_prompt = """
Task: Evaluate the relevance of a given output based on the provided judging criteria. Use the plan to guide your judgment.

1. Review the Evaluation Criteria:
   - Judging Criteria: {eval_instruction}
   - Understand the expectations and key evaluation points.

{%- if input -%}
2. Examine the Input:
   - Input: {input}
   - Assess how the output aligns with the input.
{%- endif -%}

{%- if context -%}
3. Examine the Context:
   - Context: {context}
   - Evaluate the output's relevance within the context.
{%- endif -%}

4. Examine the Output:
   - Output: {output}
   - Identify elements to be evaluated against the criteria.

5. Follow the Plan:
   - Plan: {plan}
   - Methodically analyze the output in relation to the criteria.

6. Make Notes:
   - Document how well the output meets each criterion.

7. Provide Final Judgment and Explanation:
   - Structure your response as follows:

<judgment>
[Provide a clear, concise statement of your overall judgment on the relevance of the output according to the evaluation criteria]
</judgment>

<explanation>
[Provide a clear, concise judgment in 2  3 sentences on the relevance of the output based on the evaluation criteria, identifying specific strengths and weaknesses and suggesting actionable improvements for better alignment with the criteria.]
</explanation>

Remain objective and base your evaluation solely on the provided output and criteria. Avoid introducing external information or assumptions.
"""


#2nd version

generic_text_template_plan_prompt = """
Task: Create a structured evaluation plan to assess output relevance based on specific judging criteria.

Key Elements to Review:
1. Judging Criteria: {eval_instruction}

{%- if input -%}
2. Input: {input}
{%- endif -%}

{%- if context -%}
3. Context: {context}
{%- endif -%}

4. Output: {output}

Planning Instructions:
1. Break down the judging criteria into clear evaluation components
2. Create specific checkpoints for each component
3. Design a systematic evaluation approach
4. Include methods to assess alignment between output and criteria
5. Establish clear metrics or indicators for success

Please provide your evaluation plan in this structure:

<plan>
1. [First evaluation step with specific checkpoints]
2. [Second evaluation step with specific checkpoints]
[Continue with numbered steps]
Final. [Verification step to ensure comprehensive evaluation]
</plan>

Note: Ensure each step is specific, measurable, and directly tied to the judging criteria.
"""

generic_text_template_cot_response_prompt = """
Task: Execute a systematic evaluation of the output following the established plan.

Elements for Review:
1. Judging Criteria: {eval_instruction}

{%- if input -%}
2. Input: {input}
{%- endif -%}

{%- if context -%}
3. Context: {context}
{%- endif -%}

4. Output: {output}

5. Evaluation Plan: {plan}

Evaluation Process:
1. Follow each step of the provided plan
2. Document findings for each checkpoint
3. Note specific evidence supporting judgments
4. Identify clear strengths and areas for improvement
5. Form a comprehensive evaluation

Provide your evaluation in this structure:

<judgment>
[Clear YES/NO or SCORE (1-10) judgment with a one-sentence summary]
</judgment>

<explanation>
1. Strengths: [Key strengths based on criteria]
2. Areas for Improvement: [Specific improvement points]
3. Recommendations: [1-2 actionable suggestions]
</explanation>

Note: Base all evaluations strictly on the provided criteria and evidence from the output.
"""


# 3rd version

generic_text_template_plan_prompt = """
Task: Create a structured evaluation plan to assess based on specific judging criteria.

Key Elements to Review:
1. Judging Criteria: {{ eval_instruction }}

{%- if input %}
2. Input: {{ input }}
{%- endif %}

{%- if context %}
3. Context: {{ context }}
{%- endif %}

4. Output: {{ output }}

Planning Instructions: {{ planning_instructions }}

Please provide your detailed evaluation plan in this structure:

<plan>
1. [First evaluation step with specific checkpoints]
2. [Second evaluation step with specific checkpoints]
[Continue with numbered steps]
Final. [Verification step to ensure comprehensive evaluation]
</plan>

Note: Ensure each step is specific, measurable, and directly tied to the judging criteria.
"""

generic_text_template_cot_response_prompt = """
Task: Conduct a systematic evaluation of the output by adhering to the established Evaluation Instructions.

Elements for Review:
1. Judging Criteria: {{ eval_instruction }}

{%- if input %}
2. Input: {{ input }}
{%- endif %}

{%- if context %}
3. Context: {{ context }}
{%- endif %}

4. Output: {{ output }}

{%- if fewshots %}
5. {{ fewshots }}
{%- endif %}

6. Evaluation Instructions: {{ evaluation_instructions }}

Provide your evaluation in this structure:

Scoring system:
	•	10: The answer is fully relevant, highly accurate, and covers the input comprehensively; only trivial imperfections or tiny missing details remain.
	•	8: A strong answer that is largely accurate and relevant; it may have a few minor omissions, clarity issues, or small inaccuracies but is still clearly useful.
	•	6: A satisfactory answer that addresses the main points and is generally helpful, yet shows noticeable gaps in depth, supporting detail, or precision.
	•	4: A limited answer that touches on the topic but omits several key details or contains significant inaccuracies; some relevant information is still present.
   •	2: A marginal answer: it makes a genuine attempt and includes at least one correct or relevant idea, but is mostly incomplete, vague, or error-prone.
	•	0: An answer that is essentially off-track or empty, covering none of the critical aspects and offering minimal (or no) value.

<evaluation>
Sometimes the output may contain extra information than what is required to evaluate the output. In such cases, focus on the relevant information and do not penalize the scoring too much.
Strictly provide a RFC8259 compliant JSON format object with key score and explanation only where score is from Scoring system and Provide a single-line, actionable evidence-based explanation of the score from the given information. Explanation should be in markdown format with bullet points only.
</evaluation>

Note:
1. Ensure your evaluation is objective, evidence-based, and directly tied to the judging criteria and evaluation plan. Keep highlighting the most critical factor(s) influencing the score from the given information.
2. Ensure that the Score is based on the Scoring system only from this list [10,8,6,4,2,0] and does not deviate from it.
3. Ensure the response is a RFC8259 compliant JSON format object with key score and explanation.
4. Explanation should be in markdown format with bullet points only.
"""


generic_text_template_cot_response_audio_prompt = """
Task: Conduct a systematic evaluation of the output by adhering to the established Evaluation Instructions.

Elements for Review:
1. Judging Criteria: {{ eval_instruction }}


2. Evaluation Instructions: {{ evaluation_instructions }}

Provide your evaluation in this structure:

Scoring system:
	•	10: The answer is fully relevant, highly accurate, and covers the input comprehensively; only trivial imperfections or tiny missing details remain.
	•	8: A strong answer that is largely accurate and relevant; it may have a few minor omissions, clarity issues, or small inaccuracies but is still clearly useful.
	•	6: A satisfactory answer that addresses the main points and is generally helpful, yet shows noticeable gaps in depth, supporting detail, or precision.
	•	4: A limited answer that touches on the topic but omits several key details or contains significant inaccuracies; some relevant information is still present.
   •	2: A marginal answer: it makes a genuine attempt and includes at least one correct or relevant idea, but is mostly incomplete, vague, or error-prone.
	•	0: An answer that is essentially off-track or empty, covering none of the critical aspects and offering minimal (or no) value.

<evaluation>
Sometimes the output may contain extra information than what is required to evaluate the output. In such cases, focus on the relevant information and do not penalize the scoring too much.
Strictly provide a RFC8259 compliant JSON format object with key score and explanation only where score is from Scoring system and Provide a single-line, actionable explanation based on the judging criteria, highlighting the most critical factor(s) influencing the score from the given information. Explanation should be in markdown format with bullet points only.
</evaluation>

Note:
1. Ensure your evaluation is objective, evidence-based, and directly tied to the judging criteria and evaluation plan. Keep highlighting the most critical factor(s) influencing the score from the given information.
2. Ensure that the Score is based on the Scoring system only from this list [10,8,6,4,2,0] and does not deviate from it.
3. Ensure the response is a RFC8259 compliant JSON format object with key score and explanation.
4. Explanation should be in markdown format with bullet points only.
"""


planning_instructions_generator_prompt = """
Task: Generate planning instructions to evaluate content strictly based on the given criteria.

Evaluation Criteria: {{ eval_instruction }}

Guidelines:
1. Identify the key aspects of the criteria.
2. Break down complex requirements into clear, actionable checkpoints.
3. Ensure instructions are specific, measurable, and directly tied to the criteria.

Response Requirements:
- Provide 2-3 numbered instructions.
- Keep instructions concise, actionable, and criteria-focused.
- Highlight only the critical factors influencing evaluation.

Response Format:
<planning_instructions>
1. [Instruction]
2. [Instruction]
...
</planning_instructions>

NOTE: STRICTLY FOCUS ON Evaluation Criteria TO GENERATE PLANNING INSTRUCTIONS
"""

evaluation_instructions_generator_prompt = """
Task: Generate concise evaluation instructions based on the given criteria and planning steps.

Inputs:
1. Evaluation Criteria: {{ eval_instruction }}
2. Planning Instructions: {{ planning_instructions }}

Requirements:
- Provide 2-3 numbered, actionable evaluation steps.
- Include scoring guidance (1-10 scale).
- Focus solely on objective assessment of the criteria.
- Align instructions with planning steps and emphasize critical factors influencing the score.

Response Format:
<evaluation_instructions>
1. [Evaluation step]
2. [Evaluation step]
...
</evaluation_instructions>

NOTE: STRICTLY FOCUS ON Evaluation Criteria TO GENERATE EVALUATION INSTRUCTIONS

"""
