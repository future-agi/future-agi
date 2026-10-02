rag_template_plan_prompt_whole = """

        Judging Criteria: {eval_instruction}

        Question: {question}

        Answer: {answer}

        Context: {context}

        Task: Develop a step-by-step plan to verify whether the context contains sufficient knowledge to answer the question, and  the provided answer is derived from the context , ensuring alignment with the Judging Criteria. The plan should be clear and broken down into actionable subtasks.

        Plan:

        1.	Analyze Judging Criteria: Review the criteria to understand the key factors for evaluation.
        2.	Identify Key Elements: Extract essential details from the context, question, and answer.
        3.	Break Down Criteria: Decompose the Judging Criteria into specific tasks for focused analysis.
        4.	Establish Sequence: Determine the order in which tasks should be addressed.
        5.	Evaluate Subtasks: Address each subtask using the relevant information from the context.
        6.	Synthesize Findings: Combine the results from each subtask to form a complete assessment.
        7.	Validate Results: Ensure the final assessment meets the Judging Criteria and accurately evaluates the context knowledge.

        Elaboration: Provide detailed steps for each phase, using the key information extracted from the context and eval_instruction.
        """

rag_template_cot_response_prompt_whole = """

        Judging Criteria: {eval_instruction}

        Question: {question}

        Context: {context}

        Task: Utilize the provided plan to determine whether the context contains the necessary knowledge to answer the question and aligns with the Judging Criteria. The final Judgment should include a comprehensive explanation.

        Plan:
        {plan}

        Execution:
        Carry out the steps in the plan to systematically assess the context and question. Conclude with a Judgment supported by a detailed explanation based on the evaluation.
        """

# rag_template_plan_prompt = """


#         Task: You are tasked to  Develop a step-by-step plan to evaluate whether a given context contains sufficient knowledge to answer a specific question. Your evaluation plan should align with the given judging criteria. Your goal is to break down this task into clear, actionable subtasks and provide a detailed plan for execution.

#         1. First, review the evaluation criteria:
#         Judging Criteria: {eval_instruction}

#         2. Next, examine the question, context and answer if provided:
#         Question: {question}

#         Context: {context}

#         Answer: {answer}

#         Follow these steps to Develop a step-by-step plan:

#         a) Analyze the context:
#         - Identify key information and concepts present in the context.
#         - Determine if the context contains relevant information to answer the question.

#         b) Examine the question:
#         - Break down the question into its core components.
#         - Identify what specific information is required to answer the question fully.


#         c) Apply the evaluation criteria:
#         - Go through each point in the evaluation criteria.
#         - Assess how well the context, question and align with each criterion.

#         d) Formulate your evaluation:
#         - Based on your analysis, develop a clear explanation for your evaluation.

#         Elaboration: Ensure that your plan is thorough, clear, and directly addresses all aspects of the judging criteria and provided information. Use concrete examples and specific instructions where possible to make the plan as actionable as possible.
#         """

# rag_template_cot_response_prompt = """
#         Task: Utilize the provided plan to determine Your task is to determine if the context contains the required information to answer the question and if it aligns with the Judging Criteria. You will need to provide a comprehensive explanation for your judgment.

#         Review the following information:
#         Judging Criteria: {eval_instruction}

#         Next, examine the question, context and answer if provided:

#         Question: {question}

#         Context: {context}

#         Answer: {answer}

#         Here is the plan you should follow::
#         {plan}

#         After completing all steps in the plan, formulate your final Judgment. Your Judgment should:
#         a. Clearly state whether the context contains the necessary knowledge to answer the question.
#         b. Indicate how well the context and answer align with the Judging Criteria.
#         c. Provide a detailed explanation supporting your Judgment, referencing specific aspects of the context, question, and Judging Criteria.

#         Remember to be thorough, objective, and base your Judgment solely on the provided information and criteria.
#         """


# 2nd version

rag_template_plan_prompt = """


        Task: Develop a structured evaluation plan to assess whether the provided context meets specific judging criteria.

        1. First, review the evaluation criteria:
        Judging Criteria: {eval_instruction}

        2. Next, examine the context and question if any and answer if provided:
        Question: {question}

        Context: {context}

        Answer: {answer}

Planning Instructions: {planning_instructions}

Please provide your detailed evaluation plan in this structure:

<plan>
1. [First evaluation step with specific checkpoints]
2. [Second evaluation step with specific checkpoints]
[Continue with numbered steps]
Final. [Verification step to ensure comprehensive evaluation]
</plan>

Note: Ensure each step is specific, measurable, and directly tied to the judging criteria.
"""

rag_template_cot_response_prompt = """
        Task: Use the provided Evaluation Instructions to evaluate whether the context aligns with the specified judging criteria.

        Review the following information:
        Judging Criteria: {eval_instruction}

        Next, examine the context and question if any and answer if provided:

        Question: {question}

        Context: {context}

        Answer: {answer}

{fewshots}

5. Evaluation Instructions: {evaluation_instructions}

Provide your evaluation in this structure:


Scoring system:
	•	10: The answer is fully relevant, highly accurate, and covers the input comprehensively; only trivial imperfections or tiny missing details remain.
	•	8: A strong answer that is largely accurate and relevant; it may have a few minor omissions, clarity issues, or small inaccuracies but is still clearly useful.
	•	6: A satisfactory answer that addresses the main points and is generally helpful, yet shows noticeable gaps in depth, supporting detail, or precision.
	•	4: A limited answer that touches on the topic but omits several key details or contains significant inaccuracies; some relevant information is still present.
        •	2: A marginal answer: it makes a genuine attempt and includes at least one correct or relevant idea, but is mostly incomplete, vague, or error-prone.
	•	0: An answer that is essentially off-track or empty, covering none of the critical aspects and offering minimal (or no) value.

<evaluation>
Strictly provide a RFC8259 compliant JSON format object with key score and explanation only where score is from Scoring system and Provide a single-line, actionable evidence-based explanation of the score from the given information.
</evaluation>



Note: Ensure your evaluation is objective, evidence-based, and directly tied to the judging criteria and evaluation plan. Keep highlighting the most critical factor(s) influencing the score from the  given information.
"""
